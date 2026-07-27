from app.db.database import SessionLocal
from app.core.mcp.registry import registry
from app.core.agents.chat import ChatAgent

registry.discover_tools()
tools = registry.search_tools("what tools I have ?", top_k=10)
tools_desc = [f"- {t['name']}: {t.get('description', '')}" for t in tools]
print("Tools String Length:", len("\n".join(tools_desc)))
