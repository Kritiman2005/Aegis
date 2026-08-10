import asyncio
from app.core.llm_manager import LLMManager

async def test():
    mgr = LLMManager()
    llm = mgr.get_model("gemma-local")
    print("Model loaded")
    response = llm.create_chat_completion(
        messages=[{"role": "user", "content": "Hello"}],
        response_format={"type": "json_object"},
        temperature=0.1,
        stream=True
    )
    print("Started generating")
    for chunk in response:
        delta = chunk["choices"][0].get("delta", {})
        if "content" in delta:
            print(delta["content"], end="", flush=True)

asyncio.run(test())
