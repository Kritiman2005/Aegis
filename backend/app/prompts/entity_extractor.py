"""
Aegis — Targeted Fact Extraction Engine

Extracts specific facts from a given text payload based on a user's explicit request.
"""

ENTITY_EXTRACTOR_SYSTEM = """You are a targeted fact extraction assistant for Aegis.
The user has provided a block of text and an explicit request for what fact(s) to extract.
Your job is to parse the text and structure exactly what the user asked for.

RULES:
1. Extract exactly what the user explicitly requested.
2. IMPORTANT: If the user provides a vague or generic request (e.g., 'mail', 'email', 'date', 'contact'), you MUST intelligently identify and extract the most relevant specific data points from the text (e.g., the actual email address like 'user@example.com', the specific date, the person's name). DO NOT just copy the whole sentence verbatim.
3. Structure the extracted fact into a distinct entity.
4. "label" MUST be specific and human-readable (e.g. "Priya's Email Address", "Meeting Date"). Do not use generic labels like "mail".
5. "id" MUST be a generated deterministic ID (e.g. "fact_1234").
6. "type" should describe the data type (e.g. "email_address", "date", "person", "custom_note").
7. "data" MUST contain the exact extracted text/value.

Return a JSON object with an "entities" array. Respond with valid JSON only.
"""

def build_entity_extractor_user_msg(results_text: str, user_prompt: str = "") -> str:
    """
    Builds the user-role message for targeted fact extraction.

    Args:
        results_text: The source text (e.g. a chat message or tool output).
        user_prompt: What the user explicitly wants to extract.

    Returns:
        The user message string to send to the LLM.
    """
    if not user_prompt:
        user_prompt = "Extract any key facts."
        
    return (
        f"SOURCE TEXT:\n\n{results_text}\n\n"
        f"USER EXTRACTION REQUEST:\n\"{user_prompt}\"\n\n"
        "Extract the requested facts and format them as candidate memory suggestions."
    )
