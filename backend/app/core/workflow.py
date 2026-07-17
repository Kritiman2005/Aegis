import json
import logging
from typing import Dict, List, Optional, AsyncGenerator
import anyio

from app.core.llm_manager import LLMManager
from app.mcp.manager import mcp_manager

logger = logging.getLogger(__name__)

# Initialize a global LLM manager for the workflow
llm_manager = LLMManager()

class AgentState:
    IDLE = "IDLE"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    EXECUTING = "EXECUTING"

class AgentSession:
    def __init__(self, connection_id: str):
        self.connection_id = connection_id
        self.state = AgentState.IDLE
        self.plan: Optional[List[Dict]] = None
        self.chat_history: List[Dict] = []
        
    async def get_available_tools(self) -> str:
        """Fetches the available tools from the active MCP session."""
        if "google_drive" not in mcp_manager.active_sessions:
            return "No active MCP sessions found. Please authenticate with Google first."
            
        session = mcp_manager.active_sessions["google_drive"]
        tools_result = await session.list_tools()
        
        tools_desc = []
        for tool in tools_result.tools:
            tools_desc.append(f"- {tool.name}: {tool.description}")
            
        return "\n".join(tools_desc)

    def _generate_plan_sync(self, user_message: str, tools_str: str) -> str:
        """Synchronous wrapper for llama.cpp chat completion."""
        try:
            llm = llm_manager.get_model("gemma-local")
        except Exception as e:
            return json.dumps({"error": f"LLM not loaded: {e}"})
            
        system_prompt = f"""You are an AI assistant orchestrating an MCP (Model Context Protocol) server.
Available MCP tools:
{tools_str}

The user will ask you to perform a task. You must output a JSON object containing a "plan" array.
Each item in the "plan" array must have:
- "tool": the exact name of the tool to use.
- "reason": why this tool is being used.
- "arguments": a dictionary of arguments to pass to the tool (if any).

If a requested action cannot be mapped to an available tool, mention it in a "warnings" array in the JSON.
Your entire response must be valid JSON."""

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.chat_history)
        messages.append({"role": "user", "content": user_message})
        
        try:
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1
            )
            return response["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return json.dumps({"error": "Failed to generate plan."})

    async def handle_message(self, message: str) -> str:
        """Main state machine handler for incoming user messages."""
        
        if self.state == AgentState.IDLE:
            # Step 1: User gives query, we generate a plan
            tools_str = await self.get_available_tools()
            if "No active MCP sessions" in tools_str:
                return tools_str
                
            self.chat_history.append({"role": "user", "content": message})
            
            # Generate plan using LLM in a thread
            plan_json_str = await anyio.to_thread.run_sync(self._generate_plan_sync, message, tools_str)
            
            try:
                plan_data = json.loads(plan_json_str)
                self.plan = plan_data.get("plan", [])
                warnings = plan_data.get("warnings", [])
                
                self.chat_history.append({"role": "assistant", "content": plan_json_str})
                self.state = AgentState.WAITING_CONFIRMATION
                
                # Format the response for the user
                response = "I have prepared the following execution plan based on the available MCP tools:\n\n"
                for i, step in enumerate(self.plan):
                    response += f"{i+1}. **{step.get('tool')}**: {step.get('reason')}\n"
                    
                if warnings:
                    response += "\nWarnings:\n" + "\n".join([f"- {w}" for w in warnings])
                    
                response += "\nIs this what you wanted? (Reply 'yes' to proceed or specify changes)"
                return response
                
            except json.JSONDecodeError:
                return "Error: LLM did not output valid JSON for the plan."

        elif self.state == AgentState.WAITING_CONFIRMATION:
            # Step 2: User reviews the plan
            self.chat_history.append({"role": "user", "content": message})
            
            positive_keywords = ['yes', 'proceed', 'go ahead', 'do it', 'sure', 'ok', 'okay', 'yep', 'yeah', 'looks good']
            is_positive = any(word in message.lower() for word in positive_keywords)
            
            if is_positive and len(message.split()) < 10: # Simple heuristic for agreement
                self.state = AgentState.EXECUTING
                return "Great! Proceeding with the execution... (Please wait)"
            else:
                # User wants to refine the plan
                tools_str = await self.get_available_tools()
                plan_json_str = await anyio.to_thread.run_sync(self._generate_plan_sync, "Please refine the plan based on my previous feedback.", tools_str)
                
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

        elif self.state == AgentState.EXECUTING:
            return "I am currently executing the tasks. Please wait..."
            
        return "Unknown state."

    async def execute_plan(self) -> AsyncGenerator[str, None]:
        """Executes the approved plan step by step and yields progress."""
        if self.state != AgentState.EXECUTING or not self.plan:
            yield "No plan to execute."
            return
            
        session = mcp_manager.active_sessions.get("google_drive")
        if not session:
            yield "Error: MCP session lost."
            self.state = AgentState.IDLE
            return
            
        for i, step in enumerate(self.plan):
            tool_name = step.get("tool")
            arguments = step.get("arguments", {})
            
            yield f"\n⏳ Executing Task {i+1}: Calling `{tool_name}`...\n"
            
            try:
                # Call the MCP tool
                result = await session.call_tool(tool_name, arguments)
                
                # Format the result to string (MCP results can have text or images)
                result_text = ""
                for content in result.content:
                    if content.type == "text":
                        result_text += content.text + "\n"
                        
                yield f"✅ Result for `{tool_name}`:\n{result_text[:500]}... (truncated)\n"
            except Exception as e:
                logger.error(f"Tool execution failed: {e}")
                yield f"❌ Error executing `{tool_name}`: {e}\n"
                break # Stop execution on error
                
        yield "\n🎉 Execution complete! What would you like to do next?"
        self.state = AgentState.IDLE
        self.plan = None
        self.chat_history = [] # Reset history for the next task
