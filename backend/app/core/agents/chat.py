import json
import logging
from typing import Dict, List, Optional, AsyncGenerator, Any
import anyio

from app.core.llm_manager import LLMManager
from app.mcp.registry import mcp_registry
from app.db.database import SessionLocal
from app.db.crud import save_entity, build_entity_context_block

from .base import BaseAgent
from .planner import PlannerAgent
from .executor import ExecutorAgent
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
        import time
        llm_mgr = get_llm_manager()
        super().__init__(llm_mgr)
        self.connection_id = connection_id
        self._state = AgentState.IDLE
        self.state_entered_at = time.time()
        self.plan: Optional[List[Dict]] = None
        
        # Start with empty history — loaded lazily after WebSocket handshake via load_history()
        self.chat_history: List[Dict] = []

        # Entities proposed after execution — awaiting user confirmation
        # Format: [{"label": ..., "type": ..., "id": ..., "data": {...}}, ...]
        self._pending_entities: List[Dict] = []
        self.requires_entity_extraction: bool = False
        
        # Instantiate sub-agents
        self.planner = PlannerAgent(llm_mgr)
        self.executor = ExecutorAgent(llm_mgr)
        self.extractor = EntityExtractorAgent(llm_mgr)

    def load_history(self):
        """Load chat history from DB. Called after WebSocket handshake to avoid blocking."""
        try:
            from app.db.crud import get_chat_history
            db = SessionLocal()
            self.chat_history = get_chat_history(db, self.connection_id)
            db.close()
        except Exception as e:
            logger.warning(f"Could not load chat history: {e}")
            self.chat_history = []

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        import time
        self._state = value
        self.state_entered_at = time.time()

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

    def get_searched_tools(self, query: str) -> str:
        """Fetches top-k relevant tools from registry using semantic RAG based on the query."""
        tools = mcp_registry.search_tools(query, top_k=10)
        if not tools:
            return ""
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

    def _call_llm_text(self, messages, token_callback=None):
        llm = self.get_llm()
        if not llm:
            return ""
        try:
            response = llm.create_chat_completion(
                messages=messages,
                temperature=0.7,
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
            logger.error(f"LLM TEXT call failed: {e}")
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # State machine
    # ─────────────────────────────────────────────────────────────────────────

    async def handle_message(self, message: str, mode: str = "chat", token_callback=None, status_callback=None) -> str:
        """Main state machine dispatcher."""
        
        if message == "__system_mode_switch__":
            if self.state in [AgentState.WAITING_CONFIRMATION, AgentState.WAITING_MEMORY_CONFIRMATION]:
                self.state = AgentState.IDLE
                self.plan = None
                self._pending_entities = []
                return "__system_toast__:Pending action discarded."
            return ""

        if self.state == AgentState.IDLE:
            return await self._handle_idle(message, mode, token_callback, status_callback)

        elif self.state == AgentState.WAITING_CONFIRMATION:
            return await self._handle_confirmation(message, token_callback)

        elif self.state == AgentState.EXECUTING:
            return "I am currently executing the tasks. Please wait..."

        elif self.state == AgentState.WAITING_MEMORY_CONFIRMATION:
            return await self._handle_memory_confirmation(message)

        return "Unknown state."

    async def _handle_idle(self, message: str, mode: str = "chat", token_callback=None, status_callback=None) -> str:
        """IDLE → generate plan → WAITING_CONFIRMATION."""
        self.chat_history.append({"role": "user", "content": message})
        
        # Persist user message to DB
        try:
            from app.db.crud import add_chat_message
            db = SessionLocal()
            add_chat_message(db, self.connection_id, "user", message)
            db.close()
        except Exception as e:
            logger.warning(f"Failed to persist user message: {e}")
        
        entity_context = self._get_entity_context()

        # We will pass chat history EXCLUDING the current message to planner, as planner appends it.
        # Trim to the last 6 messages and cap each message to 600 chars to stay within the
        # local LLM's 4096-token context window and prevent the planner from hanging.
        _MAX_PLANNER_HISTORY = 6
        _MAX_MSG_CHARS = 600
        raw_history = self.chat_history[:-1]
        history_for_planner = [
            {"role": m["role"], "content": m["content"][:_MAX_MSG_CHARS] + ("..." if len(m["content"]) > _MAX_MSG_CHARS else "")}
            for m in raw_history[-_MAX_PLANNER_HISTORY:]
        ]

        # ── Mode Branching ─────────────────────────────
        if mode == "chat":
            # 1. RAG Retrieval for Uploaded Documents
            try:
                from app.core.rag.processor import hybrid_search
                # Retrieve top 5 most relevant chunks across user's docs for this session
                relevant_chunks = hybrid_search(query=message, conversation_id=self.connection_id, top_k=5)
            except Exception as e:
                logger.warning(f"RAG search failed: {e}")
                relevant_chunks = []
                
            document_context = ""
            if relevant_chunks:
                document_context = "Relevant excerpts from your uploaded documents:\n\n"
                for chunk in relevant_chunks:
                    document_context += f"--- Source: {chunk.get('filename')} ---\n{chunk.get('content')}\n\n"

            from app.prompts.chat import build_chat_prompt
            # Append document context to the base entity context
            full_context = entity_context
            if document_context:
                full_context += "\n" + document_context
                
            chat_prompt = build_chat_prompt(full_context)
            messages = [{"role": "system", "content": chat_prompt}]
            messages.extend(self.chat_history)

            # In chat mode, we expect pure raw text, no JSON.
            chat_response = await anyio.to_thread.run_sync(
                lambda: self._call_llm_text(messages, token_callback)
            )
            
            self.chat_history.append({"role": "assistant", "content": chat_response})
            
            # Persist assistant message to DB
            try:
                from app.db.crud import add_chat_message
                db = SessionLocal()
                add_chat_message(db, self.connection_id, "assistant", chat_response)
                db.close()
            except Exception as e:
                logger.warning(f"Failed to persist assistant message: {e}")

            return chat_response

        # If mode == "agent", we skip the Chat LLM and go straight to Plan Generation
        tools_str = self.get_searched_tools(message)
        if not tools_str:
            return "I couldn't find any connected tools relevant to your request. Are you sure you have the right MCP servers connected?"

        # ── Plan Generation & Self-Correction Loop ────────────────────────────
        import jsonschema
        all_tools = mcp_registry.list_all_tools()
        valid_tool_names = {t["name"] for t in all_tools}
        tool_schemas = {t["name"]: t.get("inputSchema", {}) for t in all_tools}
        
        if status_callback:
            await status_callback("Drafting execution plan...")

        plan_json_str = await anyio.to_thread.run_sync(
            lambda: self.planner.generate_plan(message, tools_str, entity_context, history_for_planner, None)
        )

        try:
            plan_data = json.loads(plan_json_str)
        except json.JSONDecodeError:
            return "Error: Planner output invalid JSON."
        
        if isinstance(plan_data, list):
            raw_plan = plan_data
        else:
            raw_plan = plan_data.get("plan", [])
            
        # Handle clarification escape hatch
        if isinstance(plan_data, dict) and plan_data.get("clarifying_question"):
            question = plan_data.get("clarifying_question")
            self.state = AgentState.IDLE
            self.chat_history.append({"role": "assistant", "content": question})
            
            # Persist clarifying question to DB
            try:
                from app.db.crud import add_chat_message
                db = SessionLocal()
                add_chat_message(db, self.connection_id, "assistant", question)
                db.close()
            except Exception as e:
                logger.warning(f"Failed to persist clarifying question: {e}")

            return question
        
        # Filter out placeholder tools that the LLM might hallucinate when no tools are needed
        valid_plan = []
        for step in raw_plan:
            if isinstance(step, dict) and step.get("tool"):
                tool_name = str(step.get("tool")).lower()
                if tool_name not in ("none", "none_available", "null", "n/a", "unknown"):
                    valid_plan.append(step)
        raw_plan = valid_plan
        
        # Basic validation
        validation_errors = []
        for step in raw_plan:
            tool_name = step.get("tool")
            if tool_name not in valid_tool_names:
                validation_errors.append(f"Tool `{tool_name}` does not exist.")
        
        if validation_errors:
            self.state = AgentState.IDLE
            return f"❌ Planner hallucinated invalid tools: {validation_errors[0]}. Please try rephrasing your request."
        
        # 2. Metadata Validation (Dependencies, IDs)
        self.plan = []
        
        if isinstance(plan_data, dict):
            warnings = plan_data.get("warnings", [])
        else:
            warnings = []
            
        if isinstance(warnings, str):
            warnings = [warnings]
        
        # First pass: collect all declared step_ids
        all_step_ids = {step.get("step_id") for step in raw_plan if step.get("step_id")}
        
        for step in raw_plan:
            tool_name = step.get("tool")
            if tool_name not in valid_tool_names:
                continue # Already caught above, but safe to skip
            
            # Validate depends_on hallucinated IDs
            depends_on = step.get("depends_on")
            if isinstance(depends_on, list):
                for did in depends_on:
                    if did not in all_step_ids:
                        self.state = AgentState.IDLE
                        self.plan = None
                        return f"❌ Planner generated an invalid dependency (`{did}`). The plan has been aborted for safety. Please try rephrasing your request."
                step["depends_on"] = depends_on
            else:
                step["depends_on"] = []
                
            # Validate foreach target
            foreach_target = step.get("foreach")
            if foreach_target and foreach_target not in all_step_ids:
                step["foreach"] = None
                
            # Ensure every step has an ID
            if not step.get("step_id"):
                import uuid
                step["step_id"] = f"step_{str(uuid.uuid4())[:8]}"
                all_step_ids.add(step["step_id"])
                
            self.plan.append(step)

        if not self.plan:
            self.state = AgentState.IDLE
            if warnings:
                return "**Note:**\n" + "\n".join([f"- {w}" for w in warnings])
            return f"Available tools:\n{tools_str}\n\nWhat would you like me to do with them?"

        # Set requires_entity_extraction to True if any tool is called, since we reverted the planner prompt.
        # We can default to True when planner is invoked.
        self.requires_entity_extraction = True
        self.state = AgentState.WAITING_CONFIRMATION

        response = "**Proposed Execution Plan:**\n\n"
        for i, step in enumerate(self.plan):
            response += f"**Step {i+1}: `{step.get('tool')}`**\n"
            if step.get("reason"):
                response += f"> {step.get('reason')}\n"
            
            depends = step.get("depends_on")
            if depends:
                response += f"- *Depends on:* {', '.join(depends)}\n"
            response += "\n"

        if warnings:
            response += "**Warnings:**\n" + "\n".join([f"- {w}" for w in warnings]) + "\n\n"

        # Inject the raw JSON block invisibly at the end so the UI can parse it for the interactive card
        response += f"\n```json\n{plan_json_str}\n```\n\n"

        response += "Would you like me to proceed with this? (Reply **'yes'** to execute or tell me what to edit)"
        
        self.chat_history.append({"role": "assistant", "content": response})
        try:
            from app.db.crud import add_chat_message
            db = SessionLocal()
            add_chat_message(db, self.connection_id, "assistant", response)
            db.close()
        except Exception as e:
            logger.warning(f"Failed to persist planner response: {e}")

        return response

    async def _handle_confirmation(self, message: str, token_callback=None) -> str:
        """WAITING_CONFIRMATION → confirm → EXECUTING  or  refine plan."""
        self.chat_history.append({"role": "user", "content": message})
        
        # Persist user message to DB
        try:
            from app.db.crud import add_chat_message
            db = SessionLocal()
            add_chat_message(db, self.connection_id, "user", message)
            db.close()
        except Exception as e:
            logger.warning(f"Failed to persist user message: {e}")


        positive_keywords = ['yes', 'proceed', 'go ahead', 'do it', 'sure', 'ok', 'okay', 'yep', 'yeah', 'looks good']
        is_positive = any(word in message.lower() for word in positive_keywords)

        if is_positive and len(message.split()) < 10:
            self.state = AgentState.EXECUTING
            return "Great! Proceeding with the execution... (Please wait)"
            
        cancel_keywords = ['cancel', 'abort', 'stop', 'nevermind']
        if any(word in message.lower() for word in cancel_keywords):
            self.state = AgentState.IDLE
            self.plan = []
            return "Plan cancelled. What would you like to do next?"
            
        else:
            # ── Plan Refinement Loop: Questions vs Edits ─────────────────────
            msg_lower = message.lower().strip()
            edit_verbs = ['change', 'update', 'use', 'make', 'edit', 'add', 'remove', 'instead', 'no', 'dont', 'do not']
            is_question = "?" in msg_lower and not any(verb in msg_lower for verb in edit_verbs)

            if is_question:
                entity_context = self._get_entity_context()
                from app.prompts.chat import build_chat_prompt
                chat_prompt = build_chat_prompt(entity_context)
                
                system_injection = (
                    f"\n\n[SYSTEM]: The user has a pending plan they are reviewing. "
                    f"The current plan is: {json.dumps(self.plan)}. "
                    f"Answer their question about the plan conversationally. Do NOT execute it."
                )
                
                messages = [{"role": "system", "content": chat_prompt + system_injection}]
                messages.extend(self.chat_history)
                
                response_text = await anyio.to_thread.run_sync(
                    self._call_llm_text, messages
                )
                final_response = response_text + "\n\n*(Plan is still pending. Reply 'yes' to execute or tell me what to change)*"
                self.chat_history.append({"role": "assistant", "content": final_response})
                
                # Persist assistant response to DB
                try:
                    from app.db.crud import add_chat_message
                    db = SessionLocal()
                    add_chat_message(db, self.connection_id, "assistant", final_response)
                    db.close()
                except Exception as e:
                    logger.warning(f"Failed to persist assistant response: {e}")
                    
                return final_response

            # Otherwise, treat as an edit request and route to Planner
            tools_str = self.get_searched_tools(message)
            if not tools_str:
                return "I couldn't find any tools relevant to that edit request. Please clarify what you want to do."
                
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
                
                response = "I have refined the execution plan:\n\n"
                for i, step in enumerate(self.plan):
                    response += f"{i+1}. **{step.get('tool')}**: {step.get('reason')}\n"
                response += "\nIs this better? (Reply 'yes' to proceed)"
                
                self.chat_history.append({"role": "assistant", "content": response})
                
                # Persist refined plan to DB
                try:
                    from app.db.crud import add_chat_message
                    db = SessionLocal()
                    add_chat_message(db, self.connection_id, "assistant", response)
                    db.close()
                except Exception as e:
                    logger.warning(f"Failed to persist refined plan: {e}")
                    
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

        # ── JSON Override from UI ────────────────────────────────────────────
        edit_prefix = "Please use exactly this updated memory:\n\n"
        if msg_raw.startswith(edit_prefix):
            edited_json = msg_raw[len(edit_prefix):].strip()
            try:
                selected = json.loads(edited_json)
                msg_lower = "yes" # Force the 'yes' branch to save it
                entities = selected # Replace pending entities with the edited ones
            except json.JSONDecodeError:
                return "Error parsing updated memory JSON. Please try again."

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

    async def execute_plan(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Executes the approved plan step by step, then proposes entities to remember."""
        if self.state != AgentState.EXECUTING or not self.plan:
            yield {"text": "No plan to execute.", "node_id": None}
            return

        all_tools = mcp_registry.list_all_tools()
        if not all_tools:
            yield {"text": "Error: No connected MCP servers found.", "node_id": None}
            self.state = AgentState.IDLE
            return

        tool_schemas = {t["name"]: t.get("inputSchema", {}) for t in all_tools}
        import jsonschema
        import traceback

        # Retrieve context for the ExecutorAgent
        # Trim chat history to last 6 messages, capped at 600 chars each, to keep the executor
        # within the local LLM context window.
        _MAX_EXEC_HISTORY = 6
        _MAX_EXEC_CHARS = 600
        trimmed_history = [
            {"role": m["role"], "content": m["content"][:_MAX_EXEC_CHARS] + ("..." if len(m["content"]) > _MAX_EXEC_CHARS else "")}
            for m in self.chat_history[-_MAX_EXEC_HISTORY:]
        ]
        full_chat_history = json.dumps(trimmed_history, indent=2)
        entity_context = self._get_entity_context()

        # Run each tool step and collect raw results
        tool_results: List[Dict] = []
        prior_results_map: Dict[str, Any] = {}
        total_steps = len(self.plan)

        for i, step in enumerate(self.plan):
            tool_name = step.get("tool")
            node_id = step.get("step_id")
            step_reason = step.get("reason", "")
            
            yield {"text": f"\nExecuting Task {i+1}/{total_steps}: Calling `{tool_name}`...\n", "node_id": node_id, "status": "running"}

            schema = tool_schemas.get(tool_name, {})
            if not schema:
                yield {"text": f"❌ Plan aborted: Tool `{tool_name}` no longer exists.\n", "node_id": node_id, "status": "failed"}
                self.state = AgentState.IDLE
                self.plan = None
                return

            yield {"text": f"Generating exact parameters for `{tool_name}`...\n", "node_id": node_id, "status": "running"}

            # Generate arguments live using the deterministic Executor Agent
            arguments = await anyio.to_thread.run_sync(
                lambda: self.executor.generate_arguments(
                    tool_name=tool_name,
                    tool_schema=schema,
                    overall_plan=self.plan,
                    step_reason=step_reason,
                    prior_results=prior_results_map,
                    entity_context=entity_context,
                    user_request=full_chat_history
                )
            )

            # Handle Executor Escape Hatch
            if isinstance(arguments, dict) and "error" in arguments:
                err_msg = arguments["error"]
                logger.error(f"Executor aborted for {tool_name}: {err_msg}")
                yield {"text": f"❌ Plan aborted: {err_msg}\n", "node_id": node_id, "status": "failed"}
                self.state = AgentState.IDLE
                self.plan = None
                return

            # Single strict check to catch catastrophic failure
            try:
                jsonschema.validate(instance=arguments, schema=schema)
            except jsonschema.exceptions.ValidationError as e:
                logger.error(f"Executor failed schema validation for {tool_name}: {e.message}")
                yield {"text": f"❌ Plan aborted: Executor generated invalid arguments for `{tool_name}`: {e.message}\n", "node_id": node_id, "status": "failed"}
                self.state = AgentState.IDLE
                self.plan = None
                return

            try:
                result = await anyio.to_thread.run_sync(
                    lambda t=tool_name, a=arguments: mcp_registry.call_tool(t, a)
                )
                result_str = str(result)
                
                # Send the entire output to the UI without truncation
                display_str = result_str
                yield {"text": f"Result for `{tool_name}`:\n{display_str}\n", "node_id": node_id, "status": "completed"}

                # Collect result for next steps and extraction
                prior_results_map[node_id] = result
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result_str
                })
            except Exception as e:
                logger.error(f"Tool execution failed at step {i+1}: {e}\n{traceback.format_exc()}")
                
                error_type = type(e).__name__
                if "HttpError" in error_type or "RuntimeError" in error_type:
                    msg = f"API Error executing `{tool_name}`: {e}"
                else:
                    msg = f"Internal bug executing `{tool_name}`: {e}"
                
                # Report partial progress
                if i > 0:
                    progress_msg = f"\nStep {i+1} of {total_steps} failed. Steps 1-{i} completed successfully, but all remaining steps have been aborted."
                else:
                    progress_msg = f"\nStep {i+1} of {total_steps} failed. The plan has been aborted."
                    
                yield {"text": f"❌ {msg}\n{progress_msg}\n", "node_id": node_id, "status": "failed"}
                
                # Append partial results to chat history so the LLM remembers what worked before the failure
                if tool_results:
                    # Provide full text to the LLM context without arbitrary truncation
                    summary = "\n".join([f"Tool `{r['tool']}` output:\n{r['result']}" for r in tool_results])
                    content = f"Execution Results (Partial before failure):\n{summary}"
                    self.chat_history.append({
                        "role": "assistant",
                        "content": content
                    })
                    try:
                        from app.db.crud import add_chat_message
                        db = SessionLocal()
                        add_chat_message(db, self.connection_id, "assistant", content)
                        db.close()
                    except Exception as e:
                        logger.warning(f"Failed to persist partial execution results: {e}")

                # Hard-fail and reset
                self.state = AgentState.IDLE
                self.plan = None
                return

        yield {"text": "\nExecution complete!", "node_id": None}

        # Append summary of results to chat history so the LLM remembers them for the next turn
        if tool_results:
            # Provide full text to the LLM context without arbitrary truncation
            summary = "\n".join([f"Tool `{r['tool']}` output:\n{r['result']}" for r in tool_results])
            content = f"Execution Results:\n{summary}"
            self.chat_history.append({
                "role": "assistant",
                "content": content
            })
            try:
                from app.db.crud import add_chat_message
                db = SessionLocal()
                add_chat_message(db, self.connection_id, "assistant", content)
                db.close()
            except Exception as e:
                logger.warning(f"Failed to persist execution results: {e}")

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

            preview_str = json.dumps(proposed, indent=2)
            formatted_preview = preview_str.strip().replace('\n', '\n> ')
            
            lines = [
                "Proposed Memory Extraction:",
                "└ *Preview:*",
                f"> {formatted_preview}"
            ]
            
            response = "\n".join(lines)
            
            # Save the preview to history so it survives refresh
            self.chat_history.append({"role": "assistant", "content": response})
            try:
                from app.db.crud import add_chat_message
                db = SessionLocal()
                add_chat_message(db, self.connection_id, "assistant", response)
                db.close()
            except Exception as e:
                logger.warning(f"Failed to persist extraction preview: {e}")
                
            yield response
        else:
            yield "\nNo new entities found to save."

    async def save_whole_message(self, content: str) -> AsyncGenerator[str, None]:
        """Bypasses LLM, generates deterministic ID, saves as raw_message."""
        import hashlib
        from app.db.database import SessionLocal
        from app.db.crud import save_entity

        # Generate cheap deterministic ID and label
        msg_hash = hashlib.md5(content.encode()).hexdigest()[:10]
        label = content[:40].replace('\n', ' ') + "..." if len(content) > 40 else content
        
        try:
            db = SessionLocal()
            save_entity(
                db=db,
                conversation_id=self.connection_id,
                label=label,
                entity_type="raw_message",
                entity_id=f"msg_{msg_hash}",
                data={"raw_content": content, "user_directed": True}
            )
            db.close()
            yield "\n**Message saved to memory successfully!** (Deterministic save)"
        except Exception as e:
            logger.error(f"Failed to save whole message: {e}")
            yield f"\nFailed to save message: {e}"

    async def extract_specific_facts(self, payload: dict) -> AsyncGenerator[str, None]:
        """Uses the Extractor LLM with a specific user prompt to extract targeted facts."""
        content = payload.get("content", "")
        user_prompt = payload.get("user_prompt", "")
        
        yield f"\nExtracting specific fact based on your request: *\"{user_prompt}\"*\n"
        
        try:
            # Wrap content in a generic structure the extractor understands
            tool_results = [{"tool": "manual_extraction", "arguments": {"user_prompt": user_prompt}, "result": content}]
            entities_json_str = await anyio.to_thread.run_sync(
                lambda: self.extractor.extract_entities(tool_results, user_prompt)
            )
            entities_data = json.loads(entities_json_str)
            proposed = entities_data.get("entities", [])
        except Exception as e:
            logger.error(f"Targeted entity extraction error: {e}")
            proposed = []

        if proposed:
            self._pending_entities = proposed
            self.state = AgentState.WAITING_MEMORY_CONFIRMATION

            preview_str = json.dumps(proposed, indent=2)
            formatted_preview = preview_str.strip().replace('\n', '\n> ')
            
            lines = [
                "Proposed Memory Extraction:",
                "└ *Preview:*",
                f"> {formatted_preview}"
            ]
            yield "\n".join(lines)
        else:
            yield "\nI could not find facts matching your request in this message."
