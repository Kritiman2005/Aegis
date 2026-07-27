import sys
from llama_cpp import Llama
from app.prompts.planner import build_planner_prompt

def main():
    print("Loading model...")
    llm = Llama(
        model_path="/Users/kritimantalukdar/Aegis/Aegis/backend/models/qwen2.5-3b-instruct-q4_k_m.gguf",
        chat_format="chatml",
        n_ctx=4096,
        verbose=False
    )
    print("Model loaded.")

    tools_str = "- WebSearch: Searches the web.\n- ReadFile: Reads a file."
    system_prompt = build_planner_prompt(tools_str, "")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "what tools do I have?"}
    ]

    print("Generating completion...")
    response = llm.create_chat_completion(
        messages=messages,
        temperature=0.1,
        stream=False
    )
    print("\nResult:", response["choices"][0]["message"]["content"])

if __name__ == "__main__":
    main()
