import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  MarkerType,
  type Node,
  type Edge,
  type Connection,
  type NodeChange,
  type EdgeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Workflow as WorkflowIcon, Plus, Play, Save, Trash2, ArrowLeft, Loader2,
  CheckCircle2, XCircle, Sparkles, Wrench, Brain, Plug, Repeat,
  Database as DatabaseIcon, Search, X, FileText, Scissors,
  MessageCircle, ScanText, AudioLines, Globe, FolderOpen, FileDown,
  FileSpreadsheet, Presentation, FileType2, Link2,
  Layers, Upload,
  Boxes, FileOutput, MessageSquareText, GitBranch, ArrowDownUp,
  History as HistoryIcon, Clock, AlertTriangle, Send,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useSocket } from '../hooks/useSocket';
import AegisDatabaseBrowser from './AegisDatabaseBrowser';
import { ServiceLogo, getServiceIcon } from '@/lib/serviceIcons';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface ToolDef {
  name: string;
  description?: string;
  inputSchema?: { properties?: Record<string, any>; required?: string[] };
  server?: string | null; // owning MCP server name, or null for a local tool
}

interface ModelDef {
  id: number;
  name: string;
  display_name?: string;
  status: string;
  // Mirrors GET /api/hub/downloaded — true + "downloaded" together mean
  // this model can actually see an image (its vision-tower/mmproj file is
  // present, not just that it's architecturally a vision model). Used by
  // an "llm" node's config panel to surface when picking this model
  // enables reading an image an upstream document_upload_trigger fed in
  // (see NodeData.includeImages and engine.py's _attach_workflow_vision_image).
  is_vision?: boolean;
  mmproj_status?: string | null;
}

interface InstalledDB {
  id: number;
  name: string;
  engine_id: string;
  category: 'relational' | 'vector';
  status: 'installing' | 'ready' | 'failed';
  config?: { embedding_dim?: number; distance?: string };
  is_builtin?: boolean;
}

interface EmbeddingModelDef {
  id: number;
  model_id: string;
  display_name: string;
  dim: number;
  status: 'downloading' | 'downloaded' | 'failed';
}

interface RerankerModelDef {
  model_id: string;
  display_name: string;
  status: 'downloading' | 'downloaded' | 'failed';
}

interface ExtractionEngineDef {
  format: string;
  engine_id: string;
  name: string;
  description: string;
  default: boolean;
}

interface ChunkingStrategyDef {
  id: string;
  name: string;
  description: string;
  default: boolean;
}

// Mirrors GET /api/chat/sessions — every existing conversation, for the
// picker on chat_trigger/document_upload_trigger/send_to_chat's
// conversationId field (see NodeData.conversationId's docstring).
interface ChatSessionDef {
  id: string;
  preview: string;
  message_count: number;
  created_at: string | null;
}

// A connected MCP tool an Extract node can pick as a custom extractor —
// how a user integrates an extraction tool Aegis doesn't bundle itself,
// without Aegis running arbitrary third-party code (see Connectors). Not
// scoped to one format (see list_mcp_candidates' own docstring), so the
// same list is offered under every format in the Extract node's picker.
interface McpExtractionToolDef {
  engine_id: string;
  name: string;
  description: string;
  server: string;
  tool: string;
}

interface WorkflowSummary {
  id: number;
  name: string;
  updated_at?: string;
  created_at?: string;
  is_chat_handler?: boolean;
  is_ingestion_handler?: boolean;
  // Non-null only for a seeded row (currently just the default pipeline —
  // see app.core.workflows.seed.SEED_WORKFLOW_KEY). The backend already
  // refuses to delete one of these; this is just so the list card doesn't
  // even offer a delete button that would fail.
  seed_key?: string | null;
  // The list endpoint returns the full saved graph (same _serialize as a
  // single GET) — used here only to show a step count and a small
  // node-kind icon strip per card, no separate per-workflow fetch needed.
  graph?: { nodes?: { data?: NodeData }[] };
}

// "2h ago" / "3d ago" style relative timestamp for a workflow card — falls
// back to a plain date once it's old enough that "Nd ago" stops being the
// more useful reading (a week is the same cutoff Aegis's chat history list
// already uses elsewhere in the sidebar).
function relativeTime(iso?: string): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 5) return 'just now';
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return new Date(iso).toLocaleDateString();
}

// Mirrors app.api.workflows's _serialize_run — status/timing only, no
// per-node outputs (fetched separately per run, see WorkflowRunDetail).
interface WorkflowRunSummary {
  id: string;
  trigger: 'manual' | 'chat' | 'ingestion';
  status: 'running' | 'completed' | 'failed';
  failed_node_id?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

interface WorkflowRunDetail extends WorkflowRunSummary {
  node_outputs: Record<string, any>;
}

type NodeStatus = 'idle' | 'running' | 'completed' | 'failed';

// Matches the node kinds app.core.workflows.engine actually knows how to run
// (engine.py's module docstring) — a small, generic, n8n-style set:
// configure a plain node differently per use rather than reaching for a new
// dedicated kind, unless a task is genuinely mechanical/unique enough to
// need one (Extract, Chunk — real distinct operations, not judgment calls).
// "tool" covers both an MCP node (data.server set) and a local tool node
// (data.server null) — the engine treats them identically, only the
// palette/config panel distinguish them for the user. "llm" is a pure
// reasoning step — freeform text by default, or a structured JSON judgment
// call (needs_search, is_export, or anything else) when data.outputFields
// is set — see PromptNodeFields/its Output-format section below. "logic" is
// a generic gate (n8n's "IF" node) — see its own config panel. "chat_trigger"
// is the "On chat message" entry point — see app.api.workflows's
// /set-chat-handler and engine.run_chat_workflow; it never runs through a
// manual "Run" click, only when this workflow is connected as the live chat
// handler. "chat_reply" and "export_document" only run in that same
// chat-connected mode: "chat_reply" is the ONE dedicated terminal node that
// actually generates and streams the reply (gathering whatever's wired into
// it — a plain "llm" node's structured output, a "vector" search's results,
// anything else) — a plain "llm" node elsewhere in the graph is NOT this,
// it's the generic building block any other reasoning step uses.
// "database" targets an installed InstalledDatabase row (data.databaseId)
// — either one installed from the Marketplace's Databases category (a SQL
// query, data.query/staticInputs) or Aegis's OWN SQLite data (auto-
// registered as a normal-looking, ⭐-marked row, same as the vector node's
// bundled store below — see app.core.aegis_db_browser for the safety
// model): data.table/operation/limit instead, same allowlist-only browser
// the "Browse tables…" button opens. Which UI this node's config panel
// shows is decided by the SELECTED row's is_builtin flag, not a separate
// hardcoded node kind.
// "document_upload_trigger" is the ingestion pipeline's own entry point —
// mirrors "chat_trigger" exactly but fires on a document upload instead of
// a chat message (app.api.workflows's /set-ingestion-handler and
// engine.run_ingestion_workflow); also never runs through a manual "Run".
// "schedule_trigger" is a third entry point, for a chain nothing live
// kicks off — it fires on its own timer (data.intervalMinutes, set by the
// user on the node itself) instead of waiting for a chat message or an
// upload. Runs in the background (app.core.scheduler's SchedulerDaemon →
// engine.run_schedule_workflow), never through a manual "Run" either — the
// canonical use is a poll-and-check chain: this trigger -> an "mcp"/"tool"
// node like gmail_list_messages -> a "logic" node with
// operator=="changed_since_last_run" (only lets the rest of the chain run
// when the fetched value is actually different from last time) -> "llm" to
// summarize -> "send_to_chat" to deliver it. "send_to_chat" is the
// dedicated delivery node for exactly that last step — unlike "chat_reply"
// (which only works inside a live chat_trigger turn, replying into the
// SAME conversation the user just typed into), "send_to_chat" works from
// a schedule_trigger chain with no such conversation to reply into: it
// writes a real message into one dedicated conversation per workflow and
// notifies the user, wherever they are right now.
type NodeKind =
  | 'tool' | 'llm' | 'logic' | 'loop' | 'database' | 'vector' | 'reranker' | 'embedding' | 'extract' | 'chunk' | 'chat_trigger'
  | 'document_upload_trigger' | 'schedule_trigger'
  | 'chat_reply' | 'export_document' | 'send_to_chat';

interface NodeData {
  label: string;
  kind: NodeKind;
  // "tool" kind (MCP node or local tool node)
  toolName?: string;
  server?: string | null;
  isAi: boolean;
  instruction?: string;
  staticInputs?: Record<string, string>;
  // "llm" kind — a pure reasoning step bound to one explicitly-picked
  // downloaded model, no tool schema involved. Freeform text by default;
  // set outputFields to grammar-constrain the call to a JSON shape and get
  // back a parsed object instead of a string (e.g. a "needs search?"
  // judgment call) — see app.core.workflows.engine's
  // _build_structured_output_grammar/_run_ai_reasoning_node. temperature/
  // maxTokens default to 0.0/1024 when unset (deterministic, one judgment
  // call's worth of output) — not hardcoded, just a sane default for the
  // common case.
  modelName?: string;
  outputFields?: { name: string; type: 'boolean' | 'string' | 'number' | 'array'; description?: string }[];
  temperature?: number;
  maxTokens?: number;
  // "logic" kind — a generic gate (n8n's "IF" node). field names a key to
  // read off the upstream value (blank uses the upstream value directly);
  // operator/value decide whether the condition holds. Passes the
  // upstream value through unchanged when true, or skips everything
  // downstream of it when false — app.core.workflows.engine._run_logic_node.
  field?: string;
  // "changed_since_last_run" compares against the value this SAME node saw
  // last time (persisted server-side, keyed by this node), not anything in
  // the current run — see app.core.workflows.engine._run_logic_node. Only
  // meaningful inside a saved workflow (needs a workflow_id to key state
  // by), which is always true once it's actually connected/scheduled.
  operator?: 'is_true' | 'is_false' | 'equals' | 'not_equals' | 'contains' | 'matches_regex' | 'changed_since_last_run';
  value?: string;
  // "loop" kind — iterates the chain of nodes wired directly after it once
  // per item of this upstream field.
  itemsField?: string;
  // "schedule_trigger" kind — how often this fires, in minutes, set by the
  // user on the node itself (no fixed default baked into the backend —
  // app.core.scheduler.SchedulerDaemon.check_and_run_workflow_triggers
  // reads this directly off the node).
  intervalMinutes?: number;
  // "chat_trigger"/"document_upload_trigger": which conversation this
  // workflow handles. Blank = the one GLOBAL handler (every conversation
  // with no more specific match) — the only option before this existed.
  // Set = scoped to exactly that conversation; app.api.workflows's
  // /set-chat-handler and /set-ingestion-handler read this off the
  // trigger node at connect time (see _trigger_conversation_id) and
  // persist it onto the Workflow row, so app.db.crud
  // .get_active_chat_workflow/get_active_ingestion_workflow's
  // scoped-then-global lookup doesn't need to re-parse the graph on every
  // message/upload. "send_to_chat": which conversation this delivers
  // into — blank uses the dedicated per-workflow thread
  // ("workflow_{id}_auto") it's always used; set targets any real
  // conversation directly (e.g. your main chat). All three share one
  // picker UI (NodeConfigPanel's conversationField) populated from
  // GET /api/chat/sessions.
  conversationId?: string;
  // "document_upload_trigger" only — off (default) means EXACTLY today's
  // behavior: an image upload never reaches this trigger at all, handled
  // instead by the vision-at-chat-send-time path (or rejected if no
  // vision model is active) — see app.api.documents's upload endpoint.
  // On, an image upload fires this trigger like any other file
  // ({file_path, document_id, filename, file_type}, file_type one of
  // png/jpg/jpeg) — nothing downstream is auto-routed for it: Extract
  // only understands pdf/docx/pptx/xlsx/text, so an image reaching it
  // fails. Branch on file_type with a Logic node (the autocomplete
  // suggests it) before wiring an image toward an "llm" node with a
  // vision-capable model picked — that node reads the image as real
  // vision input (app.core.workflows.engine._attach_workflow_vision_image),
  // independent of whatever model happens to be active for chat.
  includeImages?: boolean;
  // "database" and "vector" kinds — an installed database (see
  // app.db.models.InstalledDatabase), referenced by id rather than a raw
  // file path so any engine in the catalog works the same way. For
  // "database", the SELECTED row's is_builtin flag (not a separate node
  // kind) decides which fields below apply: a Marketplace-installed
  // relational database uses query/staticInputs; Aegis's own built-in row
  // uses table/operation/limit instead (app.core.aegis_db_browser) — same
  // `operation` field "vector" reuses below, each kind/case interpreting
  // it with its own literal set (matching how e.g. `instruction` is
  // already reused across several kinds).
  databaseId?: number;
  query?: string;                          // "database" kind, non-builtin row
  table?: string;                          // "database" kind, builtin row — an EXISTING table's name for list/insert/update/delete, or the NEW table's name to create when operation is "create_table"
  limit?: number;                          // "database" kind, builtin row + operation "list"
  operation?: 'upsert' | 'search' | 'list' | 'insert' | 'update' | 'delete' | 'create_table' | 'query';
  // "database" kind, builtin row, operation "create_table" only — columns
  // for the new table (see app.core.aegis_db_browser.create_table). Always
  // gets its own auto-increment "id" primary key, not listed here.
  newTableColumns?: { name: string; type: 'text' | 'integer' | 'real' | 'boolean'; nullable: boolean }[];
  embeddingModel?: string;                 // "vector" kind — a Marketplace-downloaded model id, or unset for Aegis's built-in model
  topK?: number;                           // "vector" and "reranker" kinds
  // "vector" kind, search only, bundled Aegis store only — a Marketplace-
  // installed store always does plain dense search regardless of this
  // (see app.core.rag.processor.hybrid_search's mode param). Reranking is
  // never done inside this node — wire a "reranker" node downstream.
  searchMode?: 'hybrid' | 'semantic' | 'bm25';
  // "reranker" kind — re-scores an upstream "vector" search's candidates
  // with a cross-encoder. rerankerModel is a Marketplace-downloaded model
  // id, or unset for Aegis's own bundled default
  // (app.core.workflows.engine._run_reranker_node).
  rerankerModel?: string;
  // "extract" kind — pulls text out of a document file. extractorFormat is
  // a cosmetic-only hint (which palette row placed this node) — the engine
  // always auto-detects the real extractor from the file's own extension
  // (app.core.workflows.engine's _run_extract_node), so this never changes
  // runtime behavior, only which icon/label the node shows.
  filePath?: string;
  extractorFormat?: 'pdf' | 'docx' | 'pptx' | 'xlsx' | 'text';
  // Which engine from app.core.extraction_engines' registry to use — see
  // the Marketplace's Document Extraction section. Unset = that format's
  // default (byte-identical to the app's own document-upload extractor).
  // enginePerFormat is the one actually used going forward — a map since a
  // node fed by an upstream trigger doesn't know its format ahead of time
  // and may need a choice for more than one; `engine` (single, unscoped)
  // is kept only for a node built before enginePerFormat existed.
  engine?: string;
  enginePerFormat?: Record<string, string>;
  // "chunk" kind — splits upstream text into overlapping pieces.
  // strategy picks which algorithm from app.core.chunking_engines' registry
  // runs (recursive/paragraph/sentence/fixed) — unset uses the recursive
  // default, the same one the app's own document uploads use.
  chunkSize?: number;
  overlap?: number;
  strategy?: string;
  status?: NodeStatus;
  // Derived at render time from the current edges (see decoratedNodes in
  // WorkflowsView) — never set by hand, never persisted. True when at
  // least one unmapped incoming edge's source produces a shape this node's
  // kind doesn't expect (see outputPortType/inputPortTypes).
  hasInputWarning?: boolean;
  inputWarningReason?: string;
  [key: string]: unknown;
}

// Every distinct tool/format gets its own icon rather than sharing one
// generic Wrench for everything local — used both for a placed canvas node
// (nodeIcon below) and for each row in the tool palette.
function toolIconByName(name?: string, server?: string | null) {
  const cls = "w-3 h-3 flex-shrink-0";
  if (name === 'web_scrape' || name === 'extract_webpage_text' || name?.startsWith('browser_')) {
    return <Globe className={`${cls} text-aegis-primary-light`} />;
  }
  if (name === 'transcribe_media') return <AudioLines className={`${cls} text-aegis-primary-light`} />;
  if (name === 'extract_image_text') return <ScanText className={`${cls} text-aegis-primary-light`} />;
  if (name === 'export_file') return <FileDown className={`${cls} text-aegis-text-muted`} />;
  if (name && ['search_local_files', 'list_folder', 'read_file', 'write_file', 'copy_file', 'move_file', 'delete_file'].includes(name)) {
    return <FolderOpen className={`${cls} text-aegis-text-muted`} />;
  }
  if (server) {
    // The server's own real brand icon (serviceIcons.tsx, keyed by the
    // same name the backend registers it under) — a generic Plug for
    // every MCP-connected tool regardless of which server it's from made
    // every MCP node on the canvas look identical; this makes a Slack
    // node and a Postgres node visually distinct at a glance, same as the
    // palette row that placed it. Falls back to a generic icon on its own
    // (getServiceIcon's DEFAULT_ICON) for a custom server with no known
    // brand mark — never a broken/missing icon.
    const { Icon, color } = getServiceIcon(server);
    return <Icon size={12} color={color} className="flex-shrink-0" />;
  }
  return <Wrench className={`${cls} text-aegis-text-muted`} />;
}

function extractFormatIcon(format?: string) {
  const cls = "w-3 h-3 text-aegis-primary-light flex-shrink-0";
  if (format === 'docx') return <FileType2 className={cls} />;
  if (format === 'pptx') return <Presentation className={cls} />;
  if (format === 'xlsx') return <FileSpreadsheet className={cls} />;
  return <FileText className={cls} />;
}

function nodeIcon(data: NodeData) {
  if (data.kind === 'chat_trigger') return <MessageCircle className="w-3 h-3 text-aegis-success flex-shrink-0" />;
  if (data.kind === 'document_upload_trigger') return <Upload className="w-3 h-3 text-aegis-success flex-shrink-0" />;
  if (data.kind === 'schedule_trigger') return <Clock className="w-3 h-3 text-aegis-success flex-shrink-0" />;
  if (data.kind === 'send_to_chat') return <Send className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  // "llm" is a real model call under the hood regardless of what it's
  // configured to do (freeform text or a structured judgment call) —
  // Brain icon, same as "chat_reply" (also a model call, just the
  // dedicated one that sends the result) — their label text and position
  // in the graph tell them apart, not the icon.
  if (data.kind === 'llm') return <Brain className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'chat_reply') return <MessageSquareText className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'logic') return <GitBranch className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'export_document') return <FileOutput className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'loop') return <Repeat className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'database') return <DatabaseIcon className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'embedding') return <Layers className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'vector') return <Boxes className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'reranker') return <ArrowDownUp className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'extract') return extractFormatIcon(data.extractorFormat);
  if (data.kind === 'chunk') return <Scissors className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.isAi) return <Sparkles className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  return toolIconByName(data.toolName, data.server);
}

function nodeSubtitle(data: NodeData): string {
  switch (data.kind) {
    case 'chat_trigger':
      return 'Starts when you send a chat message';
    case 'document_upload_trigger':
      return 'Starts when you upload a document';
    case 'schedule_trigger':
      return data.intervalMinutes ? `Every ${data.intervalMinutes} min` : 'Set how often to check';
    case 'send_to_chat':
      return 'Posts to a dedicated chat thread for this workflow';
    case 'export_document':
      return 'Turns the reply into a file, if requested';
    case 'llm':
      return data.outputFields?.length
        ? `Structured: ${data.outputFields.map(f => f.name).join(', ')}`
        : (data.modelName || 'No model selected');
    case 'chat_reply':
      return 'Sends whatever the upstream "llm" node generates';
    case 'logic':
      if (data.operator === 'changed_since_last_run') return 'Only continues when this changes';
      return data.field
        ? `${data.field} ${data.operator || 'is_true'}${data.value ? ` "${data.value}"` : ''}`
        : 'Set a field and condition';
    case 'loop':
      return data.itemsField ? `for each "${data.itemsField}"` : 'Set the field to loop over';
    case 'database':
      return data.table ? `${data.operation || 'list'} · ${data.table}` : (data.query ? 'Query configured' : 'No database selected');
    case 'embedding':
      return data.embeddingModel ? data.embeddingModel : 'BAAI/bge-base-en-v1.5 (768d, bundled)';
    case 'vector':
      return data.databaseId ? `${data.operation === 'upsert' ? 'Store' : 'Search'} vectors` : 'No vector store selected';
    case 'reranker':
      return `Rerank top ${data.topK ?? 5} with ${data.rerankerModel || 'Aegis\'s bundled cross-encoder'}`;
    case 'extract':
      return data.filePath || 'No file path set — or wired in from upstream';
    case 'chunk':
      return `${data.chunkSize || 300} words, ${data.overlap || 50} overlap`;
    default:
      return data.toolName || (data.isAi ? 'AI step — no tool' : 'No tool selected');
  }
}

// ── Real-time connection typing ─────────────────────────────────────────────
// Aegis has no formal per-field schema between nodes, but every node kind's
// *whole-output* shape is already fixed by what the engine actually returns
// (app.core.workflows.engine) — this mirrors that, just enough to catch the
// same handful of "wrong node wired in" mistakes the engine already raises
// WorkflowError for at Run time (a Vector node needing an Embedding node's
// output, a Chunk node needing plain text, ...), but flagged the moment the
// edge is drawn instead of after a full Run. Deliberately coarse — 'any'
// wins ties rather than risk false-positive warnings, and any edge with an
// explicit field mapping (EdgeConfigPanel's outputField/inputField) is
// skipped entirely, since picking one named field out of a structured
// output is a case this shape-only check has no way to verify either way.
type PortType = 'text' | 'text[]' | 'record' | 'records' | 'none' | 'any';

const PORT_TYPE_LABEL: Record<PortType, string> = {
  text: 'text',
  'text[]': 'a list of text chunks',
  record: 'a structured object (e.g. an Embedding node\'s output)',
  records: 'a list of records',
  none: 'nothing',
  any: 'anything',
};

function outputPortType(data: NodeData): PortType {
  switch (data.kind) {
    case 'chat_trigger':
    case 'document_upload_trigger':
    case 'schedule_trigger':
    case 'embedding':
      return 'record';
    case 'llm':
      // Grammar-constrained (outputFields set) returns a parsed object;
      // otherwise it's a freeform string — see NodeData.outputFields' docstring.
      return data.outputFields?.length ? 'record' : 'text';
    case 'database':
    case 'vector':
    case 'reranker':
      return 'records';
    case 'extract':
      return 'text';
    case 'chunk':
      return 'text[]';
    case 'export_document':
      // _run_export_document_node returns the (possibly export-augmented)
      // reply text — a real string, not a dead end; nothing happens to be
      // wired after it in practice, but that's a graph shape, not a rule.
      return 'text';
    case 'send_to_chat':
      // _run_send_to_chat_node returns the exact text it delivered — a
      // real string, same reasoning as export_document above.
      return 'text';
    case 'chat_reply':
    case 'logic':
    case 'loop':
      // Pass-through nodes — their real output type is whatever's upstream
      // of THEM, not a fixed shape (chat_reply carries its upstream "llm"
      // node's result forward unchanged — see engine.py's `elif kind ==
      // "chat_reply":` branch — so it's exactly as dynamic as that "llm"
      // node's own output, same reasoning as logic/loop below). 'any'
      // rather than tracing the chain back further, matching this
      // feature's bias toward not false-flagging.
      return 'any';
    default: // "tool"/"mcp" — StdioMCPClient/StreamableHTTPMCPClient.call_tool
      // always flattens a tool's result to a string (see http_client.py/
      // stdio_client.py), so this is accurate, not a guess — except an
      // AI-driven tool step's arguments (and by extension what it might
      // effectively represent downstream) are too open-ended to pin down.
      return data.isAi ? 'any' : 'text';
  }
}

function inputPortTypes(data: NodeData): PortType[] {
  switch (data.kind) {
    case 'chat_trigger':
    case 'document_upload_trigger':
    case 'schedule_trigger':
      return ['none'];
    case 'vector':
      // Both "search" and "upsert" need an Embedding node's {texts, vectors}
      // output wired in — _run_vector_node raises WorkflowError without one.
      return ['record'];
    case 'reranker':
      return ['records'];
    case 'embedding':
      // _run_embedding_node accepts a plain string, a list of strings
      // (Chunk's output), or a dict carrying a "query" field.
      return ['text', 'text[]', 'record'];
    case 'extract':
      return ['text'];
    case 'chunk':
      return ['text'];
    case 'chat_reply':
      return ['text'];
    case 'send_to_chat':
      // _run_send_to_chat_node stringifies whatever it receives — plain
      // text (the common case, an "llm" summary) or a structured "record"
      // (json.dumps'd) — same reasoning as export_document below.
      return ['text', 'record'];
    case 'export_document':
      // Genuinely two different upstream shapes, both real (see
      // _run_export_document_node): the reply text from a "chat_reply"
      // node, AND — on a separate edge — an optional classification dict
      // (a structured "llm" node's {is_export, format, parts}) it looks
      // up by kind/key, not by "the" single upstream. Each incoming edge
      // is checked against this whole list rather than the pair being
      // matched positionally, so either shape on either edge is accepted.
      return ['text', 'record'];
    default: // tool/mcp, llm, logic, database, loop — all read a named
      // field via per-edge mapping or fold the whole upstream into
      // context/a prompt, so nothing about the upstream's shape is wrong.
      return ['any'];
  }
}

function typesCompatible(output: PortType, inputs: PortType[]): boolean {
  if (output === 'any' || inputs.includes('any')) return true;
  return inputs.includes(output);
}

function describeMismatch(output: PortType, inputs: PortType[]): string {
  const expected = inputs.map(t => PORT_TYPE_LABEL[t]).join(' or ');
  return `Expects ${expected}, but the node feeding it produces ${PORT_TYPE_LABEL[output]}.`;
}

function WorkflowNode({ data, selected }: { data: NodeData; selected: boolean }) {
  const statusColor =
    data.status === 'running' ? 'border-aegis-primary shadow-[0_0_0_3px_rgba(var(--aegis-primary-rgb,255,100,60),0.15)]' :
    data.status === 'completed' ? 'border-aegis-success' :
    data.status === 'failed' ? 'border-aegis-error' :
    // A live run's own status border takes priority over the static wiring
    // warning below it — once a run is happening/done, "was this ever
    // mis-wired" is stale information next to "did it actually just work."
    data.hasInputWarning ? 'border-aegis-error/60' :
    selected ? 'border-aegis-primary' : 'border-aegis-border';

  return (
    <div className={`min-w-[180px] rounded-lg border-2 ${statusColor} bg-aegis-raised px-3 py-2.5 shadow-sm transition-colors`}>
      <Handle type="target" position={Position.Top} className="!bg-aegis-primary !w-2.5 !h-2.5 !border-2 !border-aegis-raised" />
      <div className="flex items-center gap-1.5 mb-1">
        {nodeIcon(data)}
        <span className="text-xs font-bold text-aegis-text-primary truncate">{data.label || 'Untitled step'}</span>
        {data.status === 'running' && <Loader2 className="w-3 h-3 animate-spin text-aegis-primary ml-auto" />}
        {data.status === 'completed' && <CheckCircle2 className="w-3 h-3 text-aegis-success ml-auto" />}
        {data.status === 'failed' && <XCircle className="w-3 h-3 text-aegis-error ml-auto" />}
        {!data.status || data.status === 'idle' ? (
          data.hasInputWarning && (
            <span title={data.inputWarningReason} className="ml-auto flex-shrink-0">
              <AlertTriangle className="w-3 h-3 text-aegis-error" />
            </span>
          )
        ) : null}
      </div>
      <div className="text-[10px] text-aegis-text-muted truncate">{nodeSubtitle(data)}</div>
      <Handle type="source" position={Position.Bottom} className="!bg-aegis-primary !w-2.5 !h-2.5 !border-2 !border-aegis-raised" />
    </div>
  );
}

// One consistent row style for every palette entry — icon, bold title, a
// one-line muted description underneath (promoted from a hover-only tooltip
// to visible text) — matching n8n's own "what triggers this workflow?" node
// picker rather than a bare icon+label button.
function PaletteRow({ icon, title, description, onClick }: { icon: React.ReactNode; title: string; description?: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-start gap-2.5 px-2.5 py-2 rounded-lg hover:bg-aegis-overlay text-left transition-colors"
    >
      <div className="mt-0.5">{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold text-aegis-text-primary truncate">{title}</div>
        {description && <div className="text-[10px] text-aegis-text-muted leading-snug line-clamp-2">{description}</div>}
      </div>
    </button>
  );
}

const nodeTypes = { workflowNode: WorkflowNode };

let nodeIdCounter = 0;
const nextNodeId = () => `node_${Date.now()}_${nodeIdCounter++}`;

export default function WorkflowsView() {
  const { addMessageHandler } = useSocket();
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [listSearch, setListSearch] = useState('');
  // Deleting a workflow is more consequential than this app's other
  // one-click deletes (an installed model/database is trivially
  // re-added; a hand-wired workflow graph isn't) — but a native
  // confirm() blocks the whole renderer (and this browser automation
  // environment) until dismissed, so this is a plain in-card "click
  // again to confirm" toggle instead of a dialog. Only one card armed
  // at a time; clicking any other delete button (or its own X) disarms it.
  const [armedDeleteId, setArmedDeleteId] = useState<number | null>(null);
  const [tools, setTools] = useState<ToolDef[]>([]);
  const [models, setModels] = useState<ModelDef[]>([]);
  const [databases, setDatabases] = useState<InstalledDB[]>([]);
  const [embeddingModels, setEmbeddingModels] = useState<EmbeddingModelDef[]>([]);
  const [rerankerModels, setRerankerModels] = useState<RerankerModelDef[]>([]);
  const [extractionEngines, setExtractionEngines] = useState<ExtractionEngineDef[]>([]);
  const [mcpExtractionTools, setMcpExtractionTools] = useState<McpExtractionToolDef[]>([]);
  const [chunkingStrategies, setChunkingStrategies] = useState<ChunkingStrategyDef[]>([]);
  const [chatSessions, setChatSessions] = useState<ChatSessionDef[]>([]);
  const [aegisDbTables, setAegisDbTables] = useState<{ name: string; label: string; user_created: boolean }[]>([]);
  const [aegisDbBrowserOpen, setAegisDbBrowserOpen] = useState(false);
  const [activeId, setActiveId] = useState<number | 'new' | null>(null);
  const [name, setName] = useState('Untitled Workflow');
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [paletteSearch, setPaletteSearch] = useState('');
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [isChatHandler, setIsChatHandler] = useState(false);
  const [connectingChat, setConnectingChat] = useState(false);
  const [isIngestionHandler, setIsIngestionHandler] = useState(false);
  const [connectingIngestion, setConnectingIngestion] = useState(false);
  // True while editing the built-in seeded workflow (Workflow.seed_key on
  // the backend) — its steps can still be reconfigured or added to, but
  // not removed (see NodeConfigPanel's onDelete below and PUT
  // /api/workflows/{id}'s own seed_key check, which rejects this same
  // thing server-side too in case a stale/direct API call bypasses this).
  const [isSeeded, setIsSeeded] = useState(false);
  // The default pipeline's own node ids, captured once at load — a ref
  // (not state) since onNodesChange below reads it inside a useCallback
  // that must stay referentially stable, and it never needs to trigger a
  // re-render on its own. Guards React Flow's own default Backspace/Delete
  // key handling on a selected node, which bypasses NodeConfigPanel's
  // delete button (and its canDelete check) entirely.
  const seededNodeIdsRef = useRef<Set<string>>(new Set());
  const [historyOpen, setHistoryOpen] = useState(false);
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [selectedRun, setSelectedRun] = useState<WorkflowRunDetail | null>(null);
  const dbIdRef = useRef<number | null>(null);
  const canvasWrapperRef = useRef<HTMLDivElement>(null);

  const fetchWorkflows = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/workflows`);
      if (res.ok) setWorkflows((await res.json()).workflows || []);
    } catch (e) {}
  }, []);

  const fetchTools = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/workflows/tools`);
      if (res.ok) setTools((await res.json()).tools || []);
    } catch (e) {}
  }, []);

  const fetchModels = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/hub/downloaded`);
      if (res.ok) {
        const data = await res.json();
        setModels((data.models || []).filter((m: ModelDef) => m.status === 'downloaded'));
      }
    } catch (e) {}
  }, []);

  const fetchDatabases = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/databases`);
      if (res.ok) {
        const data = await res.json();
        setDatabases((data.databases || []).filter((d: InstalledDB) => d.status === 'ready'));
      }
    } catch (e) {}
  }, []);

  const fetchEmbeddingModels = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/embeddings`);
      if (res.ok) {
        const data = await res.json();
        setEmbeddingModels((data.models || []).filter((m: EmbeddingModelDef) => m.status === 'downloaded'));
      }
    } catch (e) {}
  }, []);

  const fetchRerankerModels = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/rerankers`);
      if (res.ok) {
        const data = await res.json();
        setRerankerModels((data.models || []).filter((m: RerankerModelDef) => m.status === 'downloaded'));
      }
    } catch (e) {}
  }, []);

  // Per-format extraction engines (app.core.extraction_engines, surfaced
  // via the Marketplace's Extraction-category catalog entries) — powers
  // the engine picker in an Extract node's config panel.
  const fetchExtractionEngines = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/tools`);
      if (res.ok) {
        const data = await res.json();
        setExtractionEngines(
          (data.tools || [])
            .filter((t: any) => t.category === 'Extraction' && t.format && t.engine_id)
            .map((t: any) => ({ format: t.format, engine_id: t.engine_id, name: t.name, description: t.description, default: !!t.default }))
        );
      }
    } catch (e) {}
  }, []);

  // Connected MCP tools an Extract node can pick as a custom extractor
  // (app.core.extraction_engines.list_mcp_candidates) — how a user
  // integrates an extraction tool Aegis doesn't bundle itself: connect it
  // from Connectors and it shows up here, no separate "install" step.
  const fetchMcpExtractionTools = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/workflows/extraction-mcp-tools`);
      if (res.ok) {
        const data = await res.json();
        setMcpExtractionTools(data.tools || []);
      }
    } catch (e) {}
  }, []);

  // Built-in chunking strategies (app.core.chunking_engines) — powers the
  // strategy picker in a Chunk node's config panel. Not a Marketplace-
  // installed capability like extraction engines, so its own small
  // endpoint rather than the Marketplace tools listing.
  const fetchChunkingStrategies = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/workflows/chunking-strategies`);
      if (res.ok) {
        const data = await res.json();
        setChunkingStrategies(data.strategies || []);
      }
    } catch (e) {}
  }, []);

  const fetchAegisDbTables = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables`);
      if (res.ok) {
        const data = await res.json();
        setAegisDbTables((data.tables || []).map((t: any) => ({ name: t.name, label: t.label, user_created: !!t.user_created })));
      }
    } catch (e) {}
  }, []);

  // Powers the conversation picker on chat_trigger/document_upload_trigger/
  // send_to_chat — see NodeData.conversationId's docstring.
  const fetchChatSessions = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/chat/sessions`);
      if (res.ok) setChatSessions(await res.json());
    } catch (e) {}
  }, []);

  useEffect(() => { fetchWorkflows(); fetchTools(); fetchModels(); fetchDatabases(); fetchEmbeddingModels(); fetchRerankerModels(); fetchExtractionEngines(); fetchMcpExtractionTools(); fetchChunkingStrategies(); fetchAegisDbTables(); fetchChatSessions(); }, [fetchWorkflows, fetchTools, fetchModels, fetchDatabases, fetchEmbeddingModels, fetchRerankerModels, fetchExtractionEngines, fetchMcpExtractionTools, fetchChunkingStrategies, fetchAegisDbTables, fetchChatSessions]);

  // Live per-node status while a run is in flight — same WebSocket
  // broadcast pattern MCPServersPanel/ModelHub already use.
  useEffect(() => {
    return addMessageHandler((payload: any) => {
      const { type, node_id, status, workflow_id } = payload;
      if (workflow_id !== dbIdRef.current) return;
      if (type === 'workflow_node_progress') {
        setNodes(prev => prev.map(n => n.id === node_id ? { ...n, data: { ...n.data, status } } : n));
      } else if (type === 'workflow_run_complete') {
        setRunning(false);
        toast.success('Workflow run complete.');
        if (historyOpen) fetchRuns();
      } else if (type === 'workflow_run_failed') {
        setRunning(false);
        toast.error(payload.message || 'Workflow run failed.', { duration: 8000 });
        if (historyOpen) fetchRuns();
      }
    });
  }, [addMessageHandler, historyOpen]);

  // Recent run history (app.core.workflows.engine writes a WorkflowRun row
  // on every manual/chat/ingestion run) — status/timing list only; a
  // specific run's node_outputs are fetched on demand (fetchRunDetail)
  // since they can be sizeable.
  const fetchRuns = useCallback(async () => {
    if (!dbIdRef.current) return;
    setLoadingRuns(true);
    try {
      const res = await fetch(`${API_BASE}/api/workflows/${dbIdRef.current}/runs`);
      if (res.ok) {
        const data = await res.json();
        setRuns(data.runs || []);
      }
    } catch (e) {
    } finally {
      setLoadingRuns(false);
    }
  }, []);

  const fetchRunDetail = async (runId: string) => {
    if (!dbIdRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/api/workflows/${dbIdRef.current}/runs/${runId}`);
      if (res.ok) setSelectedRun(await res.json());
    } catch (e) {
      toast.error('Could not load that run.');
    }
  };

  const openHistory = () => {
    setHistoryOpen(true);
    setSelectedRun(null);
    fetchRuns();
  };

  const openWorkflow = async (id: number) => {
    const res = await fetch(`${API_BASE}/api/workflows/${id}`);
    if (!res.ok) { toast.error('Could not load that workflow.'); return; }
    const w = await res.json();
    dbIdRef.current = w.id;
    setActiveId(w.id);
    setName(w.name);
    const loadedNodes = (w.graph.nodes || []).map((n: Node) => ({
      ...n,
      type: 'workflowNode',
      data: { kind: 'tool', isAi: false, ...n.data, status: 'idle' },
    }));
    setNodes(loadedNodes);
    setEdges(w.graph.edges || []);
    setSelectedNodeId(null);
    setIsChatHandler(!!w.is_chat_handler);
    setIsIngestionHandler(!!w.is_ingestion_handler);
    setIsSeeded(!!w.seed_key);
    seededNodeIdsRef.current = w.seed_key ? new Set(loadedNodes.map((n: Node) => n.id)) : new Set();
  };

  const deleteWorkflow = async (w: WorkflowSummary) => {
    try {
      const res = await fetch(`${API_BASE}/api/workflows/${w.id}`, { method: 'DELETE' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || `Could not delete "${w.name}".`);
        return;
      }
      toast.success(`Deleted "${w.name}".`);
      fetchWorkflows();
    } catch (e) {
      toast.error('Could not reach the backend.');
    }
  };

  const startNew = () => {
    dbIdRef.current = null;
    setActiveId('new');
    setName('Untitled Workflow');
    setNodes([]);
    setEdges([]);
    setSelectedNodeId(null);
    setIsChatHandler(false);
    setIsIngestionHandler(false);
    setIsSeeded(false);
    seededNodeIdsRef.current = new Set();
  };

  const backToList = () => {
    setActiveId(null);
    fetchWorkflows();
  };

  const placeNode = (data: NodeData) => {
    const id = nextNodeId();
    setNodes(prev => [...prev, { id, type: 'workflowNode', position: { x: 80 + prev.length * 40, y: 80 + prev.length * 30 }, data }]);
    setPaletteOpen(false);
  };

  const addToolNode = (tool: ToolDef) => {
    placeNode({
      label: tool.name,
      kind: 'tool',
      toolName: tool.name,
      server: tool.server ?? null,
      isAi: false,
      instruction: '',
      staticInputs: {},
      status: 'idle',
    });
  };

  // One node kind covers every MCP server, same as Extract/Chunk/Database —
  // the palette picks WHICH SERVER (a real, unique brand icon per server via
  // ServiceLogo/serviceIcons.tsx, keyed by the server name the backend
  // already sends), the node's own config panel picks WHICH TOOL from that
  // server (scoped to just its own tools, not a flat list of every tool
  // from every connected server). toolName starts unset — the node isn't
  // runnable until a tool is chosen in config.
  const addMcpServerNode = (server: string) => {
    placeNode({
      label: server,
      kind: 'tool',
      toolName: undefined,
      server,
      isAi: false,
      instruction: '',
      staticInputs: {},
      status: 'idle',
    });
  };

  const addLlmNode = () => {
    placeNode({ label: 'LLM step', kind: 'llm', isAi: false, modelName: '', instruction: '', status: 'idle' });
  };

  const addLogicNode = () => {
    placeNode({ label: 'Logic', kind: 'logic', isAi: false, field: '', operator: 'is_true', value: '', status: 'idle' });
  };

  const addChatReplyNode = () => {
    placeNode({ label: 'Send Reply', kind: 'chat_reply', isAi: false, status: 'idle' });
  };

  const addLoopNode = () => {
    placeNode({ label: 'Loop', kind: 'loop', isAi: false, itemsField: '', status: 'idle' });
  };

  const addDatabaseNode = () => {
    placeNode({ label: 'Database', kind: 'database', isAi: false, query: '', staticInputs: {}, status: 'idle' });
  };

  const addVectorNode = () => {
    placeNode({ label: 'Vector store', kind: 'vector', isAi: false, operation: 'search', topK: 5, status: 'idle' });
  };

  const addRerankerNode = () => {
    placeNode({ label: 'Reranker', kind: 'reranker', isAi: false, topK: 5, status: 'idle' });
  };

  // A single node kind covers every format — the config panel below shows
  // one extractor picker per format (each backed by whatever the user has
  // downloaded from the Marketplace's Document Extraction category), and
  // the engine auto-detects which format it's actually looking at from
  // the file's own extension (engine.py's _run_extract_node) — no need for
  // a separate palette row (and separate node) per format.
  const addAutoExtractNode = () => {
    placeNode({
      label: 'Extract text', kind: 'extract', isAi: false,
      filePath: '', staticInputs: {}, status: 'idle',
    });
  };

  const addChunkNode = () => {
    placeNode({ label: 'Chunk text', kind: 'chunk', isAi: false, chunkSize: 300, overlap: 50, status: 'idle' });
  };

  const addChatTriggerNode = () => {
    placeNode({ label: 'On chat message', kind: 'chat_trigger', isAi: false, status: 'idle' });
  };

  const addIngestionTriggerNode = () => {
    placeNode({ label: 'On document upload', kind: 'document_upload_trigger', isAi: false, status: 'idle' });
  };

  const addScheduleTriggerNode = () => {
    placeNode({ label: 'On a schedule', kind: 'schedule_trigger', isAi: false, intervalMinutes: 5, status: 'idle' });
  };

  const addSendToChatNode = () => {
    placeNode({ label: 'Post to Chat', kind: 'send_to_chat', isAi: false, status: 'idle' });
  };

  const addExportDocumentNode = () => {
    placeNode({ label: 'Export Reply', kind: 'export_document', isAi: false, status: 'idle' });
  };

  const addEmbeddingNode = () => {
    placeNode({ label: 'Embedding', kind: 'embedding', isAi: false, status: 'idle' });
  };

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    // React Flow's own default Backspace/Delete key handling fires a
    // 'remove' change for whatever node is selected — bypasses
    // NodeConfigPanel's delete button (and its canDelete prop) entirely,
    // so the default pipeline's own nodes need blocking here too.
    const filtered = changes.filter(c => !(c.type === 'remove' && seededNodeIdsRef.current.has(c.id)));
    setNodes(nds => applyNodeChanges(filtered, nds));
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges(eds => applyEdgeChanges(changes, eds)), []);
  const onConnect = useCallback((conn: Connection) => setEdges(eds => addEdge({ ...conn, data: {}, markerEnd: { type: MarkerType.ArrowClosed } }, eds)), []);

  // Real-time connection typing (see outputPortType/inputPortTypes above) —
  // recomputed from the live nodes/edges on every render rather than stored
  // in state, so it never needs to be kept in sync by hand and never leaks
  // into what actually gets saved. "Warn, don't block" per the agreed
  // design: a mismatched edge still connects normally (so deliberate
  // per-field mapping never gets fought), it just renders flagged.
  const decoratedEdges = useMemo(() => {
    const nodeById = new Map(nodes.map(n => [n.id, n]));
    return edges.map(e => {
      const sourceNode = nodeById.get(e.source);
      const targetNode = nodeById.get(e.target);
      if (!sourceNode || !targetNode) return e;
      const edgeData = (e.data || {}) as { outputField?: string; inputField?: string };
      if (edgeData.outputField || edgeData.inputField) return e;

      const outType = outputPortType(sourceNode.data as NodeData);
      const inTypes = inputPortTypes(targetNode.data as NodeData);
      if (typesCompatible(outType, inTypes)) return e;

      return {
        ...e,
        style: { stroke: '#DC2626', strokeDasharray: '5 3' },
        label: 'type mismatch',
        labelStyle: { fill: '#DC2626', fontSize: 10, fontWeight: 600 },
        labelBgStyle: { fill: '#FFFFFF' },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#DC2626' },
      };
    });
  }, [nodes, edges]);

  const decoratedNodes = useMemo(() => {
    const warningByTarget = new Map<string, string>();
    for (const e of decoratedEdges) {
      if (e.label === 'type mismatch' && !warningByTarget.has(e.target)) {
        const nodeById = new Map(nodes.map(n => [n.id, n]));
        const sourceNode = nodeById.get(e.source);
        const targetNode = nodeById.get(e.target);
        if (sourceNode && targetNode) {
          warningByTarget.set(e.target, describeMismatch(
            outputPortType(sourceNode.data as NodeData),
            inputPortTypes(targetNode.data as NodeData),
          ));
        }
      }
    }
    if (warningByTarget.size === 0) return nodes;
    return nodes.map(n => warningByTarget.has(n.id)
      ? { ...n, data: { ...n.data, hasInputWarning: true, inputWarningReason: warningByTarget.get(n.id) } }
      : n);
  }, [nodes, decoratedEdges]);

  const saveWorkflow = async () => {
    setSaving(true);
    try {
      const graph = { nodes: nodes.map(({ id, type, position, data }) => ({ id, type, position, data })), edges };
      const body = JSON.stringify({ name, graph });
      const url = dbIdRef.current ? `${API_BASE}/api/workflows/${dbIdRef.current}` : `${API_BASE}/api/workflows`;
      const method = dbIdRef.current ? 'PUT' : 'POST';
      const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body });
      if (!res.ok) throw new Error('Save failed');
      const w = await res.json();
      dbIdRef.current = w.id;
      setActiveId(w.id);
      toast.success('Workflow saved.');
    } catch (e) {
      toast.error('Could not save the workflow.');
    } finally {
      setSaving(false);
    }
  };

  const runWorkflow = async () => {
    if (!dbIdRef.current) {
      toast.error('Save the workflow before running it.');
      return;
    }
    setNodes(prev => prev.map(n => ({ ...n, data: { ...n.data, status: 'idle' } })));
    setRunning(true);
    try {
      const res = await fetch(`${API_BASE}/api/workflows/${dbIdRef.current}/run`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        toast.error(data.detail || 'Could not start the run.');
        setRunning(false);
      }
    } catch (e) {
      toast.error('Could not reach the backend.');
      setRunning(false);
    }
  };

  // Mirrors app.api.workflows._validate_chat_handler_graph exactly: one
  // trigger, one Chat Reply node with exactly one "llm" node wired
  // directly into it (and nothing else wired after that llm node — model
  // config lives only on "llm" nodes, Chat Reply is a thin "send" marker
  // with none of its own), and exactly one final step overall.
  const hasChatTrigger = nodes.some(n => (n.data as NodeData).kind === 'chat_trigger');
  const chatReplyNode = nodes.find(n => (n.data as NodeData).kind === 'chat_reply');
  const chatReplyNodeCount = nodes.filter(n => (n.data as NodeData).kind === 'chat_reply').length;
  // The "llm" node(s) whose ONLY outgoing edge leads to the Chat Reply
  // node — exactly one is required to connect; used both for the
  // toolbar's readiness check and to tell the "llm" config panel whether
  // THIS particular node is the reply generator (streams live to the
  // user) or a plain judgment-call step elsewhere (one-shot, no stream).
  const replyGenerationIds = chatReplyNode
    ? nodes
        .filter(n => (n.data as NodeData).kind === 'llm')
        .filter(n => edges.some(e => e.source === n.id && e.target === chatReplyNode.id))
        .filter(n => edges.filter(e => e.source === n.id).every(e => e.target === chatReplyNode.id))
        .map(n => n.id)
    : [];
  const terminalNodeCount = nodes.filter(n => !edges.some(e => e.source === n.id)).length;
  const chatHandlerReady = hasChatTrigger && chatReplyNodeCount === 1 && replyGenerationIds.length === 1 && terminalNodeCount === 1;

  // Mirrors app.api.workflows._validate_ingestion_handler_graph: one
  // "On document upload" trigger and exactly one final step overall — no
  // LLM-node requirement (ingestion has no reply to generate).
  const hasIngestionTrigger = nodes.some(n => (n.data as NodeData).kind === 'document_upload_trigger');
  const ingestionHandlerReady = hasIngestionTrigger && terminalNodeCount === 1;

  const toggleIngestionHandler = async () => {
    if (!dbIdRef.current) {
      toast.error('Save the workflow before connecting it to document uploads.');
      return;
    }
    setConnectingIngestion(true);
    try {
      const path = isIngestionHandler ? 'unset-ingestion-handler' : 'set-ingestion-handler';
      const res = await fetch(`${API_BASE}/api/workflows/${dbIdRef.current}/${path}`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(data.detail || 'Could not update the ingestion connection.');
        return;
      }
      setIsIngestionHandler(!isIngestionHandler);
      toast.success(isIngestionHandler ? 'Disconnected — uploads are back to their default pipeline.' : 'Connected — this workflow now handles document uploads.');
    } catch (e) {
      toast.error('Could not reach the backend.');
    } finally {
      setConnectingIngestion(false);
    }
  };

  const toggleChatHandler = async () => {
    if (!dbIdRef.current) {
      toast.error('Save the workflow before connecting it to chat.');
      return;
    }
    setConnectingChat(true);
    try {
      const path = isChatHandler ? 'unset-chat-handler' : 'set-chat-handler';
      const res = await fetch(`${API_BASE}/api/workflows/${dbIdRef.current}/${path}`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(data.detail || 'Could not update the chat connection.');
        return;
      }
      setIsChatHandler(!isChatHandler);
      toast.success(isChatHandler ? 'Disconnected — chat is back to its default setup.' : 'Connected — this workflow now handles your chat messages.');
    } catch (e) {
      toast.error('Could not reach the backend.');
    } finally {
      setConnectingChat(false);
    }
  };

  const selectedNode = nodes.find(n => n.id === selectedNodeId);

  const updateSelectedNode = (patch: Partial<NodeData>) => {
    if (!selectedNodeId) return;
    setNodes(prev => prev.map(n => n.id === selectedNodeId ? { ...n, data: { ...n.data, ...patch } } : n));
  };

  // Field-to-field mapping (edge.data.outputField/inputField) — read by
  // app.core.workflows.engine's _resolve_named_arguments: which single
  // value moves across one connection, and what the target node should
  // call it. Already the real mechanism a tool/database/extract node's
  // staticInputs+upstream resolution has used since the start; this is
  // just the first UI to actually set it from the canvas instead of only
  // ever being baked in at seed time. Edited from the node's own Input
  // column (n8n-style — see the node modal below), not a separate click
  // on the connecting line itself.
  const updateEdgeMapping = (edgeId: string, patch: { outputField?: string; inputField?: string }) => {
    setEdges(prev => prev.map(e => e.id === edgeId ? { ...e, data: { ...(e.data || {}), ...patch } } : e));
  };

  const selectedTool = tools.find(t => t.name === (selectedNode?.data as NodeData | undefined)?.toolName);

  // Palette grouping — MCP tools drilled down per connected server (so an
  // "MCP node" comes up with exactly that server's own tools, per the
  // product ask), local tools kept as their own flat section, everything
  // filtered by the search box the same way n8n's node panel filters by
  // typing.
  const search = paletteSearch.trim().toLowerCase();
  const mcpToolsByServer = useMemo(() => {
    const map: Record<string, ToolDef[]> = {};
    for (const t of tools) {
      if (!t.server) continue;
      if (search && !t.name.toLowerCase().includes(search) && !t.server.toLowerCase().includes(search)) continue;
      (map[t.server] ||= []).push(t);
    }
    return map;
  }, [tools, search]);
  const localTools = useMemo(
    () => tools.filter(t => !t.server && (!search || t.name.toLowerCase().includes(search))),
    [tools, search]
  );
  const showTrigger = !search || 'chat'.includes(search) || 'trigger'.includes(search) || 'message'.includes(search);
  const showIngestionTrigger = !search || 'upload'.includes(search) || 'trigger'.includes(search) || 'document'.includes(search) || 'ingest'.includes(search);
  const showScheduleTrigger = !search || 'schedule'.includes(search) || 'trigger'.includes(search) || 'timer'.includes(search) || 'poll'.includes(search) || 'cron'.includes(search);
  const showSendToChat = !search || 'chat'.includes(search) || 'send'.includes(search) || 'post'.includes(search) || 'notify'.includes(search);
  const showChatContext = !search || 'chat'.includes(search) || 'reply'.includes(search) || 'export'.includes(search);
  const showLogic = !search || 'logic'.includes(search) || 'if'.includes(search) || 'gate'.includes(search) || 'condition'.includes(search);
  const showLlm = !search || 'llm'.includes(search) || 'model'.includes(search);
  const showLoop = !search || 'loop'.includes(search);
  const showDatabase = !search || 'database'.includes(search);
  const showVector = !search || 'vector'.includes(search) || 'search'.includes(search);
  const showReranker = !search || 'rerank'.includes(search) || 'reranker'.includes(search);
  const showEmbedding = !search || 'embedding'.includes(search) || 'vector'.includes(search);
  const showExtract = !search || 'extract'.includes(search) || 'document'.includes(search) || 'text'.includes(search);
  const showChunk = !search || 'chunk'.includes(search);

  // ── List view ──────────────────────────────────────────────────────────
  if (activeId === null) {
    const filteredWorkflows = listSearch.trim()
      ? workflows.filter(w => w.name.toLowerCase().includes(listSearch.trim().toLowerCase()))
      : workflows;

    return (
      <div className="flex-1 overflow-y-auto bg-aegis-base">
        <div className="px-8 pt-8 pb-5">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div>
              <div className="flex items-center gap-3 mb-1">
                <WorkflowIcon className="w-6 h-6 text-aegis-primary" />
                <h1 className="text-2xl font-bold text-aegis-text-primary">Workflows</h1>
              </div>
              <p className="text-sm text-aegis-text-secondary">Design exactly which tool runs at each step — nothing for the model to guess.</p>
            </div>
            <button
              onClick={startNew}
              className="flex-shrink-0 flex items-center gap-1.5 px-3.5 py-2 bg-aegis-primary text-white text-[13px] font-semibold rounded-lg hover:bg-aegis-primary-dark transition-colors"
            >
              <Plus className="w-4 h-4" /> New Workflow
            </button>
          </div>
        </div>

        <div className="px-8 pb-8">
          {workflows.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center py-24 px-6 bg-aegis-raised rounded-xl border border-dashed border-aegis-border">
              <div className="w-14 h-14 rounded-2xl bg-aegis-overlay flex items-center justify-center mb-4">
                <WorkflowIcon className="w-6 h-6 text-aegis-primary" />
              </div>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-1">No workflows yet</h2>
              <p className="text-[13px] text-aegis-text-muted max-w-sm mb-5">
                Build a step-by-step pipeline — connect chat, document uploads, tools, and models exactly the way you want them to run.
              </p>
              <button
                onClick={startNew}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-aegis-primary text-white text-[13px] font-semibold rounded-lg hover:bg-aegis-primary-dark transition-colors"
              >
                <Plus className="w-4 h-4" /> Create your first workflow
              </button>
            </div>
          ) : (
            <>
              {workflows.length > 5 && (
                <div className="relative mb-4 max-w-sm">
                  <Search className="w-3.5 h-3.5 text-aegis-text-muted absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    value={listSearch}
                    onChange={e => setListSearch(e.target.value)}
                    placeholder="Search workflows…"
                    className="w-full bg-aegis-raised border border-aegis-border rounded-lg pl-8 pr-3 py-2 text-[13px] text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  />
                </div>
              )}

              {filteredWorkflows.length === 0 ? (
                <p className="text-[13px] text-aegis-text-muted">No workflows match "{listSearch}".</p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {filteredWorkflows.map(w => {
                    const nodeCount = w.graph?.nodes?.length ?? 0;
                    const kindsUsed = Array.from(new Set((w.graph?.nodes || []).map(n => n.data?.kind).filter(Boolean))).slice(0, 6) as string[];
                    return (
                      <div
                        key={w.id}
                        role="button"
                        tabIndex={0}
                        onClick={() => openWorkflow(w.id)}
                        onKeyDown={e => { if (e.key === 'Enter') openWorkflow(w.id); }}
                        className="text-left bg-aegis-raised rounded-xl border border-aegis-border shadow-sm px-4 py-3.5 hover:border-aegis-primary/40 hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 cursor-pointer"
                      >
                        <div className="flex items-start justify-between gap-3 mb-2">
                          <p className="text-[13px] font-semibold text-aegis-text-primary truncate">{w.name}</p>
                          <div className="flex-shrink-0 flex items-center gap-2">
                            {w.updated_at && (
                              <span className="flex items-center gap-1 text-[10px] text-aegis-text-muted">
                                <Clock className="w-3 h-3" /> {relativeTime(w.updated_at)}
                              </span>
                            )}
                            {w.seed_key ? (
                              <span className="text-[10px] text-aegis-text-muted px-1.5">Default</span>
                            ) : armedDeleteId === w.id ? (
                              <button
                                onClick={e => { e.stopPropagation(); setArmedDeleteId(null); deleteWorkflow(w); }}
                                className="text-[10px] font-semibold text-white bg-aegis-error px-2 py-1 rounded-md hover:opacity-90 transition-opacity"
                              >
                                Confirm delete?
                              </button>
                            ) : (
                              <button
                                onClick={e => { e.stopPropagation(); setArmedDeleteId(w.id); }}
                                title={`Delete "${w.name}"`}
                                className="p-1 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error transition-colors"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            )}
                          </div>
                        </div>

                        <div className="flex items-center gap-1.5 mb-3 h-4">
                          {kindsUsed.length > 0 ? (
                            kindsUsed.map((kind, i) => (
                              <span key={i} className="flex-shrink-0">{nodeIcon({ label: '', kind: kind as NodeKind, isAi: false })}</span>
                            ))
                          ) : (
                            <span className="text-[10px] text-aegis-text-muted">Empty canvas</span>
                          )}
                        </div>

                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-[10px] text-aegis-text-muted px-1.5 py-0.5 rounded bg-aegis-overlay">
                            {nodeCount} step{nodeCount === 1 ? '' : 's'}
                          </span>
                          {w.is_chat_handler && (
                            <span className="inline-flex items-center gap-1 text-[10px] text-aegis-primary-light px-1.5 py-0.5 rounded bg-aegis-primary/10">
                              <MessageCircle className="w-2.5 h-2.5" /> Live in chat
                            </span>
                          )}
                          {w.is_ingestion_handler && (
                            <span className="inline-flex items-center gap-1 text-[10px] text-aegis-primary-light px-1.5 py-0.5 rounded bg-aegis-primary/10">
                              <Upload className="w-2.5 h-2.5" /> Handles uploads
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    );
  }

  // ── Editor view ────────────────────────────────────────────────────────
  return (
    <div className="flex-1 overflow-hidden bg-aegis-base flex flex-col font-sans">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-aegis-border flex-shrink-0">
        <button onClick={backToList} className="p-1.5 rounded-md hover:bg-aegis-overlay text-aegis-text-secondary">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          className="text-sm font-bold text-aegis-text-primary bg-transparent border-none focus:outline-none focus:bg-aegis-overlay rounded px-1.5 py-0.5"
        />
        <div className="flex-1" />
        <button
          onClick={toggleChatHandler}
          disabled={connectingChat || (!isChatHandler && !chatHandlerReady)}
          title={
            isChatHandler
              ? 'This workflow is handling your real chat messages — click to disconnect.'
              : chatHandlerReady
              ? 'Connect this workflow to power your real chat messages.'
              : 'Add an "On chat message" trigger, exactly one Send Reply node with exactly one "llm" node wired directly into it, and exactly one final step overall to enable this.'
          }
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg disabled:opacity-40 transition-colors ${
            isChatHandler
              ? 'bg-aegis-success/10 border border-aegis-success/40 text-aegis-success hover:bg-aegis-success/20'
              : 'bg-aegis-overlay border border-aegis-border text-aegis-text-primary hover:bg-aegis-base'
          }`}
        >
          {connectingChat ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Link2 className="w-3.5 h-3.5" />}
          {isChatHandler ? 'Connected to chat' : 'Connect to chat'}
        </button>
        <button
          onClick={toggleIngestionHandler}
          disabled={connectingIngestion || (!isIngestionHandler && !ingestionHandlerReady)}
          title={
            isIngestionHandler
              ? 'This workflow is handling your real document uploads — click to disconnect.'
              : ingestionHandlerReady
              ? 'Connect this workflow to power your real document uploads.'
              : 'Add an "On document upload" trigger and exactly one final step overall to enable this.'
          }
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg disabled:opacity-40 transition-colors ${
            isIngestionHandler
              ? 'bg-aegis-success/10 border border-aegis-success/40 text-aegis-success hover:bg-aegis-success/20'
              : 'bg-aegis-overlay border border-aegis-border text-aegis-text-primary hover:bg-aegis-base'
          }`}
        >
          {connectingIngestion ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
          {isIngestionHandler ? 'Connected to uploads' : 'Connect to uploads'}
        </button>
        <button
          onClick={openHistory}
          disabled={!dbIdRef.current}
          title={dbIdRef.current ? 'View past runs of this workflow.' : 'Save the workflow first to see run history.'}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-aegis-overlay border border-aegis-border text-aegis-text-primary text-xs font-semibold rounded-lg hover:bg-aegis-base disabled:opacity-40 transition-colors"
        >
          <HistoryIcon className="w-3.5 h-3.5" /> History
        </button>
        <button
          onClick={saveWorkflow}
          disabled={saving}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-aegis-overlay border border-aegis-border text-aegis-text-primary text-xs font-semibold rounded-lg hover:bg-aegis-base disabled:opacity-50 transition-colors"
        >
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />} Save
        </button>
        <button
          onClick={runWorkflow}
          disabled={running}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-aegis-primary text-white text-xs font-semibold rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />} Run
        </button>
      </div>

      <div className="flex-1 relative min-h-0" ref={canvasWrapperRef}>
        <ReactFlow
          nodes={decoratedNodes}
          edges={decoratedEdges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={(event, n) => {
            // Opens the node modal below (n8n-style Input | Settings |
            // Output) — centered, not anchored to the click point, so it
            // no longer needs cursor-relative position math the way the
            // old side popover did.
            setSelectedNodeId(n.id);
            setPaletteOpen(false);
          }}
          onPaneClick={() => { setSelectedNodeId(null); setPaletteOpen(false); }}
          fitView
          // Aegis has no dark mode of its own (light content, dark sidebar,
          // fixed palette — see globals.css) — "system" here made this one
          // canvas follow the OS's dark/light preference independently of
          // everything else, turning it flat black on a dark-mode system
          // while the rest of the app stayed on its fixed light theme.
          colorMode="light"
        >
          <Background />
          <Controls />
        </ReactFlow>

        {/* Floating add-node trigger */}
        <button
          onClick={() => { setPaletteOpen(o => !o); setSelectedNodeId(null); }}
          className="absolute top-3 left-3 z-10 flex items-center gap-1.5 px-3 py-1.5 bg-aegis-raised border border-aegis-border rounded-lg text-xs font-semibold text-aegis-text-primary shadow-sm hover:border-aegis-primary transition-colors"
        >
          {paletteOpen ? <X className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />} Add node
        </button>

        {/* Empty-canvas ghost prompt */}
        {nodes.length === 0 && !paletteOpen && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <button
              onClick={() => setPaletteOpen(true)}
              className="pointer-events-auto flex flex-col items-center gap-2 px-8 py-6 rounded-xl border-2 border-dashed border-aegis-border text-aegis-text-muted hover:border-aegis-primary hover:text-aegis-primary-light transition-colors"
            >
              <Plus className="w-5 h-5" />
              <span className="text-xs font-semibold">Add first step…</span>
            </button>
          </div>
        )}

        {/* Node modal — n8n-style: Input (what's wired in, with each
            connection's field mapping editable right here) | Settings
            (the node's own config) | Output (what this node produces) —
            a centered overlay, not a small popover anchored to the click
            point, since a real 3-column layout needs real width. Replaces
            the old side popover; a connection is no longer separately
            clickable for this — editing its field mapping happens from
            the target node's own Input column instead (still click-to-
            select-then-Delete to remove a connection, React Flow's
            default edge behavior, unrelated to this modal). */}
        {selectedNode && (
          <div
            className="fixed inset-0 z-30 bg-black/50 backdrop-blur-md flex items-center justify-center p-6"
            onClick={() => setSelectedNodeId(null)}
          >
            <div
              className="bg-aegis-overlay border border-aegis-border rounded-xl shadow-2xl w-full max-w-4xl h-[80vh] flex overflow-hidden"
              onClick={e => e.stopPropagation()}
            >
              <NodeInputColumn
                node={selectedNode}
                edges={edges}
                nodes={nodes}
                onUpdateMapping={updateEdgeMapping}
              />
              <div className="workflow-popover-scroll flex-1 min-w-0 overflow-y-auto p-4 border-x border-aegis-border">
                <NodeConfigPanel
                  data={selectedNode.data as NodeData}
                  isReplyGenerator={replyGenerationIds.includes(selectedNode.id)}
                  upstreamFieldSuggestions={getUpstreamFieldSuggestions(selectedNode.id, edges, nodes)}
                  tools={tools}
                  models={models}
                  databases={databases}
                  embeddingModels={embeddingModels}
                  rerankerModels={rerankerModels}
                  extractionEngines={extractionEngines}
                  mcpExtractionTools={mcpExtractionTools}
                  chunkingStrategies={chunkingStrategies}
                  aegisDbTables={aegisDbTables}
                  chatSessions={chatSessions}
                  onOpenAegisDbBrowser={() => setAegisDbBrowserOpen(true)}
                  selectedTool={selectedTool}
                  onChange={updateSelectedNode}
                  canDelete={!isSeeded}
                  onDelete={() => {
                    if (isSeeded) return;
                    setNodes(prev => prev.filter(n => n.id !== selectedNodeId));
                    setEdges(prev => prev.filter(e => e.source !== selectedNodeId && e.target !== selectedNodeId));
                    setSelectedNodeId(null);
                  }}
                />
              </div>
              <NodeOutputColumn node={selectedNode} edges={edges} nodes={nodes} />
            </div>
          </div>
        )}

        {/* Run history — recent WorkflowRun rows (app.core.workflows.engine
            writes one on every manual/chat/ingestion run) so a run's
            outcome survives past the WebSocket progress messages that
            reported it live. Left column: recent runs; right column: the
            selected run's per-node outputs, fetched on demand. */}
        {historyOpen && (
          <div
            className="fixed inset-0 z-30 bg-black/50 backdrop-blur-md flex items-center justify-center p-6"
            onClick={() => setHistoryOpen(false)}
          >
            <div
              className="bg-aegis-overlay border border-aegis-border rounded-xl shadow-2xl w-full max-w-3xl h-[75vh] flex overflow-hidden"
              onClick={e => e.stopPropagation()}
            >
              <div className="w-64 flex-shrink-0 border-r border-aegis-border overflow-y-auto">
                <div className="flex items-center justify-between px-3 py-2.5 border-b border-aegis-border">
                  <span className="text-xs font-bold text-aegis-text-primary">Run history</span>
                  <button onClick={() => setHistoryOpen(false)} className="p-1 rounded hover:bg-aegis-base text-aegis-text-muted">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
                {loadingRuns ? (
                  <div className="flex items-center justify-center py-8 text-aegis-text-muted"><Loader2 className="w-4 h-4 animate-spin" /></div>
                ) : runs.length === 0 ? (
                  <div className="p-3 text-xs text-aegis-text-muted">No runs yet — click Run, or connect this workflow to chat/uploads.</div>
                ) : (
                  runs.map(r => (
                    <button
                      key={r.id}
                      onClick={() => fetchRunDetail(r.id)}
                      className={`w-full text-left px-3 py-2 border-b border-aegis-border/50 hover:bg-aegis-base transition-colors ${selectedRun?.id === r.id ? 'bg-aegis-base' : ''}`}
                    >
                      <div className="flex items-center gap-1.5">
                        {r.status === 'completed' && <CheckCircle2 className="w-3.5 h-3.5 text-aegis-success flex-shrink-0" />}
                        {r.status === 'failed' && <XCircle className="w-3.5 h-3.5 text-aegis-error flex-shrink-0" />}
                        {r.status === 'running' && <Loader2 className="w-3.5 h-3.5 text-aegis-text-muted animate-spin flex-shrink-0" />}
                        <span className="text-xs font-semibold text-aegis-text-primary capitalize">{r.trigger}</span>
                      </div>
                      <div className="flex items-center gap-1 mt-0.5 text-[11px] text-aegis-text-muted">
                        <Clock className="w-3 h-3" />
                        {r.started_at ? new Date(r.started_at).toLocaleString() : '—'}
                      </div>
                    </button>
                  ))
                )}
              </div>
              <div className="flex-1 overflow-y-auto p-4">
                {!selectedRun ? (
                  <div className="h-full flex items-center justify-center text-xs text-aegis-text-muted">Select a run to see what each node produced.</div>
                ) : (
                  <div className="flex flex-col gap-3">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-aegis-text-primary capitalize">{selectedRun.trigger} run</span>
                      <span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded ${selectedRun.status === 'completed' ? 'bg-aegis-success/10 text-aegis-success' : selectedRun.status === 'failed' ? 'bg-aegis-error/10 text-aegis-error' : 'bg-aegis-overlay text-aegis-text-muted'}`}>
                        {selectedRun.status}
                      </span>
                    </div>
                    {selectedRun.error_message && (
                      <div className="text-xs text-aegis-error bg-aegis-error/10 border border-aegis-error/30 rounded-lg p-2">
                        {selectedRun.failed_node_id && <span className="font-semibold">'{selectedRun.failed_node_id}': </span>}
                        {selectedRun.error_message}
                      </div>
                    )}
                    {Object.entries(selectedRun.node_outputs || {}).map(([nodeId, output]) => (
                      <div key={nodeId} className="border border-aegis-border rounded-lg p-2">
                        <div className="text-[11px] font-semibold text-aegis-text-secondary mb-1">{nodeId}</div>
                        <pre className="text-[11px] text-aegis-text-primary whitespace-pre-wrap break-words font-mono">
                          {typeof output === 'string' ? output : JSON.stringify(output, null, 2)}
                        </pre>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Slide-over node picker */}
        <div
          className={`absolute top-0 right-0 bottom-0 w-72 bg-aegis-raised border-l border-aegis-border shadow-xl z-30 overflow-y-auto p-3 flex flex-col gap-3 transition-transform duration-200 ${paletteOpen ? 'translate-x-0' : 'translate-x-full pointer-events-none'}`}
        >
          <div className="relative flex-shrink-0">
            <Search className="w-3.5 h-3.5 text-aegis-text-muted absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              value={paletteSearch}
              onChange={e => setPaletteSearch(e.target.value)}
              placeholder="Search nodes…"
              className="w-full bg-aegis-overlay border border-aegis-border rounded-md pl-7 pr-2 py-1.5 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
          </div>

          {/* Triggers — listed first, matching n8n's own node picker */}
          {((showTrigger && !hasChatTrigger) || (showIngestionTrigger && !hasIngestionTrigger) || showScheduleTrigger) && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Triggers</div>
              {showTrigger && !hasChatTrigger && (
                <PaletteRow
                  icon={<MessageCircle className="w-4 h-4 text-aegis-success flex-shrink-0" />}
                  title="On chat message"
                  description="Starts this workflow when you send a message in chat"
                  onClick={addChatTriggerNode}
                />
              )}
              {showIngestionTrigger && !hasIngestionTrigger && (
                <PaletteRow
                  icon={<Upload className="w-4 h-4 text-aegis-success flex-shrink-0" />}
                  title="On document upload"
                  description="Starts this workflow when you upload a document"
                  onClick={addIngestionTriggerNode}
                />
              )}
              {showScheduleTrigger && (
                <PaletteRow
                  icon={<Clock className="w-4 h-4 text-aegis-success flex-shrink-0" />}
                  title="On a schedule"
                  description="Fires on its own timer — set the interval on the node. Runs in the background, no chat message or upload needed. Add as many as you like, each on its own cadence."
                  onClick={addScheduleTriggerNode}
                />
              )}
            </section>
          )}

          {/* Chat Reply — the one dedicated terminal node a chat-connected
              workflow needs; Export Reply optionally runs after it. */}
          {showChatContext && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Chat Reply</div>
              <div className="flex flex-col gap-0.5">
                <PaletteRow icon={<MessageSquareText className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />} title="Send Reply" description="No config of its own — wire exactly one 'llm' node directly into it (nothing else wired after that llm node) to generate the reply; this just marks where it's sent" onClick={addChatReplyNode} />
                <PaletteRow icon={<FileOutput className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />} title="Export Reply" description="Turns the reply into a downloadable file, if requested — wire it after the Send Reply node" onClick={addExportDocumentNode} />
              </div>
            </section>
          )}

          {/* Post to Chat — the delivery node for a schedule-triggered
              chain, which has no live chat turn to reply into the way
              Send Reply does. */}
          {showSendToChat && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Automation</div>
              <PaletteRow
                icon={<Send className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="Post to Chat"
                description="Delivers upstream text into a dedicated chat thread for this workflow — for a schedule-triggered chain with no live chat turn to reply into"
                onClick={addSendToChatNode}
              />
            </section>
          )}

          {/* Logic */}
          {showLogic && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Logic</div>
              <PaletteRow
                icon={<GitBranch className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="Logic"
                description="A gate — passes an upstream value through when a condition holds, otherwise skips everything wired after it"
                onClick={addLogicNode}
              />
            </section>
          )}

          {/* LLM */}
          {showLlm && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">LLM</div>
              <PaletteRow
                icon={<Brain className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="LLM step"
                description="A reasoning step bound to one model — freeform text, or a structured JSON judgment call (e.g. a yes/no decision) when configured with output fields. Wire one directly into a Send Reply node (nothing else after it) to make it the reply generator — real streaming, memory settings"
                onClick={addLlmNode}
              />
            </section>
          )}

          {/* MCP — one row per connected server, each with its own real
              brand icon (ServiceLogo, keyed by server name). Which TOOL on
              that server this node calls is picked in the node's own
              config panel, not here — see addMcpServerNode. */}
          {Object.keys(mcpToolsByServer).length > 0 && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">MCP</div>
              <div className="flex flex-col gap-0.5">
                {Object.entries(mcpToolsByServer).map(([server, serverTools]) => (
                  <PaletteRow
                    key={server}
                    icon={<ServiceLogo serviceKey={server} size="sm" />}
                    title={server}
                    description={`${serverTools.length} tool${serverTools.length === 1 ? '' : 's'} available — pick which one in the node's settings`}
                    onClick={() => addMcpServerNode(server)}
                  />
                ))}
              </div>
            </section>
          )}
          {Object.keys(mcpToolsByServer).length === 0 && !search && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">MCP</div>
              <p className="text-[10px] text-aegis-text-muted leading-relaxed">No MCP servers connected yet.</p>
            </section>
          )}

          {/* Loop */}
          {showLoop && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Loop</div>
              <PaletteRow
                icon={<Repeat className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="Loop"
                description="Runs the chain of steps wired after it once per item in an upstream list"
                onClick={addLoopNode}
              />
            </section>
          )}

          {/* Database */}
          {showDatabase && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Database</div>
              <PaletteRow
                icon={<DatabaseIcon className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="Database"
                description="Query a relational database installed from the Marketplace, or the built-in SQLite database — chat history, workflows, and more, in a safe credential-free set of tables"
                onClick={addDatabaseNode}
              />
            </section>
          )}

          {/* Embedding */}
          {showEmbedding && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Embedding</div>
              <PaletteRow
                icon={<Layers className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="Embedding"
                description="Embeds upstream text into vectors — wire its output into a Vector store node to store them"
                onClick={addEmbeddingNode}
              />
            </section>
          )}

          {/* Vector store */}
          {showVector && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Vector store</div>
              <PaletteRow
                icon={<Boxes className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="Vector store"
                description="Stores vectors from an upstream Embedding node, or searches an installed vector database"
                onClick={addVectorNode}
              />
            </section>
          )}

          {/* Reranker */}
          {showReranker && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Reranker</div>
              <PaletteRow
                icon={<ArrowDownUp className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="Reranker"
                description="Re-scores an upstream Vector search's candidates with a cross-encoder, keeping only the most relevant"
                onClick={addRerankerNode}
              />
            </section>
          )}

          {/* Document extraction — a single node; which downloaded engine
              runs for each format is configured inside the node itself
              (its config panel shows one picker per format), not chosen by
              which palette row you clicked. */}
          {showExtract && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Document extraction</div>
              <div className="flex flex-col gap-0.5">
                <PaletteRow icon={<FileText className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />} title="Extract" description="Pulls text out of a document — PDF, Word, PowerPoint, Excel, or plain text. Pick which downloaded engine handles each format inside the node." onClick={addAutoExtractNode} />
              </div>
            </section>
          )}

          {/* Chunk */}
          {showChunk && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Chunk</div>
              <PaletteRow
                icon={<Scissors className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                title="Chunk text"
                description="Splits upstream text into overlapping pieces for embedding"
                onClick={addChunkNode}
              />
            </section>
          )}

          {/* Local tools */}
          {localTools.length > 0 && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Local tools ({localTools.length})</div>
              <div className="flex flex-col gap-0.5">
                {localTools.map(t => (
                  <PaletteRow
                    key={t.name}
                    icon={toolIconByName(t.name, t.server)}
                    title={t.name}
                    description={t.description}
                    onClick={() => addToolNode(t)}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      </div>

      {aegisDbBrowserOpen && (
        <AegisDatabaseBrowser onClose={() => { setAegisDbBrowserOpen(false); fetchAegisDbTables(); }} />
      )}
    </div>
  );
}

const EXTRACT_FORMAT_LABELS: Record<NonNullable<NodeData['extractorFormat']>, string> = {
  pdf: 'PDF', docx: 'Word (DOCX)', pptx: 'PowerPoint (PPTX)', xlsx: 'Excel (XLSX)', text: 'Markdown/TXT/CSV',
};

// What each node kind's own output actually looks like — static reference
// text, not dynamically introspected (nothing has run yet at edit time).
// Shown in the node modal's Input column (for each upstream connection)
// and Output column (for this node itself) so picking an "Output field"
// to map isn't a guess — e.g. knowing a "vector" search node's output is
// a list of {content, filename, document_id, ...} tells you "content" is
// a real field name to type there, not "results" or "text".
const NODE_OUTPUT_SHAPE: Partial<Record<NodeKind, string>> = {
  chat_trigger: '{ message, history, attachments, pending_attachments }',
  document_upload_trigger: '{ file_path, document_id, filename, file_type }',
  schedule_trigger: '{ fired_at } — just a timestamp; wire an "mcp"/"tool" node after it to actually fetch something fresh on each firing.',
  send_to_chat: 'The exact text it delivered — a real string, useful if you wire something after it too.',
  llm: 'Plain text — or, with structured Output configured, exactly those field names as a JSON object.',
  logic: "Whatever came in, unchanged (or nothing at all if this gate's condition is false).",
  loop: 'A list — one entry per item, each the last step in the chain\'s own output for that item.',
  tool: 'Whatever that tool returns — usually an object; check the tool\'s own description.',
  database: 'Query: a list of row objects (or { row_count } for a write). SQLite database, "List rows": { columns, rows, row_count }. Insert/Update/Delete: the affected row. Create a new table: { success, created_table }.',
  vector: 'Search: a list of matches, each { content, filename, document_id, ... a score field }. Store: { upserted: <count> }.',
  reranker: 'The same list of matches, re-ordered by relevance and trimmed to Top K (each gains a rerank_score field).',
  embedding: '{ texts: [...], vectors: [...] } — paired 1:1, in the same order.',
  extract: 'Plain text — the document\'s extracted content.',
  chunk: 'A list of text chunks.',
  chat_reply: 'The exact text the reply-generation "llm" node produced, unchanged.',
  export_document: 'The reply text, with a download link appended if a file was actually exported.',
};

// The subset of NODE_OUTPUT_SHAPE whose keys are actually fixed and
// enumerable (not "depends which operation" or "whatever that tool
// returns") — used to power real autocomplete suggestions on a "Field"
// input (Logic node) or "Items field" input (Loop node) instead of asking
// the user to already know and correctly spell an upstream key name.
// Deliberately narrower than NODE_OUTPUT_SHAPE: database/vector/tool are
// left out here (and so give no suggestions) rather than risk suggesting a
// field name that's wrong for the operation actually configured.
const NODE_OUTPUT_FIELDS: Partial<Record<NodeKind, string[]>> = {
  chat_trigger: ['message', 'history', 'attachments', 'pending_attachments'],
  document_upload_trigger: ['file_path', 'document_id', 'filename', 'file_type'],
  schedule_trigger: ['fired_at'],
  embedding: ['texts', 'vectors'],
};

// Real, this-graph field suggestions for a "Field"/"Items field" input —
// looks at what's ACTUALLY wired directly upstream of `nodeId`, not just
// the node's own kind. An upstream "llm" node with Output switched to
// structured JSON contributes its own user-authored field names (the
// exact shape it will actually return); every other kind falls back to
// NODE_OUTPUT_FIELDS above. Empty when nothing's wired in yet, or when
// what's wired in has no statically-knowable shape (a plain-text "llm",
// a "tool" call, "database"/"vector" — the input stays a free-text field
// exactly as before, no suggestions is honest here, not a bug).
function getUpstreamFieldSuggestions(nodeId: string, edges: Edge[], nodes: Node[]): string[] {
  const seen = new Set<string>();
  for (const edge of edges) {
    if (edge.target !== nodeId) continue;
    const source = nodes.find(n => n.id === edge.source);
    const sourceData = source?.data as NodeData | undefined;
    if (!sourceData) continue;
    if (sourceData.kind === 'llm' && sourceData.outputFields?.length) {
      for (const f of sourceData.outputFields) if (f.name) seen.add(f.name);
      continue;
    }
    const fixed = NODE_OUTPUT_FIELDS[sourceData.kind];
    if (fixed) for (const name of fixed) seen.add(name);
  }
  return Array.from(seen);
}

// A connection between two nodes — click one on the canvas to explicitly
// map a single named field from the source's output onto what the target
// node reads it as (app.core.workflows.engine._resolve_named_arguments).
// Both fields blank is the normal, default case: the target just reads
// its upstream the implicit way it always has.
// Left column of the node modal — n8n's own "Input" pane: every incoming
// connection, each showing where it's from, a reference of what that
// source actually outputs, and the field-mapping override
// (edge.data.outputField/inputField, read by app.core.workflows.engine's
// _resolve_named_arguments) editable right here instead of requiring a
// separate click on the connecting line. Both fields blank (the default)
// means this node keeps reading its upstream the same implicit way it
// always has — this is an override, not a requirement.
function NodeInputColumn({ node, edges, nodes, onUpdateMapping }: {
  node: Node;
  edges: Edge[];
  nodes: Node[];
  onUpdateMapping: (edgeId: string, patch: { outputField?: string; inputField?: string }) => void;
}) {
  const incoming = edges.filter(e => e.target === node.id);
  return (
    <div className="workflow-popover-scroll w-64 flex-shrink-0 overflow-y-auto p-3 flex flex-col gap-3">
      <h3 className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider">Input</h3>
      {incoming.length === 0 ? (
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          {(['chat_trigger', 'document_upload_trigger', 'schedule_trigger'] as NodeKind[]).includes((node.data as NodeData).kind)
            ? "This is a trigger — it's the entry point, nothing feeds into it."
            : 'Nothing wired in yet — connect a step to it on the canvas.'}
        </p>
      ) : (
        incoming.map(edge => {
          const source = nodes.find(n => n.id === edge.source);
          const sourceData = source?.data as NodeData | undefined;
          const edgeData = (edge.data || {}) as { outputField?: string; inputField?: string };
          const outputShape = sourceData?.kind ? NODE_OUTPUT_SHAPE[sourceData.kind] : undefined;
          return (
            <div key={edge.id} className="border border-aegis-border rounded-lg p-2.5 flex flex-col gap-2">
              <p className="text-xs font-semibold text-aegis-text-secondary">{sourceData?.label || edge.source}</p>
              {outputShape && (
                <p className="text-[10px] text-aegis-text-muted leading-relaxed bg-aegis-overlay rounded-md p-1.5">
                  {outputShape}
                </p>
              )}
              <div>
                <label className="text-[9px] font-semibold text-aegis-text-muted uppercase">Output field</label>
                <input
                  value={edgeData.outputField || ''}
                  onChange={e => onUpdateMapping(edge.id, { outputField: e.target.value })}
                  placeholder="Blank = whole output"
                  className="w-full mt-0.5 bg-aegis-overlay border border-aegis-border rounded-md px-1.5 py-1 text-[11px] font-mono text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                />
              </div>
              <div>
                <label className="text-[9px] font-semibold text-aegis-text-muted uppercase">Input field</label>
                <input
                  value={edgeData.inputField || ''}
                  onChange={e => onUpdateMapping(edge.id, { inputField: e.target.value })}
                  placeholder="e.g. query, instruction"
                  className="w-full mt-0.5 bg-aegis-overlay border border-aegis-border rounded-md px-1.5 py-1 text-[11px] font-mono text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                />
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

// Right column of the node modal — n8n's own "Output" pane. Static
// reference, not dynamically introspected (nothing has run yet at edit
// time) — for structured "llm" output, lists the exact configured field
// names instead of the generic text, since those ARE known ahead of time.
function NodeOutputColumn({ node, edges, nodes }: { node: Node; edges: Edge[]; nodes: Node[] }) {
  const data = node.data as NodeData;
  const shape = NODE_OUTPUT_SHAPE[data.kind];
  // "Feeds into" — the mirror of the Input column's own list, so which
  // downstream node(s) actually receive this output (and under what field
  // mapping, if any) is visible from THIS node's own panel too, not only
  // discoverable by opening every node downstream one at a time.
  const outgoing = edges.filter(e => e.source === node.id);
  return (
    <div className="workflow-popover-scroll w-64 flex-shrink-0 overflow-y-auto p-3 flex flex-col gap-3">
      <h3 className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider">Output</h3>
      {data.kind === 'llm' && data.outputFields && data.outputFields.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-[10px] text-aegis-text-muted leading-relaxed">Structured JSON, exactly these fields:</p>
          {data.outputFields.map((f, i) => (
            <div key={i} className="flex items-center justify-between text-[11px] bg-aegis-overlay rounded-md px-2 py-1">
              <span className="font-mono text-aegis-text-primary">{f.name || '(unnamed)'}</span>
              <span className="text-aegis-text-muted">{f.type}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">{shape || 'Depends on this step\'s own configuration.'}</p>
      )}

      <div className="pt-2 border-t border-aegis-border flex flex-col gap-2">
        <h3 className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider">Feeds into</h3>
        {outgoing.length === 0 ? (
          <p className="text-[10px] text-aegis-text-muted leading-relaxed">Nothing wired out yet — connect it to a step on the canvas.</p>
        ) : (
          outgoing.map(edge => {
            const target = nodes.find(n => n.id === edge.target);
            const targetData = target?.data as NodeData | undefined;
            const edgeData = (edge.data || {}) as { outputField?: string; inputField?: string };
            return (
              <div key={edge.id} className="border border-aegis-border rounded-lg p-2 flex flex-col gap-1">
                <p className="text-xs font-semibold text-aegis-text-secondary">{targetData?.label || edge.target}</p>
                <p className="text-[10px] text-aegis-text-muted leading-relaxed">
                  {edgeData.outputField ? <>Sends field <span className="font-mono text-aegis-text-primary">{edgeData.outputField}</span></> : 'Sends the whole output'}
                  {edgeData.inputField ? <> as <span className="font-mono text-aegis-text-primary">{edgeData.inputField}</span></> : ''}
                </p>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function NodeConfigPanel({
  data, isReplyGenerator, upstreamFieldSuggestions, tools, models, databases, embeddingModels, rerankerModels, extractionEngines, mcpExtractionTools, chunkingStrategies, aegisDbTables, chatSessions, onOpenAegisDbBrowser, selectedTool, onChange, onDelete, canDelete,
}: {
  data: NodeData;
  // Only meaningful for kind "llm" — true when THIS node is the one whose
  // sole outgoing edge leads to a "chat_reply" node (see WorkflowsView's
  // replyGenerationIds), matching engine.py's run_chat_workflow detection
  // of which "llm" node actually generates+streams the reply vs. a plain
  // judgment-call step elsewhere in the same graph.
  isReplyGenerator: boolean;
  // Real field names pulled from whatever's actually wired directly
  // upstream of this node (see getUpstreamFieldSuggestions) — powers a
  // <datalist> on the Logic node's "Field" input and the Loop node's
  // "Items field" input, so the user picks a real key instead of having
  // to already know and correctly spell one. Empty when nothing's wired
  // in, or what's wired in has no statically-knowable shape.
  upstreamFieldSuggestions: string[];
  tools: ToolDef[];
  models: ModelDef[];
  databases: InstalledDB[];
  embeddingModels: EmbeddingModelDef[];
  rerankerModels: RerankerModelDef[];
  extractionEngines: ExtractionEngineDef[];
  mcpExtractionTools: McpExtractionToolDef[];
  chunkingStrategies: ChunkingStrategyDef[];
  aegisDbTables: { name: string; label: string; user_created: boolean }[];
  chatSessions: ChatSessionDef[];
  onOpenAegisDbBrowser: () => void;
  selectedTool?: ToolDef;
  onChange: (patch: Partial<NodeData>) => void;
  onDelete: () => void;
  // False while editing the built-in seeded workflow (WorkflowsView's
  // isSeeded) — its steps can still be reconfigured, just not removed
  // (PUT /api/workflows/{id} rejects a save that drops one of its
  // original nodes too, in case this gets bypassed some other way).
  canDelete: boolean;
}) {
  const relationalDatabases = databases.filter(d => d.category === 'relational').sort((a, b) => (b.is_builtin ? 1 : 0) - (a.is_builtin ? 1 : 0));
  const vectorDatabases = databases.filter(d => d.category === 'vector').sort((a, b) => (b.is_builtin ? 1 : 0) - (a.is_builtin ? 1 : 0));
  const header = (
    <div className="flex items-center justify-between">
      <h3 className="text-xs font-bold text-aegis-text-primary">Step settings</h3>
      {canDelete ? (
        <button onClick={onDelete} className="p-1 rounded hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error">
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      ) : (
        <span title="Part of the default pipeline — can be reconfigured, but not deleted." className="p-1 text-aegis-text-muted/40 cursor-not-allowed">
          <Trash2 className="w-3.5 h-3.5" />
        </span>
      )}
    </div>
  );

  const labelField = (
    <div>
      <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Label</label>
      <input
        value={data.label}
        onChange={e => onChange({ label: e.target.value })}
        className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
      />
    </div>
  );

  // Shared by chat_trigger/document_upload_trigger/send_to_chat — see
  // NodeData.conversationId's docstring. blankOptionLabel is the only
  // thing that differs per kind (what leaving it unset actually means).
  const conversationField = (blankOptionLabel: string) => (
    <div>
      <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Conversation</label>
      <select
        value={data.conversationId || ''}
        onChange={e => onChange({ conversationId: e.target.value || undefined })}
        className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
      >
        <option value="">{blankOptionLabel}</option>
        {chatSessions.map(s => (
          <option key={s.id} value={s.id}>{s.preview.length > 60 ? s.preview.slice(0, 60) + '…' : s.preview}</option>
        ))}
      </select>
    </div>
  );

  // Native browser autocomplete backing the "Field"/"Items field" inputs
  // below (via list="upstream-field-suggestions") — the clickable chips
  // rendered next to each input are the more discoverable affordance
  // (datalist's own dropdown indicator varies a lot by browser/OS), this
  // is just the typing shortcut on top of them.
  const fieldDatalist = (
    <datalist id="upstream-field-suggestions">
      {upstreamFieldSuggestions.map(f => <option key={f} value={f} />)}
    </datalist>
  );

  if (data.kind === 'chat_trigger') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Fires when you send a message in the normal chat window — only if this workflow is connected via the toolbar's "Connect to chat" button. Needs exactly one Send Reply node fed by exactly one "llm" node (that llm node is the real reply generator — model, prompt, and memory settings live there), and exactly one final step overall.
        </p>
        {conversationField('Every conversation (global handler)')}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Leave blank to take over ALL your chats when connected — the only option before per-conversation scoping existed. Pick one here to scope this workflow to just that conversation instead; your other chats keep using the normal pipeline (or a different workflow scoped to them). A global handler and any number of conversation-scoped ones can all be connected at the same time.
        </p>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed border-t border-aegis-border pt-2">
          A document attached to a chat message uses the SAME upload path as the standalone document library, and this turn's output includes a "pending_attachments" list — wire a Loop node off this trigger (itemsField "pending_attachments") into an Extract → Chunk → Embedding → Vector chain to index it as part of this same turn, exactly like the seeded pipeline does.
        </p>
      </div>
    );
  }

  if (data.kind === 'document_upload_trigger') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Fires when you upload a document — only if this workflow is connected via the toolbar's "Connect to uploads" button. Needs exactly one final step overall. Typically feeds an Extract → Chunk → Embedding → Vector store chain.
        </p>
        {conversationField('Every upload (global handler)')}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Leave blank to handle every upload anywhere when connected. Pick one here to scope this workflow to uploads made within just that conversation instead — uploads elsewhere keep using the normal pipeline (or a different workflow scoped to them).
        </p>
        <label className="flex items-start gap-2 text-xs text-aegis-text-secondary pt-1 border-t border-aegis-border">
          <input
            type="checkbox"
            checked={!!data.includeImages}
            onChange={e => onChange({ includeImages: e.target.checked || undefined })}
            className="mt-0.5"
          />
          <span>Also trigger on image uploads</span>
        </label>
        {data.includeImages ? (
          <p className="text-[10px] text-aegis-text-muted leading-relaxed">
            Off by default: an uploaded image (PNG/JPG) normally never reaches this trigger at all — it's read by whatever vision model is active for chat instead, or rejected if none is. With this on, an image fires this trigger like any file ({'{'}file_path, document_id, filename, file_type{'}'}), letting THIS workflow's own "llm" node read it with whatever model it picks — independent of the active chat model. Extract only understands documents, not images, so branch first: add a Logic node right after this trigger, Field "file_type", condition "matches regex", value <code className="font-mono">png|jpe?g</code> — route matches to an "llm" node with a vision-capable model picked, everything else to your normal Extract chain.
          </p>
        ) : (
          <p className="text-[10px] text-aegis-text-muted leading-relaxed">
            Leave this off unless you're specifically building something for uploaded images — every existing workflow keeps working exactly as before either way.
          </p>
        )}
      </div>
    );
  }

  if (data.kind === 'export_document') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Wire this AFTER the Send Reply node, with a classifier "llm" node (outputFields including is_export/format) also feeding it directly if you want real export detection. If a file was requested, turns the reply into a real PDF/DOCX/XLSX and appends a download link — otherwise passes the reply through unchanged. Make this the workflow's final step.
        </p>
      </div>
    );
  }

  if (data.kind === 'schedule_trigger') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Check every</label>
          <div className="flex items-center gap-2 mt-1">
            <input
              type="number"
              min={1}
              value={data.intervalMinutes ?? ''}
              onChange={e => onChange({ intervalMinutes: e.target.value ? Math.max(1, parseInt(e.target.value, 10)) : undefined })}
              placeholder="5"
              className="w-24 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
            <span className="text-[11px] text-aegis-text-muted">minutes</span>
          </div>
        </div>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed border-t border-aegis-border pt-2">
          Fires on its own in the background — no chat message or upload needed, and never runs from the toolbar's Run button (it just skips, since there's nothing to fire it with). Checked at most once a minute either way, so an interval under 1 isn't meaningfully faster.
        </p>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Wire an "mcp"/"tool" node after it (e.g. gmail_list_messages) to actually fetch something fresh each time it fires. To only act when something's genuinely new — not re-process the same latest item every firing — add a Logic node set to "Changed since last run" between that tool call and whatever comes next.
        </p>
      </div>
    );
  }

  if (data.kind === 'send_to_chat') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Delivers whatever's wired into it (usually an "llm" summary) as a real chat message. Notifies you immediately wherever you are in the app, and the message is there for good whenever you open that conversation.
        </p>
        {conversationField('Dedicated thread for this workflow (default)')}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Leave blank and it lands in one dedicated conversation kept just for this workflow's automated updates, titled from the workflow's own name. Pick an existing conversation here instead — your main chat, say — to have it show up right there.
        </p>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed border-t border-aegis-border pt-2">
          Different from Send Reply: Send Reply only works inside a live chat turn, replying into the SAME conversation you just typed into. This node works from a schedule-triggered chain, which has no such conversation to reply into.
        </p>
      </div>
    );
  }

  if (data.kind === 'llm') {
    const fields = data.outputFields || [];
    const updateField = (i: number, patch: Partial<NonNullable<NodeData['outputFields']>[number]>) => {
      const next = fields.slice();
      next[i] = { ...next[i], ...patch };
      onChange({ outputFields: next });
    };
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Model</label>
          <select
            value={data.modelName || ''}
            onChange={e => onChange({ modelName: e.target.value || undefined })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="">{models.length === 0 ? 'No models downloaded yet' : 'Select a model…'}</option>
            {models.map(m => <option key={m.name} value={m.name}>{m.display_name || m.name}</option>)}
          </select>
          {(() => {
            const selected = models.find(m => m.name === data.modelName);
            return selected?.is_vision && selected.mmproj_status === 'downloaded' && (
              <p className="text-[10px] text-aegis-text-muted mt-1">
                Vision-capable — if an upstream "On document upload" trigger has "Also trigger on image uploads" on and an image reaches this node, it reads the actual image, not just this text prompt.
              </p>
            );
          })()}
        </div>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Prompt</label>
          <textarea
            value={data.instruction || ''}
            onChange={e => onChange({ instruction: e.target.value })}
            rows={5}
            placeholder="What should this model do with the upstream data?"
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary resize-y"
          />
        </div>
        <div className="flex items-center gap-2.5">
          <div className="flex-1">
            <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Temperature</label>
            <input
              type="number" min={0} max={2} step={0.1}
              value={data.temperature ?? 0}
              onChange={e => onChange({ temperature: e.target.value === '' ? undefined : parseFloat(e.target.value) })}
              className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
          </div>
          <div className="flex-1">
            <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Max tokens</label>
            <input
              type="number" min={1} step={128}
              value={data.maxTokens ?? 1024}
              onChange={e => onChange({ maxTokens: e.target.value === '' ? undefined : parseInt(e.target.value) })}
              className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
          </div>
        </div>
        <div>
          <div className="flex items-center justify-between">
            <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Output</label>
            <button
              onClick={() => onChange({ outputFields: fields.length ? undefined : [{ name: '', type: 'boolean', description: '' }] })}
              className="text-[10px] font-semibold text-aegis-primary-light hover:underline"
            >
              {fields.length ? 'Switch to plain text' : 'Switch to structured JSON'}
            </button>
          </div>
          {fields.length === 0 ? (
            <p className="text-[10px] text-aegis-text-muted mt-1 leading-relaxed">
              Plain text — the model's raw reply. Switch to structured JSON for a judgment call (e.g. a yes/no decision) a downstream "logic" node or another step can read a specific field from.
            </p>
          ) : (
            <div className="flex flex-col gap-2 mt-1">
              {fields.map((f, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <input
                    value={f.name}
                    onChange={e => updateField(i, { name: e.target.value })}
                    placeholder="field_name"
                    className="flex-1 min-w-0 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  />
                  <select
                    value={f.type}
                    onChange={e => updateField(i, { type: e.target.value as typeof f.type })}
                    className="bg-aegis-overlay border border-aegis-border rounded-md px-1.5 py-1 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  >
                    <option value="boolean">boolean</option>
                    <option value="string">string</option>
                    <option value="number">number</option>
                    <option value="array">array</option>
                  </select>
                  <button onClick={() => onChange({ outputFields: fields.filter((_, j) => j !== i) })} className="text-aegis-text-muted hover:text-aegis-error px-1">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
              <button
                onClick={() => onChange({ outputFields: [...fields, { name: '', type: 'boolean', description: '' }] })}
                className="text-[10px] font-semibold text-aegis-primary-light hover:underline text-left"
              >
                + Add field
              </button>
              <p className="text-[10px] text-aegis-text-muted leading-relaxed">
                The model is constrained to return exactly these fields as JSON — this node's output becomes that object, not text.
              </p>
            </div>
          )}
        </div>
        <p className="text-[10px] text-aegis-primary-light leading-relaxed border-t border-aegis-border pt-2">
          {isReplyGenerator
            ? 'This is the reply generator — its sole outgoing edge leads to a Chat Reply node, so it streams live to the user instead of running as a one-shot call.'
            : 'Memory settings below always apply to this call — for a plain judgment call (e.g. a decide/classify step) they still fold in, they just have less to work with than a full reply.'}
        </p>
        <div className="pt-1 border-t border-aegis-border">
          <MemorySettingsPanel />
        </div>
      </div>
    );
  }

  if (data.kind === 'logic') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        {fieldDatalist}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Passes the upstream value through unchanged when the condition holds; otherwise everything wired after this node is skipped for this run.
        </p>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Field</label>
          <input
            value={data.field || ''}
            onChange={e => onChange({ field: e.target.value })}
            placeholder="e.g. needs_search — blank uses the upstream value directly"
            list="upstream-field-suggestions"
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />
          {upstreamFieldSuggestions.length > 0 && (
            <p className="text-[10px] text-aegis-text-muted mt-1">
              Known fields from what's wired in: {upstreamFieldSuggestions.map((f, i) => (
                <span key={f}>
                  <button type="button" onClick={() => onChange({ field: f })} className="text-aegis-primary-light hover:underline font-mono">{f}</button>
                  {i < upstreamFieldSuggestions.length - 1 ? ', ' : ''}
                </span>
              ))}
            </p>
          )}
        </div>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Condition</label>
          <select
            value={data.operator || 'is_true'}
            onChange={e => onChange({ operator: e.target.value as NodeData['operator'] })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="is_true">is true</option>
            <option value="is_false">is false</option>
            <option value="equals">equals</option>
            <option value="not_equals">does not equal</option>
            <option value="contains">contains</option>
            <option value="matches_regex">matches regex</option>
            <option value="changed_since_last_run">changed since last run</option>
          </select>
        </div>
        {data.operator === 'changed_since_last_run' && (
          <p className="text-[10px] text-aegis-text-muted leading-relaxed">
            Compares against what THIS node saw last time it ran (remembered across runs, not just this one) — holds only when it's different. Built for a schedule-triggered chain: wire gmail_list_messages (or anything else you're polling) in, leave Field blank to compare its whole output, and everything after this node only runs when something's actually changed.
          </p>
        )}
        {!['is_true', 'is_false', 'changed_since_last_run'].includes(data.operator || 'is_true') && (
          <div>
            <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Value</label>
            <input
              value={data.value || ''}
              onChange={e => onChange({ value: e.target.value })}
              className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
          </div>
        )}
      </div>
    );
  }

  if (data.kind === 'chat_reply') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          No configuration — model, prompt, and memory settings all live on the "llm" node wired directly into this one (with nothing else wired after that llm node). This node just marks where the reply gets sent; exactly one required to connect this workflow to chat.
        </p>
      </div>
    );
  }

  if (data.kind === 'loop') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        {fieldDatalist}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Items field</label>
          <input
            value={data.itemsField || ''}
            onChange={e => onChange({ itemsField: e.target.value })}
            placeholder="e.g. pending_attachments — blank loops the whole input"
            title="Wire a chain of steps directly after this loop — the whole chain runs once per item."
            list="upstream-field-suggestions"
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />
          {upstreamFieldSuggestions.length > 0 && (
            <p className="text-[10px] text-aegis-text-muted mt-1">
              Known fields from what's wired in: {upstreamFieldSuggestions.map((f, i) => (
                <span key={f}>
                  <button type="button" onClick={() => onChange({ itemsField: f })} className="text-aegis-primary-light hover:underline font-mono">{f}</button>
                  {i < upstreamFieldSuggestions.length - 1 ? ', ' : ''}
                </span>
              ))}
            </p>
          )}
        </div>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Runs the whole chain of steps wired directly after this node once per item (e.g. Extract → Chunk → Embedding → Vector, once per attachment) — a step that's also fed from somewhere else ends the chain and runs once, after every item finishes.
        </p>
      </div>
    );
  }

  if (data.kind === 'database') {
    const selectedDb = relationalDatabases.find(d => d.id === data.databaseId);
    const dbOp = (data.operation as NonNullable<NodeData['operation']>) || 'list';
    const isCreatingTable = dbOp === 'create_table';
    const isRunningQuery = dbOp === 'query';
    const selectedTableInfo = aegisDbTables.find(t => t.name === data.table);
    const isSystemTableSelected = !isCreatingTable && !isRunningQuery && !!selectedTableInfo && !selectedTableInfo.user_created;
    const userTables = aegisDbTables.filter(t => t.user_created);
    const systemTables = aegisDbTables.filter(t => !t.user_created);
    const newTableColumns = data.newTableColumns || [];
    const updateNewColumn = (i: number, patch: Partial<NonNullable<NodeData['newTableColumns']>[number]>) =>
      onChange({ newTableColumns: newTableColumns.map((c, idx) => idx === i ? { ...c, ...patch } : c) });
    const addNewColumn = () => onChange({ newTableColumns: [...newTableColumns, { name: '', type: 'text', nullable: true }] });
    const removeNewColumn = (i: number) => onChange({ newTableColumns: newTableColumns.filter((_, idx) => idx !== i) });

    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Database</label>
          <select
            value={data.databaseId ?? ''}
            onChange={e => onChange({ databaseId: e.target.value ? parseInt(e.target.value) : undefined })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="">{relationalDatabases.length === 0 ? 'None installed yet' : 'Select a database…'}</option>
            {relationalDatabases.map(d => (
              <option key={d.id} value={d.id}>{d.name}{!d.is_builtin ? ` (${d.engine_id})` : ''}</option>
            ))}
          </select>
          {relationalDatabases.length === 0 && (
            <p className="text-[10px] text-aegis-text-muted mt-1">Install one from the Marketplace's Databases category.</p>
          )}
        </div>
        {selectedDb?.is_builtin ? (
          <>
            <button
              onClick={onOpenAegisDbBrowser}
              className="flex items-center justify-center gap-1.5 px-2.5 py-1.5 bg-aegis-overlay border border-aegis-border rounded-md text-xs font-semibold text-aegis-text-primary hover:bg-aegis-base transition-colors"
            >
              Browse tables…
            </button>
            <div>
              <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Operation</label>
              <select
                value={dbOp}
                onChange={e => onChange({ operation: e.target.value as any })}
                className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
              >
                <option value="list">List rows</option>
                <option value="insert">Insert a row</option>
                <option value="update">Update a row</option>
                <option value="delete">Delete a row</option>
                <option value="create_table">Create a new table</option>
                <option value="query">Run SQL query</option>
              </select>
            </div>

            {isRunningQuery ? (
              <>
                <div>
                  <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">SQL query</label>
                  <textarea
                    value={data.query || ''}
                    onChange={e => onChange({ query: e.target.value })}
                    rows={4}
                    placeholder="CREATE TABLE my_table (name TEXT) — or SELECT / INSERT / UPDATE against a table you created"
                    spellCheck={false}
                    className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs font-mono text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary resize-y"
                  />
                  <p className="text-[10px] text-aegis-text-muted mt-1 leading-relaxed">
                    Any SELECT works against any table. CREATE TABLE always works, and the new table becomes immediately usable. Any other write (INSERT/UPDATE/DELETE/...) only works against a table you created — a system table stays read-only.
                  </p>
                </div>
                <ParamsEditor
                  label="Query parameters"
                  values={data.staticInputs || {}}
                  onChange={v => onChange({ staticInputs: v })}
                />
              </>
            ) : isCreatingTable ? (
              <>
                <div>
                  <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">New table name</label>
                  <input
                    value={data.table || ''}
                    onChange={e => onChange({ table: e.target.value || undefined })}
                    placeholder="my_table"
                    className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  />
                </div>
                <div>
                  <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Columns</label>
                  <p className="text-[10px] text-aegis-text-muted mt-0.5 mb-1.5">An "id" primary key column is added automatically. This table is fully yours — the node can insert, update, and delete rows in it.</p>
                  <div className="flex flex-col gap-1.5">
                    {newTableColumns.map((col, i) => (
                      <div key={i} className="flex items-center gap-1.5">
                        <input
                          value={col.name}
                          onChange={e => updateNewColumn(i, { name: e.target.value })}
                          placeholder="column_name"
                          className="flex-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                        />
                        <select
                          value={col.type}
                          onChange={e => updateNewColumn(i, { type: e.target.value as any })}
                          className="w-24 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                        >
                          <option value="text">Text</option>
                          <option value="integer">Integer</option>
                          <option value="real">Real</option>
                          <option value="boolean">Boolean</option>
                        </select>
                        <label className="flex items-center gap-1 text-[10px] text-aegis-text-muted flex-shrink-0">
                          <input type="checkbox" checked={col.nullable} onChange={e => updateNewColumn(i, { nullable: e.target.checked })} />
                          Nullable
                        </label>
                        <button onClick={() => removeNewColumn(i)} className="p-1 rounded hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error flex-shrink-0">
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                  <button onClick={addNewColumn} className="mt-2 flex items-center gap-1.5 text-[11px] font-semibold text-aegis-primary-light hover:underline">
                    <Plus className="w-3 h-3" /> Add column
                  </button>
                </div>
              </>
            ) : (
              <>
                <div>
                  <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Table</label>
                  <select
                    value={data.table || ''}
                    onChange={e => onChange({ table: e.target.value || undefined })}
                    className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  >
                    <option value="">{aegisDbTables.length === 0 ? 'Loading…' : 'Select a table…'}</option>
                    {userTables.length > 0 && (
                      <optgroup label="Your tables">
                        {userTables.map(t => <option key={t.name} value={t.name}>{t.label}</option>)}
                      </optgroup>
                    )}
                    {systemTables.length > 0 && (
                      <optgroup label="System tables (read-only)">
                        {systemTables.map(t => <option key={t.name} value={t.name}>{t.label}</option>)}
                      </optgroup>
                    )}
                  </select>
                  {isSystemTableSelected && dbOp !== 'list' && (
                    <p className="text-[10px] text-aegis-error mt-1 leading-relaxed">
                      '{selectedTableInfo?.label}' is a system table — you can list its rows, but not {dbOp} them. Pick a table you created, or switch Operation to "List rows".
                    </p>
                  )}
                </div>
                {dbOp === 'list' ? (
                  <div>
                    <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Limit</label>
                    <input
                      type="number"
                      value={data.limit ?? 50}
                      onChange={e => onChange({ limit: parseInt(e.target.value) || 50 })}
                      className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                    />
                  </div>
                ) : (
                  <>
                    {(dbOp === 'update' || dbOp === 'delete') && (
                      <p className="text-[10px] text-aegis-text-muted leading-relaxed">
                        Needs a "__pk" value below (or wired in from upstream) naming which row's id to {dbOp}.
                      </p>
                    )}
                    <ParamsEditor
                      label={dbOp === 'insert' ? 'Column values' : 'Fixed values'}
                      values={data.staticInputs || {}}
                      onChange={v => onChange({ staticInputs: v })}
                    />
                  </>
                )}
              </>
            )}
          </>
        ) : (
          <>
            <div>
              <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">SQL query</label>
              <textarea
                value={data.query || ''}
                onChange={e => onChange({ query: e.target.value })}
                rows={4}
                placeholder="SELECT * FROM table WHERE id = :id — any SQL, including CREATE TABLE"
                spellCheck={false}
                className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs font-mono text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary resize-y"
              />
              <p className="text-[10px] text-aegis-text-muted mt-1 leading-relaxed">
                Runs directly against this installed database — write any SQL, including creating your own tables.
              </p>
            </div>
            <ParamsEditor
              label="Query parameters"
              values={data.staticInputs || {}}
              onChange={v => onChange({ staticInputs: v })}
            />
          </>
        )}
      </div>
    );
  }

  if (data.kind === 'vector') {
    const selectedVectorDb = vectorDatabases.find(d => d.id === data.databaseId);
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Vector store</label>
          <select
            value={data.databaseId ?? ''}
            onChange={e => onChange({ databaseId: e.target.value ? parseInt(e.target.value) : undefined })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="">{vectorDatabases.length === 0 ? 'None installed yet' : 'Select a vector store…'}</option>
            {vectorDatabases.map(d => (
              <option key={d.id} value={d.id}>
                {d.name}{!d.is_builtin ? ` (${d.engine_id}${d.config?.embedding_dim ? `, ${d.config.embedding_dim}d` : ''})` : ''}
              </option>
            ))}
          </select>
          {vectorDatabases.length === 0 && (
            <p className="text-[10px] text-aegis-text-muted mt-1">Install one from the Marketplace's Databases category.</p>
          )}
          {data.operation === 'search' && selectedVectorDb && (
            selectedVectorDb.is_builtin ? (
              <p className="text-[10px] text-aegis-text-muted mt-1 leading-relaxed">
                The built-in store — search strategy and reranking below are only available on it.
              </p>
            ) : (
              <p className="text-[10px] text-aegis-text-muted mt-1 leading-relaxed">
                Plain dense search only — hybrid search and rerank strategies are exclusive to the built-in store above.
              </p>
            )
          )}
        </div>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Operation</label>
          <select
            value={data.operation || 'search'}
            onChange={e => onChange({ operation: e.target.value as 'upsert' | 'search' })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="upsert">Store — save vectors from an upstream Embedding node</option>
            <option value="search">Search — embed a query and find matches</option>
          </select>
        </div>
        {data.operation === 'search' ? (
          <>
            {selectedVectorDb?.is_builtin && (
              <div>
                <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Search strategy</label>
                <select
                  value={data.searchMode || 'hybrid'}
                  onChange={e => onChange({ searchMode: e.target.value as NodeData['searchMode'] })}
                  className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                >
                  <option value="hybrid">Hybrid — dense + BM25, fused (best default)</option>
                  <option value="semantic">Semantic — dense/embedding similarity only</option>
                  <option value="bm25">BM25 — sparse keyword match only</option>
                </select>
                <p className="text-[10px] text-aegis-text-muted mt-1.5 leading-relaxed">
                  Returns candidates in raw similarity order — wire a Reranker node after this one to re-score them with a cross-encoder before they reach the reply.
                </p>
              </div>
            )}
            {!selectedVectorDb?.is_builtin && (
              <p className="text-[10px] text-aegis-text-muted leading-relaxed">
                Wire an Embedding node into this one — it searches with whatever vector that node produced (matching the model your store was created with). Without one wired in, this node has nothing to search with.
              </p>
            )}
            <div>
              <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Query text</label>
              <textarea
                value={data.instruction || ''}
                onChange={e => onChange({ instruction: e.target.value })}
                rows={3}
                placeholder="What to search for — leave blank to use upstream text"
                className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary resize-y"
              />
            </div>
            <div>
              <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Top K</label>
              <input
                type="number"
                value={data.topK ?? 5}
                onChange={e => onChange({ topK: parseInt(e.target.value) || 5 })}
                className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
              />
            </div>
          </>
        ) : (
          <>
            <p className="text-[10px] text-aegis-text-muted leading-relaxed">
              Stores each vector from an upstream Embedding node, alongside the text it came from — wire an Embedding node in above (which itself takes text from a Chunk node or similar).
            </p>
            {selectedVectorDb?.is_builtin && (
              <p className="text-[10px] text-aegis-error leading-relaxed">
                {selectedVectorDb.name} is populated automatically on upload — it refuses direct storing here. Pick a Marketplace-installed store instead.
              </p>
            )}
          </>
        )}
      </div>
    );
  }

  if (data.kind === 'reranker') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Reranker model</label>
          <select
            value={data.rerankerModel || ''}
            onChange={e => onChange({ rerankerModel: e.target.value || undefined })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="">BAAI/bge-reranker-base (bundled) — default</option>
            {rerankerModels.map(m => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
          </select>
          {rerankerModels.length === 0 && (
            <p className="text-[10px] text-aegis-text-muted mt-1">Install another one from the Marketplace's Rerankers category.</p>
          )}
        </div>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Top K</label>
          <input
            type="number"
            value={data.topK ?? 5}
            onChange={e => onChange({ topK: parseInt(e.target.value) || 5 })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />
        </div>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Re-scores an upstream Vector search's candidate list against the query and keeps only the top K, most relevant first — wire this directly after a Vector store node's "Search" operation.
        </p>
      </div>
    );
  }

  if (data.kind === 'embedding') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Embedding model</label>
          <select
            value={data.embeddingModel || ''}
            onChange={e => onChange({ embeddingModel: e.target.value || undefined })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="">BAAI/bge-base-en-v1.5 (768d, bundled) — default</option>
            {embeddingModels.map(m => <option key={m.model_id} value={m.model_id}>{m.display_name} ({m.dim}d)</option>)}
          </select>
        </div>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Embeds each piece of upstream text (e.g. from a Chunk node) into vectors — wire this node's output into a Vector store node's "Store" operation.
        </p>
      </div>
    );
  }

  if (data.kind === 'extract') {
    // A node placed from a specific palette row (e.g. "Extract PDF") knows
    // its one format up front — show just that format's config. A node fed
    // by an upstream trigger (e.g. "On document upload") or the generic
    // "Extract text (auto-detect)" row doesn't know the format until a real
    // file arrives, so it shows every format Aegis can extract, each
    // stored under its own key in enginePerFormat (see engine.py's
    // _run_extract_node) — every format gets its own row here, each always
    // naming the real tool that runs it, never a blank/generic fallback.
    const formatsToShow = data.extractorFormat
      ? [data.extractorFormat]
      : Array.from(new Set(extractionEngines.map(e => e.format)));
    const enginePerFormat = data.enginePerFormat || {};
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">File path</label>
          <input
            value={data.staticInputs?.filePath ?? data.filePath ?? ''}
            onChange={e => onChange({ staticInputs: { ...(data.staticInputs || {}), filePath: e.target.value } })}
            placeholder="/path/to/document.pdf — or wire it in from upstream"
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />
        </div>
        {formatsToShow.map(fmt => {
          const formatEngines = extractionEngines.filter(e => e.format === fmt);
          if (formatEngines.length === 0 && mcpExtractionTools.length === 0) return null;
          const defaultEngine = formatEngines.find(e => e.default) || formatEngines[0];
          const selectedId = enginePerFormat[fmt] || '';
          const activeEngine =
            formatEngines.find(e => e.engine_id === selectedId)
            || mcpExtractionTools.find(t => t.engine_id === selectedId)
            || defaultEngine;
          const fmtLabel = EXTRACT_FORMAT_LABELS[fmt as keyof typeof EXTRACT_FORMAT_LABELS] || fmt;
          const totalChoices = formatEngines.length + mcpExtractionTools.length;
          return (
            <div key={fmt}>
              <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">{fmtLabel} extractor</label>
              {totalChoices > 1 ? (
                <select
                  value={selectedId}
                  onChange={e => {
                    const next = { ...enginePerFormat };
                    if (e.target.value) next[fmt] = e.target.value; else delete next[fmt];
                    onChange({ enginePerFormat: next });
                  }}
                  className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                >
                  <option value="">Default ({defaultEngine ? defaultEngine.name : 'built-in'})</option>
                  {formatEngines.length > 0 && (
                    <optgroup label="Built in">
                      {formatEngines.map(e => <option key={e.engine_id} value={e.engine_id}>{e.name}</option>)}
                    </optgroup>
                  )}
                  {mcpExtractionTools.length > 0 && (
                    <optgroup label="Custom — via a connected MCP tool">
                      {mcpExtractionTools.map(t => <option key={t.engine_id} value={t.engine_id}>{t.name}</option>)}
                    </optgroup>
                  )}
                </select>
              ) : (
                <div className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary">
                  {activeEngine?.name ?? 'None'}
                </div>
              )}
              {activeEngine && <p className="text-[10px] text-aegis-text-muted mt-1">{activeEngine.description}</p>}
            </div>
          );
        })}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Supports PDF, DOCX, PPTX, Markdown, TXT/CSV, and XLSX. Need a tool Aegis doesn't ship — a specialized OCR
          service, a company-internal document parser? Connect it as an MCP server from{' '}
          <span className="font-medium text-aegis-text-secondary">Connectors</span> and it shows up as a "Custom" choice above.
        </p>
      </div>
    );
  }

  if (data.kind === 'chunk') {
    const activeStrategy = chunkingStrategies.find(s => s.id === data.strategy) || chunkingStrategies.find(s => s.default);
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Strategy</label>
          <select
            value={data.strategy || ''}
            onChange={e => onChange({ strategy: e.target.value || undefined })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="">{chunkingStrategies.length === 0 ? 'Loading…' : `Default (${chunkingStrategies.find(s => s.default)?.name ?? 'Recursive'})`}</option>
            {chunkingStrategies.filter(s => !s.default).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          {activeStrategy && <p className="text-[10px] text-aegis-text-muted mt-1 leading-relaxed">{activeStrategy.description}</p>}
        </div>
        <div className="flex items-center gap-2.5">
          <div className="flex-1">
            <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Chunk size</label>
            <input
              type="number"
              value={data.chunkSize ?? 300}
              onChange={e => onChange({ chunkSize: parseInt(e.target.value) || 300 })}
              className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
          </div>
          <div className="flex-1">
            <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Overlap</label>
            <input
              type="number"
              value={data.overlap ?? 50}
              onChange={e => onChange({ overlap: parseInt(e.target.value) || 50 })}
              className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
          </div>
        </div>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">Splits the text from an upstream node (e.g. Extract text) into overlapping pieces.</p>
      </div>
    );
  }

  // "tool" kind — an MCP node (data.server set) or a local tool node.
  const properties = selectedTool?.inputSchema?.properties || {};
  return (
    <div className="flex flex-col gap-3">
      {header}
      {labelField}

      {data.server && (
        <div className="flex items-center gap-1.5 text-[10px] text-aegis-text-muted">
          <ServiceLogo serviceKey={data.server} size="sm" /> MCP server: <span className="font-semibold text-aegis-text-secondary">{data.server}</span>
        </div>
      )}

      <label className="flex items-center gap-2 text-xs text-aegis-text-secondary">
        <input type="checkbox" checked={data.isAi} onChange={e => onChange({ isAi: e.target.checked })} />
        AI step (LLM fills this in, scoped to this step only)
      </label>

      <div>
        <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Tool</label>
        <select
          value={data.toolName || ''}
          onChange={e => {
            // Placed from an MCP server's own palette row (data.server
            // already fixed) — stays scoped to that server's own tools,
            // the options below never offer another server's. A node with
            // no server yet (local tool, or one from before per-server
            // scoping existed) still gets the full picker.
            const scoped = data.server ? tools.filter(t => t.server === data.server) : tools;
            const tool = scoped.find(t => t.name === e.target.value);
            onChange({ toolName: tool?.name || undefined, server: data.server ?? tool?.server ?? null, staticInputs: {} });
          }}
          title={selectedTool?.description}
          className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
        >
          <option value="">Select a tool…</option>
          {(data.server ? tools.filter(t => t.server === data.server) : tools).map(t => (
            <option key={t.name} value={t.name}>{!data.server && t.server ? `${t.server} — ${t.name}` : t.name}</option>
          ))}
        </select>
      </div>

      {data.isAi ? (
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Instruction</label>
          <textarea
            value={data.instruction || ''}
            onChange={e => onChange({ instruction: e.target.value })}
            rows={5}
            placeholder={data.toolName ? 'What should this tool call accomplish, given the upstream data?' : 'What should this step do with the upstream data?'}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary resize-y"
          />
        </div>
      ) : (
        Object.keys(properties).length > 0 && (
          <div>
            <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Fixed values</label>
            <div className="flex flex-col gap-2 mt-1">
              {Object.entries(properties).map(([key, meta]: [string, any]) => (
                <div key={key}>
                  <label className="text-[10px] text-aegis-text-muted">
                    {key}{selectedTool?.inputSchema?.required?.includes(key) && ' *'}
                  </label>
                  <input
                    value={data.staticInputs?.[key] || ''}
                    onChange={e => onChange({ staticInputs: { ...(data.staticInputs || {}), [key]: e.target.value } })}
                    placeholder={meta.description ? `Blank = ${meta.description}` : 'Blank = the tool\'s own default'}
                    title={meta.description}
                    className="w-full bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary placeholder:text-aegis-text-muted/70"
                  />
                  {meta.description && (
                    <p className="text-[10px] text-aegis-text-muted mt-0.5 leading-snug">{meta.description}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )
      )}
    </div>
  );
}

// The same shared Chat+Agent history/memory settings that used to live on
// the standalone Context & Memory page (ContextMemoryHub.tsx) — moved here
// verbatim (same fetch, same dual chat+agent save payload) so they're
// configured right on the node that actually represents "memory" in the
// canvas, instead of a separate settings screen. A real fetching/self-
// contained component (not inline state in NodeConfigPanel) since hooks
// can't be called conditionally inside that function's kind-branches.
interface MemorySettings {
  max_history_messages: number;
  max_msg_chars: number;
  max_output_tokens: number;
  max_result_snippet: number;
  max_rag_chunks: number;
}

function MemorySettingsPanel() {
  const [config, setConfig] = useState<MemorySettings | null>(null);
  const [modelMaxContext, setModelMaxContext] = useState(4096);
  const [hasChanges, setHasChanges] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [cfgRes, hwRes] = await Promise.all([
          fetch(`${API_BASE}/api/context-config`),
          fetch(`${API_BASE}/api/hardware/status`),
        ]);
        if (cfgRes.ok) {
          const data = await cfgRes.json();
          if (data.chat) setConfig(data.chat);
        }
        if (hwRes.ok) {
          const hw = await hwRes.json();
          setModelMaxContext(hw.max_context || 4096);
        }
      } catch (e) {}
    })();
  }, []);

  const handleChange = (key: keyof MemorySettings, val: number) => {
    setConfig(c => c ? { ...c, [key]: val } : c);
    setHasChanges(true);
  };

  const save = async () => {
    if (!config) return;
    setIsSaving(true);
    try {
      const { max_rag_chunks, ...shared } = config;
      const res = await fetch(`${API_BASE}/api/context-config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat: config, agent: shared }),
      });
      if (res.ok) {
        setHasChanges(false);
        toast.success('Saved.');
      } else {
        toast.error('Failed to save settings.');
      }
    } catch (e) {
      toast.error('Network error while saving settings.');
    } finally {
      setIsSaving(false);
    }
  };

  if (!config) {
    return <p className="text-[10px] text-aegis-text-muted">Loading…</p>;
  }

  const slider = (label: string, key: keyof MemorySettings, min: number, max: number, step: number, maxLabel?: string) => (
    <div>
      <div className="flex justify-between items-end mb-1">
        <span className="text-[10px] font-semibold text-aegis-text-muted uppercase">{label}</span>
        <span className="text-[10px] font-bold text-aegis-primary-light">{config[key].toLocaleString()}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step}
        value={config[key]}
        onChange={e => handleChange(key, parseInt(e.target.value))}
        className="w-full h-1.5 bg-aegis-overlay rounded-lg appearance-none cursor-pointer accent-aegis-primary focus:outline-none"
      />
      <div className="flex justify-between text-[9px] text-aegis-text-muted mt-0.5">
        <span>{min.toLocaleString()}</span>
        <span>{maxLabel || max.toLocaleString()}</span>
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-3 mt-2">
      <p className="text-[10px] text-aegis-text-muted leading-relaxed">
        Shared by Chat and the Agent — same underlying LLM. Higher values improve recall but increase RAM and latency.
      </p>
      {slider('Max response length', 'max_output_tokens', 2048, modelMaxContext, 512, `Model max: ${modelMaxContext.toLocaleString()}`)}
      {slider('Max history messages', 'max_history_messages', 1, 20, 1)}
      {slider('Max characters per message', 'max_msg_chars', 500, 10000, 500, '10.0k')}
      {slider('Tool result snippet size', 'max_result_snippet', 500, 10000, 500, '10.0k')}
      <button
        onClick={save}
        disabled={!hasChanges || isSaving}
        className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${hasChanges ? 'bg-aegis-primary text-white hover:opacity-90' : 'bg-aegis-overlay text-aegis-text-muted cursor-not-allowed'}`}
      >
        {isSaving ? 'Saving…' : 'Save'}
      </button>
    </div>
  );
}

// Free-form key/value editor for a database node's query parameters — there
// is no input schema to derive fields from (unlike a tool node), so the
// user names their own :placeholders.
function ParamsEditor({ label, values, onChange }: { label: string; values: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const [newKey, setNewKey] = useState('');
  const entries = Object.entries(values);

  const addRow = () => {
    const key = newKey.trim();
    if (!key || key in values) return;
    onChange({ ...values, [key]: '' });
    setNewKey('');
  };

  const removeRow = (key: string) => {
    const next = { ...values };
    delete next[key];
    onChange(next);
  };

  return (
    <div>
      <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">{label}</label>
      <div className="flex flex-col gap-1.5 mt-1">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-center gap-1.5">
            <span className="text-[10px] text-aegis-text-muted font-mono w-16 truncate flex-shrink-0" title={key}>:{key}</span>
            <input
              value={value}
              onChange={e => onChange({ ...values, [key]: e.target.value })}
              className="flex-1 min-w-0 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
            <button onClick={() => removeRow(key)} className="p-1 rounded hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error flex-shrink-0">
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
        <div className="flex items-center gap-1.5">
          <input
            value={newKey}
            onChange={e => setNewKey(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addRow()}
            placeholder="param name"
            className="flex-1 min-w-0 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />
          <button onClick={addRow} disabled={!newKey.trim()} className="p-1.5 rounded-md hover:bg-aegis-overlay text-aegis-text-secondary disabled:opacity-40 flex-shrink-0">
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
