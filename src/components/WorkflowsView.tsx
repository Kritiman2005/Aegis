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
  Layers, DatabaseZap, Upload,
  Boxes, FileOutput, MessageSquareText, GitBranch,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useSocket } from '../hooks/useSocket';
import AegisDatabaseBrowser from './AegisDatabaseBrowser';

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

interface ExtractionEngineDef {
  format: string;
  engine_id: string;
  name: string;
  description: string;
}

interface WorkflowSummary {
  id: number;
  name: string;
  updated_at?: string;
  is_chat_handler?: boolean;
  is_ingestion_handler?: boolean;
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
// "aegis_database" is a different database entirely from "database" above
// — it browses/edits Aegis's OWN SQLite data (a safe, credential-free
// allowlist of tables, plus any table the user creates themselves) rather
// than a Marketplace-installed one. See app.core.aegis_db_browser.
// "document_upload_trigger" is the ingestion pipeline's own entry point —
// mirrors "chat_trigger" exactly but fires on a document upload instead of
// a chat message (app.api.workflows's /set-ingestion-handler and
// engine.run_ingestion_workflow); also never runs through a manual "Run".
type NodeKind =
  | 'tool' | 'llm' | 'logic' | 'loop' | 'database' | 'aegis_database' | 'vector' | 'embedding' | 'extract' | 'chunk' | 'chat_trigger'
  | 'document_upload_trigger'
  | 'chat_reply' | 'export_document';

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
  // "chat_reply" kind — the dedicated terminal node that actually sends
  // the reply. includeSkillGuidance folds skill guidance in automatically
  // (default true) instead of needing a separate node
  // (app.core.workflows.engine._run_chat_reply_node).
  includeSkillGuidance?: boolean;
  skillIds?: string[];                     // "chat_reply" kind — skill folders always included, regardless of message match
  // "logic" kind — a generic gate (n8n's "IF" node). field names a key to
  // read off the upstream value (blank uses the upstream value directly);
  // operator/value decide whether the condition holds. Passes the
  // upstream value through unchanged when true, or skips everything
  // downstream of it when false — app.core.workflows.engine._run_logic_node.
  field?: string;
  operator?: 'is_true' | 'is_false' | 'equals' | 'not_equals' | 'contains' | 'matches_regex';
  value?: string;
  // "loop" kind — iterates the chain of nodes wired directly after it once
  // per item of this upstream field.
  itemsField?: string;
  // "database" and "vector" kinds — an installed Marketplace database
  // (see app.db.models.InstalledDatabase), referenced by id rather than a
  // raw file path so any engine in the catalog works the same way.
  databaseId?: number;
  query?: string;                          // "database" kind
  // "aegis_database" kind — table + operation against Aegis's own SQLite
  // data (app.core.aegis_db_browser), not a Marketplace-installed store.
  // Reuses the same `operation` field "vector" uses below — each kind
  // interprets it with its own literal set (matching how e.g.
  // `instruction` is already reused across several kinds).
  table?: string;
  limit?: number;                          // "aegis_database" kind, list only
  operation?: 'upsert' | 'search' | 'list' | 'insert' | 'update' | 'delete';
  embeddingModel?: string;                 // "vector" kind — a Marketplace-downloaded model id, or unset for Aegis's built-in model
  topK?: number;                           // "vector" kind, search only
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
  // "chunk" kind — splits upstream text into overlapping pieces
  chunkSize?: number;
  overlap?: number;
  status?: NodeStatus;
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
  return server
    ? <Plug className={`${cls} text-aegis-text-muted`} />
    : <Wrench className={`${cls} text-aegis-text-muted`} />;
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
  if (data.kind === 'aegis_database') return <DatabaseZap className="w-3 h-3 text-aegis-success flex-shrink-0" />;
  if (data.kind === 'embedding') return <Layers className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
  if (data.kind === 'vector') return <Boxes className="w-3 h-3 text-aegis-primary-light flex-shrink-0" />;
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
    case 'export_document':
      return 'Turns the reply into a file, if requested';
    case 'llm':
      return data.outputFields?.length
        ? `Structured: ${data.outputFields.map(f => f.name).join(', ')}`
        : (data.modelName || 'No model selected');
    case 'chat_reply':
      return 'Sends whatever the upstream "llm" node generates';
    case 'logic':
      return data.field
        ? `${data.field} ${data.operator || 'is_true'}${data.value ? ` "${data.value}"` : ''}`
        : 'Set a field and condition';
    case 'loop':
      return data.itemsField ? `for each "${data.itemsField}"` : 'Set the field to loop over';
    case 'database':
      return data.query ? 'Query configured' : 'No database selected';
    case 'aegis_database':
      return data.table ? `${data.operation || 'list'} · ${data.table}` : 'No table selected';
    case 'embedding':
      return data.embeddingModel ? data.embeddingModel : 'BAAI/bge-base-en-v1.5 (768d, bundled)';
    case 'vector':
      return data.databaseId ? `${data.operation === 'upsert' ? 'Store' : 'Search'} vectors` : 'No vector store selected';
    case 'extract':
      return data.filePath || 'No file path set — or wired in from upstream';
    case 'chunk':
      return `${data.chunkSize || 300} words, ${data.overlap || 50} overlap`;
    default:
      return data.toolName || (data.isAi ? 'AI step — no tool' : 'No tool selected');
  }
}

function WorkflowNode({ data, selected }: { data: NodeData; selected: boolean }) {
  const statusColor =
    data.status === 'running' ? 'border-aegis-primary shadow-[0_0_0_3px_rgba(var(--aegis-primary-rgb,255,100,60),0.15)]' :
    data.status === 'completed' ? 'border-aegis-success' :
    data.status === 'failed' ? 'border-aegis-error' :
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
  const [tools, setTools] = useState<ToolDef[]>([]);
  const [models, setModels] = useState<ModelDef[]>([]);
  const [databases, setDatabases] = useState<InstalledDB[]>([]);
  const [embeddingModels, setEmbeddingModels] = useState<EmbeddingModelDef[]>([]);
  const [extractionEngines, setExtractionEngines] = useState<ExtractionEngineDef[]>([]);
  const [aegisDbTables, setAegisDbTables] = useState<{ name: string; label: string }[]>([]);
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
  const [popover, setPopover] = useState<{ x: number; y: number; flip: boolean } | null>(null);
  const [isChatHandler, setIsChatHandler] = useState(false);
  const [connectingChat, setConnectingChat] = useState(false);
  const [isIngestionHandler, setIsIngestionHandler] = useState(false);
  const [connectingIngestion, setConnectingIngestion] = useState(false);
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
            .map((t: any) => ({ format: t.format, engine_id: t.engine_id, name: t.name, description: t.description }))
        );
      }
    } catch (e) {}
  }, []);

  const fetchAegisDbTables = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables`);
      if (res.ok) {
        const data = await res.json();
        setAegisDbTables((data.tables || []).map((t: any) => ({ name: t.name, label: t.label })));
      }
    } catch (e) {}
  }, []);

  useEffect(() => { fetchWorkflows(); fetchTools(); fetchModels(); fetchDatabases(); fetchEmbeddingModels(); fetchExtractionEngines(); fetchAegisDbTables(); }, [fetchWorkflows, fetchTools, fetchModels, fetchDatabases, fetchEmbeddingModels, fetchExtractionEngines, fetchAegisDbTables]);

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
      } else if (type === 'workflow_run_failed') {
        setRunning(false);
        toast.error(payload.message || 'Workflow run failed.', { duration: 8000 });
      }
    });
  }, [addMessageHandler]);

  const openWorkflow = async (id: number) => {
    const res = await fetch(`${API_BASE}/api/workflows/${id}`);
    if (!res.ok) { toast.error('Could not load that workflow.'); return; }
    const w = await res.json();
    dbIdRef.current = w.id;
    setActiveId(w.id);
    setName(w.name);
    setNodes((w.graph.nodes || []).map((n: Node) => ({
      ...n,
      type: 'workflowNode',
      data: { kind: 'tool', isAi: false, ...n.data, status: 'idle' },
    })));
    setEdges(w.graph.edges || []);
    setSelectedNodeId(null);
    setIsChatHandler(!!w.is_chat_handler);
    setIsIngestionHandler(!!w.is_ingestion_handler);
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
    placeNode({ label: 'Database query', kind: 'database', isAi: false, query: '', staticInputs: {}, status: 'idle' });
  };

  const addAegisDatabaseNode = () => {
    placeNode({ label: 'Aegis Database', kind: 'aegis_database', isAi: false, operation: 'list', staticInputs: {}, status: 'idle' });
  };

  const addVectorNode = () => {
    placeNode({ label: 'Vector store', kind: 'vector', isAi: false, operation: 'search', topK: 5, status: 'idle' });
  };

  const addExtractNode = (format: NonNullable<NodeData['extractorFormat']>) => {
    placeNode({
      label: `Extract ${EXTRACT_FORMAT_LABELS[format]}`, kind: 'extract', isAi: false,
      filePath: '', staticInputs: {}, extractorFormat: format, status: 'idle',
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

  const addExportDocumentNode = () => {
    placeNode({ label: 'Export Reply', kind: 'export_document', isAi: false, status: 'idle' });
  };

  const addEmbeddingNode = () => {
    placeNode({ label: 'Embedding', kind: 'embedding', isAi: false, status: 'idle' });
  };

  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes(nds => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges(eds => applyEdgeChanges(changes, eds)), []);
  const onConnect = useCallback((conn: Connection) => setEdges(eds => addEdge({ ...conn, data: {}, markerEnd: { type: MarkerType.ArrowClosed } }, eds)), []);

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
  // THIS particular node is the reply generator (show skill/memory
  // settings) or a plain judgment-call step elsewhere (don't).
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
  const showChatContext = !search || 'chat'.includes(search) || 'reply'.includes(search) || 'export'.includes(search);
  const showLogic = !search || 'logic'.includes(search) || 'if'.includes(search) || 'gate'.includes(search) || 'condition'.includes(search);
  const showLlm = !search || 'llm'.includes(search) || 'model'.includes(search);
  const showLoop = !search || 'loop'.includes(search);
  const showDatabase = !search || 'database'.includes(search);
  const showVector = !search || 'vector'.includes(search) || 'search'.includes(search);
  const showEmbedding = !search || 'embedding'.includes(search) || 'vector'.includes(search);
  const showExtract = !search || 'extract'.includes(search) || 'document'.includes(search) || 'text'.includes(search);
  const showChunk = !search || 'chunk'.includes(search);

  // ── List view ──────────────────────────────────────────────────────────
  if (activeId === null) {
    return (
      <div className="flex-1 overflow-y-auto bg-aegis-base p-6 font-sans">
        <div className="max-w-3xl mx-auto flex flex-col gap-4">
          <div className="flex items-center justify-between flex-shrink-0">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-aegis-primary flex items-center justify-center text-white shadow-sm">
                <WorkflowIcon className="w-4 h-4" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-aegis-text-primary leading-tight">Workflows</h1>
                <p className="text-xs text-aegis-text-secondary">Design exactly which tool runs at each step — nothing for the model to guess.</p>
              </div>
            </div>
            <button
              onClick={startNew}
              className="flex items-center gap-1.5 px-3.5 py-2 bg-aegis-primary text-white text-xs font-semibold rounded-lg hover:opacity-90 transition-opacity"
            >
              <Plus className="w-3.5 h-3.5" /> New Workflow
            </button>
          </div>

          <div className="bg-aegis-raised rounded-xl border border-aegis-border p-4">
            {workflows.length === 0 ? (
              <div className="text-xs text-aegis-text-muted">No workflows yet — create one to get started.</div>
            ) : (
              <div className="flex flex-col gap-2">
                {workflows.map(w => (
                  <button
                    key={w.id}
                    onClick={() => openWorkflow(w.id)}
                    className="text-left flex items-center justify-between gap-3 bg-aegis-overlay rounded-lg border border-aegis-border px-3.5 py-2.5 hover:border-aegis-primary transition-colors"
                  >
                    <span className="text-xs font-semibold text-aegis-text-primary">{w.name}</span>
                    <span className="text-[10px] text-aegis-text-muted">{w.updated_at ? new Date(w.updated_at).toLocaleString() : ''}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
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
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={(event, n) => {
            setSelectedNodeId(n.id);
            setPaletteOpen(false);
            const rect = canvasWrapperRef.current?.getBoundingClientRect();
            if (!rect) return;
            const x = event.clientX - rect.left;
            const y = event.clientY - rect.top;
            setPopover({ x, y: Math.max(8, Math.min(y - 20, rect.height - 360)), flip: x > rect.width - 300 });
          }}
          onPaneClick={() => { setSelectedNodeId(null); setPopover(null); setPaletteOpen(false); }}
          fitView
          colorMode="system"
        >
          <Background />
          <Controls />
        </ReactFlow>

        {/* Floating add-node trigger */}
        <button
          onClick={() => { setPaletteOpen(o => !o); setSelectedNodeId(null); setPopover(null); }}
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

        {/* Node config — a compact popover anchored next to the clicked node */}
        {selectedNode && popover && (
          <div
            className="absolute z-20 w-64"
            style={popover.flip ? { right: `${Math.max(8, canvasWrapperRef.current!.getBoundingClientRect().width - popover.x + 12)}px`, top: popover.y } : { left: popover.x + 12, top: popover.y }}
          >
            <div className="bg-aegis-raised border border-aegis-border rounded-xl shadow-xl p-3 max-h-[70vh] overflow-y-auto">
              <NodeConfigPanel
                data={selectedNode.data as NodeData}
                isReplyGenerator={replyGenerationIds.includes(selectedNode.id)}
                tools={tools}
                models={models}
                databases={databases}
                embeddingModels={embeddingModels}
                extractionEngines={extractionEngines}
                aegisDbTables={aegisDbTables}
                onOpenAegisDbBrowser={() => setAegisDbBrowserOpen(true)}
                selectedTool={selectedTool}
                onChange={updateSelectedNode}
                onDelete={() => {
                  setNodes(prev => prev.filter(n => n.id !== selectedNodeId));
                  setEdges(prev => prev.filter(e => e.source !== selectedNodeId && e.target !== selectedNodeId));
                  setSelectedNodeId(null);
                  setPopover(null);
                }}
              />
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
          {((showTrigger && !hasChatTrigger) || (showIngestionTrigger && !hasIngestionTrigger)) && (
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
                description="A reasoning step bound to one model — freeform text, or a structured JSON judgment call (e.g. a yes/no decision) when configured with output fields. Wire one directly into a Send Reply node (nothing else after it) to make it the reply generator — real streaming, skill guidance, memory settings"
                onClick={addLlmNode}
              />
            </section>
          )}

          {/* MCP */}
          {Object.keys(mcpToolsByServer).length > 0 && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">MCP</div>
              <div className="flex flex-col gap-2.5">
                {Object.entries(mcpToolsByServer).map(([server, serverTools]) => (
                  <div key={server}>
                    <div className="flex items-center gap-1.5 px-1 mb-1">
                      <Plug className="w-3 h-3 text-aegis-text-muted flex-shrink-0" />
                      <span className="text-[10px] font-semibold text-aegis-text-secondary truncate">{server}</span>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      {serverTools.map(t => (
                        <PaletteRow
                          key={t.name}
                          icon={toolIconByName(t.name, t.server)}
                          title={t.name}
                          description={t.description}
                          onClick={() => addToolNode(t)}
                        />
                      ))}
                    </div>
                  </div>
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
              <div className="flex flex-col gap-0.5">
                <PaletteRow
                  icon={<DatabaseIcon className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />}
                  title="Database query"
                  description="Runs a SQL query against a relational database installed from the Marketplace"
                  onClick={addDatabaseNode}
                />
                <PaletteRow
                  icon={<DatabaseZap className="w-4 h-4 text-aegis-success flex-shrink-0" />}
                  title="Aegis Database"
                  description="Browse/edit Aegis's own data — chat history, workflows, and more — in a safe, credential-free set of tables, or create your own"
                  onClick={addAegisDatabaseNode}
                />
              </div>
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

          {/* Document extraction — one row per format, each its own icon;
              the engine always auto-detects the real extractor from the
              file's own extension, so these presets only affect the
              label/icon shown, never runtime behavior. */}
          {showExtract && (
            <section>
              <div className="text-[10px] font-bold text-aegis-text-muted uppercase tracking-wider mb-1.5">Document extraction</div>
              <div className="flex flex-col gap-0.5">
                <PaletteRow icon={<FileText className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />} title="Extract PDF" description="Pulls text out of a PDF file" onClick={() => addExtractNode('pdf')} />
                <PaletteRow icon={<FileType2 className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />} title="Extract Word (DOCX)" description="Pulls text out of a Word document" onClick={() => addExtractNode('docx')} />
                <PaletteRow icon={<Presentation className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />} title="Extract PowerPoint (PPTX)" description="Pulls text out of a slide deck" onClick={() => addExtractNode('pptx')} />
                <PaletteRow icon={<FileSpreadsheet className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />} title="Extract Excel (XLSX)" description="Pulls text out of a spreadsheet" onClick={() => addExtractNode('xlsx')} />
                <PaletteRow icon={<FileText className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />} title="Extract Markdown/TXT/CSV" description="Pulls text out of a plain-text file" onClick={() => addExtractNode('text')} />
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

function NodeConfigPanel({
  data, isReplyGenerator, tools, models, databases, embeddingModels, extractionEngines, aegisDbTables, onOpenAegisDbBrowser, selectedTool, onChange, onDelete,
}: {
  data: NodeData;
  // Only meaningful for kind "llm" — true when THIS node is the one whose
  // sole outgoing edge leads to a "chat_reply" node (see WorkflowsView's
  // replyGenerationIds), matching engine.py's run_chat_workflow detection
  // of which "llm" node actually generates+streams the reply vs. a plain
  // judgment-call step elsewhere in the same graph.
  isReplyGenerator: boolean;
  tools: ToolDef[];
  models: ModelDef[];
  databases: InstalledDB[];
  embeddingModels: EmbeddingModelDef[];
  extractionEngines: ExtractionEngineDef[];
  aegisDbTables: { name: string; label: string }[];
  onOpenAegisDbBrowser: () => void;
  selectedTool?: ToolDef;
  onChange: (patch: Partial<NodeData>) => void;
  onDelete: () => void;
}) {
  const relationalDatabases = databases.filter(d => d.category === 'relational');
  const vectorDatabases = databases.filter(d => d.category === 'vector').sort((a, b) => (b.is_builtin ? 1 : 0) - (a.is_builtin ? 1 : 0));
  const header = (
    <div className="flex items-center justify-between">
      <h3 className="text-xs font-bold text-aegis-text-primary">Step settings</h3>
      <button onClick={onDelete} className="p-1 rounded hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error">
        <Trash2 className="w-3.5 h-3.5" />
      </button>
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

  if (data.kind === 'chat_trigger') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Fires when you send a message in the normal chat window — only if this workflow is connected via the toolbar's "Connect to chat" button. No configuration needed; needs exactly one Send Reply node fed by exactly one "llm" node (that llm node is the real reply generator — model, prompt, and skill/memory settings live there), and exactly one final step overall.
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
          Fires when you upload a document — only if this workflow is connected via the toolbar's "Connect to uploads" button. No configuration needed; needs exactly one final step overall. Typically feeds an Extract → Chunk → Embedding → Vector store chain.
        </p>
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
        </div>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Prompt</label>
          <textarea
            value={data.instruction || ''}
            onChange={e => onChange({ instruction: e.target.value })}
            rows={8}
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
        {isReplyGenerator && (
          <>
            <p className="text-[10px] text-aegis-primary-light leading-relaxed border-t border-aegis-border pt-2">
              This is the reply generator — its sole outgoing edge leads to a Chat Reply node, so it streams live to the user instead of running as a one-shot call.
            </p>
            <label className="flex items-center gap-2 text-xs text-aegis-text-secondary">
              <input
                type="checkbox"
                checked={data.includeSkillGuidance ?? true}
                onChange={e => onChange({ includeSkillGuidance: e.target.checked })}
              />
              Auto-match relevant skills to the message
            </label>
            <div>
              <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Always include these skills</label>
              <SkillsPicker selected={data.skillIds || []} onChange={ids => onChange({ skillIds: ids })} />
            </div>
            <div className="pt-1 border-t border-aegis-border">
              <MemorySettingsPanel />
            </div>
          </>
        )}
      </div>
    );
  }

  if (data.kind === 'logic') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Passes the upstream value through unchanged when the condition holds; otherwise everything wired after this node is skipped for this run.
        </p>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Field</label>
          <input
            value={data.field || ''}
            onChange={e => onChange({ field: e.target.value })}
            placeholder="e.g. needs_search — blank uses the upstream value directly"
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />
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
          </select>
        </div>
        {!['is_true', 'is_false'].includes(data.operator || 'is_true') && (
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
          No configuration — model, prompt, and skill/memory settings all live on the "llm" node wired directly into this one (with nothing else wired after that llm node). This node just marks where the reply gets sent; exactly one required to connect this workflow to chat.
        </p>
      </div>
    );
  }

  if (data.kind === 'loop') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Items field</label>
          <input
            value={data.itemsField || ''}
            onChange={e => onChange({ itemsField: e.target.value })}
            placeholder="e.g. pending_attachments — blank loops the whole input"
            title="Wire a chain of steps directly after this loop — the whole chain runs once per item."
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />
        </div>
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">
          Runs the whole chain of steps wired directly after this node once per item (e.g. Extract → Chunk → Embedding → Vector, once per attachment) — a step that's also fed from somewhere else ends the chain and runs once, after every item finishes.
        </p>
      </div>
    );
  }

  if (data.kind === 'database') {
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
            {relationalDatabases.map(d => <option key={d.id} value={d.id}>{d.name} ({d.engine_id})</option>)}
          </select>
          {relationalDatabases.length === 0 && (
            <p className="text-[10px] text-aegis-text-muted mt-1">Install one from the Marketplace's Databases category.</p>
          )}
        </div>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Query</label>
          <textarea
            value={data.query || ''}
            onChange={e => onChange({ query: e.target.value })}
            rows={4}
            placeholder="SELECT * FROM table WHERE id = :id"
            spellCheck={false}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs font-mono text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary resize-y"
          />
        </div>
        <ParamsEditor
          label="Query parameters"
          values={data.staticInputs || {}}
          onChange={v => onChange({ staticInputs: v })}
        />
      </div>
    );
  }

  if (data.kind === 'aegis_database') {
    const dbOp = (data.operation as 'list' | 'insert' | 'update' | 'delete') || 'list';
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
        <button
          onClick={onOpenAegisDbBrowser}
          className="flex items-center justify-center gap-1.5 px-2.5 py-1.5 bg-aegis-overlay border border-aegis-border rounded-md text-xs font-semibold text-aegis-text-primary hover:bg-aegis-base transition-colors"
        >
          Browse tables…
        </button>
        <div>
          <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Table</label>
          <select
            value={data.table || ''}
            onChange={e => onChange({ table: e.target.value || undefined })}
            className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          >
            <option value="">{aegisDbTables.length === 0 ? 'Loading…' : 'Select a table…'}</option>
            {aegisDbTables.map(t => <option key={t.name} value={t.name}>{t.label}</option>)}
          </select>
        </div>
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
          </select>
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
      </div>
    );
  }

  if (data.kind === 'vector') {
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
                {d.is_builtin ? '⭐ ' : ''}{d.name}{!d.is_builtin ? ` (${d.engine_id}${d.config?.embedding_dim ? `, ${d.config.embedding_dim}d` : ''})` : ''}
              </option>
            ))}
          </select>
          {vectorDatabases.length === 0 && (
            <p className="text-[10px] text-aegis-text-muted mt-1">Install one from the Marketplace's Databases category.</p>
          )}
          {data.operation === 'search' && (() => {
            const selected = vectorDatabases.find(d => d.id === data.databaseId);
            if (!selected) return null;
            return selected.is_builtin ? (
              <p className="text-[10px] text-aegis-text-muted mt-1 leading-relaxed">
                Qdrant, bundled — dense + BM25 fused search with cross-encoder reranking, only available on this store.
              </p>
            ) : (
              <p className="text-[10px] text-aegis-text-muted mt-1 leading-relaxed">
                Plain dense search only, no hybrid fusion or rerank (that's exclusive to the bundled Qdrant store above).
              </p>
            );
          })()}
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
            <div>
              <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Embedding model</label>
              <select
                value={data.embeddingModel || ''}
                onChange={e => onChange({ embeddingModel: e.target.value || undefined })}
                title="Must match the dimension the vector store was created with"
                className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
              >
                <option value="">Aegis's built-in model (768d)</option>
                {embeddingModels.map(m => <option key={m.model_id} value={m.model_id}>{m.display_name} ({m.dim}d)</option>)}
              </select>
            </div>
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
            {vectorDatabases.find(d => d.id === data.databaseId)?.is_builtin && (
              <p className="text-[10px] text-aegis-error leading-relaxed">
                Aegis's built-in store is populated automatically on upload — it refuses direct storing here. Pick a Marketplace-installed store instead.
              </p>
            )}
          </>
        )}
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
            <option value="">Aegis's built-in model (768d)</option>
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
    // its one format up front — show just that format's engine choice. A
    // node fed by an upstream trigger (e.g. "On document upload") doesn't
    // know the format until a real file arrives, so it shows every format
    // that has more than one engine to choose from, each stored under its
    // own key in enginePerFormat (see engine.py's _run_extract_node).
    const formatsToShow = data.extractorFormat
      ? [data.extractorFormat]
      : Array.from(new Set(extractionEngines.map(e => e.format))).filter(
          fmt => extractionEngines.filter(e => e.format === fmt).length > 1
        );
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
          if (formatEngines.length <= 1) return null;
          return (
            <div key={fmt}>
              <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">{EXTRACT_FORMAT_LABELS[fmt as keyof typeof EXTRACT_FORMAT_LABELS] || fmt} extractor</label>
              <select
                value={enginePerFormat[fmt] || ''}
                onChange={e => {
                  const next = { ...enginePerFormat };
                  if (e.target.value) next[fmt] = e.target.value; else delete next[fmt];
                  onChange({ enginePerFormat: next });
                }}
                className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
              >
                <option value="">Default</option>
                {formatEngines.map(e => <option key={e.engine_id} value={e.engine_id}>{e.name}</option>)}
              </select>
              <p className="text-[10px] text-aegis-text-muted mt-1">
                {formatEngines.find(e => e.engine_id === enginePerFormat[fmt])?.description || `Aegis's default extractor for ${EXTRACT_FORMAT_LABELS[fmt as keyof typeof EXTRACT_FORMAT_LABELS] || fmt}.`}
              </p>
            </div>
          );
        })}
        <p className="text-[10px] text-aegis-text-muted leading-relaxed">Supports PDF, DOCX, PPTX, Markdown, TXT/CSV, and XLSX.</p>
      </div>
    );
  }

  if (data.kind === 'chunk') {
    return (
      <div className="flex flex-col gap-3">
        {header}
        {labelField}
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
          <Plug className="w-3 h-3" /> MCP server: <span className="font-semibold text-aegis-text-secondary">{data.server}</span>
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
            const tool = tools.find(t => t.name === e.target.value);
            onChange({ toolName: tool?.name || undefined, server: tool?.server ?? null, staticInputs: {} });
          }}
          title={selectedTool?.description}
          className="w-full mt-1 bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
        >
          <option value="">Select a tool…</option>
          {tools.map(t => <option key={t.name} value={t.name}>{t.server ? `${t.server} — ${t.name}` : t.name}</option>)}
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
                  <label className="text-[10px] text-aegis-text-muted">{key}</label>
                  <input
                    value={data.staticInputs?.[key] || ''}
                    onChange={e => onChange({ staticInputs: { ...(data.staticInputs || {}), [key]: e.target.value } })}
                    title={meta.description}
                    className="w-full bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  />
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

interface SkillDef {
  name: string;
  description?: string;
  folder: string;
}

// Explicit "always include" picks for the Reply node — separate from the
// per-conversation skill toggle the chat UI already has (that one only
// gates whether match_skills considers a skill for a given message at
// all). A pick here bypasses that toggle entirely (see engine.py's
// _run_skills_context_node) since it's a stronger, workflow-level signal.
function SkillsPicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const [skills, setSkills] = useState<SkillDef[] | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/skills`);
        if (res.ok) setSkills((await res.json()).skills || []);
      } catch (e) {
        setSkills([]);
      }
    })();
  }, []);

  if (skills === null) return <p className="text-[10px] text-aegis-text-muted mt-1">Loading…</p>;
  if (skills.length === 0) return <p className="text-[10px] text-aegis-text-muted mt-1">No skills installed yet — add one from the Marketplace.</p>;

  const toggle = (folder: string) => {
    onChange(selected.includes(folder) ? selected.filter(f => f !== folder) : [...selected, folder]);
  };

  return (
    <div className="flex flex-col gap-1 mt-1 max-h-32 overflow-y-auto">
      {skills.map(s => (
        <label key={s.folder} className="flex items-start gap-2 text-xs text-aegis-text-secondary">
          <input type="checkbox" className="mt-0.5" checked={selected.includes(s.folder)} onChange={() => toggle(s.folder)} />
          <span>
            <span className="font-semibold text-aegis-text-primary">{s.name}</span>
            {s.description && <span className="block text-[10px] text-aegis-text-muted leading-snug">{s.description}</span>}
          </span>
        </label>
      ))}
    </div>
  );
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
