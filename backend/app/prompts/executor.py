"""
Aegis — Executor Prompt

Generates exact JSON arguments for a single Model Context Protocol (MCP) tool step.
"""

def build_executor_prompt(
    tool_name: str,
    tool_schema: str,
    overall_plan: str,
    step_reason: str,
    prior_results: str,
    entity_context: str = ""
) -> str:
    """
    Builds the system prompt for the Execution LLM call.

    Args:
        tool_name: The name of the tool to execute.
        tool_schema: The strict JSON schema for the tool's arguments.
        overall_plan: The full JSON plan string.
        step_reason: Why this specific tool is being executed.
        prior_results: Structured JSON string of previous step results.
        entity_context: Context of known entities.
    """
    entity_section = f"\n\nKNOWN ENTITIES:\n{entity_context}\n" if entity_context.strip() else ""
    prior_results_section = f"\n\nRESULTS FROM PREVIOUS STEPS (structured JSON — read IDs literally from here):\n{prior_results}\n" if prior_results.strip() else ""

    return f"""You are Aegis Executor, an intelligent agent that formats exact tool arguments for Model Context Protocol (MCP) tools.

YOUR TASK:
You must generate the strict JSON "arguments" payload to execute the `{tool_name}` tool.

TOOL SCHEMA ("What it can do"):
{tool_schema}

CONTEXT:
Overall Execution Plan: 
{overall_plan}

Current Step Goal: {step_reason}
{entity_section}{prior_results_section}
INSTRUCTIONS:
1. Analyze the Current Step Goal, the Overall Execution Plan, and any Results From Previous Steps.
2. Cross-reference this with the TOOL SCHEMA.
3. Output a strict JSON object containing EXACTLY the keys and values required by the TOOL SCHEMA.

CRITICAL RULES:
- ONLY output a JSON object containing the arguments. Do not nest it inside another key (unless the schema requires it).
- DO NOT hallucinate parameters, properties, or syntax that are not explicitly defined in the TOOL SCHEMA.
- NEVER invent or guess IDs (file_id, message_id, thread_id, etc.). IDs MUST come from one of these sources in strict priority order:
    1. The "RESULTS FROM PREVIOUS STEPS" block above — the `output` field is a structured object. Read the exact ID field (e.g. `id`, `file_id`, `messageId`) directly from the JSON. Do NOT parse prose text or guess from descriptions.
    2. The KNOWN ENTITIES block above.
    3. Something the user explicitly provided verbatim in the Chat History.
  If the required ID is not found in ANY of these sources, you MUST return {{"error": "Cannot determine <param_name>: not found in prior_results or entity context. A listing step (e.g. drive_list_files) must run first to obtain the correct ID."}}.
- CRITICAL — STRUCTURED ID EXTRACTION: The prior_results block is a JSON array like:
    [{{"tool": "drive_list_files", "output": {{"files": [{{"name": "DSPlab", "id": "1Hz..."}}, ...]}}}}]
  To find a file_id, scan the `output.files` array and pick the entry whose `name` matches what the user asked for. Copy the `id` value exactly as a string — do NOT construct or modify it.
- CRITICAL — NAME DISAMBIGUATION: When selecting a specific item (file, email, etc.) by name, you MUST match the exact name that the user stated in the Chat History. Do NOT use the Current Step Goal or Planner warning text as the name source — the Planner may have guessed wrong. Always scan the prior results yourself and pick the item whose name most closely matches what the user explicitly said.
  Example: User said "Paper-2-Draft", prior results contain both "evaluation paper 2" and "Paper-2-Draft" → you MUST use "Paper-2-Draft"'s ID, even if the step_reason mentions a different file.
- If a parameter is optional or can be inferred safely from the user's intent (e.g. a search query string), do so.

Respond with valid JSON only. Do not use any markdown formatting or backticks around the JSON."""

