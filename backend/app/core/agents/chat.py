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

import concurrent.futures

# Read thread pool sizes from context_config. Falls back to safe hardcoded defaults
# if the config file is missing (e.g. fresh install) or unreadable — ensuring the
# app always starts even before any settings have been saved.
try:
    from app.core import context_config as _ctx_cfg_hw
    _hw = _ctx_cfg_hw.get("hardware")
except Exception:
    _hw = {}

# Single-thread executor for all LLM calls.
# Consumer-grade local hardware (Metal, CUDA, CPU) cannot safely or
# performantly run two concurrent decode passes regardless of backend:
# Metal hard-crashes; CUDA degrades from VRAM/KV-cache contention;
# CPU starves both calls of cores. Serialization is required everywhere.
# DEFAULT: 1. This value should NOT be changed without explicit testing.
_llm_workers = int(_hw.get("llm_max_workers", 1))
if _llm_workers != 1:
    logger.warning(
        f"[ThreadPool] llm_max_workers={_llm_workers} — overriding to 1. "
        "Multiple LLM workers are unsafe on consumer hardware."
    )
    _llm_workers = 1
llm_executor = concurrent.futures.ThreadPoolExecutor(max_workers=_llm_workers)

# Dedicated thread pool for SQLite DB operations — decoupled from the LLM lane
# so a slow disk write never blocks inference. 2 workers is safe for SQLite
# in WAL mode (concurrent readers, serialized writers).
_db_workers = int(_hw.get("db_max_workers", 2))
db_executor = concurrent.futures.ThreadPoolExecutor(max_workers=_db_workers)

def get_llm_manager():
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager


class AgentState:
    IDLE                        = "IDLE"
    WAITING_CONFIRMATION        = "WAITING_CONFIRMATION"        # User reviews plan
    EXECUTING                   = "EXECUTING"                   # Tools running
    WAITING_MEMORY_CONFIRMATION = "WAITING_MEMORY_CONFIRMATION" # User decides what to remember
    WAITING_LOOP_CONTINUATION   = "WAITING_LOOP_CONTINUATION"   # Pagination cap hit — continue or stop?


class ChatAgent(BaseAgent):
    def __init__(self, connection_id: str):
        import time
        llm_mgr = get_llm_manager()
        super().__init__(llm_mgr)
        self.connection_id = connection_id
        self._state = AgentState.IDLE
        self.state_entered_at = time.time()
        self.is_processing = False
        self.plan: Optional[List[Dict]] = None

        # Entities proposed after execution — awaiting user confirmation
        # Format: [{"label": ..., "type": ..., "id": ..., "data": {...}}, ...]
        self._pending_entities: List[Dict] = []
        self.requires_entity_extraction: bool = False

        # Fix 1: Session-wide monotonically increasing step counter.
        # Ensures step IDs are globally unique across turns (e.g. t1_step_1, t2_step_1)
        # so the Planner never confuses a stale step reference from chat history
        # with a live step in the current plan.
        self._turn_counter: int = 0

        # Fix 3: Structured recent tool results injected into the Planner context.
        # Keyed by tool_name -> truncated result string. Cleared each new turn.
        # This bypasses prose chat history entirely for the "act on what I just found" case.
        self._last_tool_results: List[Dict] = []  # [{tool, result_snippet}]

        # Reconnect resilience: cache the last plan response so it can be replayed
        # if the client's WebSocket dropped during LLM inference and reconnects.
        # Cleared when the plan is confirmed, cancelled, or a new plan is built.
        self._pending_response: Optional[str] = None

        # Pagination continuation state — persists across WAITING_LOOP_CONTINUATION await.
        # Cleared when the user says "stop" or when the cursor is exhausted.
        # Shape: {"step_index": int, "node_id": str, "tool_name": str, "cursor": str,
        #         "inject_arg": str, "accumulated": list, "prior_results_map": dict,
        #         "tool_results": list, "token_callback": callable|None}
        self._pagination_state: Dict[str, Any] = {}
        
        # Instantiate sub-agents
        self.planner = PlannerAgent(llm_mgr)
        self.executor = ExecutorAgent(llm_mgr)
        self.extractor = EntityExtractorAgent(llm_mgr)

    async def _append_history(self, role: str, content: str):
        """Asynchronously persist a chat message to SQLite via db_executor."""
        import asyncio
        loop = asyncio.get_running_loop()
        
        def _write():
            from app.db.database import SessionLocal
            from app.db.crud import add_chat_message
            db = SessionLocal()
            try:
                add_chat_message(db, self.connection_id, role, content)
            except Exception as e:
                logger.error(f"Critical failure saving chat message to DB: {e}")
                raise e
            finally:
                db.close()
                
        try:
            await loop.run_in_executor(db_executor, _write)
        except Exception as e:
            # Re-raise to abort the turn and allow handle_message to catch it
            raise RuntimeError(f"Database write failed: {e}")

    async def _get_history(self) -> List[Dict]:
        """Asynchronously load history from SQLite via db_executor."""
        import asyncio
        loop = asyncio.get_running_loop()
        
        def _read():
            from app.db.database import SessionLocal
            from app.db.crud import get_chat_history
            db = SessionLocal()
            try:
                return get_chat_history(db, self.connection_id)
            except Exception as e:
                logger.error(f"Failed to load chat history from DB: {e}")
                return []
            finally:
                db.close()
                
        return await loop.run_in_executor(db_executor, _read)

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

    @staticmethod
    def _format_tool_for_planner(t: dict) -> str:
        """
        Serializes a single MCP tool dict into a rich, planner-readable block.

        Format:
            - tool_name: <description>
              REQUIRED args: arg1 (type) — description | arg2 (type) — description
              OPTIONAL args: arg3 (type) — description
              USE WHEN: <natural-language trigger hints derived from description>
        """
        name = t.get("name", "")
        description = t.get("description", "").strip()
        schema = t.get("inputSchema") or t.get("input_schema") or {}
        properties = schema.get("properties", {})
        required_fields = set(schema.get("required", []))

        required_parts = []
        optional_parts = []
        for param, meta in properties.items():
            ptype = meta.get("type", "string")
            pdesc = meta.get("description", "").strip().rstrip(".")
            entry = f"{param} ({ptype})"
            if pdesc:
                entry += f" — {pdesc}"
            if param in required_fields:
                required_parts.append(entry)
            else:
                optional_parts.append(entry)

        lines = [f"- {name}: {description}"]
        if required_parts:
            lines.append(f"  REQUIRED args: {' | '.join(required_parts)}")
        if optional_parts:
            lines.append(f"  OPTIONAL args: {' | '.join(optional_parts)}")

        return "\n".join(lines)

    @staticmethod
    def _build_metadata_context() -> str:
        """
        Reads per-server account_context_json from the DB for all connected servers.
        Builds a human-readable block injected into the planner prompt so the LLM
        uses real authenticated values (e.g. GitHub username) instead of placeholders.
        Survives server restarts because the data lives in SQLite, not in memory.
        """
        try:
            from app.db.crud import get_all_server_account_contexts
            db = SessionLocal()
            all_ctx = get_all_server_account_contexts(db)
            db.close()
        except Exception:
            return ""
        if not all_ctx:
            return ""
        lines = ["\nCONNECTED ACCOUNT CONTEXT (use these real values when constructing arguments):"]
        for server, meta in all_ctx.items():
            for key, value in meta.items():
                lines.append(f"  {server} {key.replace('_', ' ')}: {value}")
        return "\n".join(lines)

    def get_available_tools(self) -> str:
        """Fetches all available tools from all connected MCP servers in the registry."""
        tools = mcp_registry.list_all_tools()
        if not tools:
            return "No active MCP servers connected. Please authenticate with Google or connect a server first."
        tools_str = "\n".join(self._format_tool_for_planner(t) for t in tools)
        return tools_str + self._build_metadata_context()

    def _rewrite_query_for_search(self, query: str) -> tuple[str, bool]:
        """Uses a fast LLM pass to expand the user's query with keywords likely to hit the FTS5 tool index. Also flags if query is counting."""
        llm = self.get_llm()
        if not llm:
            return query, False
            
        all_tools = mcp_registry.list_all_tools()
        if not all_tools:
            return query, False
            
        tool_names = ", ".join([t["name"] for t in all_tools])
        
        prompt = f"""You are a fast tool selector for an AI agent.
The user's query is: "{query}"

Available tools in the registry: [{tool_names}]

Analyze the user's query and output a JSON object with two keys:
- "tools": either the exact string "ALL_TOOLS" (if they ask a general question about what tools are available), OR a list of the 1 to 5 most relevant tool names from the registry.
- "is_counting": boolean true if the user query implies needing a total, count, or completeness (e.g. "how many", "count of", "all of", "list all"). Otherwise false.

Do NOT invent new tool names. Output valid JSON only.
Example: {{"tools": ["slack_send_message", "google_drive_find_file"], "is_counting": false}}"""

        try:
            response = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=60,
                response_format={"type": "json_object"}
            )
            content = response["choices"][0]["message"]["content"].strip()
            import json
            data = json.loads(content)
            tools_val = data.get("tools", [])
            is_counting = bool(data.get("is_counting", False))
            
            if tools_val == "ALL_TOOLS" or (isinstance(tools_val, list) and "ALL_TOOLS" in tools_val):
                expanded_keywords = "ALL_TOOLS"
            elif isinstance(tools_val, list):
                expanded_keywords = ", ".join(tools_val)
            else:
                expanded_keywords = str(tools_val)
                
            logger.info(f"Query rewritten for tool search: '{query}' -> '{expanded_keywords}' (counting: {is_counting})")
            return expanded_keywords, is_counting
        except Exception as e:
            logger.error(f"Query rewrite failed: {e}")
            return query, False

    def get_searched_tools(self, query: str) -> tuple[str, bool]:
        """Fetches top-k relevant tools from registry using keyword expansion and SQLite FTS5."""
        optimized_query, is_counting = self._rewrite_query_for_search(query)
        
        if "ALL_TOOLS" in optimized_query:
            return self.get_available_tools(), is_counting
            
        tools = mcp_registry.search_tools(optimized_query, top_k=10)
        if not tools:
            return "", False
        tools_str = "\n".join(self._format_tool_for_planner(t) for t in tools)
        return tools_str + self._build_metadata_context(), is_counting

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

        elif self.state == AgentState.WAITING_LOOP_CONTINUATION:
            return await self._handle_loop_continuation(message)

        return "Unknown state."

    async def _handle_idle(self, message: str, mode: str = "chat", token_callback=None, status_callback=None) -> str:
        """IDLE → generate plan → WAITING_CONFIRMATION."""
        await self._append_history("user", message)
        
        entity_context = self._get_entity_context()

        # Load context window config live from the JSON config store so changes
        # from the Context Management UI take effect without a backend restart.
        from app.core import context_config as ctx_cfg
        _planner_cfg = ctx_cfg.get("planner")
        _chat_cfg    = ctx_cfg.get("chat")

        _MAX_PLANNER_HISTORY  = _planner_cfg.get("max_history_messages", 6)
        _MAX_MSG_CHARS        = _planner_cfg.get("max_msg_chars", 2000)
        _MAX_RESULT_SNIPPET   = _planner_cfg.get("max_result_snippet", 2000)
        _MAX_CHAT_HISTORY     = _chat_cfg.get("max_history_messages", 20)
        _MAX_CHAT_MSG_CHARS   = _chat_cfg.get("max_msg_chars", 4000)
        _MAX_RAG_CHUNKS       = _chat_cfg.get("max_rag_chunks", 5)

        full_history = await self._get_history()
        raw_history = full_history[:-1] if full_history else []
        history_for_planner = [
            {"role": m["role"], "content": m["content"][:_MAX_MSG_CHARS] + ("..." if len(m["content"]) > _MAX_MSG_CHARS else "")}
            for m in raw_history[-_MAX_PLANNER_HISTORY:]
        ]

        # Inject structured recent tool results as a clean context block.
        if self._last_tool_results:
            recent_block_lines = ["RECENT TOOL RESULTS (use values as literal arguments — NEVER reference these as a depends_on target):"]
            for r in self._last_tool_results:
                snippet = r["result"][:_MAX_RESULT_SNIPPET] + ("..." if len(r["result"]) > _MAX_RESULT_SNIPPET else "")
                recent_block_lines.append(f"- {r['tool']} output: {snippet}")
            recent_block = "\n".join(recent_block_lines)
            history_for_planner = [{"role": "system", "content": recent_block}] + history_for_planner

        # ── Mode Branching ─────────────────────────────
        if mode == "chat":
            # 1. RAG Retrieval for Uploaded Documents
            try:
                from app.core.rag.processor import hybrid_search
                relevant_chunks = hybrid_search(query=message, conversation_id=self.connection_id, top_k=_MAX_RAG_CHUNKS)
            except Exception as e:
                logger.warning(f"RAG search failed: {e}")
                relevant_chunks = []
                
            document_context = ""
            if relevant_chunks:
                logger.info(f"RAG retrieved {len(relevant_chunks)} chunks for query: {message}")
                document_context = "Relevant excerpts from your uploaded documents:\n\n"
                for chunk in relevant_chunks:
                    document_context += f"--- Source: {chunk.get('filename')} ---\n{chunk.get('content')}\n\n"
            else:
                logger.info(f"RAG retrieved 0 chunks for query: {message}")

            from app.prompts.chat import build_chat_prompt
            # Append document context to the base entity context
            full_context = entity_context
            if document_context:
                full_context += "\n" + document_context
                
            all_tools_str = self.get_available_tools()
            chat_prompt = build_chat_prompt(full_context, all_tools_str)
            logger.info("Generated Chat Prompt successfully.")
            
            messages = [{"role": "system", "content": chat_prompt}]
            
            import re
            # Apply Chat history cap and per-message char cap from the live config.
            capped_history = [
                {"role": m["role"], "content": m["content"][:_MAX_CHAT_MSG_CHARS] + ("..." if len(m["content"]) > _MAX_CHAT_MSG_CHARS else "")}
                for m in full_history[-_MAX_CHAT_HISTORY:]
            ]
            sanitized_history = []
            for msg in capped_history:
                content = msg["content"]
                if msg["role"] == "assistant" and "Proposed Execution Plan" in content:
                    content = content.replace("**Proposed Execution Plan:**", "**Past Action Plan:**")
                    content = content.replace("Proposed Execution Plan:", "Past Action Plan:")
                    content = re.sub(r'\n```json\n[\s\S]*?\n```\n\n', '', content)
                    content = content.replace("Would you like me to proceed with this? (Reply **'yes'** to execute or tell me what to edit)", "")
                    content = content.replace("Would you like me to proceed with this? (Reply 'yes' to execute or tell me what to edit)", "")
                sanitized_history.append({"role": msg["role"], "content": content})
                
            messages.extend(sanitized_history)

            # In chat mode, we expect pure raw text, no JSON.
            import asyncio
            loop = asyncio.get_running_loop()
            chat_response = await loop.run_in_executor(
                llm_executor,
                lambda: self._call_llm_text(messages, token_callback)
            )
            
            await self._append_history("assistant", chat_response)

            return chat_response

        # If mode == "agent", we skip the Chat LLM and go straight to Plan Generation
        import asyncio
        loop = asyncio.get_running_loop()
        tools_str, is_counting = await loop.run_in_executor(llm_executor, self.get_searched_tools, message)
        if not tools_str:
            return "I couldn't find any connected tools relevant to your request. Are you sure you have the right MCP servers connected?"

        # ── Plan Generation & Self-Correction Loop ────────────────────────────
        import jsonschema
        all_tools = mcp_registry.list_all_tools()
        valid_tool_names = {t["name"] for t in all_tools}
        tool_schemas = {t["name"]: t.get("inputSchema", {}) for t in all_tools}
        
        if status_callback:
            await status_callback("Drafting execution plan...")

        import asyncio
        loop = asyncio.get_running_loop()
        plan_json_str = await loop.run_in_executor(
            llm_executor,
            lambda: self.planner.generate_plan(message, tools_str, entity_context, history_for_planner, token_callback=None, is_counting=is_counting)
        )

        try:
            plan_data = json.loads(plan_json_str, strict=False)
        except json.JSONDecodeError:
            return "Error: Planner output invalid JSON."
        
        if isinstance(plan_data, list):
            raw_plan = plan_data
        else:
            raw_plan = plan_data.get("plan", [])
            
        # Filter out placeholder tools that the LLM might hallucinate when no tools are needed
        valid_plan = []
        for step in raw_plan:
            if isinstance(step, dict) and step.get("tool"):
                tool_name = str(step.get("tool")).lower()
                if tool_name not in ("none", "none_available", "null", "n/a", "unknown"):
                    valid_plan.append(step)
        raw_plan = valid_plan

        # Handle clarification escape hatch ONLY if no valid plan steps were generated
        if not raw_plan and isinstance(plan_data, dict) and plan_data.get("clarifying_question"):
            question = plan_data.get("clarifying_question")
            self.state = AgentState.IDLE
            await self._append_history("assistant", question)

            return question
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
        
        # Fix 1: Increment the session-wide turn counter and rewrite all step IDs
        # from the LLM (e.g. "step_1") to globally unique IDs (e.g. "t3_step_1").
        # This makes it structurally impossible for the Planner to form a valid
        # depends_on reference to a step from a previous turn, since old step IDs
        # (e.g. "t1_step_1") will never appear in all_step_ids for this new plan.
        self._turn_counter += 1
        turn_prefix = f"t{self._turn_counter}"

        # Rewrite step IDs with turn prefix before validation
        id_remap: Dict[str, str] = {}  # old_id -> new_id
        for step in raw_plan:
            old_id = step.get("step_id")
            if old_id:
                new_id = f"{turn_prefix}_{old_id}"
                id_remap[old_id] = new_id
                step["step_id"] = new_id

        # Also rewrite depends_on references using the same map
        for step in raw_plan:
            depends_on = step.get("depends_on")
            if isinstance(depends_on, list):
                step["depends_on"] = [id_remap.get(did, did) for did in depends_on]

        # First pass: collect all declared step_ids IN THIS PLAN ONLY
        all_step_ids = {step.get("step_id") for step in raw_plan if step.get("step_id")}
        
        for step in raw_plan:
            tool_name = step.get("tool")
            if tool_name not in valid_tool_names:
                continue # Already caught above, but safe to skip
            
            # Validate depends_on — must only reference steps in the current plan.
            # If the LLM hallucinated a cross-turn stale step reference (e.g. "step_1" from
            # a prior turn), strip it to [] with a warning rather than aborting the whole plan.
            # The correct value for the argument is available in the RECENT TOOL RESULTS block.
            depends_on = step.get("depends_on")
            if isinstance(depends_on, list):
                valid_deps = []
                for did in depends_on:
                    if did in all_step_ids:
                        valid_deps.append(did)
                    else:
                        logger.warning(
                            f"Stripped stale/cross-turn depends_on '{did}' "
                            f"from step '{step.get('step_id')}' — not in current plan."
                        )
                step["depends_on"] = valid_deps
            else:
                step["depends_on"] = []
                
            # Validate foreach target
            foreach_target = step.get("foreach")
            if foreach_target and foreach_target not in all_step_ids:
                step["foreach"] = None
                
            # Ensure every step has an ID (fallback for steps that had no step_id at all)
            if not step.get("step_id"):
                import uuid
                step["step_id"] = f"{turn_prefix}_step_{str(uuid.uuid4())[:8]}"
                all_step_ids.add(step["step_id"])
                
            self.plan.append(step)

        if not self.plan:
            self.state = AgentState.IDLE
            direct_response = plan_data.get("direct_response") if isinstance(plan_data, dict) else None
            if direct_response:
                response = direct_response
            elif warnings:
                response = "**Note:**\n" + "\n".join([f"- {w}" for w in warnings])
            else:
                response = f"Available tools:\n{tools_str}\n\nWhat would you like me to do with them?"
                
            await self._append_history("assistant", response)
                
            return response

        # Set requires_entity_extraction to True if any tool is called, since we reverted the planner prompt.
        # We can default to True when planner is invoked.
        self.requires_entity_extraction = True
        self.state = AgentState.WAITING_CONFIRMATION

        response = "**Proposed Execution Plan:**\n\n"
        _SCOPE_BADGES = {"single": "🔹 single", "sample": "🟡 sample", "exhaustive": "🟠 exhaustive"}
        for i, step in enumerate(self.plan):
            scope = step.get("fetch_scope", "single")
            scope_badge = _SCOPE_BADGES.get(scope, scope)
            response += f"**Step {i+1}: `{step.get('tool')}`** `[scope: {scope_badge}]`\n"
            if step.get("reason"):
                response += f"> {step.get('reason')}\n"

            depends = step.get("depends_on")
            if depends:
                response += f"- *Depends on:* {', '.join(depends)}\n"
            response += "\n"

        if warnings:
            response += "**Warnings:**\n" + "\n".join([f"- {w}" for w in warnings]) + "\n\n"

        # Inject the updated JSON block invisibly at the end so the UI can parse it for the interactive card
        updated_plan_data = {"plan": self.plan}
        if isinstance(plan_data, dict) and plan_data.get("direct_response"):
            updated_plan_data["direct_response"] = plan_data.get("direct_response")
        
        response += f"\n```json\n{json.dumps(updated_plan_data, indent=2)}\n```\n\n"

        response += "Would you like me to proceed with this? (Reply **'yes'** to execute or tell me what to edit)"
        
        await self._append_history("assistant", response)

        # Cache the plan response so it can be replayed on reconnect if the client
        # dropped its WebSocket during LLM inference (e.g. React Strict Mode remount).
        self._pending_response = response

        return response

    async def _handle_confirmation(self, message: str, token_callback=None) -> str:
        """WAITING_CONFIRMATION → confirm → EXECUTING  or  refine plan."""
        # Plan was seen and acted on by the user — clear the reconnect cache.
        self._pending_response = None
        await self._append_history("user", message)


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
                full_history = await self._get_history()
                messages.extend(full_history)
                import asyncio
                loop = asyncio.get_running_loop()
                response_text = await loop.run_in_executor(
                    llm_executor,
                    lambda: self._call_llm_text(messages, token_callback)
                )
                final_response = response_text + "\n\n*(Plan is still pending. Reply 'yes' to execute or tell me what to change)*"
                await self._append_history("assistant", final_response)
                
                return final_response

            # Otherwise, treat as an edit request and route to Planner
            tools_str, is_counting = self.get_searched_tools(message)
            if not tools_str:
                return "I couldn't find any tools relevant to that edit request. Please clarify what you want to do."
                
            entity_context = self._get_entity_context()
            import asyncio
            loop = asyncio.get_running_loop()
            full_history = await self._get_history()
            plan_json_str = await loop.run_in_executor(
                llm_executor,
                lambda: self.planner.generate_plan(
                    "Please refine the plan based on my previous feedback.",
                    tools_str, entity_context, full_history, token_callback, is_counting
                )
            )
            try:
                plan_data = json.loads(plan_json_str)
                raw_refined = plan_data.get("plan", [])

                # Apply the same turn-prefix rewriting as the main plan path so that
                # step IDs are globally unique and cross-turn depends_on refs are stripped.
                self._turn_counter += 1
                turn_prefix = f"t{self._turn_counter}"
                id_remap: Dict[str, str] = {}
                for step in raw_refined:
                    old_id = step.get("step_id")
                    if old_id:
                        new_id = f"{turn_prefix}_{old_id}"
                        id_remap[old_id] = new_id
                        step["step_id"] = new_id

                for step in raw_refined:
                    depends_on = step.get("depends_on")
                    if isinstance(depends_on, list):
                        step["depends_on"] = [id_remap.get(did, did) for did in depends_on]

                all_step_ids = {s.get("step_id") for s in raw_refined if s.get("step_id")}
                for step in raw_refined:
                    if isinstance(step.get("depends_on"), list):
                        step["depends_on"] = [did for did in step["depends_on"] if did in all_step_ids]
                    else:
                        step["depends_on"] = []

                self.plan = raw_refined

                response = "I have refined the execution plan:\n\n"
                for i, step in enumerate(self.plan):
                    response += f"{i+1}. **{step.get('tool')}**: {step.get('reason')}\n"
                response += "\nIs this better? (Reply 'yes' to proceed)"
                
                await self._append_history("assistant", response)
                    
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
    # Loop continuation (pagination cap-hit resume)
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_loop_continuation(self, message: str) -> str:
        """WAITING_LOOP_CONTINUATION → user says keep going or stop."""
        await self._append_history("user", message)

        positive_keywords = ["yes", "continue", "keep going", "more", "go ahead", "proceed"]
        is_continue = any(word in message.lower() for word in positive_keywords)

        ps = self._pagination_state
        if not ps:
            self.state = AgentState.IDLE
            return "No pagination state found. What would you like to do next?"

        tool_results: list = ps.get("tool_results", [])
        token_callback = ps.get("token_callback")

        if not is_continue:
            # User said stop — proceed with what we have
            self._pagination_state = {}
            self.state = AgentState.IDLE
            response = (
                "Got it — proceeding with the data collected so far.\n\n"
                "*Synthesizing final answer...*"
            )
            await self._append_history("assistant", response)
            # Synthesis is handled after this returns, by the execute_plan caller
            # who checks tool_results. We trigger it by returning a special marker
            # that websocket.py can detect and route to the synthesis streaming path.
            return "__system_pagination_stopped__"

        # User said continue — resume loop with fresh page counter
        # The cap_hit steps need to continue from where they left off.
        # For simplicity, we re-set executing state and let the generator
        # resume via a new execute_plan call, but with the page state injected.
        # Since execute_plan is a generator, the cleanest approach is to continue
        # from the saved cursor in a dedicated resume pass.
        cap_hit_steps = ps.get("cap_hit_steps", [])
        prior_results_map = ps.get("prior_results_map", {})

        # Re-enter EXECUTING to allow execute_plan_resume to run
        self.state = AgentState.EXECUTING
        cap_tool_list = ", ".join(f"`{r['tool']}`" for r in cap_hit_steps)
        response = f"Continuing to fetch more pages for: {cap_tool_list}..."
        await self._append_history("assistant", response)
        return response

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
        full_history = await self._get_history()
        trimmed_history = [
            {"role": m["role"], "content": m["content"][:_MAX_EXEC_CHARS] + ("..." if len(m["content"]) > _MAX_EXEC_CHARS else "")}
            for m in full_history[-_MAX_EXEC_HISTORY:]
        ]
        full_chat_history = json.dumps(trimmed_history, indent=2)
        entity_context = self._get_entity_context()

        from app.mcp.pagination_registry import get_next_cursor, is_tool_safe_to_autoloop

        # Pagination constants
        _PAGE_CAP = 20         # max pages per step for exhaustive scope
        _SAMPLE_CAP = 3        # max pages for sample scope

        # Run each tool step and collect raw results
        tool_results: List[Dict] = []
        # Stores structured output per step_id for the Executor — avoids prose-parsing for IDs.
        # Format: {node_id: {"tool": tool_name, "output": <parsed JSON or raw string>}}
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

            # Serialize structured prior results as a JSON array for the Executor.
            # shape_for_executor has already produced compact, size-bounded dicts so
            # the Executor LLM context window is never blown out by large API payloads.
            prior_results_for_executor = [
                {
                    "step_id": sid,
                    "tool": v["tool"],
                    "output": v["output"],  # already shaped — compact dict
                }
                for sid, v in prior_results_map.items()
            ]

            # Generate arguments live using the deterministic Executor Agent
            import asyncio
            loop = asyncio.get_running_loop()
            arguments = await loop.run_in_executor(
                llm_executor,
                lambda: self.executor.generate_arguments(
                    tool_name=tool_name,
                    tool_schema=schema,
                    overall_plan=self.plan,
                    step_reason=step_reason,
                    prior_results=prior_results_for_executor,
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

            # Clean up known LLM hallucinations before validation
            if isinstance(arguments, dict):
                # Small models often bleed the 'fetch_scope' step parameter into the arguments dict
                if "fetch_scope" in arguments and "fetch_scope" not in schema.get("properties", {}):
                    del arguments["fetch_scope"]

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
                from app.mcp.response_shapers import shape_for_executor, shape_for_display

                # ── Pagination-aware execution loop ────────────────────────────
                fetch_scope = step.get("fetch_scope", "single")

                # Determine page cap and safety based on scope
                if fetch_scope == "exhaustive":
                    page_cap = _PAGE_CAP
                    safe_to_loop = is_tool_safe_to_autoloop(tool_name, schema)
                elif fetch_scope == "sample":
                    page_cap = _SAMPLE_CAP
                    safe_to_loop = is_tool_safe_to_autoloop(tool_name, schema)
                else:  # "single" or unrecognized
                    page_cap = 1
                    safe_to_loop = True  # single page — gate irrelevant

                accumulated_items: List[Any] = []
                current_arguments = dict(arguments)
                prev_cursor_value = None
                cap_hit = False
                auto_paginated = False

                for page_num in range(page_cap):
                    if page_num == 0:
                        yield {"text": f"Running `{tool_name}` (page 1)...\n", "node_id": node_id, "status": "running"}
                    else:
                        yield {"text": f"Auto-fetching page {page_num + 1} for `{tool_name}`...\n", "node_id": node_id, "status": "running"}
                        auto_paginated = True

                    result = await anyio.to_thread.run_sync(
                        lambda t=tool_name, a=dict(current_arguments): mcp_registry.call_tool(t, a)
                    )

                    # Parse raw result
                    try:
                        raw_parsed = json.loads(str(result)) if isinstance(result, str) else result
                    except (json.JSONDecodeError, TypeError):
                        raw_parsed = result

                    shaped_for_exec = shape_for_executor(tool_name, raw_parsed)
                    shaped_for_display = shape_for_display(tool_name, raw_parsed)
                    accumulated_items.append(shaped_for_exec)

                    # Stream page result to UI immediately
                    yield {
                        "type": "step_result",
                        "text": shaped_for_display,
                        "node_id": node_id,
                        "status": "completed",
                        "tool": tool_name,
                    }

                    # Decide whether to fetch next page
                    if page_cap == 1:
                        break  # single scope — done

                    if not safe_to_loop:
                        logger.warning(
                            f"[Pagination] Tool '{tool_name}' failed safety gate — "
                            "stopping after page 1 (mutating or unreviewed tool)"
                        )
                        break

                    # Deterministic cursor extraction
                    cursor_info = get_next_cursor(
                        tool_name, raw_parsed if isinstance(raw_parsed, dict) else {}
                    )
                    if cursor_info is None:
                        break  # cursor exhausted — pagination complete

                    new_cursor = cursor_info["cursor_value"]
                    if new_cursor == prev_cursor_value:
                        logger.warning(f"[Pagination] Stall detected for '{tool_name}' — cursor unchanged.")
                        break

                    prev_cursor_value = new_cursor
                    current_arguments = dict(arguments)
                    current_arguments[cursor_info["inject_arg"]] = new_cursor
                else:
                    cap_hit = True  # loop ran to completion without a break

                # Merge accumulated pages into final result
                final_exec_output = (
                    accumulated_items[0] if len(accumulated_items) == 1
                    else {
                        "pages": accumulated_items,
                        "total_pages_fetched": len(accumulated_items),
                        "auto_paginated": auto_paginated,
                        "cap_hit": cap_hit,
                    }
                )

                # Store shaped executor output for subsequent steps and planner context.
                prior_results_map[node_id] = {"tool": tool_name, "output": final_exec_output}
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": json.dumps(final_exec_output, ensure_ascii=False),
                    "auto_paginated": auto_paginated,
                    "cap_hit": cap_hit,
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
                    # Provide full text to the LLM context without arbitrary truncation in a structured format
                    lines = ["```json", "["]
                    for idx, r in enumerate(tool_results):
                        block = {"tool": r["tool"], "output": r["result"]}
                        comma = "," if idx < len(tool_results) - 1 else ""
                        lines.append(json.dumps(block) + comma)
                    lines.append("]")
                    lines.append("```")
                    content = "**Execution Results (Partial before failure):**\n" + "\n".join(lines)
                    
                    await self._append_history("assistant", content)

                # Hard-fail and reset
                self.state = AgentState.IDLE
                self.plan = None
                return

        # Check if any step hit the pagination cap — if so, offer to continue
        cap_hit_steps = [r for r in tool_results if r.get("cap_hit")]
        if cap_hit_steps:
            # Save state needed to resume from where we left off
            self._pagination_state = {
                "tool_results": tool_results,
                "prior_results_map": prior_results_map,
                "cap_hit_steps": cap_hit_steps,
                "token_callback": token_callback,
            }
            self.state = AgentState.WAITING_LOOP_CONTINUATION
            cap_tool_names = ", ".join(f"`{r['tool']}`" for r in cap_hit_steps)
            prompt = (
                f"\n\n⚠️ **Pagination cap reached** for {cap_tool_names}. "
                "I've fetched as many pages as allowed but may not have the full picture yet.\n\n"
                "**Continue fetching more pages?** Reply **'yes'** to fetch another batch or **'no'** to proceed with what I have."
            )
            yield {"text": prompt, "node_id": None, "status": "waiting"}
            return

        yield {"text": "\nExecution complete!", "node_id": None}

        # Append summary of results to chat history so the LLM remembers them for the next turn
        if tool_results:
            # Provide full text to the LLM context without arbitrary truncation in a structured format
            lines = ["```json", "["]
            for idx, r in enumerate(tool_results):
                block = {"tool": r["tool"], "output": r["result"]}
                comma = "," if idx < len(tool_results) - 1 else ""
                lines.append(json.dumps(block) + comma)
            lines.append("]")
            lines.append("```")
            content = "**Execution Results:**\n" + "\n".join(lines)
            
            await self._append_history("assistant", content)

            # Fix 3: Persist structured last tool results for the NEXT turn's Planner context block.
            # Store only the raw result string; the block builder will truncate to 400 chars.
            self._last_tool_results = [
                {"tool": r["tool"], "result": r["result"]}
                for r in tool_results
            ]
        else:
            self._last_tool_results = []

        # Go back to IDLE
        self.state = AgentState.IDLE
        self.plan = None

        # ── LLM Synthesis Step ───────────────────────────────────────────────
        # Synthesize a final conversational response summarizing the tool outputs
        # and directly answering the user's original query.
        if tool_results:
            yield {"text": "\n\n*Synthesizing final answer...*\n\n", "node_id": None}
            try:
                full_history = await self._get_history()
                # Build context for the synthesis LLM call
                system_prompt = (
                    "You are Aegis, a helpful AI assistant. You just executed a plan to help the user. "
                    "Based ONLY on the Execution Results below (and prior chat history), answer the user's most recent request directly and concisely. "
                    "If the tools didn't return exactly what was asked (e.g. only returned a partial list due to pagination), explain that clearly. "
                    "Do NOT say 'What would you like to do next?' unless you genuinely need their input."
                )
                
                messages = [{"role": "system", "content": system_prompt}]
                
                # Take the last few messages, including the execution results we just appended
                capped_history = [
                    {"role": m["role"], "content": m["content"][-3000:]} 
                    for m in full_history[-6:]
                ]
                messages.extend(capped_history)
                
                synthesis = ""
                def _token_cb(t):
                    nonlocal synthesis
                    synthesis += t
                    if token_callback:
                        token_callback(t)

                import asyncio
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    llm_executor, 
                    lambda: self._call_llm_text(messages, _token_cb)
                )
                
                if synthesis:
                    await self._append_history("assistant", synthesis)
            except Exception as e:
                logger.error(f"Synthesis failed: {e}")
                yield {"text": "\n\nExecution finished. What would you like to do next?", "node_id": None}
        else:
            yield {"text": "\n\nWhat would you like to do next?", "node_id": None}

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
            import asyncio
            loop = asyncio.get_running_loop()
            entities_json_str = await loop.run_in_executor(
                llm_executor,
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
            await self._append_history("assistant", response)
                
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
            import asyncio
            loop = asyncio.get_running_loop()
            entities_json_str = await loop.run_in_executor(
                llm_executor,
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
