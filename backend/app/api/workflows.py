"""
Aegis — Workflows API (/api/workflows)

CRUD for user-designed workflow graphs plus a run trigger. The graph itself
(nodes + edges) is stored and returned verbatim in React Flow's own shape —
this API never interprets it beyond validating it's present JSON; all the
actual interpretation (topological order, tool dispatch, AI-node prompts)
lives in app.core.workflows.engine, run as a background task the same way
app.api.connectors.py's custom MCP connect already is.
"""

import json
import logging
import uuid
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Workflow
from app.core.workflows.engine import run_workflow, _reachable_subgraph, _upstream_node_ids, _downstream_node_ids
from app.core.agents.chat import ChatAgent
from app.mcp.registry import mcp_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class WorkflowPayload(BaseModel):
    name: str
    graph: Dict[str, Any]  # {"nodes": [...], "edges": [...]}
    model_config = {"defer_build": True}


@router.get("/tools")
def list_available_tools():
    """Every tool the node palette can offer: MCP-connected + local, same
    shape _format_tool_for_planner already expects (name/description/
    inputSchema) so the canvas can render argument fields directly from it.
    Each is tagged with "server" (the owning MCP server's name, or None for
    a local tool) so the palette's MCP category can drill server -> tool
    instead of showing one flat list."""
    mcp_tools = []
    for t in mcp_registry.list_all_tools():
        t = dict(t)
        t["server"] = mcp_registry.get_server_for_tool(t["name"])
        mcp_tools.append(t)

    local_tools = [dict(t, server=None) for t in ChatAgent.list_local_tools_for_palette()]
    return {"tools": mcp_tools + local_tools}


@router.get("")
def list_workflows(db: Session = Depends(get_db)):
    rows = db.query(Workflow).order_by(Workflow.updated_at.desc()).all()
    return {"workflows": [_serialize(w) for w in rows]}


@router.get("/{workflow_id}")
def get_workflow(workflow_id: int, db: Session = Depends(get_db)):
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return _serialize(w)


@router.post("")
def create_workflow(payload: WorkflowPayload, db: Session = Depends(get_db)):
    w = Workflow(name=payload.name, graph_json=json.dumps(payload.graph))
    db.add(w)
    db.commit()
    db.refresh(w)
    return _serialize(w)


@router.put("/{workflow_id}")
def update_workflow(workflow_id: int, payload: WorkflowPayload, db: Session = Depends(get_db)):
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    w.name = payload.name
    w.graph_json = json.dumps(payload.graph)
    db.commit()
    db.refresh(w)
    return _serialize(w)


@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: int, db: Session = Depends(get_db)):
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    db.delete(w)
    db.commit()
    return {"message": f"Deleted workflow {workflow_id}."}


@router.post("/{workflow_id}/run")
def run(workflow_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Kicks off the run in the background and returns immediately — progress
    arrives over the WebSocket as workflow_node_progress/_complete/_failed
    events (see engine.run_workflow), the same live-progress pattern already
    used for MCP-connect and model-download progress."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    graph = json.loads(w.graph_json)
    if not graph.get("nodes"):
        raise HTTPException(status_code=400, detail="This workflow has no nodes yet.")

    run_id = uuid.uuid4().hex[:8]
    background_tasks.add_task(run_workflow, workflow_id, run_id, graph)
    return {"status": "running", "run_id": run_id}


def _validate_chat_handler_graph(graph: Dict) -> None:
    """A workflow can only be connected as the live chat handler if it has
    exactly one entry point (chat_trigger), exactly one Chat Reply node
    with exactly one "llm" node wired directly into it (and nothing else
    wired after that llm node) — model config lives only on "llm" nodes,
    "chat_reply" is a thin "send" marker with none of its own; see
    app.core.workflows.engine.run_chat_workflow's generation_ids detection,
    mirrored here — and exactly one final step — all counted only within
    the subgraph reachable from that trigger (via
    app.core.workflows.engine._reachable_subgraph), not the whole canvas,
    since one workflow may also hold an unrelated "On document upload"
    tree side by side. Raises HTTPException(400) with a message naming the
    exact problem rather than silently picking one node."""
    all_nodes = {n["id"]: n for n in graph.get("nodes", [])}
    all_edges = graph.get("edges", [])

    triggers = [n for n in all_nodes.values() if n.get("data", {}).get("kind") == "chat_trigger"]
    if len(triggers) == 0:
        raise HTTPException(status_code=400, detail="Add an \"On chat message\" trigger node before connecting this workflow to chat.")
    if len(triggers) > 1:
        raise HTTPException(status_code=400, detail="Only one \"On chat message\" trigger node is allowed per workflow.")

    nodes, edges = _reachable_subgraph(all_nodes, all_edges, triggers[0]["id"])

    reply_nodes = [n for n in nodes.values() if n.get("data", {}).get("kind") == "chat_reply"]
    if len(reply_nodes) == 0:
        raise HTTPException(status_code=400, detail="Add a Chat Reply node — it sends the reply.")
    if len(reply_nodes) > 1:
        raise HTTPException(status_code=400, detail="Only one Chat Reply node is allowed per chat-connected workflow.")

    generation_ids = [
        nid for nid in _upstream_node_ids(reply_nodes[0]["id"], edges)
        if nodes.get(nid, {}).get("data", {}).get("kind") == "llm"
        and _downstream_node_ids(nid, edges) == [reply_nodes[0]["id"]]
    ]
    if len(generation_ids) == 0:
        raise HTTPException(status_code=400, detail="Wire an \"llm\" node directly into the Chat Reply node — that's what generates the reply.")
    if len(generation_ids) > 1:
        raise HTTPException(status_code=400, detail="Only one \"llm\" node may lead ONLY into the Chat Reply node — that's the one treated as the reply generator.")

    downstream_of = {e["source"] for e in edges}
    terminal_nodes = [n for n in nodes.values() if n["id"] not in downstream_of]
    if len(terminal_nodes) == 0:
        raise HTTPException(status_code=400, detail="This workflow has no final step — every node leads to another node.")
    if len(terminal_nodes) > 1:
        raise HTTPException(status_code=400, detail="More than one node has nothing wired after it — there must be exactly one final step.")


@router.post("/{workflow_id}/set-chat-handler")
def set_chat_handler(workflow_id: int, db: Session = Depends(get_db)):
    """Connects this workflow as the live handler for real chat messages —
    at most one workflow may hold this at a time (mirrors ModelRegistry's
    single-active-row pattern in app.api.context_config's load_active_model).
    See app.api.websocket's process_message_task for where this is read."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    _validate_chat_handler_graph(json.loads(w.graph_json))

    db.query(Workflow).update({Workflow.is_chat_handler: False})
    w.is_chat_handler = True
    db.commit()
    return {"success": True}


@router.post("/{workflow_id}/unset-chat-handler")
def unset_chat_handler(workflow_id: int, db: Session = Depends(get_db)):
    """Disconnects this workflow — chat reverts to the built-in ChatAgent
    pipeline exactly as it behaves with nothing connected."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    w.is_chat_handler = False
    db.commit()
    return {"success": True}


def _validate_ingestion_handler_graph(graph: Dict) -> None:
    """A workflow can only be connected as the live ingestion handler if it
    has exactly one entry point (document_upload_trigger) and exactly one
    final step — both counted only within the subgraph reachable from that
    trigger (see _validate_chat_handler_graph's matching comment on why —
    the same workflow may also hold an unrelated "On chat message" tree).
    Mirrors _validate_chat_handler_graph's shape, minus the "exactly one
    LLM node" rule (ingestion has no reply to generate)."""
    all_nodes = {n["id"]: n for n in graph.get("nodes", [])}
    all_edges = graph.get("edges", [])

    triggers = [n for n in all_nodes.values() if n.get("data", {}).get("kind") == "document_upload_trigger"]
    if len(triggers) == 0:
        raise HTTPException(status_code=400, detail="Add an \"On document upload\" trigger node before connecting this workflow to ingestion.")
    if len(triggers) > 1:
        raise HTTPException(status_code=400, detail="Only one \"On document upload\" trigger node is allowed per workflow.")

    nodes, edges = _reachable_subgraph(all_nodes, all_edges, triggers[0]["id"])

    downstream_of = {e["source"] for e in edges}
    terminal_nodes = [n for n in nodes.values() if n["id"] not in downstream_of]
    if len(terminal_nodes) == 0:
        raise HTTPException(status_code=400, detail="This workflow has no final step — every node leads to another node.")
    if len(terminal_nodes) > 1:
        raise HTTPException(status_code=400, detail="More than one node has nothing wired after it — there must be exactly one final step.")


@router.post("/{workflow_id}/set-ingestion-handler")
def set_ingestion_handler(workflow_id: int, db: Session = Depends(get_db)):
    """Connects this workflow as the live handler for future document
    uploads — at most one workflow may hold this at a time. See
    app.api.documents's upload handler for where this is read."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    _validate_ingestion_handler_graph(json.loads(w.graph_json))

    db.query(Workflow).update({Workflow.is_ingestion_handler: False})
    w.is_ingestion_handler = True
    db.commit()
    return {"success": True}


@router.post("/{workflow_id}/unset-ingestion-handler")
def unset_ingestion_handler(workflow_id: int, db: Session = Depends(get_db)):
    """Disconnects this workflow — uploads revert to the built-in
    app.core.rag.processor.ingest_document pipeline exactly as before."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    w.is_ingestion_handler = False
    db.commit()
    return {"success": True}


def _serialize(w: Workflow) -> Dict:
    return {
        "id": w.id,
        "name": w.name,
        "graph": json.loads(w.graph_json),
        "is_chat_handler": bool(w.is_chat_handler),
        "is_ingestion_handler": bool(w.is_ingestion_handler),
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }
