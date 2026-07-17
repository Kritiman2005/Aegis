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
        
    def get_available_tools(self) -> str:
        """Fetches the available tools from the GoogleAPIManager."""
        if not mcp_manager.credentials:
            return "No active Google session found. Please authenticate with Google first."
            
        tools = mcp_manager.list_tools()
        tools_desc = [f"- {t['name']}: {t['description']}" for t in tools]
        return "\n".join(tools_desc)

    def _generate_plan_sync(self, user_message: str, tools_str: str, token_callback=None) -> str:
        """Synchronous LLM call that optionally streams tokens via a callback."""
        try:
            llm = llm_manager.get_model("gemma-local")
        except Exception as e:
            return json.dumps({"error": f"LLM not loaded: {e}"})
            
        system_prompt = f"""You are an intelligent AI assistant with access to the following tools:

{tools_str}

Analyze the user's request and conversation history to determine the best sequence of tools to fulfill their intent.

Return a JSON object with a "plan" array. Each step in "plan" must have:
- "tool": exact tool name from the available list.
- "reason": brief explanation of why this tool was selected for the request.
- "arguments": object containing argument key-value pairs for the tool.

If no tools are required or available for the request, return an empty "plan" array with a "warnings" array explaining why.

Respond with valid JSON only."""

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.chat_history)
        messages.append({"role": "user", "content": user_message})
        
        try:
            # Stream tokens exactly like test_chat.py does
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
                            token_callback(token)  # stream token live
                            
            return full_response
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return json.dumps({"error": "Failed to generate plan."})

    async def handle_message(self, message: str, token_callback=None) -> str:
        """Main state machine handler for incoming user messages."""
        
        if self.state == AgentState.IDLE:
            # Step 1: User gives query, we generate a plan
            tools_str = self.get_available_tools()
            if "No active Google session" in tools_str:
                return tools_str
                
            self.chat_history.append({"role": "user", "content": message})
            
            # Generate plan using LLM in a thread — tokens stream via callback if provided
            plan_json_str = await anyio.to_thread.run_sync(
                lambda: self._generate_plan_sync(message, tools_str, token_callback)
            )
            
            try:
                plan_data = json.loads(plan_json_str)
                raw_plan = plan_data.get("plan", [])
                warnings = plan_data.get("warnings", [])
                
                # Validate hallucinated/unsupported tools against active tools
                valid_tool_names = {t["name"] for t in mcp_manager.list_tools()}
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
                
                # Format the human-readable plan summary
                response = "\n\n📋 Execution plan based on your request:\n\n"
                for i, step in enumerate(self.plan):
                    response += f"{i+1}. **{step.get('tool')}**: {step.get('reason')}\n"
                    
                if warnings:
                    response += "\n⚠️ Warnings:\n" + "\n".join([f"- {w}" for w in warnings])
                    
                response += "\n\nIs this what you wanted? (Reply 'yes' to proceed or tell me what to change)"
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
                tools_str = self.get_available_tools()
                plan_json_str = await anyio.to_thread.run_sync(
                    lambda: self._generate_plan_sync("Please refine the plan based on my previous feedback.", tools_str, token_callback)
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

        elif self.state == AgentState.EXECUTING:
            return "I am currently executing the tasks. Please wait..."
            
        return "Unknown state."

    async def execute_plan(self) -> AsyncGenerator[str, None]:
        """Executes the approved plan step by step and yields progress."""
        if self.state != AgentState.EXECUTING or not self.plan:
            yield "No plan to execute."
            return
            
        if not mcp_manager.credentials:
            yield "Error: Google session lost. Please re-authenticate."
            self.state = AgentState.IDLE
            return
            
        for i, step in enumerate(self.plan):
            tool_name = step.get("tool")
            arguments = step.get("arguments", {})
            
            yield f"\n⏳ Executing Task {i+1}: Calling `{tool_name}`...\n"
            
            try:
                # Fix: capture loop variables by value using default arguments
                result = await anyio.to_thread.run_sync(
                    lambda t=tool_name, a=arguments: mcp_manager.call_tool(t, a)
                )
                yield f"✅ Result for `{tool_name}`:\n{str(result)[:1000]}\n"
            except Exception as e:
                logger.error(f"Tool execution failed: {e}")
                yield f"❌ Error executing `{tool_name}`: {e}\n"
                break
                
        yield "\n🎉 Execution complete! What would you like to do next?"
        self.state = AgentState.IDLE
        self.plan = None
        self.chat_history = []  # Reset history for the next task
