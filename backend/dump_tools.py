import asyncio
from app.core.mcp.registry import mcp_registry

async def main():
    await mcp_registry.start_all()
    tools = mcp_registry.list_all_tools()
    for t in tools:
        print(t["name"])
    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
