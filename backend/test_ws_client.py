import asyncio
import json
import logging
import sys
import websockets
import webbrowser

logging.basicConfig(level=logging.ERROR) # Suppress noisy websockets logs

async def receive_messages(websocket):
    """Listens for messages from the server and prints them."""
    try:
        async for message in websocket:
            data = json.loads(message)
            msg_type = data.get("type")
            content = data.get("content", "")
            
            if msg_type == "connected":
                print(f"\n[System] Connected to server (ID: {data.get('connection_id')})")
            elif msg_type == "token":
                # Print tokens as they stream in without newlines
                print(content, end="", flush=True)
            elif msg_type == "done":
                # Stream finished, print a newline
                print("\n")
            elif msg_type == "error":
                print(f"\n[Error] {content}\n")
    except websockets.exceptions.ConnectionClosed:
        print("\n[System] Connection to server closed.")
    except Exception as e:
        print(f"\n[System Error] {e}")

async def send_messages(websocket):
    """Gets user input from terminal and sends it to the server."""
    loop = asyncio.get_running_loop()
    
    print("--------------------------------------------------")
    print("Welcome to the Aegis Terminal Client!")
    print("--------------------------------------------------")
    print("IMPORTANT: Before testing MCP tools, you must authenticate.")
    print("Opening your browser to authenticate with Google...")
    
    # Automatically open the auth flow in the browser
    webbrowser.open("http://localhost:8000/auth/google/login")
    
    print("\nAfter you successfully authenticate and the terminal says 'Available MCP Tools',")
    print("you can start chatting below. Type 'quit' to exit.\n")
    
    while True:
        # Use a thread for input so it doesn't block the async receiving loop
        user_input = await loop.run_in_executor(None, input, "You: ")
        
        if user_input.lower() in ["quit", "exit"]:
            print("Exiting...")
            # We cancel the process
            sys.exit(0)
            
        if not user_input.strip():
            continue
            
        payload = {
            "type": "message",
            "content": user_input
        }
        
        await websocket.send(json.dumps(payload))

async def main():
    uri = "ws://localhost:8000/ws"
    print(f"Connecting to {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            # Run receive and send loops concurrently
            receive_task = asyncio.create_task(receive_messages(websocket))
            send_task = asyncio.create_task(send_messages(websocket))
            
            # Wait for either to finish (send_task ends on 'quit', receive_task on disconnect)
            done, pending = await asyncio.wait(
                [receive_task, send_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in pending:
                task.cancel()
                
    except ConnectionRefusedError:
        print(f"Failed to connect to {uri}.")
        print("Are you sure the FastAPI backend is running? Start it with:")
        print("uvicorn main:app --reload")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExited.")
