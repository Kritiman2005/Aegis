"""
Aegis — Planner Prompt

Generates a structured JSON tool-execution plan from the user's request,
available tools, and any confirmed session entities.
"""


def build_planner_prompt(tools_str: str, entity_context: str = "") -> str:
    """
    Builds the system prompt for the plan-generation LLM call.

    Args:
        tools_str:      Newline-separated list of available tool descriptions.
        entity_context: Optional block of confirmed session entities to inject
                        so the LLM can reference them without re-fetching.

    Returns:
        A fully-formed system prompt string ready to pass to the LLM.
    """
    entity_section = f"\n\n{entity_context}\n" if entity_context.strip() else ""

    return f"""You are an intelligent AI assistant with access to the following tools:

{tools_str}
{entity_section}
Analyze the user's request and conversation history to determine the best \
sequence of tools to fulfill their intent.

Return a JSON object with a "plan" array. Each step in "plan" must have:
- "tool"     : exact tool name from the available list above.
- "reason"   : brief explanation of why this tool was chosen.
- "arguments": object of key-value argument pairs for the tool.

If no tools are required or available for the request, return an empty "plan" \
array and include a "warnings" array explaining why.

Respond with valid JSON only."""
