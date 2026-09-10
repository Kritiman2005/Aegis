"""
Aegis — Database Engines

Backs the Marketplace's Databases category and the workflow "database"/
"vector" node kinds (see app.core.workflows.engine). Two categories:

  - "relational": sqlite, duckdb — schema-migrated SQL stores, queried with
    named-parameter SQL (relational.py).
  - "vector": qdrant, lancedb, chromadb — embedding stores, operated on
    through a small create_store/upsert/search interface (vector.py).

registry.py is the single source of truth for which engines exist. Adding a
new engine to the Marketplace is just one new EngineSpec entry there plus
(for anything that isn't already bundled, i.e. not sqlite/qdrant) a branch in
the matching worker script under workers/ — everything else (install
plumbing, the workflow node UI, progress broadcasting) is generic.

sqlite and qdrant run in-process (sqlite3 is stdlib; qdrant-client is already
a bundled dependency for the app's own document RAG — see
app.core.rag.processor). Every other engine is pip-installed on first use
into an isolated environment via `uv run --with <package>`, downloading a
portable `uv` the same way app.mcp.runtime_manager already does for MCP
servers — never into the app's own frozen/bundled interpreter, which can't
have new packages injected into it at runtime.
"""
