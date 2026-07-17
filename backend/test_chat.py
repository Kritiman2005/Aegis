import sys
import logging
from pathlib import Path

# Add backend directory to Python path so we can import from app
backend_dir = Path(__file__).resolve().parent
sys.path.append(str(backend_dir))

from app.core.llm_manager import LLMManager

logging.basicConfig(level=logging.INFO)

def main():
    print("Initializing LLM Manager...")
    manager = LLMManager()
    
    model_name = "gemma-local"
    print(f"\nLoading model '{model_name}' (this might take a while if downloading for the first time)...")
    
    try:
        llm = manager.get_model(model_name)
    except Exception as e:
        print(f"Failed to load model: {e}")
        print("Please ensure llama-cpp-python is installed (`pip install llama-cpp-python`).")
        return

    print("\nModel loaded successfully! Starting interactive chat.")
    print("Type 'quit' or 'exit' to stop.")
    print("-" * 50)
    
    # Store chat history
    messages = []
    
    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.lower() in ['quit', 'exit']:
                break
            
            if not user_input.strip():
                continue
                
            messages.append({"role": "user", "content": user_input})
            
            print("\nAssistant: ", end="", flush=True)
            
            # Using stream=True for better interactive experience
            response = llm.create_chat_completion(
                messages=messages,
                stream=True
            )
            
            assistant_response = ""
            for chunk in response:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        content = delta["content"]
                        print(content, end="", flush=True)
                        assistant_response += content
            
            print() # newline after response finishes
            
            messages.append({"role": "assistant", "content": assistant_response})
            
        except KeyboardInterrupt:
            print("\nExiting chat...")
            break
        except Exception as e:
            print(f"\nAn error occurred during chat completion: {e}")
            # If they just want the exact snippet format they provided:
            # llm.create_chat_completion(messages="No input example has been defined for this model task.")
            # We log the error and continue
            break

if __name__ == "__main__":
    main()
