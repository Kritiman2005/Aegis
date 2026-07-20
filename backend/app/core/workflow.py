import json
import logging
from typing import Dict, List, Optional, AsyncGenerator
import anyio

from app.core.llm_manager import LLMManager
from app.mcp.registry import mcp_registry
from app.db.database import SessionLocal
from app.db.crud import save_entity, build_entity_context_block
from app.prompts import build_planner_prompt, ENTITY_EXTRACTOR_SYSTEM, build_entity_extractor_user_msg

logger = logging.getLogger(__name__)

# Initialize a global LLM manager for the workflow
llm_manager = LLMManager()


class AgentState:
    IDLE                       = "IDLE"
    WAITING_CONFIRMATION       = "WAITING_CONFIRMATION"       # User reviews plan
    EXECUTING                  = "EXECUTING"                  # Tools running
    WAITING_MEMORY_CONFIRMATION = "WAITING_MEMORY_CONFIRMATION"  # User decides what to remember


class AgentSession:
    def __init__(self, connection_id: str):
        self.connection_id = connection_id
        self.state = AgentState.IDLE
        self.plan: Optional[List[Dict]] = None
        self.chat_history: List[Dict] = []

        # Entities proposed after execution — awaiting user confirmation
        # Format: [{"label": ..., "type": ..., "id": ..., "data": {...}}, ...]
        self._pending_entities: List[Dict] = []

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

    # ─────────────────────────────────────────────────────────────────────────
    # LLM calls (all synchronous — run via anyio thread pool)
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_plan_sync(self, user_message: str, tools_str: str, token_callback=None) -> str:
        """Generates a tool execution plan as a JSON string."""
        try:
            llm = llm_manager.get_model("gemma-local")
        except Exception as e:
            return json.dumps({"error": f"LLM not loaded: {e}"})

        # Inject confirmed session entities so LLM can reference them directly
        entity_context = self._get_entity_context()

        # Build system prompt from the prompts package
        system_prompt = build_planner_prompt(tools_str, entity_context)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.chat_history)
        messages.append({"role": "user", "content": user_message})

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
                        token = delta["content"]
                        full_response += token
                        if token_callback:
                            token_callback(token)
            return full_response
        except Exception as e:
            logger.error(f"LLM plan generation failed: {e}")
            return json.dumps({"error": "Failed to generate plan."})

    def _extract_entities_sync(self, tool_results: List[Dict]) -> str:
        """
        After execution, asks the LLM to identify entities worth remembering
        from the tool results. Returns a JSON string.
        """
        try:
            llm = llm_manager.get_model("gemma-local")
        except Exception as e:
            return json.dumps({"entities": []})

        results_text = json.dumps(tool_results, indent=2, ensure_ascii=False)

        messages = [
            {"role": "system", "content": ENTITY_EXTRACTOR_SYSTEM},
            {"role": "user", "content": build_entity_extractor_user_msg(results_text)}
        ]

        try:
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=1024
            )
            return response["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return json.dumps({"entities": []})

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

        # ── Meta-query interceptor (no LLM needed) ──────────────────────────
        meta_keywords = [
            "list tools", "list the tools", "show tools", "what tools",
            "available tools", "what can you do", "help", "what are the tools",
            "which tools", "tools available", "mcp tools", "list mcp"
        ]
        msg_lower = message.strip().lower()
        if any(kw in msg_lower for kw in meta_keywords):
            tools = mcp_manager.list_tools()
            lines = [f"\n🛠️  Available tools ({len(tools)}):\n"]
            for t in tools:
                lines.append(f"  • **{t['name']}** — {t['description']}")
            lines.append("\nJust describe what you'd like to do and I'll use them automatically.")
            return "\n".join(lines)
        # ────────────────────────────────────────────────────────────────────

        self.chat_history.append({"role": "user", "content": message})

        plan_json_str = await anyio.to_thread.run_sync(
            lambda: self._generate_plan_sync(message, tools_str, token_callback)
        )

        try:
            plan_data = json.loads(plan_json_str)
            raw_plan = plan_data.get("plan", [])
            # Validate hallucinated/unsupported tools against active tools in registry
            valid_tool_names = {t["name"] for t in mcp_registry.list_all_tools()}
            self.plan = []
            for step in raw_plan:
                tool_name = step.get("tool")
                if tool_name in valid_tool_names:
                    self.plan.append(step)
                else:
                    warnings.append(f"Tool `{tool_name}` is not currently available.")

            if not self.plan:
                self.state = AgentState.IDLE
                if warnings:
                    return "Note:\n" + "\n".join([f"- {w}" for w in warnings])
                return f"Available tools:\n{tools_str}\n\nWhat would you like me to do with them?"

            self.chat_history.append({"role": "assistant", "content": plan_json_str})
            self.state = AgentState.WAITING_CONFIRMATION

            response = "📋 **Proposed Execution Plan:**\n\n"
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
                response += "⚠️ **Warnings:**\n" + "\n".join([f"- {w}" for w in warnings]) + "\n\n"

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
            plan_json_str = await anyio.to_thread.run_sync(
                lambda: self._generate_plan_sync(
                    "Please refine the plan based on my previous feedback.",
                    tools_str, token_callback
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
                    f"💾 **Saved your custom note to memory (Top Priority):**\n"
                    f"  ✅ \"{custom_note}\"\n\n"
                    "I'll remember this for all future steps. What's next?"
                )
            except Exception as ex:
                logger.error(f"Failed to save custom note: {ex}")
                self._pending_entities = []
                self.state = AgentState.IDLE
                return f"⚠️ Could not save custom note: {ex}"

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
                        f"💾 **Saved custom note to memory (Top Priority):**\n"
                        f"  ✅ \"{msg_raw}\"\n\n"
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
            return f"⚠️ Could not save to memory: {ex}"

        self._pending_entities = []
        self.state = AgentState.IDLE
        saved_list = "\n".join([f"  ✅ {l}" for l in saved_labels])
        return (
            f"💾 Saved to session memory:\n{saved_list}\n\n"
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

            yield f"\n⏳ Executing Task {i+1}: Calling `{tool_name}`...\n"

            try:
                result = await anyio.to_thread.run_sync(
                    lambda t=tool_name, a=arguments: mcp_registry.call_tool(t, a)
                )
                result_str = str(result)
                yield f"✅ Result for `{tool_name}`:\n{result_str[:1000]}\n"

                # Collect result for entity extraction
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result_str
                })
            except Exception as e:
                logger.error(f"Tool execution failed: {e}")
                yield f"❌ Error executing `{tool_name}`: {e}\n"
                break

        yield "\n🎉 Execution complete!"

        # ── Entity extraction ────────────────────────────────────────────────
        if tool_results:
            yield "\n\n🔍 Analysing results for things worth remembering...\n"
            try:
                entities_json_str = await anyio.to_thread.run_sync(
                    lambda: self._extract_entities_sync(tool_results)
                )
                entities_data = json.loads(entities_json_str)
                proposed = entities_data.get("entities", [])
            except Exception as e:
                logger.error(f"Entity extraction error: {e}")
                proposed = []

            if proposed:
                self._pending_entities = proposed
                self.state = AgentState.WAITING_MEMORY_CONFIRMATION

                lines = ["\n💾 I found these items worth remembering for this session:\n"]
                for idx, e in enumerate(proposed, start=1):
                    lines.append(f"  {idx}. **{e.get('label')}** ({e.get('type')})")
                lines.append(
                    "\nSave to session memory? Reply:\n"
                    "  • **yes** — save all\n"
                    "  • **no** — skip\n"
                    "  • **numbers** (e.g. `1 3`) — save only those"
                )
                yield "\n".join(lines)
                return  # Stay in WAITING_MEMORY_CONFIRMATION

        # Nothing to propose — go back to IDLE
        self.state = AgentState.IDLE
        self.plan = None
        yield "\n\nWhat would you like to do next?"
