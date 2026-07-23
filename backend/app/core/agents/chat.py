import json
import logging
from typing import Dict, List, Optional, AsyncGenerator
import anyio

from app.core.llm_manager import LLMManager
from app.mcp.registry import mcp_registry
from app.db.database import SessionLocal
from app.db.crud import save_entity, build_entity_context_block

from .base import BaseAgent
from .planner import PlannerAgent
from .extractor import EntityExtractorAgent
from app.prompts.chat import build_chat_prompt

logger = logging.getLogger(__name__)

# Lazy initialization of LLM manager to prevent DB queries at import time
_llm_manager = None

def get_llm_manager():
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager


class AgentState:
    IDLE                       = "IDLE"
    WAITING_CONFIRMATION       = "WAITING_CONFIRMATION"       # User reviews plan
    EXECUTING                  = "EXECUTING"                  # Tools running
    WAITING_MEMORY_CONFIRMATION = "WAITING_MEMORY_CONFIRMATION"  # User decides what to remember


class ChatAgent(BaseAgent):
    def __init__(self, connection_id: str):
        llm_mgr = get_llm_manager()
        super().__init__(llm_mgr)
        self.connection_id = connection_id
        self.state = AgentState.IDLE
        self.plan: Optional[List[Dict]] = None
        self.chat_history: List[Dict] = []

        # Entities proposed after execution — awaiting user confirmation
        # Format: [{"label": ..., "type": ..., "id": ..., "data": {...}}, ...]
        self._pending_entities: List[Dict] = []
        self.requires_entity_extraction: bool = False
        
        # Instantiate sub-agents
        self.planner = PlannerAgent(llm_mgr)
        self.extractor = EntityExtractorAgent(llm_mgr)

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def get_available_tools(self) -> str:
        """Fetches all available tools from all connected MCP servers in the registry."""
        tools = mcp_registry.list_all_tools()
        if not tools:
            return "No active MCP servers connected. Please authenticate with Google or connect a server first."
        tools_desc = [f"- {t['name']}: {t.get('description', '')}" for t in tools]
        return "\n".join(tools_desc)

    def _get_entity_context(self) -> str:
        """Loads confirmed session entities from SQLite and returns the context block."""
        try:
            db = SessionLocal()
            block = build_entity_context_block(db, self.connection_id)
            db.close()
            return block
        except Exception as e:
            logger.warning(f"Could not load entity context: {e}")
            return ""

    def _call_llm_json(self, messages):
        llm = self.get_llm()
        if not llm:
            return "{}"
        try:
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                stream=True
            )
            full_response = ""
            for chunk in response:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        full_response += delta["content"]
            return full_response
        except Exception as e:
            logger.error(f"LLM JSON call failed: {e}")
            return "{}"

    # ─────────────────────────────────────────────────────────────────────────
    # State machine
    # ─────────────────────────────────────────────────────────────────────────

    async def handle_message(self, message: str, token_callback=None) -> str:
        """Main state machine dispatcher."""

        if self.state == AgentState.IDLE:
            return await self._handle_idle(message, token_callback)

        elif self.state == AgentState.WAITING_CONFIRMATION:
            return await self._handle_confirmation(message, token_callback)

        elif self.state == AgentState.EXECUTING:
            return "I am currently executing the tasks. Please wait..."

        elif self.state == AgentState.WAITING_MEMORY_CONFIRMATION:
            return await self._handle_memory_confirmation(message)

        return "Unknown state."

    async def _handle_idle(self, message: str, token_callback=None) -> str:
        """IDLE → generate plan → WAITING_CONFIRMATION."""
        tools_str = self.get_available_tools()
        if "No active Google session" in tools_str:
            return tools_str

        self.chat_history.append({"role": "user", "content": message})
        
        entity_context = self._get_entity_context()

        # We will pass chat history EXCLUDING the current message to planner, as planner appends it.
        history_for_planner = self.chat_history[:-1]

        # ── Chat Router (Decide if tools needed) ─────────────────────────────
        chat_prompt = build_chat_prompt(tools_str, entity_context)
        messages = [{"role": "system", "content": chat_prompt}]
        messages.extend(self.chat_history)

        chat_response_json = await anyio.to_thread.run_sync(
            lambda: self._call_llm_json(messages)
        )

        try:
            chat_data = json.loads(chat_response_json)
            if chat_data.get("tool") != "invoke_planner":
                direct_response = chat_data.get("response", "I'm not sure how to respond.")
                self.chat_history.append({"role": "assistant", "content": direct_response})
                return direct_response
        except Exception as e:
            logger.warning(f"Chat router failed to parse JSON, treating as raw response: {e}")
            direct_response = chat_response_json
            self.chat_history.append({"role": "assistant", "content": direct_response})
            return direct_response

        # ── Plan Generation ──────────────────────────────────────────────────
        plan_json_str = await anyio.to_thread.run_sync(
            lambda: self.planner.generate_plan(message, tools_str, entity_context, history_for_planner, token_callback)
        )

        try:
            plan_data = json.loads(plan_json_str)
            raw_plan = plan_data.get("plan", [])
            # Validate hallucinated/unsupported tools against active tools in registry
            valid_tool_names = {t["name"] for t in mcp_registry.list_all_tools()}
            self.plan = []
            warnings = []
            for step in raw_plan:
                tool_name = step.get("tool")
                if tool_name in valid_tool_names:
                    self.plan.append(step)
                else:
                    warnings.append(f"Tool `{tool_name}` is not currently available.")

            if not self.plan:
                self.state = AgentState.IDLE
                if warnings:
                    return "**Note:**\n" + "\n".join([f"- {w}" for w in warnings])
                return f"Available tools:\n{tools_str}\n\nWhat would you like me to do with them?"

            # Set requires_entity_extraction to True if any tool is called, since we reverted the planner prompt.
            # We can default to True when planner is invoked.
            self.requires_entity_extraction = True
            self.chat_history.append({"role": "assistant", "content": plan_json_str})
            self.state = AgentState.WAITING_CONFIRMATION

            response = "**Proposed Execution Plan:**\n\n"
            for i, step in enumerate(self.plan):
                response += f"**Step {i+1}: `{step.get('tool')}`**\n"
                if step.get("reason"):
                    response += f"└ *Purpose:* {step.get('reason')}\n"
                
                args = step.get("arguments", {})
                if isinstance(args, dict) and args:
                    response += "└ *Parameters:*\n"
                    for k, v in args.items():
                        if isinstance(v, (dict, list)):
                            val_str = json.dumps(v, ensure_ascii=False)
                        else:
                            val_str = str(v)
                        if len(val_str) > 140:
                            val_str = val_str[:137] + "..."
                        response += f"   • `{k}`: {val_str}\n"

                preview = step.get("payload_preview")
                if preview:
                    preview_str = json.dumps(preview, indent=2, ensure_ascii=False) if isinstance(preview, (dict, list)) else str(preview)
                    formatted_preview = preview_str.strip().replace('\n', '\n> ')
                    response += f"└ *Payload Preview:*\n> {formatted_preview}\n"
                response += "\n"

            if warnings:
                response += "**Warnings:**\n" + "\n".join([f"- {w}" for w in warnings]) + "\n\n"

            response += "Would you like me to proceed with this? (Reply **'yes'** to execute or tell me what to edit)"
            return response

        except json.JSONDecodeError:
            return "Error: LLM did not output valid JSON for the plan."

    async def _handle_confirmation(self, message: str, token_callback=None) -> str:
        """WAITING_CONFIRMATION → confirm → EXECUTING  or  refine plan."""
        self.chat_history.append({"role": "user", "content": message})

        positive_keywords = ['yes', 'proceed', 'go ahead', 'do it', 'sure', 'ok', 'okay', 'yep', 'yeah', 'looks good']
        is_positive = any(word in message.lower() for word in positive_keywords)

        if is_positive and len(message.split()) < 10:
            self.state = AgentState.EXECUTING
            return "Great! Proceeding with the execution... (Please wait)"
        else:
            tools_str = self.get_available_tools()
            entity_context = self._get_entity_context()
            plan_json_str = await anyio.to_thread.run_sync(
                lambda: self.planner.generate_plan(
                    "Please refine the plan based on my previous feedback.",
                    tools_str, entity_context, self.chat_history, token_callback
                )
            )
            try:
                plan_data = json.loads(plan_json_str)
                self.plan = plan_data.get("plan", [])
                self.chat_history.append({"role": "assistant", "content": plan_json_str})
                response = "I have refined the execution plan:\n\n"
                for i, step in enumerate(self.plan):
                    response += f"{i+1}. **{step.get('tool')}**: {step.get('reason')}\n"
                response += "\nIs this better? (Reply 'yes' to proceed)"
                return response
            except json.JSONDecodeError:
                return "Error parsing refined plan from LLM."

    async def _handle_memory_confirmation(self, message: str) -> str:
        """
        WAITING_MEMORY_CONFIRMATION → parse user selection or custom user memory directive → save → IDLE.

        Accepts:
          "yes" / "all"       → save all AI suggestions
          "no" / "skip"       → skip all
          "1 3" / "1, 2"      → save specific suggestions by index
          "remember ..."      → save custom user note with TOP PRIORITY
          "label name"        → match and save suggested item
        """
        import re
        msg_raw = message.strip()
        msg_lower = msg_raw.lower()
        entities = self._pending_entities

        # ── Hard no ──────────────────────────────────────────────────────────
        if msg_lower in ("no", "n", "skip", "nope", "nah", "dont save", "don't save", "nothing"):
            self._pending_entities = []
            self.state = AgentState.IDLE
            return "Got it. Nothing saved. What would you like to do next?"

        # ── Explicit user custom memory directive ("remember that...", "note that...", "save ...") ──
        custom_prefixes = ["remember", "note", "keep in mind", "save that", "save note"]
        is_custom_directive = any(msg_lower.startswith(p) for p in custom_prefixes)

        if is_custom_directive:
            # Extract the actual custom note body
            custom_note = msg_raw
            for p in custom_prefixes:
                if msg_lower.startswith(p):
                    custom_note = msg_raw[len(p):].strip(" :,-").capitalize()
                    break

            try:
                db = SessionLocal()
                save_entity(
                    db=db,
                    conversation_id=self.connection_id,
                    label=custom_note[:60],
                    entity_type="user_custom_note",
                    entity_id=f"custom_{hash(custom_note)}",
                    data={"custom_note": custom_note, "user_directed": True}
                )
                db.close()
                self._pending_entities = []
                self.state = AgentState.IDLE
                return (
                    f"**Saved your custom note to memory (Top Priority):**\n"
                    f"  - \"{custom_note}\"\n\n"
                    "I'll remember this for all future steps. What's next?"
                )
            except Exception as ex:
                logger.error(f"Failed to save custom note: {ex}")
                self._pending_entities = []
                self.state = AgentState.IDLE
                return f"Could not save custom note: {ex}"

        # ── Hard yes (Save all suggested entities) ───────────────────────────
        selected = []
        if msg_lower in ("yes", "y", "all", "save all", "yeah", "yep", "ok", "okay", "sure", "save"):
            selected = entities

        # ── Numeric selection: "1 3", "1, 2" ─────────────────────────────────
        elif re.search(r'\d', msg_lower):
            nums = re.findall(r'\d+', msg_lower)
            indices = [int(n) - 1 for n in nums]
            selected = [entities[i] for i in indices if 0 <= i < len(entities)]

        # ── Fuzzy match against suggested entity labels ──────────────────────
        else:
            for e in entities:
                label_lower = e.get("label", "").lower()
                if any(word in msg_lower for word in label_lower.split()):
                    selected.append(e)

            # If still nothing matched, treat user's message as a custom memory note!
            if not selected and len(msg_raw.split()) > 2:
                try:
                    db = SessionLocal()
                    save_entity(
                        db=db,
                        conversation_id=self.connection_id,
                        label=msg_raw[:60],
                        entity_type="user_custom_note",
                        entity_id=f"custom_{hash(msg_raw)}",
                        data={"custom_note": msg_raw, "user_directed": True}
                    )
                    db.close()
                    self._pending_entities = []
                    self.state = AgentState.IDLE
                    return (
                        f"**Saved custom note to memory (Top Priority):**\n"
                        f"  - \"{msg_raw}\"\n\n"
                        "What would you like to do next?"
                    )
                except Exception as ex:
                    logger.error(f"Failed to save custom memory: {ex}")

        if not selected:
            self._pending_entities = []
            self.state = AgentState.IDLE
            return "Got it. What would you like to do next?"

        # Persist selected suggested entities to SQLite
        try:
            db = SessionLocal()
            saved_labels = []
            for e in selected:
                save_entity(
                    db=db,
                    conversation_id=self.connection_id,
                    label=e["label"],
                    entity_type=e["type"],
                    entity_id=e["id"],
                    data=e["data"]
                )
                saved_labels.append(f"\"{e['label']}\" ({e['type']})")
            db.close()
        except Exception as ex:
            logger.error(f"Failed to save entities: {ex}")
            self._pending_entities = []
            self.state = AgentState.IDLE
            return f"Could not save to memory: {ex}"

        self._pending_entities = []
        self.state = AgentState.IDLE
        saved_list = "\n".join([f"  - {l}" for l in saved_labels])
        return (
            f"Saved to session memory:\n{saved_list}\n\n"
            "I'll use these directly in future responses — no need to re-fetch. What's next?"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Plan execution
    # ─────────────────────────────────────────────────────────────────────────

    async def execute_plan(self) -> AsyncGenerator[str, None]:
        """Executes the approved plan step by step, then proposes entities to remember."""
        if self.state != AgentState.EXECUTING or not self.plan:
            yield "No plan to execute."
            return

        all_tools = mcp_registry.list_all_tools()
        if not all_tools:
            yield "Error: No connected MCP servers found."
            self.state = AgentState.IDLE
            return

        # Run each tool step and collect raw results
        tool_results: List[Dict] = []

        for i, step in enumerate(self.plan):
            tool_name = step.get("tool")
            arguments = step.get("arguments", {})

            yield f"\nExecuting Task {i+1}: Calling `{tool_name}`...\n"

            try:
                result = await anyio.to_thread.run_sync(
                    lambda t=tool_name, a=arguments: mcp_registry.call_tool(t, a)
                )
                result_str = str(result)
                yield f"Result for `{tool_name}`:\n{result_str[:1000]}\n"

                # Collect result for entity extraction
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result_str
                })
            except Exception as e:
                logger.error(f"Tool execution failed: {e}")
                yield f"Error executing `{tool_name}`: {e}\n"
                break

        yield "\nExecution complete!"

        # Go back to IDLE
        self.state = AgentState.IDLE
        self.plan = None
        yield "\n\nWhat would you like to do next?"

    # ─────────────────────────────────────────────────────────────────────────
    # Manual Memory Extraction
    # ─────────────────────────────────────────────────────────────────────────

    async def extract_memory(self, content: str) -> AsyncGenerator[str, None]:
        """Manually triggered by the UI to extract memory from a context window."""
        if self.state != AgentState.IDLE:
            yield "Please finish your current action before saving to memory."
            return

        yield "\nAnalyzing text for things worth remembering...\n"
        
        try:
            # Wrap content in a generic structure the extractor understands
            tool_results = [{"tool": "manual_extraction", "arguments": {}, "result": content}]
            entities_json_str = await anyio.to_thread.run_sync(
                lambda: self.extractor.extract_entities(tool_results)
            )
            entities_data = json.loads(entities_json_str)
            proposed = entities_data.get("entities", [])
        except Exception as e:
            logger.error(f"Entity extraction error: {e}")
            proposed = []

        if proposed:
            self._pending_entities = proposed
            self.state = AgentState.WAITING_MEMORY_CONFIRMATION

            lines = ["\nI found these items worth remembering:\n"]
            for idx, e in enumerate(proposed, start=1):
                lines.append(f"  {idx}. **{e.get('label')}** ({e.get('type')})")
            lines.append(
                "\nSave to session memory? Reply:\n"
                "  • **yes** — save all\n"
                "  • **no** — skip\n"
                "  • **numbers** (e.g. `1 3`) — save only those"
            )
            yield "\n".join(lines)
        else:
            yield "\nNo new entities found to save."
