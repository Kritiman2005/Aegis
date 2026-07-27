import asyncio
import websockets

async def test():
    uri = "ws://localhost:8000/ws?client_id=session-test-123"
    async with websockets.connect(uri) as websocket:
        # Immediately close the connection just like React Strict Mode
        await websocket.close()
        print("Closed immediately.")

asyncio.run(test())
