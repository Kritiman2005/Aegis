import asyncio
import json
import logging
import sys
import threading
import websockets
import webbrowser

logging.basicConfig(level=logging.ERROR)

# Flag to block the input loop while a response is streaming
is_responding = threading.Event()

async def receive_messages(websocket):
    """Listens for messages from the server and prints them."""
    try:
        async for message in websocket:
            data = json.loads(message)
            msg_type = data.get("type")
            content = data.get("content", "")

            if msg_type == "connected":
                print(f"\n[System] Connected to server (ID: {data.get('connection_id')})")

            elif msg_type == "auth_ready":
                print("\n" + "=" * 50)
                print(content)
                print("=" * 50 + "\n")

            elif msg_type == "token":
                # Only print tokens that are NOT raw JSON (skip { } planning phase)
                # The JSON plan is streamed internally but not shown to the user
                is_responding.set()
                if not content.strip().startswith("{") and not content.strip().startswith('"'):
                    print(content, end="", flush=True)

            elif msg_type == "done":
                # Response finished — re-show the prompt
                print("\n")
                is_responding.clear()

            elif msg_type == "error":
                print(f"\n[Error] {content}\n")
                is_responding.clear()

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

    webbrowser.open("http://localhost:8000/auth/google/login")

    print("\nAfter you successfully authenticate, you can start chatting below.")
    print("Type 'quit' to exit.\n")

    while True:
        # Block input while a response is being streamed
        while is_responding.is_set():
            await asyncio.sleep(0.05)

        user_input = await loop.run_in_executor(None, input, "You: ")

        if user_input.lower() in ["quit", "exit"]:
            print("Exiting...")
            sys.exit(0)

        if not user_input.strip():
            continue

        # Mark that we're now waiting for a response
        is_responding.set()

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
            receive_task = asyncio.create_task(receive_messages(websocket))
            send_task = asyncio.create_task(send_messages(websocket))

            done, pending = await asyncio.wait(
                [receive_task, send_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel()

    except ConnectionRefusedError:
        print(f"Failed to connect to {uri}.")
        print("Are you sure the FastAPI backend is running? Start it with:")
        print("cd backend && uvicorn main:app")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExited.")
