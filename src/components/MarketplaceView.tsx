'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Store, Download, CheckCircle2, Loader2, Trash2, XCircle, Search, ChevronRight, Plus } from 'lucide-react';
import { ServiceLogo } from '../lib/serviceIcons';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface Tool {
  id: string;
  name: string;
  description: string;
  category: string;
  size_estimate: string;
  status_endpoint: string;
  installed: boolean;
  format?: string;      // Extraction-category tools only — groups engines per document format
  engine_id?: string;
}

const EXTRACTION_FORMAT_LABELS: Record<string, string> = {
  pdf: 'PDF', docx: 'Word', pptx: 'PowerPoint', xlsx: 'Excel', text: 'Markdown / TXT / CSV',
};

interface DBEngine {
  id: string;
  name: string;
  category: 'relational' | 'vector';
  description: string;
  requires_download: boolean;
  placeholder_syntax: string;
}

interface InstalledDB {
  id: number;
  name: string;
  engine_id: string;
  category: 'relational' | 'vector';
  status: 'installing' | 'ready' | 'failed';
  error_message?: string;
  config: Record<string, any>;
  created_at?: string;
  is_builtin?: boolean;
}

interface EmbeddingModelCatalogEntry {
  id: string;
  display_name: string;
  dim: number;
  size_gb: number;
  description: string;
}

interface InstalledEmbeddingModel {
  id: number;
  model_id: string;
  display_name: string;
  dim: number;
  size_gb: number;
  status: 'downloading' | 'downloaded' | 'failed';
  error_message?: string;
  created_at?: string;
}

// A connected MCP tool usable as a custom document-extraction engine — how
// a user integrates an extractor Aegis doesn't bundle itself (a
// specialized OCR service, a company-internal parser, ...). Connecting
// happens in Connectors, not here; this just surfaces what's already
// connected and offers a shortcut there when nothing is yet — mirrors
// app.core.extraction_engines.list_mcp_candidates, the same data source
// WorkflowsView's Extract node picker reads.
interface McpExtractionToolDef {
  engine_id: string;
  name: string;
  description: string;
  server: string;
  tool: string;
}

interface RerankerCatalogEntry {
  id: string;
  display_name: string;
  size_gb: number;
  description: string;
}

interface InstalledReranker {
  id: number;
  model_id: string;
  display_name: string;
  size_gb: number;
  status: 'downloading' | 'downloaded' | 'failed';
  error_message?: string;
  created_at?: string;
}

type ToolStatus = 'not_installed' | 'installing' | 'ready' | 'failed';

function ToolCard({ tool, onInstalled }: { tool: Tool; onInstalled: () => void }) {
  const [status, setStatus] = useState<ToolStatus>(tool.installed ? 'ready' : 'not_installed');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setStatus(tool.installed ? 'ready' : 'not_installed');
  }, [tool.installed]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const handleInstall = async () => {
    setStatus('installing');
    try {
      await fetch(`${API_BASE}/api/marketplace/tools/${tool.id}/install`, { method: 'POST' });
    } catch {
      setStatus('failed');
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}${tool.status_endpoint}`);
        const json = await res.json();
        if (json.status === 'ready') {
          if (pollRef.current) clearInterval(pollRef.current);
          setStatus('ready');
          onInstalled();
        } else if (json.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current);
          setStatus('failed');
        }
      } catch {
        // transient — keep polling
      }
    }, 2500);
  };

  return (
    <div className="flex items-start gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-4 hover:border-aegis-primary/40 transition-colors">
      <ServiceLogo serviceKey={tool.format ? `extract_${tool.format}` : tool.id} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-[13px] font-semibold text-aegis-text-primary">{tool.name}</p>
          <span className="text-[11px] text-aegis-text-muted">· {tool.size_estimate}</span>
        </div>
        <p className="text-[13px] text-aegis-text-secondary mt-0.5">{tool.description}</p>
      </div>
      <div className="flex-shrink-0">
        {status === 'ready' && (
          <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-success px-3 py-1.5">
            <CheckCircle2 className="w-3.5 h-3.5" /> Installed
          </span>
        )}
        {status === 'not_installed' && (
          <button
            onClick={handleInstall}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors"
          >
            <Download className="w-3.5 h-3.5" /> Install
          </button>
        )}
        {status === 'installing' && (
          <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-warning px-3 py-1.5">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Installing...
          </span>
        )}
        {status === 'failed' && (
          <button
            onClick={handleInstall}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-error px-3.5 py-1.5 rounded-lg border border-aegis-error/30 hover:bg-aegis-error/10 transition-colors"
          >
            <XCircle className="w-3.5 h-3.5" /> Retry
          </button>
        )}
      </div>
    </div>
  );
}


function DatabaseEngineRow({ engine, onInstalled }: { engine: DBEngine; onInstalled: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [schemaSql, setSchemaSql] = useState('');
  const [embeddingDim, setEmbeddingDim] = useState(768);
  const [distance, setDistance] = useState<'cosine' | 'euclidean' | 'dot'>('cosine');
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleInstall = async () => {
    if (!name.trim()) { setError('Give it a name.'); return; }
    if (engine.category === 'relational' && !schemaSql.trim()) { setError('Paste at least one CREATE TABLE statement.'); return; }
    setInstalling(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/databases/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name, engine_id: engine.id,
          schema_sql: engine.category === 'relational' ? schemaSql : undefined,
          embedding_dim: engine.category === 'vector' ? embeddingDim : undefined,
          distance: engine.category === 'vector' ? distance : undefined,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Install failed.');
      }
      setOpen(false);
      setName('');
      setSchemaSql('');
      onInstalled();
    } catch (e: any) {
      setError(e.message || 'Could not reach the backend.');
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div className={`bg-aegis-raised border border-aegis-border rounded-xl shadow-sm px-5 py-4 hover:border-aegis-primary/40 hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 ${open ? 'md:col-span-2' : ''}`}>
      <div className="flex items-start gap-4">
        <ServiceLogo serviceKey={engine.id} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-[13px] font-semibold text-aegis-text-primary">{engine.name}</p>
            {!engine.requires_download && (
              <span className="text-[10px] text-aegis-text-muted">· bundled, no download</span>
            )}
          </div>
          <p className="text-[13px] text-aegis-text-secondary mt-0.5">{engine.description}</p>
        </div>
        <button
          onClick={() => setOpen(o => !o)}
          className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors"
        >
          <Download className="w-3.5 h-3.5" /> {open ? 'Cancel' : 'Install…'}
        </button>
      </div>

      {open && (
        <div className="mt-4 pl-14 flex flex-col gap-2.5">
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder={`e.g. "${engine.category === 'relational' ? 'My App Data' : 'My Document Embeddings'}"`}
            className="w-full bg-aegis-overlay border border-aegis-border rounded-lg px-3 py-2 text-[13px] text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />

          {engine.category === 'relational' ? (
            <textarea
              value={schemaSql}
              onChange={e => setSchemaSql(e.target.value)}
              rows={5}
              spellCheck={false}
              placeholder={`CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  name TEXT\n);`}
              className="w-full bg-aegis-overlay border border-aegis-border rounded-lg px-3 py-2 text-[12px] font-mono text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary resize-y"
            />
          ) : (
            <div className="flex items-center gap-2.5">
              <div className="flex items-center gap-1.5">
                <label className="text-[11px] text-aegis-text-muted">Dimensions</label>
                <input
                  type="number"
                  value={embeddingDim}
                  onChange={e => setEmbeddingDim(parseInt(e.target.value) || 768)}
                  className="w-20 bg-aegis-overlay border border-aegis-border rounded-lg px-2 py-1.5 text-[12px] text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[11px] text-aegis-text-muted">Distance</label>
                <select
                  value={distance}
                  onChange={e => setDistance(e.target.value as any)}
                  className="bg-aegis-overlay border border-aegis-border rounded-lg px-2 py-1.5 text-[12px] text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                >
                  <option value="cosine">Cosine</option>
                  <option value="euclidean">Euclidean</option>
                  <option value="dot">Dot product</option>
                </select>
              </div>
            </div>
          )}

          {error && <p className="text-[12px] text-aegis-error">{error}</p>}

          <div>
            <button
              onClick={handleInstall}
              disabled={installing}
              className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
            >
              {installing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
              {installing ? 'Installing…' : 'Create'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function InstalledDatabaseRow({ db, onRemoved }: { db: InstalledDB; onRemoved: () => void }) {
  const [busy, setBusy] = useState(false);

  const handleDelete = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/marketplace/databases/${db.id}`, { method: 'DELETE' });
      onRemoved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
      <ServiceLogo serviceKey={db.engine_id} size="sm" />
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-aegis-text-primary truncate">{db.name}</p>
        <p className="text-[11px] text-aegis-text-muted">{db.engine_id} · {db.category}{db.status === 'failed' && db.error_message ? ` · ${db.error_message}` : ''}</p>
      </div>
      <div className="flex-shrink-0 flex items-center gap-2">
        {db.status === 'ready' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Ready</span>}
        {db.status === 'installing' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-warning"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Installing…</span>}
        {db.status === 'failed' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-error"><XCircle className="w-3.5 h-3.5" /> Failed</span>}
        {db.is_builtin ? (
          <span className="text-[11px] text-aegis-text-muted px-1.5">Built-in</span>
        ) : (
          <button onClick={handleDelete} disabled={busy} className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>
    </div>
  );
}

// fastembed's own catalog descriptions are written for ML practitioners
// ("Unimodal (text), English, 512 input tokens truncation, Prefixes for
// queries/documents: not so necessary, 2023 year.") — pulling just the
// language out into a short pill (and keeping the rest as a hover tooltip,
// not always-visible body text) is the difference between a browsable list
// and a wall of jargon nobody outside ML actually needs to read up front.
function parseEmbeddingLanguage(description: string): string {
  const multilingual = description.match(/Multilingual\s*\(([^)]+)\)/i);
  if (multilingual) return `Multilingual (${multilingual[1]})`;
  const match = description.match(/\b(English|Chinese|German|Spanish|French|Japanese|Korean|Russian|Arabic)\b/i);
  return match ? match[1] : 'Text';
}

// The model Aegis's own bundled default (app.core.rag.processor.get_dense_model)
// already uses — surfaced as "Recommended" so a first-time visitor isn't
// stuck picking cold between 30 near-identical options.
const AEGIS_DEFAULT_EMBEDDING_MODEL = 'BAAI/bge-base-en-v1.5';

function EmbeddingModelCatalogRow({ model, onDownloaded }: { model: EmbeddingModelCatalogEntry; onDownloaded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/embeddings/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: model.id }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Download failed.');
      }
      onDownloaded();
    } catch (e: any) {
      setError(e.message || 'Could not reach the backend.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-start gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-4 hover:border-aegis-primary/40 transition-colors">
      <ServiceLogo serviceKey="embedding" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-[13px] font-semibold text-aegis-text-primary">{model.display_name}</p>
          <span className="text-[11px] text-aegis-text-muted">· {model.dim}d · {model.size_gb} GB</span>
        </div>
        <p className="text-[13px] text-aegis-text-secondary mt-0.5">{model.description}</p>
        {error && <p className="text-[12px] text-aegis-error mt-1">{error}</p>}
      </div>
      <button
        onClick={handleDownload}
        disabled={busy}
        className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
      >
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
        {busy ? 'Starting…' : 'Download'}
      </button>
    </div>
  );
}

function InstalledEmbeddingModelRow({ model, onRemoved }: { model: InstalledEmbeddingModel; onRemoved: () => void }) {
  const [busy, setBusy] = useState(false);

  const handleDelete = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/marketplace/embeddings/${model.id}`, { method: 'DELETE' });
      onRemoved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
      <ServiceLogo serviceKey="embedding" size="sm" />
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-aegis-text-primary truncate">{model.display_name}</p>
        <p className="text-[11px] text-aegis-text-muted">{model.dim}d · {model.size_gb} GB{model.status === 'failed' && model.error_message ? ` · ${model.error_message}` : ''}</p>
      </div>
      <div className="flex-shrink-0 flex items-center gap-2">
        {model.status === 'downloaded' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Ready</span>}
        {model.status === 'downloading' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-warning"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Downloading…</span>}
        {model.status === 'failed' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-error"><XCircle className="w-3.5 h-3.5" /> Failed</span>}
        <button onClick={handleDelete} disabled={busy} className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        </button>
      </div>
    </div>
  );
}

function RerankerCatalogRow({ model, onDownloaded }: { model: RerankerCatalogEntry; onDownloaded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/rerankers/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: model.id }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Download failed.');
      }
      onDownloaded();
    } catch (e: any) {
      setError(e.message || 'Could not reach the backend.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-start gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-4 hover:border-aegis-primary/40 transition-colors">
      <ServiceLogo serviceKey="reranker" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-[13px] font-semibold text-aegis-text-primary">{model.display_name}</p>
          <span className="text-[11px] text-aegis-text-muted">· {model.size_gb} GB</span>
        </div>
        <p className="text-[13px] text-aegis-text-secondary mt-0.5">{model.description}</p>
        {error && <p className="text-[12px] text-aegis-error mt-1">{error}</p>}
      </div>
      <button
        onClick={handleDownload}
        disabled={busy}
        className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
      >
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
        {busy ? 'Starting…' : 'Download'}
      </button>
    </div>
  );
}

function InstalledRerankerRow({ model, onRemoved }: { model: InstalledReranker; onRemoved: () => void }) {
  const [busy, setBusy] = useState(false);

  const handleDelete = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/marketplace/rerankers/${model.id}`, { method: 'DELETE' });
      onRemoved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
      <ServiceLogo serviceKey="reranker" size="sm" />
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-aegis-text-primary truncate">{model.display_name}</p>
        <p className="text-[11px] text-aegis-text-muted">{model.size_gb} GB{model.status === 'failed' && model.error_message ? ` · ${model.error_message}` : ''}</p>
      </div>
      <div className="flex-shrink-0 flex items-center gap-2">
        {model.status === 'downloaded' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Ready</span>}
        {model.status === 'downloading' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-warning"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Downloading…</span>}
        {model.status === 'failed' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-error"><XCircle className="w-3.5 h-3.5" /> Failed</span>}
        <button onClick={handleDelete} disabled={busy} className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        </button>
      </div>
    </div>
  );
}

interface HFModelResult {
  id: string;
  author?: string;
  downloads?: number;
  likes?: number;
  tags?: string[];
}

// Shared by both the Embedding Models and Rerankers sections: live-search
// Hugging Face beyond whichever curated/fixed list that category ships
// with, and install any result (or a raw repo id the user already knows
// and just pastes in — the search only helps discovery, it's not a gate).
function HuggingFaceModelSearch({
  searchEndpoint, downloadEndpoint, alreadyInstalledIds, onInstalled, resultHint,
}: {
  searchEndpoint: string;
  downloadEndpoint: string;
  alreadyInstalledIds: Set<string>;
  onInstalled: () => void;
  resultHint: (tags: string[] | undefined) => string | null;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<HFModelResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [customId, setCustomId] = useState('');
  const [installing, setInstalling] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const runSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setSearched(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}${searchEndpoint}?q=${encodeURIComponent(query.trim())}&limit=20`);
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setResults(data.models || []);
      } else {
        setError(data.detail || 'Search failed.');
        setResults([]);
      }
    } catch (e) {
      setError('Could not reach the backend.');
    } finally {
      setSearching(false);
    }
  };

  const install = async (modelId: string) => {
    setInstalling(prev => ({ ...prev, [modelId]: true }));
    setError(null);
    try {
      const res = await fetch(`${API_BASE}${downloadEndpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || `Failed to install '${modelId}'.`);
        return;
      }
      onInstalled();
    } catch (e) {
      setError('Could not reach the backend.');
    } finally {
      setInstalling(prev => { const n = { ...prev }; delete n[modelId]; return n; });
    }
  };

  return (
    <div className="bg-aegis-raised rounded-xl border border-aegis-border shadow-sm overflow-hidden mb-4">
      <button onClick={() => setOpen(v => !v)} className="w-full flex items-center gap-2.5 px-5 py-3 text-left">
        <Search className="w-4 h-4 text-aegis-text-muted flex-shrink-0" />
        <p className="flex-1 min-w-0 text-[13px] text-aegis-text-secondary">Search Hugging Face for more models.</p>
        <ChevronRight className={`w-4 h-4 text-aegis-text-muted flex-shrink-0 transition-transform duration-200 ${open ? 'rotate-90' : ''}`} />
      </button>

      {open && (
        <div className="px-5 pb-4 flex flex-col gap-3">
          <div className="flex gap-2">
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') runSearch(); }}
              placeholder="Search Hugging Face…"
              className="flex-1 bg-aegis-overlay border border-aegis-border rounded-lg px-3 py-2 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
            <button
              onClick={runSearch}
              disabled={searching || !query.trim()}
              className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
            >
              {searching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
              Search
            </button>
          </div>

          {error && <p className="text-[12px] text-aegis-error">{error}</p>}
          {searched && !searching && results.length === 0 && !error && (
            <p className="text-[12px] text-aegis-text-muted">No models found for that search.</p>
          )}

          {results.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {results.map(r => {
                const already = alreadyInstalledIds.has(r.id);
                const hint = resultHint(r.tags);
                return (
                  <div key={r.id} className="flex items-center justify-between gap-3 bg-aegis-overlay rounded-lg px-3 py-2">
                    <div className="min-w-0">
                      <p className="text-[12px] font-medium text-aegis-text-primary truncate">{r.id}</p>
                      <p className="text-[10px] text-aegis-text-muted">
                        {(r.downloads ?? 0).toLocaleString()} downloads{hint ? ` · ${hint}` : ''}
                      </p>
                    </div>
                    {already ? (
                      <span className="flex-shrink-0 inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Installed</span>
                    ) : (
                      <button
                        onClick={() => install(r.id)}
                        disabled={!!installing[r.id]}
                        className="flex-shrink-0 inline-flex items-center gap-1.5 text-[11px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-2.5 py-1 rounded-md transition-colors disabled:opacity-50"
                      >
                        {installing[r.id] ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                        Install
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div className="flex gap-2 pt-1 border-t border-aegis-border">
            <input
              value={customId}
              onChange={e => setCustomId(e.target.value)}
              placeholder="…or paste a repo id you already know, e.g. org/model-name"
              className="flex-1 mt-2 bg-aegis-overlay border border-aegis-border rounded-lg px-3 py-2 text-xs font-mono text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
            <button
              onClick={() => { if (customId.trim()) { install(customId.trim()); setCustomId(''); } }}
              disabled={!customId.trim() || !!installing[customId.trim()]}
              className="flex-shrink-0 mt-2 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
            >
              <Plus className="w-3.5 h-3.5" /> Install
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function McpExtractorRow({ tool }: { tool: McpExtractionToolDef }) {
  return (
    <div className="flex items-start gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-4">
      <ServiceLogo serviceKey={tool.server} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-[13px] font-semibold text-aegis-text-primary">{tool.name}</p>
        </div>
        <p className="text-[13px] text-aegis-text-secondary mt-0.5 line-clamp-2">{tool.description}</p>
      </div>
      <span className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-success px-3 py-1.5">
        <CheckCircle2 className="w-3.5 h-3.5" /> Connected
      </span>
    </div>
  );
}

export default function MarketplaceView({ onOpenConnectors }: { onOpenConnectors?: () => void }) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [dbEngines, setDbEngines] = useState<DBEngine[]>([]);
  const [installedDbs, setInstalledDbs] = useState<InstalledDB[]>([]);
  const [embeddingCatalog, setEmbeddingCatalog] = useState<EmbeddingModelCatalogEntry[]>([]);
  const [installedEmbeddings, setInstalledEmbeddings] = useState<InstalledEmbeddingModel[]>([]);
  const [embeddingSearch, setEmbeddingSearch] = useState('');
  const [rerankerCatalog, setRerankerCatalog] = useState<RerankerCatalogEntry[]>([]);
  const [installedRerankers, setInstalledRerankers] = useState<InstalledReranker[]>([]);
  const [mcpExtractionTools, setMcpExtractionTools] = useState<McpExtractionToolDef[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    try {
      const [toolsRes, dbCatalogRes, dbInstalledRes, embedCatalogRes, embedInstalledRes, rerankCatalogRes, rerankInstalledRes, mcpExtractionRes] = await Promise.all([
        fetch(`${API_BASE}/api/marketplace/tools`),
        fetch(`${API_BASE}/api/marketplace/databases/catalog`),
        fetch(`${API_BASE}/api/marketplace/databases`),
        fetch(`${API_BASE}/api/marketplace/embeddings/catalog`),
        fetch(`${API_BASE}/api/marketplace/embeddings`),
        fetch(`${API_BASE}/api/marketplace/rerankers/catalog`),
        fetch(`${API_BASE}/api/marketplace/rerankers`),
        fetch(`${API_BASE}/api/workflows/extraction-mcp-tools`),
      ]);
      const toolsJson = await toolsRes.json();
      const dbCatalogJson = await dbCatalogRes.json();
      const dbInstalledJson = await dbInstalledRes.json();
      const embedCatalogJson = await embedCatalogRes.json();
      const embedInstalledJson = await embedInstalledRes.json();
      const rerankCatalogJson = await rerankCatalogRes.json();
      const rerankInstalledJson = await rerankInstalledRes.json();
      const mcpExtractionJson = await mcpExtractionRes.json();
      setTools(Array.isArray(toolsJson.tools) ? toolsJson.tools : []);
      setDbEngines(Array.isArray(dbCatalogJson.engines) ? dbCatalogJson.engines : []);
      setInstalledDbs(Array.isArray(dbInstalledJson.databases) ? dbInstalledJson.databases : []);
      setEmbeddingCatalog(Array.isArray(embedCatalogJson.models) ? embedCatalogJson.models : []);
      setInstalledEmbeddings(Array.isArray(embedInstalledJson.models) ? embedInstalledJson.models : []);
      setRerankerCatalog(Array.isArray(rerankCatalogJson.models) ? rerankCatalogJson.models : []);
      setInstalledRerankers(Array.isArray(rerankInstalledJson.models) ? rerankInstalledJson.models : []);
      setMcpExtractionTools(Array.isArray(mcpExtractionJson.tools) ? mcpExtractionJson.tools : []);
    } catch {
      // leave state as-is
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Live-ish status for anything still installing/downloading — same
  // polling cadence ToolCard already uses, just at the list level since
  // neither install endpoint has a single per-item status endpoint to hit.
  useEffect(() => {
    if (
      !installedDbs.some(d => d.status === 'installing') &&
      !installedEmbeddings.some(m => m.status === 'downloading') &&
      !installedRerankers.some(m => m.status === 'downloading')
    ) return;
    const t = setInterval(fetchAll, 2500);
    return () => clearInterval(t);
  }, [installedDbs, installedEmbeddings, installedRerankers, fetchAll]);

  // Includes the bundled built-in engines (SQLite, Qdrant) alongside
  // whatever the user installed themselves from the catalog below —
  // they're real installed databases too (InstalledDatabaseRow shows a
  // "Built-in" badge instead of a delete button for them, since they
  // can't be removed).
  const allInstalledDbs = installedDbs;
  // SQLite/Qdrant need no download at all — their catalog card only ever
  // existed to spin up an extra named instance of the same in-process
  // engine. Once the built-in instance is there, that card sitting right
  // under a "Ready · Built-in" row just reads as "why is it asking me to
  // install what's already installed" — so hide it once the built-in
  // instance exists. Every other engine (DuckDB/LanceDB/Chroma) is a real,
  // repeatable "add another" action (a genuine download), so it always stays.
  //
  // The built-in rows don't carry the catalog's own engine ids — they're
  // seeded with app.core.workflows.seed's BUILTIN_AEGIS_DB_ENGINE_ID /
  // BUILTIN_VECTOR_ENGINE_ID ("aegis_app_db" / "aegis_hybrid"), which the
  // workflow engine's own special-casing depends on, so this maps the
  // catalog's plain "sqlite"/"qdrant" ids across rather than assuming
  // they're literally the same string.
  const BUILTIN_ENGINE_ID_FOR: Record<string, string> = { sqlite: 'aegis_app_db', qdrant: 'aegis_hybrid' };
  const isRedundantBuiltinCard = (e: DBEngine) =>
    !e.requires_download &&
    allInstalledDbs.some(d => d.is_builtin && d.engine_id === (BUILTIN_ENGINE_ID_FOR[e.id] || e.id));
  const relationalEngines = dbEngines.filter(e => e.category === 'relational' && !isRedundantBuiltinCard(e));
  const vectorEngines = dbEngines.filter(e => e.category === 'vector' && !isRedundantBuiltinCard(e));
  const installedEmbeddingIds = new Set(installedEmbeddings.filter(m => m.status !== 'failed').map(m => m.model_id));
  const availableEmbeddings = embeddingCatalog.filter(m => !installedEmbeddingIds.has(m.id));
  const installedRerankerIds = new Set(installedRerankers.filter(m => m.status !== 'failed').map(m => m.model_id));
  const availableRerankers = rerankerCatalog.filter(m => !installedRerankerIds.has(m.id));

  // Extraction-category tools (per-format engine choices — see
  // app.core.extraction_engines) get their own grouped section, one
  // subsection per document format, instead of sitting in the flat
  // Automation Tools list — everything else (web scraping, transcription,
  // OCR, web-page extraction) stays there.
  const automationTools = tools.filter(t => t.category !== 'Extraction');
  const extractionByFormat = tools
    .filter(t => t.category === 'Extraction' && t.format)
    .reduce<Record<string, Tool[]>>((acc, t) => {
      (acc[t.format!] ||= []).push(t);
      return acc;
    }, {});
  const extractionFormatOrder = ['pdf', 'docx', 'pptx', 'xlsx', 'text'];

  return (
    <div className="flex-1 overflow-y-auto bg-aegis-base">
      <div className="px-8 pt-8 pb-5">
        <div className="flex items-center gap-3 mb-1">
          <Store className="w-6 h-6 text-aegis-primary" />
          <h1 className="text-2xl font-bold text-aegis-text-primary">Marketplace</h1>
        </div>
        <p className="text-sm text-aegis-text-secondary">
          Choose what Aegis can do. Nothing downloads or activates until you install it here.
        </p>
      </div>

      <div className="px-8 pb-8 space-y-8">
        {loading ? (
          <p className="text-sm text-aegis-text-muted">Loading...</p>
        ) : (
          <>
            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Automation Tools</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {automationTools.map(tool => (
                  <ToolCard key={tool.id} tool={tool} onInstalled={fetchAll} />
                ))}
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Document Extraction</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Every format has its own choice of engine — pick a different one per node in a Workflow's Extract step when the default doesn't give you what you need.
              </p>
              <div className="space-y-5">
                {extractionFormatOrder.filter(fmt => extractionByFormat[fmt]).map(fmt => (
                  <div key={fmt}>
                    <div className="text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wider mb-2">{EXTRACTION_FORMAT_LABELS[fmt] || fmt}</div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {extractionByFormat[fmt].map(tool => (
                        <ToolCard key={tool.id} tool={tool} onInstalled={fetchAll} />
                      ))}
                    </div>
                  </div>
                ))}

                <div>
                  <div className="text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wider mb-2">Custom — via a connected MCP tool</div>
                  {mcpExtractionTools.length > 0 ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-2">
                      {mcpExtractionTools.map(t => (
                        <McpExtractorRow key={t.engine_id} tool={t} />
                      ))}
                    </div>
                  ) : (
                    <p className="text-[13px] text-aegis-text-muted mb-2">
                      No custom extractors connected yet — connect an MCP server that can read a file (a specialized OCR service, a company-internal parser, or any tool from the registry/GitHub) and it appears here and in a Workflow's Extract node automatically.
                    </p>
                  )}
                  <button
                    onClick={onOpenConnectors}
                    className="text-[12px] font-medium text-aegis-primary-light hover:underline"
                  >
                    {mcpExtractionTools.length > 0 ? 'Connect another in Connectors →' : 'Go to Connectors →'}
                  </button>
                </div>
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Databases</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Install a local database or vector store to use from a workflow's Database/Vector node. Relational engines run your own table schema as a migration; vector engines store embeddings for you to search.
              </p>

              {allInstalledDbs.length > 0 && (
                <div className="space-y-2 mb-4">
                  {allInstalledDbs.map(db => (
                    <InstalledDatabaseRow key={db.id} db={db} onRemoved={fetchAll} />
                  ))}
                </div>
              )}

              <div className="text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wider mb-2">Relational</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4 items-start">
                {relationalEngines.map(e => (
                  <DatabaseEngineRow key={e.id} engine={e} onInstalled={fetchAll} />
                ))}
              </div>

              <div className="text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wider mb-2">Vector</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 items-start">
                {vectorEngines.map(e => (
                  <DatabaseEngineRow key={e.id} engine={e} onInstalled={fetchAll} />
                ))}
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Embedding Models</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Download a model to use from a workflow's Vector node — otherwise it falls back to the model Aegis already ships with.
              </p>

              {installedEmbeddings.length > 0 && (
                <div className="space-y-2 mb-4">
                  {installedEmbeddings.map(m => (
                    <InstalledEmbeddingModelRow key={m.id} model={m} onRemoved={fetchAll} />
                  ))}
                </div>
              )}

              <HuggingFaceModelSearch
                searchEndpoint="/api/marketplace/embeddings/search"
                downloadEndpoint="/api/marketplace/embeddings/download"
                alreadyInstalledIds={installedEmbeddingIds}
                onInstalled={fetchAll}
                resultHint={tags => {
                  const license = (tags || []).find(t => t.startsWith('license:'));
                  return license ? license.replace('license:', '') : null;
                }}
              />

              <input
                value={embeddingSearch}
                onChange={e => setEmbeddingSearch(e.target.value)}
                placeholder="Search embedding models…"
                className="w-full mb-2.5 bg-aegis-overlay border border-aegis-border rounded-lg px-3 py-2 text-[13px] text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
              />
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {availableEmbeddings
                  .filter(m => !embeddingSearch.trim() || m.display_name.toLowerCase().includes(embeddingSearch.trim().toLowerCase()))
                  .sort((a, b) => (a.id === AEGIS_DEFAULT_EMBEDDING_MODEL ? -1 : b.id === AEGIS_DEFAULT_EMBEDDING_MODEL ? 1 : 0))
                  .map(m => (
                    <EmbeddingModelCatalogRow key={m.id} model={m} onDownloaded={fetchAll} />
                  ))}
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Rerankers</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Download a cross-encoder to use from a workflow's Reranker node — otherwise it falls back to the one Aegis already ships with.
              </p>

              {installedRerankers.length > 0 && (
                <div className="space-y-2 mb-4">
                  {installedRerankers.map(m => (
                    <InstalledRerankerRow key={m.id} model={m} onRemoved={fetchAll} />
                  ))}
                </div>
              )}

              <HuggingFaceModelSearch
                searchEndpoint="/api/marketplace/rerankers/search"
                downloadEndpoint="/api/marketplace/rerankers/download"
                alreadyInstalledIds={installedRerankerIds}
                onInstalled={fetchAll}
                resultHint={tags => {
                  const license = (tags || []).find(t => t.startsWith('license:'));
                  return license ? license.replace('license:', '') : null;
                }}
              />

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {availableRerankers.map(m => (
                  <RerankerCatalogRow key={m.id} model={m} onDownloaded={fetchAll} />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
