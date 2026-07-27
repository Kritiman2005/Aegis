import asyncio
import websockets

async def test():
    uri = "ws://localhost:8000/ws?client_id=session-test-traceback"
    try:
        async with websockets.connect(uri) as websocket:
            await websocket.close()
            print("Closed immediately.")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
