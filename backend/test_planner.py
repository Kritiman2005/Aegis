import asyncio
import json
from app.core.agents.planner import PlannerAgent
from app.core.llm_manager import LLMManager

async def main():
    LLMManager._instance = LLMManager()
    LLMManager._instance._register_default_models()
    LLMManager._instance._load_model("gemma-local")
    
    planner = PlannerAgent("test")
    print("Generating plan...")
    
    def on_token(token):
        print(token, end="", flush=True)
        
    plan = planner.generate_plan("what tools do I have?", "", "", [], token_callback=on_token)
    print("\n\nFinished:", plan)

if __name__ == "__main__":
    asyncio.run(main())
