import asyncio
from app.db.database import SessionLocal
from app.mcp.registry import mcp_registry

async def main():
    db = SessionLocal()
    await mcp_registry.start_all(db=db)
    tools = mcp_registry.list_all_tools()
    print("TOOLS:", [t["name"] for t in tools])

if __name__ == "__main__":
    asyncio.run(main())
