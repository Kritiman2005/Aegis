"""
Aegis — Workflow Execution Engine

Runs a user-designed workflow graph (nodes + edges, the same shape React
Flow's canvas produces — see app.db.models.Workflow.graph_json). This is
what Agent Mode's LLM-driven planning was replaced with: the user decides
which tool runs at each step by placing a node for it, so there is no
"guess the right tool from a menu" decision for a local model to get wrong.

Nine node kinds (node.data.kind):
  - "mcp" / "tool": a direct tool call — MCP-connected or local. Arguments
    come from data.staticInputs (fixed values, deterministic) unless
    data.isAi is set, in which case ExecutorAgent.generate_arguments fills
    them in — scoped to exactly this one tool's schema, never a menu.
  - "llm": a pure reasoning step bound to one explicitly-chosen downloaded
    model (data.modelName) — no tool, just the node's own instruction plus
    its upstream inputs. data.temperature/data.maxTokens (default 0.0/1024
    when unset) control the call itself. Freeform text by default; when
    data.outputFields is set (a small user-authored field list —
    name/type/description) the call is grammar-constrained to that JSON
    shape (see _build_structured_output_grammar) and this node returns the
    PARSED DICT instead of a string — this is how a generic "llm" node can
    be configured to do a judgment call (e.g. "does this need a document
    search, and what's the query") without a dedicated node kind for it.
  - "logic": a generic gate — data.field (a key to read off the upstream
    value; blank uses the upstream value directly), data.operator
    (is_true/is_false/equals/not_equals/contains/matches_regex), data.value
    (comparand). Passes the upstream value through unchanged when the
    condition holds; otherwise the whole branch downstream of it is
    skipped for this run (see _run_chat_workflow/_run_workflow's gate-
    propagation pass) — this is how "should we even search" or "does this
    look export-related" becomes a visible, reconfigurable node instead of
    logic hardcoded inside some other node.
  - "switch": a multi-way router generalizing "logic" to N labeled
    branches — same data.field/subject resolution, but matching happens
    per OUTGOING EDGE (edge.data.caseValue, plain string equality) rather
    than a single node-level condition, since one switch node can have
    many outgoing edges. Edges with no caseValue set act as the default
    branch when nothing else matches. See _run_switch_node/
    _gate_switch_node/_downstream_gated_by_edges.
  - "database": targets data.databaseId, an app.db.models.InstalledDatabase
    row — either one installed from the Marketplace's Databases category
    (a SQL query, any relational engine in app.core.dbengines' catalog:
    sqlite, duckdb, ...) or Aegis's OWN SQLite database
    (seed.BUILTIN_AEGIS_DB_ENGINE_ID, auto-registered as a normal-looking
    row so it's just another dropdown choice, not a separate hardcoded
    node kind): data.operation (list/insert/update/delete) against a
    curated, credential-free allowlist of tables plus any table the user
    created themselves through this node's canvas browser — see
    app.core.aegis_db_browser for the full safety model.
  - "embedding": embeds upstream text with data.embeddingModel — a model
    downloaded from the Marketplace's Embedding Models category
    (app.core.embeddings), or the app's own bundled dense model if unset.
    Output is {"texts": [...], "vectors": [...]} — wire it into a "vector"
    node's upsert to store each vector alongside the text it came from.
  - "vector": upserts or searches (data.operation) an installed vector
    store (data.databaseId, category "vector" — qdrant, lancedb,
    chromadb, ...). "upsert" expects an upstream "embedding" node's output
    (it no longer embeds text itself); "search" still embeds its own
    query text internally with data.embeddingModel — a deliberate
    asymmetry, see _run_vector_node's docstring for why.
  - "reranker": re-scores an upstream "vector" search's list of matches
    against a query with a cross-encoder (data.rerankerModel — a model
    downloaded from the Marketplace's Rerankers category, or the app's own
    default if unset, NOT bundled either way — needs sentence-transformers
    installed from the Dependencies panel first) and returns the top
    data.topK, reordered by relevance rather than raw embedding/BM25
    similarity — see _run_reranker_node. Deliberately its own node, not a
    checkbox on "vector", so a workflow can search wide and rerank down
    explicitly.
  - "extract": pulls plain text out of a document file (data.staticInputs
    filePath / an upstream-wired path) — the same extractor the app's own
    document upload pipeline uses (app.core.rag.processor.extract_text).
  - "chunk": splits upstream text into overlapping chunks
    (data.chunkSize/overlap). data.strategy picks which chunking strategy
    from app.core.chunking_engines' registry (recursive/paragraph/
    sentence/fixed) — unset uses the same recursive strategy the app's own
    document RAG pipeline uses (app.core.rag.processor.chunk_text).
  - "loop": runs its single direct downstream node once per item in an
    upstream array, collecting the results — see _run_loop_node for the
    (deliberately scoped-down) semantics.
  - "http_request": a raw outbound HTTP call (data.url/method/headers/
    body) — see _run_http_request_node. The escape hatch for any REST API
    with no dedicated MCP server; retried on transient failure like
    "tool"/"database" (see _RETRYABLE_KINDS).
  - "set_fields": deterministic dict reshaping (rename/pick/add fields)
    without an "llm" judgment call — see _run_set_fields_node.
  - "merge": explicitly combines every upstream node's output (list,
    concat, or first-non-empty) — see _run_merge_node's own docstring for
    why this exists alongside every other node's "first upstream only"
    default.
  - "chat_trigger": the graph's entry point when this workflow is connected
    as the live chat handler (Workflow.is_chat_handler — see
    app.api.workflows's /set-chat-handler and run_chat_workflow below).
    Never runs through the normal top-to-bottom pass or a manual "Run"
    click; its "output" is injected directly by run_chat_workflow as
    {message, history, attachments} for the current turn.
  - "document_upload_trigger": the analogous entry point for a workflow
    connected as the live ingestion handler (Workflow.is_ingestion_handler
    — see app.api.workflows's /set-ingestion-handler and
    run_ingestion_workflow below). Ingestion is workflow-only — with no
    workflow connected, app.api.documents's upload handler rejects the
    upload outright rather than falling back to any built-in pipeline.
    Its "output" is {file_path, document_id, filename, file_type} for the
    document being ingested. A real ingestion pipeline built from this
    plus the existing "extract" -> "chunk" -> "embedding" -> "vector"
    (upsert) nodes needs no new per-step logic — only the trigger and
    orchestration are new.

Two more kinds exist ONLY for a chat-connected graph (run_chat_workflow) —
and, per the rule that model config lives ONLY on "llm" nodes, "chat_reply"
itself holds none:
  - "chat_reply": a thin "send" marker with no config of its own — just
    carries forward whatever text is wired into it (e.g. to an
    "export_document" node). The one "llm" node wired DIRECTLY into it
    (with nothing else wired after that llm node) is the real reply
    generator: it gathers whatever's wired directly into IT (an
    "embedding"+"vector" search's results, another "llm" node's structured
    "is_export"/"format"/"parts" output, or anything else, folded in
    generically), builds the prompt via app.prompts.chat.build_chat_prompt,
    and streams via chat_agent._call_llm_text, the shared JSON-leak-guarded
    generation call every chat reply goes through now (see
    _run_chat_generation_node). Exactly one such pair is required per
    chat-connected workflow (see run_chat_workflow) — every OTHER "llm"
    node in the graph is a plain, non-streaming reasoning step (a
    decide/classify judgment call, say), completely unaffected.
  - "export_document": the post-reply export pipeline (fence extraction,
    the LLM-fallback extractor, the actual file export, appending the
    download link — see _run_export_document_node) that runs after
    generating a reply — runs AFTER the "chat_reply" node, so it's the one
    case where the graph's true terminal isn't "chat_reply" itself; see
    run_chat_workflow's docstring.

A workflow's memory/entity context and the bundled hybrid document store's
real hybrid search are all still available — the first as config on the
reply-generating "llm" node (see its memory-settings panel), the second via
the generic "embedding" + "vector" (+ optional "reranker") nodes pointed at
the bundled aegis_hybrid store (see _run_vector_node/_run_reranker_node) —
there's no dedicated node kind for either anymore; a plain "llm" node
handles any other judgment call a workflow needs (e.g. "does this turn need
document search" or "should this reply export to a file"), same as any
other reasoning step.

Every step broadcasts progress over the same WebSocket connection manager
used elsewhere in this app (MCP-connect progress, model-download progress)
so the canvas can highlight whichever node is currently running.

A workflow connected as the live chat handler runs through
run_chat_workflow instead of run_workflow — see its docstring for how that
differs (real token streaming + the JSON-leak guard for the reply node,
per-connection progress instead of a broadcast).
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anyio

from app.core.connection_manager import manager
from app.core.friendly_errors import humanize_exception
from app.core.agents.chat import ChatAgent, llm_executor, _trim_history_to_token_budget
from app.core.agents.executor import ExecutorAgent
from app.core.dbengines import relational as relational_engine
from app.core.dbengines import vector as vector_engine
from app.mcp.registry import mcp_registry

logger = logging.getLogger(__name__)


class WorkflowError(Exception):
    """Raised for a structural problem with the graph itself (cycle, missing
    node, missing config), as opposed to one node's tool call failing —
    which is caught and reported per-node instead of aborting the whole run."""


def _topological_order(nodes: List[Dict], edges: List[Dict]) -> List[str]:
    """Kahn's algorithm. Raises WorkflowError on a cycle — a workflow graph
    must be a DAG, same requirement React Flow's own connection model has no
    way to prevent on its own."""
    node_ids = [n["id"] for n in nodes]
    in_degree = {nid: 0 for nid in node_ids}
    adjacency: Dict[str, List[str]] = {nid: [] for nid in node_ids}
    for e in edges:
        src, tgt = e["source"], e["target"]
        if src not in in_degree or tgt not in in_degree:
            continue  # dangling edge reference — ignore rather than crash the whole run
        adjacency[src].append(tgt)
        in_degree[tgt] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    order: List[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for downstream in adjacency[nid]:
            in_degree[downstream] -= 1
            if in_degree[downstream] == 0:
                queue.append(downstream)

    if len(order) != len(node_ids):
        raise WorkflowError("Workflow graph has a cycle — every node must eventually lead forward, not back to an earlier step.")
    return order


def _reachable_subgraph(nodes: Dict[str, Dict], edges: List[Dict], start_id: str) -> Tuple[Dict[str, Dict], List[Dict]]:
    """
    Restricts a graph to just the node reachable from `start_id` (its own
    trigger) plus everything downstream of it — so a single workflow can
    hold more than one independent pipeline (e.g. both an "On chat
    message" tree and an "On document upload" tree side by side on the
    same canvas, one workflow instead of two) without one trigger's run
    tripping over the other's unrelated nodes. Used by run_chat_workflow/
    run_ingestion_workflow before computing "exactly one LLM node" /
    "exactly one terminal node" and before topological ordering — both of
    those must only ever see the one subgraph actually being run.
    """
    seen = {start_id}
    queue = [start_id]
    while queue:
        current = queue.pop()
        for tgt in _downstream_node_ids(current, edges):
            if tgt not in seen and tgt in nodes:
                seen.add(tgt)
                queue.append(tgt)
    sub_nodes = {nid: n for nid, n in nodes.items() if nid in seen}
    sub_edges = [e for e in edges if e["source"] in seen and e["target"] in seen]
    return sub_nodes, sub_edges


def _upstream_node_ids(node_id: str, edges: List[Dict]) -> List[str]:
    return [e["source"] for e in edges if e["target"] == node_id]


def _downstream_node_ids(node_id: str, edges: List[Dict]) -> List[str]:
    return [e["target"] for e in edges if e["source"] == node_id]


def _resolve_named_arguments(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> Dict:
    """Static field values from the node's own config, overlaid with any
    edge-mapped values pulled out of an upstream node's output. An edge's
    own data.outputField/inputField (set when the user draws the connection
    in the canvas) says which single value moves across it — this is
    deliberately a plain field-to-field mapping, not free-form JS transforms
    like n8n itself supports, to keep a first version's data model small.
    Used for tool-call arguments AND a database node's query parameters —
    both are just "a dict of named values this step needs"."""
    data = node.get("data", {})
    arguments = dict(data.get("staticInputs") or {})

    incoming = [e for e in edges if e["target"] == node["id"]]
    for edge in incoming:
        edge_data = edge.get("data") or {}
        output_field = edge_data.get("outputField")
        input_field = edge_data.get("inputField")
        if not input_field:
            continue
        upstream_output = node_outputs.get(edge["source"])
        if upstream_output is None:
            continue
        if output_field and isinstance(upstream_output, dict):
            value = upstream_output.get(output_field)
        else:
            value = upstream_output
        arguments[input_field] = value

    return arguments


_JSON_TYPED_FIELD_TYPES = ("object", "array", "boolean", "number", "integer")


def _coerce_structured_arguments(arguments: Dict, schema: Optional[Dict]) -> Dict:
    """A node's "Fixed values" panel only ever produces plain strings (see
    WorkflowsView.tsx's staticInputs) — even its boolean checkbox and number
    inputs store "true"/"false"/"5", not a real bool/int. But a tool's own
    inputSchema may declare a parameter as object/array (an IAM policy
    document, a Lambda payload, ...), boolean, or number/integer. Without
    this, that string would reach the MCP server verbatim and fail its
    schema validation (e.g. the literal string "true" where the server
    expects the JSON boolean true). JSON-parses just those typed fields —
    which handles all five cases uniformly, since "true"/"5"/"5.5" are valid
    JSON literals too — anything already non-string (e.g. an edge-mapped
    value pulled straight from an upstream node's own JSON output) passes
    through untouched, and a blank field is left for the tool's own
    default."""
    properties = (schema or {}).get("properties") or {}
    coerced = dict(arguments)
    for key, meta in properties.items():
        if not isinstance(meta, dict) or meta.get("type") not in _JSON_TYPED_FIELD_TYPES:
            continue
        value = coerced.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            coerced[key] = json.loads(value)
        except json.JSONDecodeError as e:
            raise WorkflowError(f"'{key}' needs valid JSON (a {meta['type']}) — got: {value[:80]!r} ({e})")
    return coerced


async def _dispatch_tool(chat_agent: ChatAgent, tool_name: str, arguments: Dict, media_engine: Optional[str] = None) -> Any:
    """Local tools go through ChatAgent's own sandboxed executors, everything
    else through the MCP registry. Returns the raw result (dict for local
    tools; string or parsed-JSON for MCP tools, matching
    mcp_registry.call_tool's contract). media_engine only applies to the two
    Media tool names — a node-config choice (data.mediaEngine, see
    _run_tool_node), never part of arguments itself."""
    if tool_name == "transcribe_media":
        return await chat_agent._execute_transcribe_media(arguments, media_engine)
    if tool_name == "extract_image_text":
        return await chat_agent._execute_extract_image_text(arguments, media_engine)
    if tool_name in chat_agent._FILESYSTEM_TOOL_NAMES:
        return await chat_agent._execute_filesystem_tool(tool_name, arguments)

    import anyio
    raw_result = await anyio.to_thread.run_sync(
        lambda: mcp_registry.call_tool(tool_name, arguments)
    )
    try:
        return json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    except (json.JSONDecodeError, TypeError):
        return raw_result


_STRUCTURED_FIELD_JSON_TYPES = {"boolean": "boolean", "string": "string", "number": "number", "array": "array"}
_STRUCTURED_FIELD_DEFAULTS = {"boolean": False, "string": "", "number": 0, "array": []}


def _build_structured_output_grammar(fields: List[Dict[str, Any]]):
    """
    Generalizes grammar-constrained-JSON generation to an arbitrary
    user-authored field list — this is what lets a plain "llm" node be
    configured to do any structured judgment call (is_export/format/parts,
    needs_search/whole_document/query, or anything else a user defines)
    instead of needing its own dedicated node kind per judgment. Returns
    None (caller falls back to response_format json_object) if llama_cpp
    isn't importable, there are no fields, or compilation fails for any
    reason.
    """
    if not fields:
        return None
    try:
        from llama_cpp import LlamaGrammar
    except ImportError:
        return None

    properties = {}
    for f in fields:
        name = f.get("name")
        if not name:
            continue
        prop: Dict[str, Any] = {"type": _STRUCTURED_FIELD_JSON_TYPES.get(f.get("type"), "string")}
        if f.get("description"):
            prop["description"] = f["description"]
        if prop["type"] == "array":
            prop["items"] = {"type": "string"}
        properties[name] = prop
    if not properties:
        return None

    schema = {"type": "object", "properties": properties, "required": list(properties.keys())}
    try:
        return LlamaGrammar.from_json_schema(json.dumps(schema))
    except Exception as e:
        logger.warning(f"Structured-output grammar compile failed, falling back to json_object mode: {e}")
        return None


_VISION_IMAGE_EXTS = {"png", "jpg", "jpeg"}
_MAX_VISION_IMAGE_BYTES = 15 * 1024 * 1024  # matches BaseAgent._MAX_VISION_IMAGE_BYTES


def _find_upstream_image(upstream_outputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Looks for an image reference directly among this node's upstream
    outputs — the shape a "document_upload_trigger" injects
    ({file_path, document_id, filename, file_type}) when its own
    "Also trigger on image uploads" option is on (see
    app.api.documents's upload endpoint). Only ever finds one this way:
    images never flow through Extract/Chunk (no OCR path for them — see
    that endpoint's comment), so a trigger's own direct output is the only
    place an image shows up in a workflow today. Returns the first match,
    or None.
    """
    for value in upstream_outputs.values():
        if isinstance(value, dict) and str(value.get("file_type", "")).lower() in _VISION_IMAGE_EXTS and value.get("file_path"):
            return value
    return None


def _attach_workflow_vision_image(messages: List[Dict], model_name: Optional[str], upstream_outputs: Dict[str, Any]) -> None:
    """
    Mirrors app.core.agents.base.BaseAgent._attach_vision_images for a
    workflow "llm" node — mutates messages[-1]'s content into an OpenAI-
    style content-parts list with the image embedded as real vision input,
    but ONLY when:
      1. an image is actually present among this node's upstream outputs
         (see _find_upstream_image), and
      2. the model THIS NODE resolved (data.modelName, or the active model
         when left blank) is itself vision-capable.
    Deliberately does NOT reuse BaseAgent._attach_vision_images directly —
    that method checks whether the globally ACTIVE chat model is vision-
    capable, which is the wrong question here: a workflow node picks its
    own model explicitly, independent of whatever's active for chat.
    """
    image = _find_upstream_image(upstream_outputs)
    if not image or not messages or messages[-1].get("role") != "user":
        return

    from app.db.database import SessionLocal
    from app.db.crud import get_active_vision_mmproj_path, get_model_vision_mmproj_path
    with SessionLocal() as db:
        if model_name:
            mmproj_path = get_model_vision_mmproj_path(db, model_name)
        else:
            mmproj_path = get_active_vision_mmproj_path(db)
    if not mmproj_path:
        return

    import os
    import base64
    file_path = image["file_path"]
    try:
        if os.path.getsize(file_path) > _MAX_VISION_IMAGE_BYTES:
            logger.warning(f"Skipping oversized image for workflow vision input: {file_path}")
            return
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    except OSError as e:
        logger.warning(f"Could not read image '{file_path}' for workflow vision input: {e}")
        return

    ext = str(image.get("file_type", "")).lower()
    mime = "jpeg" if ext == "jpg" else ext
    messages[-1]["content"] = [
        {"type": "text", "text": messages[-1]["content"]},
        {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
    ]


async def _run_ai_reasoning_node(
    executor: ExecutorAgent, instruction: str, upstream_outputs: Dict[str, Any], model_name: str,
    output_fields: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None, max_tokens: Optional[int] = None,
) -> Any:
    """The "llm" node kind — a pure reasoning step (e.g. "summarize this",
    "decide which of these matches"), always bound to an explicit,
    user-picked model. No tool schema, so there's nothing for the model to
    hallucinate a tool call for; it just answers the one instruction it was
    configured with, grounded in its actual upstream inputs. Mirrors
    ExecutorAgent.generate_arguments' directness (one-shot, not streamed).

    temperature/max_tokens (data.temperature/data.maxTokens on the node)
    default to 0.0/1024 when unset — a judgment call (the common case: a
    decide/classify-style structured output) wants determinism, not
    creativity, and 1024 tokens is already generous for one — but nothing
    stops a node built for a different purpose (e.g. a creative freeform
    summary) from being configured with its own values.

    output_fields (data.outputFields on the node — a list of {name, type,
    description}) turns this into a structured judgment call instead of a
    plain-text one: the model is grammar-constrained (see
    _build_structured_output_grammar) to emit exactly that JSON shape, and
    this returns the PARSED DICT rather than a string — e.g. a "Decide:
    need search?" use configures fields
    [needs_search: boolean, whole_document: boolean, query: string] and a
    downstream "logic" node reads needs_search directly. Left unset
    (the default), behavior is unchanged — plain text out.

    On a JSON-parse failure with output_fields set, logs a warning and
    returns type-appropriate empty defaults for every field rather than
    raising — the same graceful-degradation behavior the dedicated
    decide/classify methods this generalizes already had, so a hiccup in
    one judgment call doesn't hard-fail the whole chat turn."""
    llm = executor.get_llm(model_name)
    if not llm:
        detail = f"Model '{model_name}' isn't loaded/downloaded" if model_name else "No model is active"
        raise WorkflowError(f"{detail} — pick one from the LLM panel, or on this node.")

    context_str = json.dumps(upstream_outputs, indent=2, default=str) if upstream_outputs else "No upstream input."
    # Clamped against this node's OWN resolved model's real context window
    # (llm.n_ctx(), read from its GGUF metadata) — a node-configured value
    # saved while a larger-context model was picked can otherwise exceed
    # what the model actually running this call supports.
    requested_max_tokens = max_tokens if max_tokens is not None else 1024
    kwargs: Dict[str, Any] = {
        "temperature": temperature if temperature is not None else 0.0,
        "max_tokens": min(requested_max_tokens, llm.n_ctx()),
    }

    if output_fields:
        system_prompt = (
            "You are one step in a user-designed workflow. Perform exactly the "
            "instruction below, using the provided upstream data as your input. "
            "Respond with a single JSON object matching the required shape — "
            "no other text."
        )
        grammar = _build_structured_output_grammar(output_fields)
        if grammar is not None:
            kwargs["grammar"] = grammar
        else:
            kwargs["response_format"] = {"type": "json_object"}
    else:
        system_prompt = (
            "You are one step in a user-designed workflow. Perform exactly the "
            "instruction below, using the provided upstream data as your input. "
            "Answer directly and only with the result — no preamble, no meta-"
            "commentary about being an AI step."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Instruction: {instruction}\n\nUpstream data:\n{context_str}"},
    ]
    # Purely additive — a no-op unless an image is actually sitting among
    # this node's upstream outputs AND its own picked model is vision-
    # capable (see _attach_workflow_vision_image's docstring).
    _attach_workflow_vision_image(messages, model_name, upstream_outputs)
    # Every blocking llama.cpp call for one chat turn must run on the SAME
    # thread (llm_executor is single-worker by default — see chat.py) as
    # any other call sharing this model instance (_run_chat_generation_node's
    # own _call_llm_text call in particular): calling create_chat_completion
    # directly on whatever thread happens to be running this coroutine
    # (the asyncio loop's own thread) and then handing the SAME instance to
    # a different thread for the next call in the same turn produced
    # empty/corrupted output against the Metal backend in real testing —
    # not just a style preference.
    import asyncio
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(llm_executor, lambda: llm.create_chat_completion(messages=messages, **kwargs))
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

    if not output_fields:
        return content

    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Structured llm node returned invalid JSON, using empty defaults: {e}")
        return {f["name"]: _STRUCTURED_FIELD_DEFAULTS.get(f.get("type"), "") for f in output_fields if f.get("name")}


def _get_node_state(workflow_id: int, node_id: str, key: str) -> Optional[str]:
    """Reads one persisted (workflow, node, key) value — state a node needs
    to remember BETWEEN separate runs. See app.db.models.WorkflowNodeState."""
    from app.db.database import SessionLocal
    from app.db.models import WorkflowNodeState
    with SessionLocal() as db:
        row = (
            db.query(WorkflowNodeState)
            .filter(
                WorkflowNodeState.workflow_id == workflow_id,
                WorkflowNodeState.node_id == node_id,
                WorkflowNodeState.key == key,
            )
            .first()
        )
        return row.value if row else None


def _set_node_state(workflow_id: int, node_id: str, key: str, value: str) -> None:
    """Upserts one persisted (workflow, node, key) value — see _get_node_state."""
    from app.db.database import SessionLocal
    from app.db.models import WorkflowNodeState
    with SessionLocal() as db:
        row = (
            db.query(WorkflowNodeState)
            .filter(
                WorkflowNodeState.workflow_id == workflow_id,
                WorkflowNodeState.node_id == node_id,
                WorkflowNodeState.key == key,
            )
            .first()
        )
        if row:
            row.value = value
        else:
            db.add(WorkflowNodeState(workflow_id=workflow_id, node_id=node_id, key=key, value=value))
        db.commit()


def _run_logic_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any], workflow_id: Optional[int] = None) -> Any:
    """
    A generic gate/condition — n8n's "IF" node. data.field names a key to
    read off the upstream value (blank uses the upstream value directly —
    e.g. when upstream is already a plain boolean/string, not a dict);
    data.operator/data.value decide whether the condition holds. Returns
    the upstream value UNCHANGED when true (so a downstream node reads
    exactly what it would have without this gate in the way), or None when
    false — the same "None means skip" signal a "needs_search: False"
    decision used to silently short-circuit search with before this node
    kind existed. The dispatch loops (run_chat_workflow/run_workflow/
    run_ingestion_workflow/run_schedule_workflow) separately track which
    node IDS are gated closed (by identity, not by inspecting return
    values — a ordinary node legitimately returning None elsewhere must
    not be mistaken for a closed gate) and skip running anything with no
    other path in whose sole upstream is one of those ids, cascading
    forward.

    operator=="changed_since_last_run" is the odd one out: unlike every
    other operator (a pure function of THIS run's data), it compares
    against a value persisted from the PREVIOUS run (WorkflowNodeState,
    keyed by this exact node) and holds only when the subject is
    different — this is what makes a schedule-triggered chain (e.g.
    gmail_list_messages on a timer) fire the rest of the chain only when
    there's actually something new, instead of re-processing the same
    latest item every single poll. Requires workflow_id — raises if it's
    missing rather than silently always/never holding.
    """
    data = node.get("data", {})
    label = data.get("label") or node["id"]
    upstream_ids = _upstream_node_ids(node["id"], edges)
    upstream = node_outputs.get(upstream_ids[0]) if upstream_ids else None

    field = data.get("field")
    subject = upstream.get(field) if field and isinstance(upstream, dict) else upstream

    operator = data.get("operator", "is_true")
    value = data.get("value")

    if operator == "is_true":
        holds = bool(subject)
    elif operator == "is_false":
        holds = not bool(subject)
    elif operator == "equals":
        holds = str(subject) == str(value)
    elif operator == "not_equals":
        holds = str(subject) != str(value)
    elif operator == "contains":
        holds = str(value or "").lower() in str(subject or "").lower()
    elif operator == "matches_regex":
        import re
        try:
            holds = bool(re.search(str(value or ""), str(subject or ""), re.IGNORECASE))
        except re.error as e:
            raise WorkflowError(f"Node '{label}': invalid regex — {e}")
    elif operator == "changed_since_last_run":
        if workflow_id is None:
            raise WorkflowError(f"Node '{label}': \"changed since last run\" only works inside a saved workflow.")
        subject_str = "" if subject is None else str(subject)
        previous = _get_node_state(workflow_id, node["id"], "last_value")
        holds = subject_str != (previous or "")
        if holds:
            _set_node_state(workflow_id, node["id"], "last_value", subject_str)
    else:
        raise WorkflowError(f"Node '{label}' has an unknown condition '{operator}'.")

    return upstream if holds else None


def _lookup_installed_database(database_id) -> Dict[str, Any]:
    """Resolves a Marketplace-installed database/vector-store row (see
    app.api.marketplace_databases) to what the dbengines dispatch layer
    needs to talk to it. Raises WorkflowError — never returns a partial or
    not-ready row silently — since a node pointing at a deleted or still-
    installing database is a configuration problem the user needs to see,
    not something to paper over."""
    from app.db.database import SessionLocal
    from app.db.models import InstalledDatabase

    with SessionLocal() as db:
        row = db.query(InstalledDatabase).filter(InstalledDatabase.id == database_id).first()
        if not row:
            raise WorkflowError(f"No installed database with id {database_id} — install one from the Marketplace's Databases category first.")
        if row.status != "ready":
            raise WorkflowError(f"Database '{row.name}' isn't ready yet (status: {row.status}).")
        return {"engine_id": row.engine_id, "storage_path": row.storage_path, "category": row.category, "name": row.name}


async def _run_database_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> Any:
    """Runs against an installed relational database — one node covers
    both a Marketplace-installed database of the user's own choosing (any
    engine in app.core.dbengines' catalog: sqlite, duckdb, ... — a raw SQL
    query, params bound as that engine's own named-placeholder style, see
    GET /api/marketplace/databases/catalog's placeholder_syntax) AND the
    app's own bundled SQLite database (seed.BUILTIN_AEGIS_DB_ENGINE_ID —
    browsed/edited through app.core.aegis_db_browser's safety model: its
    system tables are always visible via "list" but never writable —
    insert/update/delete/a write through "query" only ever work on a
    table the user created themselves, the same allowlist/permission
    split the canvas browser enforces), selected the same way any other
    installed database is: from data.databaseId, not a separate hardcoded
    node kind. data.operation (list/insert/update/delete/create_table/
    query) and data.table only apply to the SQLite case (table unused for
    "query"); data.query only applies otherwise UNLESS operation=="query",
    which reuses that same field — the raw-SQL escape hatch works
    identically whichever database is selected. insert/update values, and
    a query's own named parameters, come from the same staticInputs+edge-
    mapping every tool node already uses; update/delete additionally need
    a primary-key value, resolved the same way under the key "__pk".
    create_table takes data.table as the NEW table's name and
    data.newTableColumns (a list of {name, type, nullable}) as its
    columns — see aegis_db_browser.create_table. query runs data.query
    directly — see aegis_db_browser.run_query for what it allows (any
    SELECT, any statement against a table the user created, or a fresh
    CREATE TABLE, which becomes immediately usable afterward)."""
    from app.core.workflows.seed import BUILTIN_AEGIS_DB_ENGINE_ID

    data = node.get("data", {})
    label = data.get("label") or node["id"]
    database_id = data.get("databaseId")
    if not database_id:
        raise WorkflowError(f"Node '{label}' has no database selected — pick one installed from the Marketplace.")

    info = _lookup_installed_database(database_id)
    if info["category"] != "relational":
        raise WorkflowError(f"'{info['name']}' is a vector store, not a relational database — use a vector node instead.")

    if info["engine_id"] == BUILTIN_AEGIS_DB_ENGINE_ID:
        from app.core import aegis_db_browser as browser
        from app.db.database import SessionLocal

        operation = data.get("operation", "list")
        args = _resolve_named_arguments(node, edges, node_outputs)

        db = SessionLocal()
        try:
            if operation == "query":
                query = data.get("query")
                if not query:
                    raise WorkflowError(f"Node '{label}' has no query configured.")
                return browser.run_query(db, query, args)

            table = data.get("table")
            if not table:
                raise WorkflowError(f"Node '{label}' has no table {'name' if operation == 'create_table' else 'selected'}.")

            if operation == "list":
                limit = int(data.get("limit") or 50)
                return browser.get_rows(db, table, limit, 0)
            elif operation == "insert":
                return browser.insert_row(db, table, args)
            elif operation in ("update", "delete"):
                pk_value = args.pop("__pk", None)
                if pk_value is None:
                    raise WorkflowError(f"Node '{label}' needs a primary key value — set a \"__pk\" fixed value or wire one in from upstream.")
                if operation == "update":
                    return browser.update_row(db, table, pk_value, args)
                return browser.delete_row(db, table, pk_value)
            elif operation == "create_table":
                columns = data.get("newTableColumns") or []
                if not columns:
                    raise WorkflowError(f"Node '{label}' needs at least one column to create '{table}' with.")
                return browser.create_table(db, table, columns)
            else:
                raise WorkflowError(f"Node '{label}' has an unknown operation '{operation}'.")
        except browser.TableError as e:
            raise WorkflowError(f"'{label}': {e}")
        finally:
            db.close()

    query = data.get("query")
    if not query:
        raise WorkflowError(f"Node '{label}' has no query configured.")
    params = _resolve_named_arguments(node, edges, node_outputs)
    return await relational_engine.run_query(info["engine_id"], info["storage_path"], query, params)


async def _run_embedding_node(
    node: Dict, edges: List[Dict], node_outputs: Dict[str, Any], chat_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Embeds upstream text with data.embeddingModel — a model id downloaded
    from the Marketplace's Embedding Models category (see
    app.core.embeddings), or the app's own bundled dense model if unset.
    Split out from the "vector" node (which used to embed AND store/search
    in one step) so embedding is its own single-purpose node, matching
    every other node kind here. Returns both the vectors and the original
    texts together so a downstream "vector" node's upsert can still store
    each vector alongside the text it came from — and, for a "search" use
    (e.g. wired after an "llm" node's structured decision, maybe through a
    "logic" gate), that same vector is used directly by a
    Marketplace-installed store's plain dense search instead of being
    redundantly recomputed there (see _run_vector_node).

    A dict upstream carrying a "query" key (e.g. a structured "llm" node's
    {needs_search, whole_document, query} output, or a "logic" node
    passing that same dict through) is a special case: embeds just the
    "query" field (falling back to chat_ctx's message when query is empty
    — the whole-document case — so this doesn't hard-fail a turn that
    legitimately has no short query string) rather than stringifying the
    whole dict, and carries every other field through unchanged so
    anything reading the upstream chain further downstream still sees
    them despite this node sitting in between.
    """
    from app.core.embeddings.manager import get_embedder

    data = node.get("data", {})
    label = data.get("label") or node["id"]
    upstream_ids = _upstream_node_ids(node["id"], edges)
    upstream = node_outputs.get(upstream_ids[0]) if upstream_ids else None

    passthrough_meta: Dict[str, Any] = {}
    if isinstance(upstream, dict) and "query" in upstream:
        passthrough_meta = {k: v for k, v in upstream.items() if k not in ("texts", "vectors")}
        query_text = upstream.get("query") or (chat_ctx.get("message") if chat_ctx else None)
        texts = [query_text] if query_text else []
    else:
        texts = upstream if isinstance(upstream, list) else [upstream]
        texts = [str(t) for t in texts if t]
    if not texts:
        raise WorkflowError(f"Node '{label}' had no text to embed — wire in a Chunk node or another text-producing step.")

    try:
        model = await anyio.to_thread.run_sync(get_embedder, data.get("embeddingModel"))
    except ValueError as e:
        raise WorkflowError(f"Node '{label}': {e}")

    vectors = await anyio.to_thread.run_sync(lambda: [v.tolist() for v in model.embed(texts)])
    return {**passthrough_meta, "texts": texts, "vectors": vectors}


async def _run_vector_node(
    node: Dict, edges: List[Dict], node_outputs: Dict[str, Any],
    conversation_id: Optional[str] = None, chat_ctx: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Upserts or searches (data.operation) an installed vector store — pure
    storage/search, no embedding of its own: both "upsert" and "search"
    against a Marketplace-installed store need an "embedding" node wired in
    upstream (its {"texts", "vectors"} output for upsert; its "vectors" for
    search's query vector) — a plain dense-search store never resolves
    data.embeddingModel itself, so there's exactly one place a workflow
    configures which embedding model it's using, not two.

    One engine_id is special: seed.BUILTIN_VECTOR_ENGINE_ID
    ("aegis_hybrid") is Aegis's own bundled document store — the real
    hybrid dense+BM25+reranked collection app.core.rag.processor.
    hybrid_search searches, registered as a normal-looking installed
    database by seed.ensure_builtin_vector_store so it shows up as a real
    dropdown option. Selecting it for "search" runs that real function
    directly (needs conversation_id, only available inside a chat-
    connected run — see run_chat_workflow, which passes
    chat_agent.connection_id). "upsert" against it re-embeds the upstream
    text itself via the real dense+sparse indexing
    app.core.rag.processor.hybrid_embed_and_upsert uses — a generic
    Embedding node's dense-only vectors can't reproduce this collection's
    hybrid shape, so (like "search" above) they're ignored in favor of
    this store's own real embedding — and needs to run inside a loop over
    attachments so each chunk batch can be linked to the right
    document_id (see _find_ancestor_loop_item); outside of that, it
    raises rather than storing under the wrong document.
    """
    from app.core.workflows.seed import BUILTIN_VECTOR_ENGINE_ID

    data = node.get("data", {})
    label = data.get("label") or node["id"]
    database_id = data.get("databaseId")
    operation = data.get("operation", "search")
    if not database_id:
        raise WorkflowError(f"Node '{label}' has no vector store selected — pick one installed from the Marketplace.")

    info = _lookup_installed_database(database_id)
    if info["category"] != "vector":
        raise WorkflowError(f"'{info['name']}' is a relational database, not a vector store — use a database node instead.")

    upstream_ids = _upstream_node_ids(node["id"], edges)
    upstream = node_outputs.get(upstream_ids[0]) if upstream_ids else None

    if operation == "upsert":
        if info["engine_id"] == BUILTIN_VECTOR_ENGINE_ID:
            # Real hybrid ingestion, not a refusal: this collection's
            # dense+sparse point shape and SQL-side document linkage can't
            # come from a generic Embedding node's dense-only vectors (same
            # reason "search" below always embeds its own query internally
            # rather than trusting an upstream Embedding node) — so this
            # re-embeds the upstream text itself via the exact same
            # dense+sparse indexing app.core.rag.processor.ingest_document
            # uses, via the shared hybrid_embed_and_upsert. Needs the
            # document_id/filename the original attachment item carried —
            # recovered from the nearest ancestor loop's current item (see
            # _find_ancestor_loop_item) since Chunk/Embedding's own outputs
            # are plain text with no document identity attached.
            texts = upstream.get("texts") if isinstance(upstream, dict) else upstream
            texts = [str(t) for t in texts] if isinstance(texts, list) else []
            if not texts:
                raise WorkflowError(f"Node '{label}' had no text to store — wire in a Chunk (or Embedding) node upstream.")
            loop_item = _find_ancestor_loop_item(node["id"], edges, node_outputs)
            if not loop_item:
                raise WorkflowError(
                    f"Node '{label}': storing into Aegis's built-in document store needs to run inside a loop "
                    f"over attachments (e.g. the seeded pipeline's \"pending attachments\" loop) so each chunk "
                    f"batch can be linked back to the right document."
                )
            from app.core.rag.processor import hybrid_embed_and_upsert
            count = await anyio.to_thread.run_sync(hybrid_embed_and_upsert, loop_item["document_id"], texts, loop_item.get("filename") or "document")
            return {"upserted": count}
        if not (isinstance(upstream, dict) and upstream.get("vectors")):
            raise WorkflowError(f"Node '{label}' needs an Embedding node wired in upstream — it has no vectors to store.")
        vectors = upstream["vectors"]
        texts = upstream.get("texts") or []
        # Deterministic per node+position, not per content — re-running the
        # same workflow overwrites the same points instead of accumulating
        # duplicates. A plain string like "node_0" isn't a valid Qdrant
        # point id (must be an unsigned int or a UUID), so it's hashed into
        # one via uuid5 — still just as deterministic, and a valid id on
        # every engine in the catalog, not only the ones that accept
        # arbitrary strings.
        ids = [str(uuid.uuid5(uuid.NAMESPACE_OID, f"{node['id']}:{i}")) for i in range(len(vectors))]
        payloads = [{"text": t} for t in texts] if texts else [{} for _ in vectors]
        count = await vector_engine.upsert(info["engine_id"], info["storage_path"], ids, vectors, payloads)
        return {"upserted": count}

    elif operation == "search":
        # Whether search should run at all is a "logic" node's job now
        # (wired upstream, gating this whole branch — see
        # _run_logic_node and the dispatch loops' gate-propagation pass),
        # not a special case in here.
        # An incoming edge explicitly mapped to inputField "query" (see
        # WorkflowsView.tsx's EdgeConfigPanel) wins over every implicit
        # fallback below — e.g. wiring a specific field of a multi-field
        # upstream dict in as the query, rather than this node's own
        # "upstream.get('query')" guess.
        query_text = _resolve_named_arguments(node, edges, node_outputs).get("query") or None
        if not query_text:
            query_text = data.get("instruction") or None
        if not query_text and isinstance(upstream, dict):
            query_text = upstream.get("query") or None
        if not query_text and isinstance(upstream, str):
            query_text = upstream
        if not query_text and chat_ctx:
            query_text = chat_ctx.get("message")
        if not query_text:
            raise WorkflowError(f"Node '{label}' has no query text — set one or wire in upstream text.")

        top_k = int(data.get("topK") or 5)

        if info["engine_id"] == BUILTIN_VECTOR_ENGINE_ID:
            if not conversation_id:
                raise WorkflowError(f"Node '{label}': Aegis's built-in document store can only be searched from a chat-connected workflow.")
            # Always re-embeds the query itself here, even if an "Embedding"
            # node is wired in upstream — hybrid_search needs BOTH a dense
            # AND a sparse vector, which a generic Embedding node's single
            # dense-only output can't provide (same reason upsert against
            # this store ignores an upstream Embedding node's vectors too).
            # data.searchMode is a real choice exposed on this node's config
            # (only meaningful for this store — the only one with a sparse
            # index at all) — see app.core.rag.processor.hybrid_search's
            # mode param. Reranking is never done here — it's always
            # rerank=False, trusting the store's own fusion/similarity
            # order; wire a "reranker" node downstream (see
            # _run_reranker_node) to re-score these candidates with a
            # cross-encoder instead.
            from app.core.rag.processor import hybrid_search
            search_mode = data.get("searchMode") or "hybrid"
            return await anyio.to_thread.run_sync(
                lambda: hybrid_search(query=query_text, conversation_id=conversation_id, top_k=top_k, mode=search_mode, rerank=False)
            )

        # A Marketplace-installed store's plain dense search always uses an
        # upstream "Embedding" node's own vector — never re-embeds here —
        # so a workflow configures its embedding model in exactly one
        # place (e.g. "Decide" -> "Embedding" -> "Vector").
        query_vector = upstream["vectors"][0] if isinstance(upstream, dict) and upstream.get("vectors") else None
        if query_vector is None:
            raise WorkflowError(f"Node '{label}' needs an Embedding node wired in upstream — it has no vector to search with.")
        return await vector_engine.search(info["engine_id"], info["storage_path"], query_vector, top_k)

    else:
        raise WorkflowError(f"Node '{label}' has an unknown vector operation '{operation}'.")


async def _run_reranker_node(
    node: Dict, edges: List[Dict], node_outputs: Dict[str, Any],
    chat_ctx: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Re-scores an upstream "vector" search's candidate list against a query
    using a cross-encoder, returning the top data.topK reordered by actual
    relevance rather than raw embedding/BM25 similarity. Deliberately its
    own node, not a checkbox on "vector" — a workflow can search wide (a
    generous vector node topK) and rerank down to a smaller final count
    explicitly, the same two-step shape a real retrieval pipeline has.

    The candidate list comes from an incoming edge explicitly mapped to
    inputField "results" (see NodeInputColumn in WorkflowsView.tsx), else
    the nearest upstream node whose output is a list — normally the
    "vector" node feeding this one directly. The query text follows the
    same resolution order a "vector" search node's own query text does:
    a mapped "query" field, then data.instruction, then the live chat
    message.

    data.rerankerModel: a cross-encoder downloaded from the Marketplace's
    Rerankers category (app.core.rerankers), or the app's own default
    (app.core.rag.processor.get_reranker) when unset — same resolution
    shape as a "vector"/"embedding" node's data.embeddingModel, except
    neither this nor the default is ever bundled: both need
    sentence-transformers installed from the Dependencies panel first, and
    a missing install surfaces as a real, visible workflow-run failure
    here (unlike hybrid_search's own automatic rerank pass, which quietly
    skips reranking instead — see rerank_chunks's own docstring).
    """
    from app.core.rag.processor import rerank_chunks
    from app.core.rerankers import get_cross_encoder

    data = node.get("data", {})
    label = data.get("label") or node["id"]

    mapped = _resolve_named_arguments(node, edges, node_outputs)
    results = mapped.get("results")
    if not isinstance(results, list):
        for src_id in _upstream_node_ids(node["id"], edges):
            val = node_outputs.get(src_id)
            if isinstance(val, list):
                results = val
                break
    if not isinstance(results, list) or not results:
        return results if isinstance(results, list) else []

    query_text = mapped.get("query") or data.get("instruction") or None
    if not query_text and chat_ctx:
        query_text = chat_ctx.get("message")
    if not query_text:
        raise WorkflowError(f"Node '{label}' has no query text — set one or wire in upstream text.")

    try:
        reranker = await anyio.to_thread.run_sync(get_cross_encoder, data.get("rerankerModel") or None)
    except ValueError as e:
        raise WorkflowError(f"Node '{label}': {e}")

    top_k = int(data.get("topK") or 5)
    return await anyio.to_thread.run_sync(lambda: rerank_chunks(query_text, results, top_k=top_k, reranker=reranker))


async def _run_extract_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> str:
    """Pulls plain text out of a document file: PDF, DOCX, PPTX, MD,
    TXT/CSV, XLSX. data.enginePerFormat optionally maps a format
    ("pdf"/"docx"/...) to a specific engine from app.core.extraction_engines'
    per-format registry (populated from the Marketplace's Extraction
    category) — a map rather than one flat engine id because a node fed by
    an upstream trigger (e.g. "On document upload") doesn't know the file's
    format ahead of time, so one node has to be able to hold a choice for
    every format it might see. data.engine (a single id, no format
    scoping) is kept only for a node built before enginePerFormat existed,
    or one wired to a fixed, known file path of one known format — checked
    only when enginePerFormat has no entry for the format actually
    encountered. Left fully unset, this is byte-identical to calling
    app.core.rag.processor.extract_text directly — the same extractor the
    app's own document upload/RAG pipeline always uses, regardless of what
    any workflow does here."""
    from app.core import extraction_engines

    data = node.get("data", {})
    file_path = _resolve_named_arguments(node, edges, node_outputs).get("filePath") or data.get("filePath")
    if not file_path:
        raise WorkflowError(f"Node '{data.get('label') or node['id']}' has no file path — set one or wire it in from upstream.")

    ext = Path(file_path).suffix.lstrip(".").lower()
    fmt = extraction_engines.format_for_ext(ext)
    engine_id = (data.get("enginePerFormat") or {}).get(fmt) or data.get("engine")
    return await anyio.to_thread.run_sync(lambda: extraction_engines.extract(file_path, ext, engine_id))


async def _run_http_request_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    A generic outbound HTTP call — the escape hatch for any REST API that
    doesn't have (and doesn't need) its own MCP server, since nothing else
    here lets a workflow call an arbitrary endpoint. data.url/data.method
    are static node config, overridable per-run via an edge explicitly
    mapped to inputField "url"/"method"/"body" (see
    _resolve_named_arguments's own docstring on why this is a plain
    field-to-field mapping, not free-form templating) — e.g. a classifier
    "llm" node deciding which endpoint to hit. data.headers is a plain
    name->value map, always static (a header value that needs to vary
    per-run belongs in the URL or body instead, kept simple rather than
    adding a second mapping surface for the same job). data.body, for
    POST/PUT/PATCH, is sent as real JSON if it parses as valid JSON, else
    as plain text — no guessing beyond that.

    Returns {status_code, headers, body} — body is the parsed JSON object
    when the response is JSON, else the raw text, so a downstream node
    (e.g. "Set fields", or a classifier "llm") can read a specific
    response field either way.
    """
    import httpx

    data = node.get("data", {})
    label = data.get("label") or node["id"]
    mapped = _resolve_named_arguments(node, edges, node_outputs)
    url = mapped.get("url") or data.get("url")
    if not url:
        raise WorkflowError(f"Node '{label}' has no URL — set one or wire it in from upstream.")
    method = str(mapped.get("method") or data.get("method") or "GET").upper()
    headers = data.get("headers") or {}
    body = mapped.get("body") if "body" in mapped else data.get("body")

    request_kwargs: Dict[str, Any] = {"headers": headers}
    if body and method in ("POST", "PUT", "PATCH"):
        try:
            request_kwargs["json"] = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            request_kwargs["content"] = body

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.request(method, url, **request_kwargs)
    except httpx.HTTPError as e:
        raise WorkflowError(f"Node '{label}': request to '{url}' failed — {e}")

    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            parsed_body: Any = resp.json()
        except ValueError:
            parsed_body = resp.text
    else:
        parsed_body = resp.text

    return {"status_code": resp.status_code, "headers": dict(resp.headers), "body": parsed_body}


def _run_set_fields_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic dict reshaping — n8n's "Set"/"Edit Fields" node, for
    renaming/picking/adding fields without needing an "llm" judgment call
    (slow, non-deterministic, overkill) just to reformat data between two
    nodes with different shapes. data.staticInputs (name -> literal
    string, set directly on this node) and any edge explicitly mapped to a
    named inputField (see _resolve_named_arguments) both become keys on
    the output dict — a field mapping wins over a same-named static value,
    since it's a real pull from upstream rather than a hand-typed
    fallback. data.mode "merge" (default) additionally starts from a
    shallow copy of the upstream value when it's itself a dict, so this
    only has to declare the fields that actually change; "replace" starts
    from nothing but this node's own fields, dropping everything else the
    upstream value carried.
    """
    data = node.get("data", {})
    upstream_ids = _upstream_node_ids(node["id"], edges)
    upstream = node_outputs.get(upstream_ids[0]) if upstream_ids else None

    fields = _resolve_named_arguments(node, edges, node_outputs)

    if data.get("mode", "merge") == "merge" and isinstance(upstream, dict):
        result = dict(upstream)
        result.update(fields)
        return result
    return fields


def _run_merge_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> Any:
    """
    Explicitly combines EVERY upstream node's output into one value —
    every other node here that can technically have more than one
    incoming edge (llm, http_request, set_fields, database's query
    parameters) only ever reads ONE upstream by default
    (_upstream_node_ids(...)[0], or a static/mapped field) for anything
    not routed through _resolve_named_arguments's explicit field mapping;
    wiring a second, unmapped edge into one of those silently drops it
    with no warning (see WorkflowsView.tsx's own edge-count warning on
    those kinds, which points here instead). This node exists so
    combining branches is a deliberate, visible step.

    data.mergeMode:
    - "list" (default): every upstream output, in edge order, as a plain
      list — safe for any input shape.
    - "concat": every upstream value stringified (dicts/lists as JSON) and
      joined with data.separator (default: two newlines) — for combining
      several text-producing branches into one block.
    - "first": the first upstream output that isn't None/empty/falsy — a
      fallback chain (e.g. "prefer the vector search result, but if that
      branch was gated closed by an upstream Logic node, use this static
      default instead").
    """
    data = node.get("data", {})
    upstream_ids = _upstream_node_ids(node["id"], edges)
    values = [node_outputs.get(uid) for uid in upstream_ids]

    mode = data.get("mergeMode", "list")
    if mode == "concat":
        sep = data.get("separator", "\n\n")
        return sep.join(v if isinstance(v, str) else json.dumps(v, default=str) for v in values if v is not None)
    if mode == "first":
        for v in values:
            if v:
                return v
        return None
    return values


def _run_chunk_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> List[str]:
    """Splits upstream text into overlapping chunks. data.strategy picks
    which one from app.core.chunking_engines' registry (recursive/
    paragraph/sentence/fixed) — left unset, this is byte-identical to the
    app's own document RAG pipeline (app.core.rag.processor.chunk_text),
    same as every other strategy-registry node (Extract) defaults to the
    real pipeline's own behavior when unconfigured."""
    from app.core import chunking_engines

    data = node.get("data", {})
    upstream_ids = _upstream_node_ids(node["id"], edges)
    text = node_outputs.get(upstream_ids[0]) if upstream_ids else None
    if not isinstance(text, str) or not text.strip():
        raise WorkflowError(f"Node '{data.get('label') or node['id']}' had no text to chunk — wire in an Extract node or another text-producing step.")

    chunk_size = int(data.get("chunkSize") or 300)
    overlap = int(data.get("overlap") or 50)
    return chunking_engines.chunk(text, chunk_size=chunk_size, overlap=overlap, strategy_id=data.get("strategy"))


async def _run_send_to_chat_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any], workflow_id: Optional[int] = None) -> str:
    """
    Delivers upstream text into a chat conversation from OUTSIDE any live
    chat turn — the node a schedule-triggered chain uses to actually
    notify the user. A "chat_reply" node only works inside a real
    chat_trigger turn (it replies into the SAME conversation the user just
    typed into); a schedule_trigger has no such conversation to reply
    into. Writes a real ChatMessage row (so it's there whenever the
    conversation is opened, durable across restarts) into one dedicated,
    stable conversation per workflow — every "Post to Chat" node on the
    same workflow lands in the same thread by default, titled from the
    workflow's own name via the normal first-message preview — and
    broadcasts a notification so the user learns about it immediately,
    wherever they are in the app right now, without having to go check.
    """
    if workflow_id is None:
        raise WorkflowError("A \"Post to Chat\" node only works inside a saved workflow.")

    data = node.get("data", {})
    label = data.get("label") or node["id"]
    upstream_ids = _upstream_node_ids(node["id"], edges)
    if not upstream_ids:
        raise WorkflowError(f"Node '{label}' has nothing wired into it to send.")
    upstream = node_outputs.get(upstream_ids[0])

    field = data.get("field")
    content = upstream.get(field) if field and isinstance(upstream, dict) else upstream
    if content is None:
        content = ""
    elif not isinstance(content, str):
        content = json.dumps(content, default=str)
    if not content.strip():
        raise WorkflowError(f"Node '{label}' received empty content — nothing to send.")

    from app.db.database import SessionLocal
    from app.db.crud import add_chat_message
    from app.db.models import Workflow as WorkflowModel

    # data.conversationId (set on the node — a real conversation the user
    # picked, e.g. their main chat) wins when present; otherwise the
    # dedicated per-workflow thread this node has always used.
    conversation_id = data.get("conversationId") or f"workflow_{workflow_id}_auto"

    def _persist():
        with SessionLocal() as db:
            row = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
            workflow_name = row.name if row else "Workflow"
            add_chat_message(db, conversation_id, "assistant", content)
            return workflow_name

    workflow_name = await anyio.to_thread.run_sync(_persist)

    await manager.broadcast_json({
        "type": "workflow_chat_message",
        "workflow_id": workflow_id,
        "conversation_id": conversation_id,
        "workflow_name": workflow_name,
        "content": content,
    })

    return content


_RETRYABLE_KINDS = {"mcp", "tool", "database", "vector", "embedding", "reranker", "http_request"}
_RETRY_DELAYS = (0.5, 1.5)  # gaps between attempts — 3 tries total


async def _execute_node_with_retry(
    node_id: str,
    nodes: Dict[str, Dict],
    edges: List[Dict],
    node_outputs: Dict[str, Any],
    chat_agent: ChatAgent,
    executor: ExecutorAgent,
    conversation_id: Optional[str] = None,
    chat_ctx: Optional[Dict[str, Any]] = None,
    workflow_id: Optional[int] = None,
) -> Any:
    """
    Thin wrapper around _execute_node that automatically retries node kinds
    which talk to something outside this process — an MCP tool call, an
    installed relational/vector database, a downloaded embedding/reranker
    model, or a raw HTTP request — since a transient network blip or a
    momentary rate limit shouldn't fail an entire run when trying again a
    moment later would have worked. Every OTHER kind ("llm", "logic",
    "extract", "chunk", "set_fields", "merge", the trigger/reply kinds) is
    pure local compute where a second attempt would just fail identically,
    so those go straight through with no added latency.

    Never retries a WorkflowError — that signals a structural/config
    problem (no tool selected, invalid regex, missing database) that a
    retry can't fix, exactly like it can't fix it the first time. Only a
    plain Exception bubbling out of the underlying call (the actual
    tool/DB/model call failing) is treated as possibly-transient.
    """
    kind = nodes[node_id].get("data", {}).get("kind", "tool")
    if kind not in _RETRYABLE_KINDS:
        return await _execute_node(node_id, nodes, edges, node_outputs, chat_agent, executor, conversation_id=conversation_id, chat_ctx=chat_ctx, workflow_id=workflow_id)

    label = nodes[node_id].get("data", {}).get("label") or node_id
    last_exc: Exception = None
    for attempt, delay in enumerate((0.0,) + _RETRY_DELAYS):
        if delay:
            logger.warning(f"Node '{label}' failed ({last_exc}) — retrying in {delay}s (attempt {attempt + 1}/{len(_RETRY_DELAYS) + 1}).")
            await anyio.sleep(delay)
        try:
            return await _execute_node(node_id, nodes, edges, node_outputs, chat_agent, executor, conversation_id=conversation_id, chat_ctx=chat_ctx, workflow_id=workflow_id)
        except WorkflowError:
            raise
        except Exception as e:
            last_exc = e
    raise last_exc


async def _execute_node(
    node_id: str,
    nodes: Dict[str, Dict],
    edges: List[Dict],
    node_outputs: Dict[str, Any],
    chat_agent: ChatAgent,
    executor: ExecutorAgent,
    conversation_id: Optional[str] = None,
    chat_ctx: Optional[Dict[str, Any]] = None,
    workflow_id: Optional[int] = None,
) -> Any:
    """Runs exactly one node and returns its output. Shared by the main
    top-to-bottom pass and _run_loop_node (which calls this once per item
    for a loop's body node, with node_outputs patched to expose the current
    item as that body's "upstream" for the duration of that one call).
    conversation_id/chat_ctx are only ever set when called from within a
    chat-connected run (run_chat_workflow) — needed by the "vector" node's
    engine_id=="aegis_hybrid" special case; every other node ignores them.
    workflow_id is only needed by a "logic" node using the
    "changed_since_last_run" operator (persisted dedup state) — every other
    kind ignores it."""
    node = nodes[node_id]
    data = node.get("data", {})
    kind = data.get("kind", "tool")
    label = data.get("label") or data.get("toolName") or node_id

    if kind in ("mcp", "tool"):
        tool_name = data.get("toolName")
        if not tool_name:
            raise WorkflowError(f"Node '{label}' has no tool selected.")

        if data.get("isAi"):
            schema = _find_tool_schema(tool_name)
            upstream = {src: node_outputs.get(src) for src in _upstream_node_ids(node_id, edges)}
            arguments = executor.generate_arguments(
                tool_name=tool_name,
                tool_schema=schema or {},
                overall_plan=[],
                step_reason=data.get("instruction", ""),
                prior_results=upstream,
                entity_context="",
                user_request=data.get("instruction", ""),
                model_name=data.get("modelName") or None,
            )
            if isinstance(arguments, dict) and "error" in arguments:
                raise WorkflowError(arguments["error"])
        else:
            arguments = _resolve_named_arguments(node, edges, node_outputs)
            arguments = _coerce_structured_arguments(arguments, _find_tool_schema(tool_name))

        return await _dispatch_tool(chat_agent, tool_name, arguments, data.get("mediaEngine"))

    elif kind == "llm":
        # Left unset, this resolves to whichever model is currently active
        # (ExecutorAgent.get_llm(None) — same convention chat_reply and
        # every ChatAgent judgment call already use), not an error — a
        # decide/classify-style node shouldn't need its own explicit model
        # pick just to work out of the box.
        upstream = {src: node_outputs.get(src) for src in _upstream_node_ids(node_id, edges)}
        # An incoming edge explicitly mapped to inputField "instruction"
        # (see WorkflowsView.tsx's EdgeConfigPanel) overrides this node's
        # own static Prompt text — e.g. wiring a search result's "content"
        # straight in as the instruction itself, not just contextual data.
        # Every other resolved field (if any) is folded into the context
        # dict too, under its own mapped name, alongside the normal
        # per-source-id upstream — purely additive, so a graph that never
        # sets any mapping behaves exactly as before.
        mapped = _resolve_named_arguments(node, edges, node_outputs)
        instruction = mapped.pop("instruction", None) or data.get("instruction", "")
        if mapped:
            upstream = {**upstream, **mapped}
        return await _run_ai_reasoning_node(
            executor, instruction, upstream, data.get("modelName") or None,
            output_fields=data.get("outputFields"), temperature=data.get("temperature"), max_tokens=data.get("maxTokens"),
        )

    elif kind == "logic":
        return _run_logic_node(node, edges, node_outputs, workflow_id=workflow_id)

    elif kind == "database":
        return await _run_database_node(node, edges, node_outputs)

    elif kind == "vector":
        return await _run_vector_node(node, edges, node_outputs, conversation_id=conversation_id, chat_ctx=chat_ctx)

    elif kind == "reranker":
        return await _run_reranker_node(node, edges, node_outputs, chat_ctx=chat_ctx)

    elif kind == "embedding":
        return await _run_embedding_node(node, edges, node_outputs, chat_ctx=chat_ctx)

    elif kind == "extract":
        return await _run_extract_node(node, edges, node_outputs)

    elif kind == "chunk":
        return _run_chunk_node(node, edges, node_outputs)

    elif kind == "loop":
        return await _run_loop_node(node_id, nodes, edges, node_outputs, chat_agent, executor, conversation_id=conversation_id, chat_ctx=chat_ctx, workflow_id=workflow_id)

    elif kind == "send_to_chat":
        return await _run_send_to_chat_node(node, edges, node_outputs, workflow_id=workflow_id)

    elif kind in ("chat_trigger", "chat_reply", "export_document"):
        raise WorkflowError(f"'{label}' only runs when this workflow is connected as the chat handler — connect it from the toolbar, or send a chat message instead of clicking Run.")

    elif kind == "document_upload_trigger":
        raise WorkflowError(f"'{label}' only runs when this workflow is connected as the ingestion handler — connect it from the toolbar, or upload a document instead of clicking Run.")

    elif kind == "schedule_trigger":
        raise WorkflowError(f"'{label}' only runs on its own schedule — it fires automatically in the background, nothing to click.")

    elif kind == "http_request":
        return await _run_http_request_node(node, edges, node_outputs)

    elif kind == "set_fields":
        return _run_set_fields_node(node, edges, node_outputs)

    elif kind == "merge":
        return _run_merge_node(node, edges, node_outputs)

    elif kind == "switch":
        return _run_switch_node(node, edges, node_outputs)

    else:
        raise WorkflowError(f"Node '{label}' has an unrecognized type '{kind}'.")


def _trace_loop_body_chain(node_id: str, edges: List[Dict]) -> List[str]:
    """
    Traces the loop's body forward as a linear chain of steps — e.g. a
    per-attachment "Extract -> Chunk -> Embedding -> Vector" ingestion run
    — stopping at whichever comes first: a step with no further children
    (the chain ends inside the loop), or a step that's also fed by
    something OTHER than the previous chain step (a join back into the
    surrounding pipeline — that step runs once, after every item finishes,
    not once per item). Raises if any chain step branches into more than
    one next step — only a single linear chain is supported, not a
    sub-graph with its own branching.
    """
    body_ids = _downstream_node_ids(node_id, edges)
    if not body_ids:
        return []
    if len(body_ids) > 1:
        raise WorkflowError(f"Loop's body branches into more than one step directly after it — only a single linear chain is supported.")

    chain = [body_ids[0]]
    while True:
        next_ids = _downstream_node_ids(chain[-1], edges)
        if not next_ids:
            break
        if len(next_ids) > 1:
            raise WorkflowError(f"Loop's body step '{chain[-1]}' branches into more than one next step — only a single linear chain is supported.")
        candidate = next_ids[0]
        if _upstream_node_ids(candidate, edges) != [chain[-1]]:
            break  # fed by something besides this chain — a join point, not part of the loop body
        chain.append(candidate)
    return chain


def _downstream_gated_by(start_id: str, edges: List[Dict]) -> set:
    """
    The full set of nodes that would have NO surviving input if `start_id`
    (a "logic" node) gates closed this run — computed as a fixed point ("a
    node is gated once EVERY one of its upstream nodes is gated"), not a
    simple linear trace like _trace_loop_body_chain: unlike a loop's body
    (always a plain chain by construction), a logic node's downstream can
    both fan out (e.g. one classifier feeding both the reply generator AND
    an export node directly) and fan back in at a join fed by a DIFFERENT,
    ungated branch too (e.g. that same reply generator, also fed by an
    independent vector search) — such a join must never be gated just
    because ONE of its several inputs happens to be, so a plain forward
    trace (which would either stop too early at the first fan-out or,
    worse, incorrectly swallow the join) doesn't work here.
    """
    gated = {start_id}
    changed = True
    while changed:
        changed = False
        for e in edges:
            src, tgt = e["source"], e["target"]
            if tgt in gated or src not in gated:
                continue
            tgt_upstream = _upstream_node_ids(tgt, edges)
            if tgt_upstream and all(u in gated for u in tgt_upstream):
                gated.add(tgt)
                changed = True
    gated.discard(start_id)
    return gated


def _downstream_gated_by_edges(closed_edge_ids: set, edges: List[Dict]) -> set:
    """
    Generalizes _downstream_gated_by to per-EDGE gating, for a "switch"
    node — which (unlike "logic"'s all-or-nothing single gate) closes all
    but one of its own outgoing edges each run, so the thing being closed
    is a specific set of edges, not a whole node. Same fixed-point
    reasoning: a node is gated once every one of its incoming edges is
    either in closed_edge_ids or comes from an already-gated node. A node
    with one closed-edge input and one ordinary, ungated input is
    correctly NOT gated — the ungated input alone is enough to run it,
    same join-safety guarantee _downstream_gated_by gives per-node,
    generalized to work when only some of a source's edges close instead
    of the whole node.
    """
    edges_by_target: Dict[str, List[Dict]] = {}
    for e in edges:
        edges_by_target.setdefault(e["target"], []).append(e)

    gated: set = set()
    changed = True
    while changed:
        changed = False
        for tgt, incoming in edges_by_target.items():
            if tgt in gated:
                continue
            if all(e["id"] in closed_edge_ids or e["source"] in gated for e in incoming):
                gated.add(tgt)
                changed = True
    return gated


def _run_switch_node(node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> Any:
    """
    A multi-way router — n8n's "Switch" node, generalizing "logic"'s
    binary gate to N labeled branches instead of chaining several logic
    gates to get the same effect. data.field/subject resolution is
    identical to _run_logic_node (blank field = whole upstream value).
    Matching is plain string equality against each outgoing edge's OWN
    edge.data.caseValue (set per-edge in the canvas — see NodeOutputColumn
    — since a single node can have many outgoing edges, unlike every other
    per-node config field here): the edge(s) whose caseValue equals str(
    subject) stay open; if none match, every edge with NO caseValue set
    acts as the default branch instead. Returns the upstream value
    unchanged (same convention as "logic") — actual branch selection
    happens via edge gating (_gate_switch_node), not this return value.
    """
    data = node.get("data", {})
    upstream_ids = _upstream_node_ids(node["id"], edges)
    upstream = node_outputs.get(upstream_ids[0]) if upstream_ids else None

    field = data.get("field")
    subject = upstream.get(field) if field and isinstance(upstream, dict) else upstream
    return subject


def _switch_matched_edges(node_id: str, node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> List[Dict]:
    """Re-derives which of this "switch" node's own outgoing edges match
    its current run's resolved value — same field/subject resolution
    _run_switch_node used to produce its return value, recomputed here
    (cheap, purely local) rather than threading the match out of band
    through _execute_node's single-return-value contract."""
    data = node.get("data", {})
    upstream_ids = _upstream_node_ids(node_id, edges)
    upstream = node_outputs.get(upstream_ids[0]) if upstream_ids else None
    field = data.get("field")
    subject = upstream.get(field) if field and isinstance(upstream, dict) else upstream
    subject_str = str(subject)

    outgoing = [e for e in edges if e["source"] == node_id]
    matched = [e for e in outgoing if (e.get("data") or {}).get("caseValue") not in (None, "") and str((e.get("data") or {}).get("caseValue")) == subject_str]
    if not matched:
        matched = [e for e in outgoing if not (e.get("data") or {}).get("caseValue")]
    return matched


def _gate_switch_node(node_id: str, node: Dict, edges: List[Dict], node_outputs: Dict[str, Any]) -> set:
    """Every downstream node that should be skipped this run because it's
    only reachable through one of THIS switch node's non-matching outgoing
    edges — see _switch_matched_edges/_downstream_gated_by_edges."""
    outgoing_ids = {e["id"] for e in edges if e["source"] == node_id}
    matched_ids = {e["id"] for e in _switch_matched_edges(node_id, node, edges, node_outputs)}
    closed_edge_ids = outgoing_ids - matched_ids
    if not closed_edge_ids:
        return set()
    return _downstream_gated_by_edges(closed_edge_ids, edges)


async def _run_loop_node(
    node_id: str,
    nodes: Dict[str, Dict],
    edges: List[Dict],
    node_outputs: Dict[str, Any],
    chat_agent: ChatAgent,
    executor: ExecutorAgent,
    conversation_id: Optional[str] = None,
    chat_ctx: Optional[Dict[str, Any]] = None,
    workflow_id: Optional[int] = None,
) -> List[Any]:
    """
    Deliberately scoped down from full n8n loop semantics: the loop's body
    is a single linear chain of steps (see _trace_loop_body_chain) — no
    branching, no nested loops. Resolves data.itemsField from the loop's
    own upstream input into an array, runs the whole chain once per item
    (the item stands in as the chain's first step's "upstream output" for
    the duration of that one run, the same way any other edge-mapped value
    would arrive), and collects each run's last step's result.

    A per-item document ingestion chain (the seeded pipeline's "On chat
    message" -> loop over pending attachments -> Extract -> Chunk ->
    Embedding -> Vector) needs one more thing real re-ingestion-avoidance
    requires: once an item that carries a "document_id" successfully runs
    a chain ending in a "vector" upsert step, that UserDocument row is
    marked "ready" — otherwise every later turn in the same conversation
    would re-extract/re-chunk/re-embed the same already-indexed document
    from scratch. This is intentionally narrow (gated on both the specific
    item shape AND the chain's last step kind+operation) rather than a
    generic side effect any loop triggers.
    """
    node = nodes[node_id]
    data = node.get("data", {})
    label = data.get("label") or node_id

    chain = _trace_loop_body_chain(node_id, edges)
    if not chain:
        raise WorkflowError(f"Loop '{label}' has nothing wired after it to repeat.")

    upstream_ids = _upstream_node_ids(node_id, edges)
    if not upstream_ids:
        raise WorkflowError(f"Loop '{label}' has nothing feeding it a list to iterate.")
    source_output = node_outputs.get(upstream_ids[0])

    items_field = data.get("itemsField")
    items = source_output.get(items_field) if items_field and isinstance(source_output, dict) else source_output
    if not isinstance(items, list):
        raise WorkflowError(f"Loop '{label}' didn't receive a list to iterate over (got {type(items).__name__}).")

    last_step = nodes.get(chain[-1], {})
    marks_documents_ready = (
        last_step.get("data", {}).get("kind") == "vector"
        and last_step.get("data", {}).get("operation") == "upsert"
    )

    results = []
    for item in items:
        originals = {step_id: node_outputs.get(step_id) for step_id in [node_id, *chain]}
        node_outputs[node_id] = item
        try:
            last_result = None
            for step_id in chain:
                last_result = await _execute_node_with_retry(step_id, nodes, edges, node_outputs, chat_agent, executor, conversation_id=conversation_id, chat_ctx=chat_ctx, workflow_id=workflow_id)
                node_outputs[step_id] = last_result
            results.append(last_result)
            if marks_documents_ready and isinstance(item, dict) and item.get("document_id") is not None:
                await anyio.to_thread.run_sync(_mark_user_document_ready, item["document_id"])
        finally:
            for step_id, original in originals.items():
                node_outputs[step_id] = original

    return results


def _mark_user_document_ready(document_id: int) -> None:
    from app.db.database import SessionLocal
    from app.db.models import UserDocument

    with SessionLocal() as db:
        doc = db.query(UserDocument).filter(UserDocument.id == document_id).first()
        if doc and doc.status != "ready":
            doc.status = "ready"
            db.commit()


def _enrich_attachments(attachments: List[Dict]) -> List[Dict]:
    """
    The WebSocket payload only ever carries {document_id, filename,
    file_type} per attachment (see useSocket.ts's Attachment interface) —
    but a workflow's own Extract node needs the real file_path, and the
    ingestion loop needs "ready"/"processing" to know which attachments
    still need indexing at all. Looked up once here (a single DB round
    trip for the whole turn) rather than by each node individually.
    """
    if not attachments:
        return []
    from app.db.database import SessionLocal
    from app.db.models import UserDocument

    ids = [a["document_id"] for a in attachments if a.get("document_id") is not None]
    with SessionLocal() as db:
        rows = {d.id: d for d in db.query(UserDocument).filter(UserDocument.id.in_(ids)).all()}

    enriched = []
    for a in attachments:
        doc = rows.get(a.get("document_id"))
        enriched.append({
            **a,
            "file_path": doc.file_path if doc else None,
            "status": doc.status if doc else "failed",
        })
    return enriched


def _find_ancestor_loop_item(node_id: str, edges: List[Dict], node_outputs: Dict[str, Any]) -> Optional[Dict]:
    """
    Walks straight upstream (only ever one predecessor at a time — this is
    only ever called from inside a loop's own single linear body chain, see
    _trace_loop_body_chain) until it finds a "loop" node, returning
    node_outputs for that loop's id. During _run_loop_node's per-item
    execution this holds the CURRENT item (see the patch/restore there),
    so a chain step several hops downstream of the loop (e.g. a "vector"
    node three steps after it) can still recover the original item's own
    fields (e.g. document_id/filename) that don't survive being piped
    through Extract/Chunk/Embedding's own plain-text outputs. Returns None
    outside of a loop body (nothing to find).
    """
    current = node_id
    while True:
        upstream = _upstream_node_ids(current, edges)
        if len(upstream) != 1:
            return None
        current = upstream[0]
        val = node_outputs.get(current)
        if isinstance(val, dict) and "document_id" in val and "file_path" in val:
            return val


def _upstream_by_kind(node_id: str, nodes: Dict[str, Dict], edges: List[Dict], node_outputs: Dict[str, Any], kind: str):
    """First upstream node of the given kind directly wired into node_id, or
    None. Used by the chat-only node executors below to find their specific
    typed inputs (e.g. export_document looking for its "chat_reply"
    upstream) without caring about wiring order."""
    for src_id in _upstream_node_ids(node_id, edges):
        if nodes.get(src_id, {}).get("data", {}).get("kind") == kind:
            return node_outputs.get(src_id)
    return None


def _upstream_dict_with_key(node_id: str, edges: List[Dict], node_outputs: Dict[str, Any], key: str) -> Optional[Dict]:
    """First upstream output that's a dict containing `key`, or None —
    duck-typed rather than kind-checked, since the value's shape (e.g. a
    structured "llm" node configured with is_export/format/parts fields)
    is what matters, not which node kind produced it."""
    for src_id in _upstream_node_ids(node_id, edges):
        val = node_outputs.get(src_id)
        if isinstance(val, dict) and key in val:
            return val
    return None


async def _run_export_document_node(
    chat_agent: ChatAgent,
    node: Dict,
    nodes: Dict[str, Dict],
    edges: List[Dict],
    node_outputs: Dict[str, Any],
    token_callback,
) -> str:
    """
    Post-reply export pipeline: prefers the "```export" fence the reply's
    system prompt asked for, falls back to _extract_export_content_via_llm,
    then _execute_export_document, then appends the download link — pushed
    through token_callback the same way, since the reply text was already
    streamed (by the "llm" node feeding "chat_reply" — see
    _run_chat_generation_node) before this node ever runs. Passthrough
    (the reply text, unchanged) when nothing requested an export.

    Whether to export, and in what format, is entirely a classifier
    "llm" node's judgment call (structured output with is_export/format
    fields — see WorkflowsView.tsx's EXPORT_CLASSIFIER_PROMPT for a
    starting-point prompt shape) wired directly upstream of this node —
    there's no other way to request one; the chat composer's own Export
    menu (which used to set this explicitly, overriding the classifier)
    was removed in favor of this node.
    """
    import asyncio
    import re

    label = node.get("data", {}).get("label") or node["id"]
    text = _upstream_by_kind(node["id"], nodes, edges, node_outputs, "chat_reply")
    if text is None:
        raise WorkflowError(f"'{label}' has no upstream Chat Reply node wired in — it needs the reply text to (maybe) export.")

    # Duck-typed, not kind-checked: whichever upstream (typically a plain
    # "llm" node configured with is_export/format/parts structured
    # output) carries this key.
    classification = _upstream_dict_with_key(node["id"], edges, node_outputs, "is_export") or {}
    export_fmt = classification.get("format") if classification.get("is_export") else None
    if not export_fmt:
        return text

    loop = asyncio.get_running_loop()
    fence_match = re.search(r'```export\s*\n(.*?)```', text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        export_content = chat_agent._clean_export_content(fence_match.group(1).strip())
        text = (text[:fence_match.start()] + export_content + text[fence_match.end():]).strip()
    else:
        export_content = await loop.run_in_executor(llm_executor, lambda: chat_agent._extract_export_content_via_llm(text, export_fmt))
        text = export_content

    export_result = await chat_agent._execute_export_document({"content": export_content, "format": export_fmt})
    if export_result.get("success"):
        extra = f"\n\n[Download {export_result['filename']}]({export_result['download_url']})"
    else:
        extra = f"\n\n*(Couldn't export that as {export_fmt}: {export_result.get('error')})*"
    text += extra
    if token_callback:
        token_callback(extra)
    return text


async def _run_chat_generation_node(
    chat_agent: ChatAgent,
    node: Dict,
    nodes: Dict[str, Dict],
    edges: List[Dict],
    node_outputs: Dict[str, Any],
    chat_ctx: Dict[str, Any],
    token_callback,
) -> str:
    """
    Runs a plain "llm" node in "chat generation" mode — model config lives
    ONLY on "llm" nodes, never on "chat_reply" (see run_chat_workflow's
    generation_ids detection: this only ever runs for the one "llm" node
    wired directly into the graph's "chat_reply" node with nothing else
    wired after it). Every OTHER "llm" node in the same graph (a
    decide/classify judgment call, elsewhere) is completely unaffected —
    those still run through the plain one-shot _run_ai_reasoning_node via
    the normal _execute_node dispatch, no streaming, no context-gathering.
    "chat_reply" itself is just a thin "send" marker downstream of this —
    see its own dispatch case in run_chat_workflow.

    Reuses ChatAgent._call_llm_text directly rather than reimplementing it,
    so the reply gets the exact same real token-by-token streaming and
    JSON-leak guard normal Chat Mode already depends on.

    Every non-None upstream output (of THIS llm node — memory/vector
    search/classifier/etc., whatever's wired directly into it, not into
    chat_reply) is folded into full_context and passed through
    build_chat_prompt — the node's own data.instruction is the base_prompt
    override slot, scoped to this graph. A dict upstream carrying an
    "is_export" key (duck-typed, not kind-checked — e.g. another plain
    "llm" node configured with is_export/format/parts structured output)
    folds in as the exact same [MULTI-PART QUESTION]/[EXPORT INSTRUCTION]
    blocks below. A "vector" node's search-result list formats as a
    document-excerpt block.
    Anything else (a tool result, plain text, a "logic" pass-through, ...)
    is folded in generically as text/JSON, so this still composes with a
    fully custom graph, not only the seeded one.
    """
    from app.prompts.chat import build_chat_prompt

    data = node.get("data", {})
    label = data.get("label") or node["id"]

    context_parts: List[str] = []
    compound_parts = None
    export_fmt = None
    for src_id in _upstream_node_ids(node["id"], edges):
        src_kind = nodes.get(src_id, {}).get("data", {}).get("kind")
        val = node_outputs.get(src_id)
        if val is None:
            continue
        if src_kind == "chat_trigger":
            continue  # already available as chat_ctx — not duplicated into context
        elif src_kind in ("vector", "reranker"):
            # A generic vector node's "search" output (or a "reranker"
            # node's reordered subset of the same shape) is a raw list of
            # chunk dicts — format it the same way a document-excerpt
            # block reads, rather than a JSON dump, so a swapped-in
            # Marketplace store reads identically to the bundled one.
            # Two shapes exist: aegis_hybrid's hybrid_search returns flat
            # {content, filename, ...} dicts; every other engine's generic
            # vector_engine.search returns {id, score, payload: {text,
            # ...}} (payload is whatever the upsert step stored — see
            # _run_vector_node's upsert branch, which stores extracted
            # text under "text", not "content"). None here means an
            # upstream "logic" gate judged this branch wasn't needed this
            # turn (see run_chat_workflow's gate-propagation pass).
            if isinstance(val, list) and val:
                block = "Relevant excerpts from your uploaded documents:\n\n"
                for chunk in val:
                    if not isinstance(chunk, dict):
                        continue
                    payload = chunk.get("payload") if isinstance(chunk.get("payload"), dict) else chunk
                    content = payload.get("content") or payload.get("text") or ""
                    filename = payload.get("filename") or "document"
                    block += f"--- Source: {filename} ---\n{content}\n\n"
                context_parts.append(block)
        elif isinstance(val, dict) and "is_export" in val:
            # A classifier "llm" node's raw is_export/format/parts output —
            # translated here (not by a Python wrapper method), since this
            # is just a plain structured "llm" node's output.
            compound_parts = val.get("parts") or None
            export_fmt = val.get("format") if val.get("is_export") else None
        else:
            context_parts.append(val if isinstance(val, str) else json.dumps(val, indent=2, default=str))

    full_context = "\n\n".join(context_parts)
    if compound_parts:
        full_context += (
            "\n\n[MULTI-PART QUESTION]: The user asked multiple distinct things in "
            "one message. Address EACH of the following separately and completely "
            "in your answer — do not skip any:\n"
            + "\n".join(f"{i+1}. {p}" for i, p in enumerate(compound_parts))
        )
    if export_fmt:
        full_context += (
            f"\n\n[EXPORT INSTRUCTION]: The user also wants this turn's content "
            f"exported as a {export_fmt.upper()} file — that happens automatically "
            f"right after you answer, no tool call needed from you. Write your "
            f"answer as usual, and additionally wrap ONLY the exact final content "
            f"that should go into the exported file in a fenced block tagged "
            f"\"export\", e.g.:\n```export\n<the exact content to export, nothing "
            f"else>\n```\nPut just the clean final content there — no meta-commentary, "
            f"no mention of tools, modes, or capabilities, no apologies. If your "
            f"answer already IS the exportable content (a summary, a tagline, a "
            f"table), the fence can just repeat that same text."
        )

    llm = chat_agent.get_llm(data.get("modelName") or None)
    if not llm:
        raise WorkflowError(f"'{label}' has no model available — download/activate one from the LLM panel, or pick one on this node.")

    all_tools_str = chat_agent.get_available_tools(chat_ctx["message"])
    chat_prompt = build_chat_prompt(full_context, all_tools_str, base_prompt=data.get("instruction") or None)

    from app.core import context_config as ctx_cfg
    chat_cfg = ctx_cfg.get("chat")
    max_history = chat_cfg.get("max_history_messages", 20)
    max_chars = chat_cfg.get("max_msg_chars", 4000)
    # Clamped against THIS node's own resolved model's real context window —
    # same reasoning as _run_ai_reasoning_node above: a node can call a
    # different model than whatever's globally active, so the configured
    # response length must be bounded by the model that actually runs this
    # call, not just trusted outright.
    reply_budget = min(chat_cfg.get("max_output_tokens", 5120), llm.n_ctx())
    history = [
        {"role": m["role"], "content": m["content"][:max_chars] + ("..." if len(m["content"]) > max_chars else "")}
        for m in (chat_ctx.get("history") or [])[-max_history:]
    ]
    history = _trim_history_to_token_budget(llm, chat_prompt, history, reply_buffer=reply_budget)

    messages = [{"role": "system", "content": chat_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": chat_ctx["message"]})
    # Purely additive — a no-op unless an image is actually sitting among
    # this node's upstream outputs AND its own picked model is vision-
    # capable (see _attach_workflow_vision_image's docstring). Needed here
    # too, not just in _run_ai_reasoning_node above: this is the node the
    # WorkflowsView.tsx UI itself points users toward for vision ("wire it
    # directly into an 'llm' node with a vision-capable model picked") is
    # usually THIS node — the one llm node feeding chat_reply directly —
    # not a separate judgment-call node.
    upstream_outputs = {src: node_outputs.get(src) for src in _upstream_node_ids(node["id"], edges)}
    _attach_workflow_vision_image(messages, data.get("modelName") or None, upstream_outputs)

    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        llm_executor, lambda: chat_agent._call_llm_text(messages, token_callback, max_tokens=reply_budget)
    )


_HISTORY_MAX_STR = 4000
_HISTORY_MAX_LIST = 20


def _summarize_output_for_history(value: Any, _depth: int = 0) -> Any:
    """
    Shrinks a node's raw output down to something safe to persist in
    WorkflowRun.node_outputs_json — run history is meant for "what did each
    step produce, roughly", not a second copy of every embedding vector or
    a document's full extracted text. Recurses into dicts/lists (bounded to
    3 levels — deep enough for the shapes these nodes actually return, e.g.
    a vector search's list-of-dicts-with-nested-payload) with two special
    cases: an "vectors" key (an embedding node's own output) collapses to a
    one-line description instead of the raw floats, and an overlong
    string/list is truncated with a "... N more" marker rather than
    silently dropped, so it's still obvious something was there.
    """
    if _depth > 3:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "vectors" and isinstance(v, list):
                dim = len(v[0]) if v and isinstance(v[0], list) else 0
                out[k] = f"<{len(v)} vector(s), dim {dim}>"
            else:
                out[k] = _summarize_output_for_history(v, _depth + 1)
        return out
    if isinstance(value, list):
        head = [_summarize_output_for_history(v, _depth + 1) for v in value[:_HISTORY_MAX_LIST]]
        if len(value) > _HISTORY_MAX_LIST:
            head.append(f"... {len(value) - _HISTORY_MAX_LIST} more")
        return head
    if isinstance(value, str) and len(value) > _HISTORY_MAX_STR:
        return value[:_HISTORY_MAX_STR] + f"... ({len(value) - _HISTORY_MAX_STR} more chars)"
    return value


def _record_run_start(run_id: str, workflow_id: int, trigger: str) -> None:
    from app.db.database import SessionLocal
    from app.db.models import WorkflowRun

    with SessionLocal() as db:
        db.add(WorkflowRun(id=run_id, workflow_id=workflow_id, trigger=trigger, status="running"))
        db.commit()


def _record_run_finish(
    run_id: str, status: str, node_outputs: Dict[str, Any],
    failed_node_id: Optional[str] = None, error_message: Optional[str] = None,
) -> None:
    from app.db.database import SessionLocal
    from app.db.models import WorkflowRun

    summarized = {nid: _summarize_output_for_history(out) for nid, out in node_outputs.items()}
    with SessionLocal() as db:
        row = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if not row:
            return  # run row was never created (shouldn't happen) — nothing to update
        row.status = status
        row.failed_node_id = failed_node_id
        row.error_message = error_message
        row.node_outputs_json = json.dumps(summarized, default=str)
        row.finished_at = datetime.utcnow()
        db.commit()


async def run_chat_workflow(
    chat_agent: ChatAgent,
    workflow_id: int,
    message: str,
    history: List[Dict],
    attachments: Optional[List[Dict]],
    connection_id: str,
    token_callback,
    message_source: Optional[str] = None,
) -> str:
    """
    Runs one workflow graph as the live handler for a real chat turn — used
    by app.api.websocket's process_message_task when a workflow has been
    connected via app.api.workflows's /set-chat-handler; this is the only
    way a chat turn ever gets a reply now. `chat_agent` is the SAME
    per-connection instance websocket.py already keeps in agent_sessions
    (not a throwaway one) so cancellation, history persistence, and
    token-usage logging all work the same way they would for any other
    turn.

    Reuses every existing node executor unchanged via _execute_node for
    tool/database/vector/extract/chunk/loop/logic nodes, and for any
    plain "llm" node NOT wired as described below. Model config lives only
    on "llm" nodes — "chat_reply" is a thin "send" marker with no config
    of its own (see its own dispatch case below): the one "llm" node wired
    directly into it, with nothing else wired after that llm node, is
    "the" reply generator and runs through _run_chat_generation_node (real
    streaming + JSON-leak guard) instead of the one-shot
    _run_ai_reasoning_node every other "llm" node uses; "export_document"
    runs its own small wrapper too.

    "The reply" is whichever node has NO downstream edges at all — usually
    the "chat_reply" node itself, but "export_document" (if present) runs
    AFTER it and becomes the new terminal node instead. This is why
    "exactly one chat_reply node (with exactly one llm node feeding only
    it), reachable from the trigger" and "exactly
    one terminal node overall" are validated separately rather than
    assuming they're the same node
    (app.api.workflows._validate_chat_handler_graph mirrors this).

    Raises WorkflowError on any structural problem (missing/duplicate
    trigger or reply node, a node failing) — app.api.websocket
    catches this the same way it catches any other chat-turn exception,
    surfacing it as a normal chat error rather than crashing the
    connection.

    message_source: "voice" only when the composer's own mic auto-send
    fired this turn (the connected trigger node's own "Send automatically"
    setting — see app.api.voice._get_active_voice_config), None for a
    normal typed message (which includes a voice transcript the user
    reviewed before hitting Send — only the immediate, unedited auto-send
    path is ever tagged). Gated against the trigger node's own
    data.acceptsVoice in _run_chat_workflow_body below — accepted by
    default; an explicit False (unchecking "Also accept voice input")
    rejects it.
    """
    from app.db.database import SessionLocal
    from app.db.models import Workflow as WorkflowModel

    with SessionLocal() as db:
        row = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
        if not row:
            raise WorkflowError("The connected chat workflow no longer exists.")
        graph = json.loads(row.graph_json)

    run_id = uuid.uuid4().hex[:8]
    await anyio.to_thread.run_sync(_record_run_start, run_id, workflow_id, "chat")

    node_outputs: Dict[str, Any] = {}
    try:
        return await _run_chat_workflow_body(
            chat_agent, workflow_id, message, history, attachments, connection_id, token_callback, graph, run_id, node_outputs,
            message_source=message_source,
        )
    except Exception as e:
        friendly = humanize_exception(e, context="running this chat workflow")
        await anyio.to_thread.run_sync(_record_run_finish, run_id, "failed", node_outputs, None, friendly)
        raise


async def _run_chat_workflow_body(
    chat_agent: ChatAgent,
    workflow_id: int,
    message: str,
    history: List[Dict],
    attachments: Optional[List[Dict]],
    connection_id: str,
    token_callback,
    graph: Dict,
    run_id: str,
    node_outputs: Dict[str, Any],
    message_source: Optional[str] = None,
) -> str:
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    trigger_ids = [nid for nid, n in nodes.items() if n.get("data", {}).get("kind") == "chat_trigger"]
    if len(trigger_ids) != 1:
        raise WorkflowError('The connected chat workflow must have exactly one "On chat message" trigger node.')
    trigger_id = trigger_ids[0]

    # Voice messages are accepted by default — data.acceptsVoice is
    # opt-OUT (missing/None/True all accept; only an explicit False, set by
    # unchecking "Also accept voice input" on the trigger node, rejects).
    # A rejected voice turn surfaces as a real, visible WorkflowError
    # (caught by app.api.websocket the same as any other chat-turn
    # failure) rather than silently falling through to a confusing
    # generic reply.
    if message_source == "voice" and nodes[trigger_id].get("data", {}).get("acceptsVoice") is False:
        raise WorkflowError(
            'This workflow\'s "On chat message" trigger doesn\'t accept voice input yet — turn on '
            '"Also accept voice input" on that node, or type your message instead.'
        )

    enriched_attachments = await anyio.to_thread.run_sync(_enrich_attachments, attachments or [])
    node_outputs[trigger_id] = {
        "message": message, "history": history, "attachments": enriched_attachments,
        # Only attachments this pipeline hasn't already indexed — feeds a
        # "loop over pending attachments" ingestion chain (see
        # _run_loop_node) without redoing extract/chunk/embed/store for a
        # document a previous turn in this same conversation already
        # finished ingesting.
        "pending_attachments": [a for a in enriched_attachments if a.get("status") != "ready"],
        # "voice" or "text" — a real field so a Logic/Switch node can route
        # differently on it (e.g. a voice-command branch through different
        # MCP tools) instead of it only being an invisible accept/reject
        # gate on the trigger itself.
        "source": message_source or "text",
    }

    # A single workflow may hold more than one independent pipeline (e.g. a
    # separate "On document upload" tree living on the same canvas) — only
    # the subgraph reachable from THIS trigger is a chat run's concern.
    nodes, edges = _reachable_subgraph(nodes, edges, trigger_id)

    reply_ids = [nid for nid, n in nodes.items() if n.get("data", {}).get("kind") == "chat_reply"]
    if len(reply_ids) != 1:
        raise WorkflowError("The connected chat workflow must have exactly one Chat Reply node — it sends the reply.")
    chat_reply_id = reply_ids[0]

    # "chat_reply" itself is a thin "send" marker — model config lives only
    # on "llm" nodes (see _run_chat_generation_node's docstring). The real
    # generation (with streaming) happens in whichever "llm" node feeds
    # chat_reply directly AND leads ONLY to chat_reply — that's what
    # distinguishes it from an "llm" node used elsewhere in the same graph
    # for a plain judgment call (e.g. decide/classify), which must not
    # stream to the user.
    generation_ids = [
        nid for nid in _upstream_node_ids(chat_reply_id, edges)
        if nodes.get(nid, {}).get("data", {}).get("kind") == "llm"
        and _downstream_node_ids(nid, edges) == [chat_reply_id]
    ]
    if len(generation_ids) != 1:
        raise WorkflowError(
            'Wire exactly one "llm" node directly into the Chat Reply node, with nothing else wired after '
            'that llm node — that\'s what generates the reply.'
        )
    reply_source_id = generation_ids[0]

    downstream_of = {e["source"] for e in edges}
    terminal_ids = [nid for nid in nodes if nid not in downstream_of]
    if len(terminal_ids) != 1:
        raise WorkflowError("The connected chat workflow must have exactly one final step — everything should lead to one last node.")
    terminal_id = terminal_ids[0]

    executor = ExecutorAgent(chat_agent.llm_manager)
    order = _topological_order(list(nodes.values()), edges)
    chat_ctx = {"message": message, "history": history, "attachments": enriched_attachments}

    # Every node inside a "loop" node's own body chain (see
    # _trace_loop_body_chain) only ever runs THROUGH that loop's per-item
    # invocation — the main pass below must skip them, the same way it
    # already skips the trigger, or each one would run a second time here
    # with no valid per-item input (its upstream, the loop's patched
    # node_outputs slot, is back to its pre-loop value by the time this
    # pass reaches it).
    loop_body_ids = {
        step_id
        for nid, n in nodes.items()
        if n.get("data", {}).get("kind") == "loop"
        for step_id in _trace_loop_body_chain(nid, edges)
    }

    # A "logic" node whose condition is false gates off every node that
    # would have NO surviving input without it — computed as a fixed
    # point (_downstream_gated_by), not a simple linear trace: a logic
    # node's downstream can fan out (e.g. one classifier feeding both the
    # reply generator AND an export node directly) and fan back in at a
    # join fed by a DIFFERENT, ungated branch too (e.g. that same reply
    # generator, also fed by an independent vector search) — such a join
    # must never be gated just because ONE of its several inputs happens
    # to be. Precomputed once per logic node (not derived from cascading
    # "is my upstream gated" checks, which can't tell "every path in
    # happens to be gated this turn" apart from "structurally exists only
    # to serve this one gate").
    logic_gated_chains = {
        nid: _downstream_gated_by(nid, edges)
        for nid, n in nodes.items()
        if n.get("data", {}).get("kind") == "logic"
    }
    gated_closed: set = set()

    for node_id in order:
        # The "Stop Generating" button (app.api.websocket's "cancel" message
        # handler) sets this same threading.Event chat_agent already uses
        # inside its own _call_llm_text streaming loop — but that's only
        # ever consulted from WITHIN one node's own LLM call.
        # Every node before the final reply-generating one (a classifier
        # "llm" node, a tool call, a database query, ...) used to run to
        # completion regardless, since nothing here ever checked
        # cancel_event BETWEEN nodes — a turn stuck in one of those earlier
        # steps had no way to be cancelled at all, only one already
        # mid-stream on the final node did. Checked once per node, not
        # per-line, since a node's own body isn't generally interruptible
        # mid-way (same granularity the two existing per-chunk checks
        # already accept for a single LLM call). The eventual return value
        # is discarded by websocket.py's generation_id/superseded() check
        # regardless, so bailing out with whatever's accumulated so far is
        # safe — nothing downstream ever sees a cancelled turn's output.
        if getattr(chat_agent, "cancel_event", None) and chat_agent.cancel_event.is_set():
            await anyio.to_thread.run_sync(
                _record_run_finish, run_id, "failed", node_outputs, None, "Cancelled by user"
            )
            return node_outputs.get(terminal_id) or ""

        if node_id == trigger_id or node_id in loop_body_ids:
            continue

        if node_id in gated_closed:
            node_outputs[node_id] = None
            continue

        node = nodes[node_id]
        kind = node.get("data", {}).get("kind")
        label = node.get("data", {}).get("label") or node_id

        await manager.send_json(connection_id, {"type": "status", "content": f"Running '{label}'…"})
        try:
            if node_id == reply_source_id:
                result = await _run_chat_generation_node(chat_agent, node, nodes, edges, node_outputs, chat_ctx, token_callback)
            elif kind == "chat_reply":
                # Thin "send" marker — the llm node just above it (already
                # run, as reply_source_id) already did the real work and
                # already streamed live; this just carries its text
                # forward (e.g. to an Export Reply node wired after it).
                upstream_ids = _upstream_node_ids(node_id, edges)
                result = node_outputs.get(upstream_ids[0]) if upstream_ids else ""
            elif kind == "export_document":
                result = await _run_export_document_node(chat_agent, node, nodes, edges, node_outputs, token_callback)
            else:
                result = await _execute_node_with_retry(
                    node_id, nodes, edges, node_outputs, chat_agent, executor,
                    conversation_id=chat_agent.connection_id, chat_ctx=chat_ctx, workflow_id=workflow_id,
                )
        except WorkflowError:
            raise
        except Exception as e:
            raise WorkflowError(f"'{label}' failed: {e}")
        node_outputs[node_id] = result
        if kind == "logic" and result is None:
            gated_closed.update(logic_gated_chains.get(node_id, []))
        elif kind == "switch":
            gated_closed.update(_gate_switch_node(node_id, node, edges, node_outputs))

    reply_text = node_outputs.get(terminal_id) or ""
    await anyio.to_thread.run_sync(_record_run_finish, run_id, "completed", node_outputs)
    return reply_text


async def run_ingestion_workflow(document_id: int, file_path: str, filename: str, file_type: str) -> None:
    """
    Runs one workflow graph as the live handler for a document upload —
    the only way a document ever gets ingested now (app.api.documents's
    upload handler rejects the upload outright when no workflow is
    connected via app.api.workflows's /set-ingestion-handler). Structurally
    mirrors run_chat_workflow (load
    graph, inject trigger output, topological run) but has no reply/
    streaming semantics — it just runs to completion or raises, same as
    run_workflow's manual-run shape.

    Reuses the already-built "extract"/"chunk"/"embedding"/"vector" node
    executors verbatim via the normal _execute_node dispatch — no new
    per-node logic for the pipeline steps themselves, only the trigger and
    orchestration are new. No conversation_id is available here (this
    isn't a chat turn), so a "vector" node pointed at Aegis's own bundled
    store in search mode would correctly refuse to run — upsert against it
    is refused unconditionally anyway (see _run_vector_node).

    Progress broadcasts to every connected client (manager.broadcast_json),
    same as run_workflow's manual-run progress — an upload isn't tied to
    one specific chat connection the way a chat turn is.

    Raises WorkflowError on any structural problem or node failure — the
    caller (app.api.documents) catches this and marks the document
    "failed" with the error message rather than crashing the upload
    endpoint.
    """
    from app.db.database import SessionLocal
    from app.db.models import Workflow as WorkflowModel

    with SessionLocal() as db:
        row = db.query(WorkflowModel).filter(WorkflowModel.is_ingestion_handler == True).first()  # noqa: E712
        if not row:
            raise WorkflowError("No ingestion workflow is connected.")
        graph = json.loads(row.graph_json)
        workflow_id = row.id

    run_id = f"ingest_{document_id}"
    await anyio.to_thread.run_sync(_record_run_start, run_id, workflow_id, "ingestion")
    node_outputs: Dict[str, Any] = {}
    try:
        await _run_ingestion_workflow_body(document_id, file_path, filename, file_type, graph, workflow_id, node_outputs)
    except Exception as e:
        friendly = humanize_exception(e, context=f"processing '{filename}'")
        await anyio.to_thread.run_sync(_record_run_finish, run_id, "failed", node_outputs, None, friendly)
        raise
    await anyio.to_thread.run_sync(_record_run_finish, run_id, "completed", node_outputs)


async def _run_ingestion_workflow_body(
    document_id: int, file_path: str, filename: str, file_type: str,
    graph: Dict, workflow_id: int, node_outputs: Dict[str, Any],
) -> None:
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    trigger_ids = [nid for nid, n in nodes.items() if n.get("data", {}).get("kind") == "document_upload_trigger"]
    if len(trigger_ids) != 1:
        raise WorkflowError('The connected ingestion workflow must have exactly one "On document upload" trigger node.')
    trigger_id = trigger_ids[0]
    node_outputs[trigger_id] = {
        "file_path": file_path, "document_id": document_id, "filename": filename, "file_type": file_type,
    }

    # Same reasoning as run_chat_workflow: this workflow may also hold an
    # unrelated "On chat message" tree side by side on the same canvas —
    # only what's reachable from THIS trigger belongs to an ingestion run.
    nodes, edges = _reachable_subgraph(nodes, edges, trigger_id)

    chat_agent = ChatAgent(f"ingestion_run_{document_id}")
    executor = ExecutorAgent(chat_agent.llm_manager)
    order = _topological_order(list(nodes.values()), edges)

    # See run_chat_workflow's matching comment — a loop's body chain only
    # ever runs through that loop's own per-item invocation.
    loop_body_ids = {
        step_id
        for nid, n in nodes.items()
        if n.get("data", {}).get("kind") == "loop"
        for step_id in _trace_loop_body_chain(nid, edges)
    }

    # See run_chat_workflow's matching comment — a fixed-point computation
    # (_downstream_gated_by), not cascading "any upstream gated" checks or
    # a simple linear trace, so a multi-upstream join point downstream of
    # one gated branch (and one ungated one) is never itself gated, and a
    # branching downstream (e.g. one classifier feeding two next steps)
    # doesn't crash the precomputation itself.
    logic_gated_chains = {
        nid: _downstream_gated_by(nid, edges)
        for nid, n in nodes.items()
        if n.get("data", {}).get("kind") == "logic"
    }
    gated_closed: set = set()

    for node_id in order:
        if node_id == trigger_id or node_id in loop_body_ids:
            continue

        if node_id in gated_closed:
            node_outputs[node_id] = None
            continue

        node = nodes[node_id]
        kind = node.get("data", {}).get("kind")
        label = node.get("data", {}).get("label") or node_id

        await manager.broadcast_json({
            "type": "workflow_node_progress", "workflow_id": workflow_id, "run_id": f"ingest_{document_id}",
            "node_id": node_id, "status": "running", "message": f"Running '{label}'…",
        })
        try:
            result = await _execute_node_with_retry(node_id, nodes, edges, node_outputs, chat_agent, executor, workflow_id=workflow_id)
        except WorkflowError:
            raise
        except Exception as e:
            raise WorkflowError(f"'{label}' failed: {e}")
        node_outputs[node_id] = result
        if kind == "logic" and result is None:
            gated_closed.update(logic_gated_chains.get(node_id, []))
        elif kind == "switch":
            gated_closed.update(_gate_switch_node(node_id, node, edges, node_outputs))
        await manager.broadcast_json({
            "type": "workflow_node_progress", "workflow_id": workflow_id, "run_id": f"ingest_{document_id}",
            "node_id": node_id, "status": "completed", "message": f"'{label}' completed.",
        })


async def run_schedule_workflow(workflow_id: int, trigger_node_id: str) -> None:
    """
    Runs one workflow graph because its "On a schedule" trigger came due —
    invoked periodically by app.core.scheduler's scheduler_daemon background
    loop (see its own check_and_run_workflow_triggers), never by an HTTP
    request. Structurally
    mirrors run_ingestion_workflow: load the graph, restrict to the
    subgraph reachable from ONE specific trigger node (a canvas may hold
    more than one schedule_trigger, each on its own cadence, side by side
    with an unrelated chat/ingestion tree), topological run, broadcast
    progress. The only real difference is that a schedule_trigger carries
    no payload of its own to inject — whatever's wired after it (typically
    an "mcp"/"tool" node like gmail_list_messages) is what actually fetches
    something fresh on each firing, and a "logic" node further downstream
    with the "changed_since_last_run" operator is what decides whether this
    firing is actually worth acting on (see _run_logic_node).
    """
    from app.db.database import SessionLocal
    from app.db.models import Workflow as WorkflowModel

    with SessionLocal() as db:
        row = db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()
        if not row:
            raise WorkflowError(f"Workflow {workflow_id} no longer exists.")
        graph = json.loads(row.graph_json)

    run_id = uuid.uuid4().hex[:8]
    await anyio.to_thread.run_sync(_record_run_start, run_id, workflow_id, "schedule")
    node_outputs: Dict[str, Any] = {}
    try:
        await _run_schedule_workflow_body(workflow_id, trigger_node_id, graph, run_id, node_outputs)
    except Exception as e:
        friendly = humanize_exception(e, context="running this scheduled workflow")
        await anyio.to_thread.run_sync(_record_run_finish, run_id, "failed", node_outputs, None, friendly)
        raise
    await anyio.to_thread.run_sync(_record_run_finish, run_id, "completed", node_outputs)


async def _run_schedule_workflow_body(
    workflow_id: int, trigger_node_id: str, graph: Dict, run_id: str, node_outputs: Dict[str, Any],
) -> None:
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    if trigger_node_id not in nodes:
        raise WorkflowError(f"Schedule trigger node '{trigger_node_id}' no longer exists on this workflow.")

    nodes, edges = _reachable_subgraph(nodes, edges, trigger_node_id)
    node_outputs[trigger_node_id] = {"fired_at": datetime.utcnow().isoformat()}

    chat_agent = ChatAgent(f"schedule_run_{run_id}")
    executor = ExecutorAgent(chat_agent.llm_manager)
    order = _topological_order(list(nodes.values()), edges)

    loop_body_ids = {
        step_id
        for nid, n in nodes.items()
        if n.get("data", {}).get("kind") == "loop"
        for step_id in _trace_loop_body_chain(nid, edges)
    }
    logic_gated_chains = {
        nid: _downstream_gated_by(nid, edges)
        for nid, n in nodes.items()
        if n.get("data", {}).get("kind") == "logic"
    }
    gated_closed: set = set()

    for node_id in order:
        if node_id == trigger_node_id or node_id in loop_body_ids:
            continue
        if node_id in gated_closed:
            node_outputs[node_id] = None
            continue

        node = nodes[node_id]
        kind = node.get("data", {}).get("kind")
        label = node.get("data", {}).get("label") or node_id

        await manager.broadcast_json({
            "type": "workflow_node_progress", "workflow_id": workflow_id, "run_id": run_id,
            "node_id": node_id, "status": "running", "message": f"Running '{label}'…",
        })
        try:
            result = await _execute_node_with_retry(node_id, nodes, edges, node_outputs, chat_agent, executor, workflow_id=workflow_id)
        except WorkflowError:
            raise
        except Exception as e:
            raise WorkflowError(f"'{label}' failed: {e}")
        node_outputs[node_id] = result
        if kind == "logic" and result is None:
            gated_closed.update(logic_gated_chains.get(node_id, []))
        elif kind == "switch":
            gated_closed.update(_gate_switch_node(node_id, node, edges, node_outputs))
        await manager.broadcast_json({
            "type": "workflow_node_progress", "workflow_id": workflow_id, "run_id": run_id,
            "node_id": node_id, "status": "completed", "message": f"'{label}' completed.",
        })

    await manager.broadcast_json({"type": "workflow_run_complete", "workflow_id": workflow_id, "run_id": run_id, "outputs": {}})


async def run_workflow(workflow_id: int, run_id: str, graph: Dict) -> None:
    """
    Executes one workflow run top to bottom, broadcasting progress as it
    goes. Never raises past this point — a node failure is reported via
    workflow_node_progress/workflow_run_failed, not an exception the caller
    has to catch (this always runs as a background task; there's no HTTP
    response waiting on it — see app/api/workflows.py).
    """
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    node_outputs: Dict[str, Any] = {}

    chat_agent = ChatAgent(f"workflow_run_{run_id}")
    executor = ExecutorAgent(chat_agent.llm_manager)  # share the already-resolved LLM manager instance

    await anyio.to_thread.run_sync(_record_run_start, run_id, workflow_id, "manual")

    async def broadcast(node_id: Optional[str], status: str, message: str, output: Any = None) -> None:
        await manager.broadcast_json({
            "type": "workflow_node_progress",
            "workflow_id": workflow_id,
            "run_id": run_id,
            "node_id": node_id,
            "status": status,
            "message": message,
            "output": output,
        })

    try:
        order = _topological_order(list(nodes.values()), edges)
    except WorkflowError as e:
        await manager.broadcast_json({"type": "workflow_run_failed", "workflow_id": workflow_id, "run_id": run_id, "message": str(e)})
        await anyio.to_thread.run_sync(_record_run_finish, run_id, "failed", node_outputs, None, str(e))
        return

    # Loop bodies are executed inline by their owning loop node (once per
    # item, as a whole linear chain — see _trace_loop_body_chain), not by
    # the main pass — skip them there so they don't also run once, up
    # front, with no item to work with.
    loop_body_ids = {
        step_id
        for n in nodes.values()
        if n.get("data", {}).get("kind") == "loop"
        for step_id in _trace_loop_body_chain(n["id"], edges)
    }

    # See run_chat_workflow's matching comment on gate propagation — a
    # fixed-point computation (_downstream_gated_by) per logic node, not
    # cascading "any upstream gated" checks or a simple linear trace, so a
    # multi-upstream join point downstream of one gated branch (and one
    # ungated one) is never itself gated, and a branching downstream
    # doesn't crash the precomputation itself.
    logic_gated_chains = {
        nid: _downstream_gated_by(nid, edges)
        for nid, n in nodes.items()
        if n.get("data", {}).get("kind") == "logic"
    }
    gated_closed: set = set()

    for node_id in order:
        if node_id in loop_body_ids:
            continue

        if node_id in gated_closed:
            node_outputs[node_id] = None
            await broadcast(node_id, "completed", f"'{nodes[node_id].get('data', {}).get('label') or node_id}' skipped (gated).", output=None)
            continue

        node = nodes[node_id]
        data = node.get("data", {})
        kind = data.get("kind")
        label = data.get("label") or data.get("toolName") or node_id

        # A trigger only ever has real input when its own live path injects
        # it (run_chat_workflow / run_ingestion_workflow) — a manual Run
        # has none to give it. Rather than let _execute_node's dispatch
        # raise for it (aborting the ENTIRE run at node 1, before anything
        # else on the canvas gets a chance — previously true of every
        # workflow with a chat_trigger/document_upload_trigger node, which
        # is most workflows with a real live purpose), treat it as an
        # inert no-op here: whatever's actually wired downstream of it
        # will fail on its own missing input if it genuinely needs live
        # trigger data, same as any other node with missing config —
        # everything NOT dependent on the trigger still gets to run.
        if kind in ("chat_trigger", "document_upload_trigger", "schedule_trigger"):
            node_outputs[node_id] = None
            await broadcast(node_id, "completed", f"'{label}' skipped — only runs live (connect it from the toolbar, or trigger it for real, to exercise this node).", output=None)
            continue

        await broadcast(node_id, "running", f"Running '{label}'…")

        try:
            result = await _execute_node_with_retry(node_id, nodes, edges, node_outputs, chat_agent, executor, workflow_id=workflow_id)
            node_outputs[node_id] = result
            if kind == "logic" and result is None:
                gated_closed.update(logic_gated_chains.get(node_id, []))
            elif kind == "switch":
                gated_closed.update(_gate_switch_node(node_id, node, edges, node_outputs))
            await broadcast(node_id, "completed", f"'{label}' completed.", output=result)
        except Exception as e:
            friendly = humanize_exception(e, context=f"running '{label}'")
            logger.info(f"[Workflow {workflow_id} run {run_id}] Node '{node_id}' raw error: {e}")
            logger.error(f"[Workflow {workflow_id} run {run_id}] Node '{node_id}' failed: {friendly}")
            await broadcast(node_id, "failed", friendly)
            await manager.broadcast_json({
                "type": "workflow_run_failed",
                "workflow_id": workflow_id,
                "run_id": run_id,
                "message": f"'{label}' failed: {friendly}",
            })
            await anyio.to_thread.run_sync(_record_run_finish, run_id, "failed", node_outputs, node_id, friendly)
            return

    await manager.broadcast_json({"type": "workflow_run_complete", "workflow_id": workflow_id, "run_id": run_id, "outputs": node_outputs})
    await anyio.to_thread.run_sync(_record_run_finish, run_id, "completed", node_outputs)


def _find_tool_schema(tool_name: str) -> Optional[Dict]:
    for t in mcp_registry.list_all_tools() + ChatAgent.list_local_tools_for_palette():
        if t["name"] == tool_name:
            return t.get("inputSchema")
    return None
