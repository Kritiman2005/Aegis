'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Store, Download, CheckCircle2, Loader2, Trash2, XCircle, Search, Plus, ScanText, AudioLines, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { ServiceLogo } from '../lib/serviceIcons';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

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
  default?: boolean;    // Extraction-category tools only — this format's always-installed engine
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
  // Aegis's own bundled default (see app.core.bundled_defaults) carries the
  // string id "bundled-default" instead of a real EmbeddingModelRegistry
  // row id — it isn't tracked as a download in this app's own DB, just
  // synthesized from what's really sitting in the Hugging Face cache.
  id: number | string;
  model_id: string;
  display_name: string;
  dim: number;
  size_gb: number;
  status: 'downloading' | 'downloaded' | 'failed';
  error_message?: string;
  created_at?: string;
  bundled?: boolean;
  // Present only mid-download (see backend's _progress_by_row) — picked up
  // by the same "poll the list every 2.5s while anything's downloading"
  // mechanism this component already had, no websocket needed.
  progress?: number;
  downloaded_bytes?: number;
  total_bytes?: number;
}

interface RerankerCatalogEntry {
  id: string;
  display_name: string;
  size_gb: number;
  description: string;
}

interface InstalledReranker {
  // See InstalledEmbeddingModel.id — Aegis's own bundled default reranker
  // carries the string id "bundled-default" the same way.
  id: number | string;
  model_id: string;
  display_name: string;
  size_gb: number;
  status: 'downloading' | 'downloaded' | 'failed';
  error_message?: string;
  created_at?: string;
  bundled?: boolean;
  progress?: number;
  downloaded_bytes?: number;
  total_bytes?: number;
}

type ToolStatus = 'not_installed' | 'installing' | 'ready' | 'failed';

function ToolCard({ tool, onInstalled }: { tool: Tool; onInstalled: () => void }) {
  const [status, setStatus] = useState<ToolStatus>(tool.installed ? 'ready' : 'not_installed');
  // Real per-component progress — a tool's status_endpoint may report a
  // named component and its own 0-100%; the rendering below treats a
  // missing value as "no number yet" for tools that don't report one.
  const [component, setComponent] = useState<string | undefined>();
  const [percent, setPercent] = useState<number | undefined>();
  const [uninstalling, setUninstalling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setStatus(tool.installed ? 'ready' : 'not_installed');
  }, [tool.installed]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const handleUninstall = async () => {
    setUninstalling(true);
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/tools/${tool.id}`, { method: 'DELETE' });
      if (res.ok) {
        setStatus('not_installed');
        onInstalled();
      }
    } finally {
      setUninstalling(false);
    }
  };

  const handleInstall = async () => {
    setStatus('installing');
    setComponent(undefined);
    setPercent(undefined);
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
        } else {
          setComponent(json.component);
          setPercent(json.percent);
        }
      } catch {
        // transient — keep polling
      }
    }, 2500);
  };

  return (
    <div className="bg-aegis-raised border border-aegis-border rounded-xl px-5 py-4 hover:border-aegis-primary/40 transition-colors">
      <div className="flex items-start gap-4">
        <ServiceLogo serviceKey={tool.format ? `extract_${tool.format}` : tool.id} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-[13px] font-semibold text-aegis-text-primary">{tool.name}</p>
            <span className="text-[11px] text-aegis-text-muted">· {tool.size_estimate}</span>
          </div>
          <p className="text-[13px] text-aegis-text-secondary mt-0.5">
            {status === 'installing' && component ? `Downloading ${component}…` : tool.description}
          </p>
        </div>
        <div className="flex-shrink-0">
          {status === 'ready' && (
            <div className="flex items-center gap-1">
              <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-success px-1.5 py-1.5">
                <CheckCircle2 className="w-3.5 h-3.5" /> {tool.default ? 'Default' : 'Installed'}
              </span>
              <button
                onClick={handleUninstall}
                disabled={uninstalling}
                title={`Uninstall ${tool.name}`}
                className="p-1.5 rounded-md text-aegis-text-muted hover:bg-aegis-error/10 hover:text-aegis-error disabled:opacity-40 transition-colors"
              >
                {uninstalling ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
              </button>
            </div>
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
              {typeof percent === 'number' ? <span className="font-semibold tabular-nums">{Math.round(percent)}%</span> : <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {typeof percent !== 'number' && 'Installing...'}
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
      {status === 'installing' && typeof percent === 'number' && (
        <div className="mt-2.5 h-1 w-full rounded-full bg-aegis-border overflow-hidden">
          <div className="h-full rounded-full bg-aegis-warning transition-[width] duration-300 ease-out" style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
        </div>
      )}
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
      const message = e.message || 'Could not reach the backend.';
      setError(message);
      toast.error(message);
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
    <div className="bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
      <div className="flex items-center gap-4">
        <ServiceLogo serviceKey="embedding" size="sm" />
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-aegis-text-primary truncate">{model.display_name}</p>
          <p className="text-[11px] text-aegis-text-muted">
            {model.status === 'downloading' && model.total_bytes
              ? `${formatBytes(model.downloaded_bytes || 0)} of ${formatBytes(model.total_bytes)}`
              : `${model.dim}d · ${model.size_gb} GB${model.status === 'failed' && model.error_message ? ` · ${model.error_message}` : ''}`}
          </p>
        </div>
        <div className="flex-shrink-0 flex items-center gap-2">
          {model.status === 'downloaded' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Ready</span>}
          {model.status === 'downloading' && (
            <span className="inline-flex items-center gap-1 text-[11px] text-aegis-warning">
              {typeof model.progress === 'number' ? <span className="font-semibold tabular-nums">{Math.round(model.progress)}%</span> : <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {typeof model.progress !== 'number' && 'Downloading…'}
            </span>
          )}
          {model.status === 'failed' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-error"><XCircle className="w-3.5 h-3.5" /> Failed</span>}
          <button onClick={handleDelete} disabled={busy} className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>
      {model.status === 'downloading' && typeof model.progress === 'number' && (
        <div className="mt-2 h-1 w-full rounded-full bg-aegis-border overflow-hidden">
          <div className="h-full rounded-full bg-aegis-warning transition-[width] duration-300 ease-out" style={{ width: `${Math.min(100, Math.max(0, model.progress))}%` }} />
        </div>
      )}
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
      const message = e.message || 'Could not reach the backend.';
      setError(message);
      toast.error(message);
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
    <div className="bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
      <div className="flex items-center gap-4">
        <ServiceLogo serviceKey="reranker" size="sm" />
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-aegis-text-primary truncate">{model.display_name}</p>
          <p className="text-[11px] text-aegis-text-muted">
            {model.status === 'downloading' && model.total_bytes
              ? `${formatBytes(model.downloaded_bytes || 0)} of ${formatBytes(model.total_bytes)}`
              : `${model.size_gb} GB${model.status === 'failed' && model.error_message ? ` · ${model.error_message}` : ''}`}
          </p>
        </div>
        <div className="flex-shrink-0 flex items-center gap-2">
          {model.status === 'downloaded' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Ready</span>}
          {model.status === 'downloading' && (
            <span className="inline-flex items-center gap-1 text-[11px] text-aegis-warning">
              {typeof model.progress === 'number' ? <span className="font-semibold tabular-nums">{Math.round(model.progress)}%</span> : <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {typeof model.progress !== 'number' && 'Downloading…'}
            </span>
          )}
          {model.status === 'failed' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-error"><XCircle className="w-3.5 h-3.5" /> Failed</span>}
          <button onClick={handleDelete} disabled={busy} className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>
      {model.status === 'downloading' && typeof model.progress === 'number' && (
        <div className="mt-2 h-1 w-full rounded-full bg-aegis-border overflow-hidden">
          <div className="h-full rounded-full bg-aegis-warning transition-[width] duration-300 ease-out" style={{ width: `${Math.min(100, Math.max(0, model.progress))}%` }} />
        </div>
      )}
    </div>
  );
}

// A Workflow Media tool node's (transcribe_media, extract_image_text)
// swappable OCR/transcription engine — app.core.media_engines' catalog,
// unlike embeddings/rerankers, ships bundled default + every downloadable
// alternative from a single GET, so one row component (not a separate
// catalog-row/installed-row pair) covers all three states: bundled
// default (no button), not-yet-installed (Install), and installed (Ready
// + delete). Scoped to just those two workflow nodes — see that module's
// own docstring for why the mic composer is untouched by anything here.
interface MediaEngine {
  id: string;
  display_name: string;
  description: string;
  default: boolean;
  downloadable: boolean;
  size_gb?: number;
  installed: boolean;
  // True only for a custom (Hugging-Face-search-installed) engine — see
  // app.core.media_engines.list_engines, which sets this on every entry
  // discovered via list_custom_engine_ids, never on a static-catalog one.
  custom?: boolean;
  status?: 'running';
  message?: string;
  downloaded_bytes?: number;
  total_bytes?: number;
  progress?: number;
}

function MediaEngineRow({ capability, engine, onChanged }: { capability: 'ocr' | 'transcription'; engine: MediaEngine; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const downloading = engine.status === 'running';

  const handleInstall = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/marketplace/media-engines/${capability}/${engine.id}/install`, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Install failed.');
      }
      onChanged();
    } catch (e: any) {
      const message = e.message || 'Could not reach the backend.';
      setError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/marketplace/media-engines/${capability}/${engine.id}`, { method: 'DELETE' });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
      <div className="flex items-center gap-4">
        {capability === 'ocr' ? <ScanText className="w-5 h-5 text-aegis-primary-light flex-shrink-0" /> : <AudioLines className="w-5 h-5 text-aegis-primary-light flex-shrink-0" />}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-[13px] font-semibold text-aegis-text-primary truncate">{engine.display_name}</p>
            {typeof engine.size_gb === 'number' && <span className="text-[11px] text-aegis-text-muted">· {engine.size_gb} GB</span>}
          </div>
          <p className="text-[13px] text-aegis-text-secondary mt-0.5">
            {downloading && engine.total_bytes
              ? `${formatBytes(engine.downloaded_bytes || 0)} of ${formatBytes(engine.total_bytes)}`
              : engine.description}
          </p>
          {error && <p className="text-[12px] text-aegis-error mt-1">{error}</p>}
        </div>
        <div className="flex-shrink-0 flex items-center gap-2">
          {engine.default && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Default</span>}
          {!engine.default && engine.installed && (
            <>
              <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Ready</span>
              <button onClick={handleDelete} disabled={busy} className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
              </button>
            </>
          )}
          {!engine.default && !engine.installed && !downloading && (
            <button
              onClick={handleInstall}
              disabled={busy}
              className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
            >
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
              {busy ? 'Starting…' : 'Install'}
            </button>
          )}
          {!engine.default && downloading && (
            <span className="inline-flex items-center gap-1 text-[11px] text-aegis-warning">
              {typeof engine.progress === 'number' ? <span className="font-semibold tabular-nums">{Math.round(engine.progress)}%</span> : <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {typeof engine.progress !== 'number' && 'Downloading…'}
            </span>
          )}
        </div>
      </div>
      {downloading && typeof engine.progress === 'number' && (
        <div className="mt-2 h-1 w-full rounded-full bg-aegis-border overflow-hidden">
          <div className="h-full rounded-full bg-aegis-warning transition-[width] duration-300 ease-out" style={{ width: `${Math.min(100, Math.max(0, engine.progress))}%` }} />
        </div>
      )}
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

interface HFSearchCategory {
  key: string;
  label: string;
  searchEndpoint: string;
  downloadEndpoint: string;
  alreadyInstalledIds: Set<string>;
  resultHint: (tags: string[] | undefined) => string | null;
}

// A custom (Hugging-Face-search-installed) model, from any of the four
// categories, shown in GlobalHFSearch's own "Downloads" list — so what
// got installed from a search is visible right where it was searched for,
// not just buried in that category's own curated-list section further
// down the page.
interface HFDownloadEntry {
  key: string;
  label: string;
  categoryLabel: string;
  status: 'downloading' | 'downloaded' | 'failed';
  onRemove: () => void;
}

function HFDownloadRow({ entry }: { entry: HFDownloadEntry }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex items-center justify-between gap-3 bg-aegis-overlay rounded-lg px-3 py-2">
      <div className="min-w-0">
        <p className="text-[12px] font-medium text-aegis-text-primary truncate">{entry.label}</p>
        <p className="text-[10px] text-aegis-text-muted">{entry.categoryLabel}</p>
      </div>
      <div className="flex-shrink-0 flex items-center gap-2">
        {entry.status === 'downloaded' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Ready</span>}
        {entry.status === 'downloading' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-warning"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Downloading…</span>}
        {entry.status === 'failed' && <span className="inline-flex items-center gap-1 text-[11px] text-aegis-error"><XCircle className="w-3.5 h-3.5" /> Failed</span>}
        <button
          onClick={() => { setBusy(true); entry.onRemove(); }}
          disabled={busy}
          className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors"
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        </button>
      </div>
    </div>
  );
}

// One search box for everything Aegis can pull in from Hugging Face —
// replaces what used to be four separate "Search Hugging Face" widgets
// repeated inline under Embedding Models, Rerankers, and Media
// Extraction's OCR/Transcription subsections, plus the earlier per-
// category-dropdown "paste a repo id" install row (removed — search
// itself, now unfiltered free text, is the only way in, and every
// category's own curated list below already offers its own one-click
// picks). A single query fans out to every category's own search
// endpoint in parallel, and results render grouped by category, each
// with its own Install wired to that category's real install endpoint —
// a search result only ever "helps discovery," it's still each
// category's own backend that decides whether a given repo id actually
// loads. downloads lists every custom install across all four
// categories in one place (see HFDownloadEntry), regardless of which
// category it came from.
function GlobalHFSearch({ categories, downloads, onInstalled }: { categories: HFSearchCategory[]; downloads: HFDownloadEntry[]; onInstalled: () => void }) {
  const [query, setQuery] = useState('');
  const [resultsByCategory, setResultsByCategory] = useState<Record<string, HFModelResult[]>>({});
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [installing, setInstalling] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const runSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setSearched(true);
    setError(null);
    try {
      const perCategory = await Promise.all(categories.map(async c => {
        try {
          const res = await fetch(`${API_BASE}${c.searchEndpoint}?q=${encodeURIComponent(query.trim())}&limit=8`);
          const data = await res.json().catch(() => ({}));
          return res.ok ? (data.models || []) as HFModelResult[] : [];
        } catch {
          return [] as HFModelResult[];
        }
      }));
      const next: Record<string, HFModelResult[]> = {};
      categories.forEach((c, i) => { next[c.key] = perCategory[i]; });
      setResultsByCategory(next);
    } finally {
      setSearching(false);
    }
  };

  const install = async (categoryKey: string, modelId: string) => {
    const category = categories.find(c => c.key === categoryKey);
    if (!category) return;
    const installKey = `${categoryKey}:${modelId}`;
    setInstalling(prev => ({ ...prev, [installKey]: true }));
    setError(null);
    try {
      const res = await fetch(`${API_BASE}${category.downloadEndpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const message = data.detail || `Failed to install '${modelId}'.`;
        setError(message);
        toast.error(message);
        return;
      }
      onInstalled();
    } catch {
      const message = 'Could not reach the backend.';
      setError(message);
      toast.error(message);
    } finally {
      setInstalling(prev => { const n = { ...prev }; delete n[installKey]; return n; });
    }
  };

  const totalResults = Object.values(resultsByCategory).reduce((sum, r) => sum + r.length, 0);
  const hasSearchState = searched || query.length > 0;

  const clearSearch = () => {
    setQuery('');
    setResultsByCategory({});
    setSearched(false);
    setError(null);
  };

  return (
    <div className="bg-aegis-raised rounded-xl border border-aegis-border shadow-sm px-5 py-4 mb-2">
      <div className="flex items-center gap-2 mb-1">
        <Search className="w-4 h-4 text-aegis-primary flex-shrink-0" />
        <p className="text-[13px] font-semibold text-aegis-text-primary">Search Hugging Face</p>
      </div>
      <p className="text-[12px] text-aegis-text-muted mb-3">
        One search across everything Aegis can install from Hugging Face — embedding models, rerankers, OCR/document-extraction models, and transcription models — beyond each category's own curated list below.
      </p>

      <div className="flex gap-2">
        <div className="flex-1 relative">
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') runSearch(); }}
            placeholder="Search Hugging Face…"
            className="w-full bg-aegis-overlay border border-aegis-border rounded-lg px-3 py-2 pr-8 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
          />
          {hasSearchState && (
            <button
              onClick={clearSearch}
              title="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-aegis-text-muted hover:text-aegis-text-primary transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <button
          onClick={runSearch}
          disabled={searching || !query.trim()}
          className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
        >
          {searching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
          Search
        </button>
      </div>

      {error && <p className="text-[12px] text-aegis-error mt-3">{error}</p>}
      {searched && !searching && totalResults === 0 && !error && (
        <p className="text-[12px] text-aegis-text-muted mt-3">No models found across Embeddings, Rerankers, OCR, or Transcription.</p>
      )}

      {(() => {
        // One flat, deduplicated list instead of a separate section per
        // category — the same query fans out to all four categories'
        // search endpoints independently, and a generic term can legitimately
        // match in more than one of them, surfacing the same repo id
        // duplicated under several headers. First category to claim a given
        // id (in `categories`' own order) wins it; still tagged with that
        // category's own label inline so what it actually installs as
        // stays visible, just without the redundant grouping.
        const seen = new Set<string>();
        const flat: { categoryKey: string; categoryLabel: string; result: HFModelResult; hint: string | null; already: boolean }[] = [];
        for (const c of categories) {
          for (const r of resultsByCategory[c.key] || []) {
            if (seen.has(r.id)) continue;
            seen.add(r.id);
            flat.push({ categoryKey: c.key, categoryLabel: c.label, result: r, hint: c.resultHint(r.tags), already: c.alreadyInstalledIds.has(r.id) });
          }
        }
        flat.sort((a, b) => (b.result.downloads ?? 0) - (a.result.downloads ?? 0));
        if (flat.length === 0) return null;
        return (
          <div className="mt-4 flex flex-col gap-1.5">
            {flat.map(({ categoryKey, categoryLabel, result: r, hint, already }) => {
              const installKey = `${categoryKey}:${r.id}`;
              return (
                <div key={r.id} className="flex items-center justify-between gap-3 bg-aegis-overlay rounded-lg px-3 py-2">
                  <div className="min-w-0">
                    <p className="text-[12px] font-medium text-aegis-text-primary truncate">{r.id}</p>
                    <p className="text-[10px] text-aegis-text-muted">
                      {(r.downloads ?? 0).toLocaleString()} downloads · {categoryLabel}{hint ? ` · ${hint}` : ''}
                    </p>
                  </div>
                  {already ? (
                    <span className="flex-shrink-0 inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Installed</span>
                  ) : (
                    <button
                      onClick={() => install(categoryKey, r.id)}
                      disabled={!!installing[installKey]}
                      className="flex-shrink-0 inline-flex items-center gap-1.5 text-[11px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-2.5 py-1 rounded-md transition-colors disabled:opacity-50"
                    >
                      {installing[installKey] ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                      Install
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        );
      })()}

      {downloads.length > 0 && (
        <div className="pt-3 mt-3 border-t border-aegis-border">
          <div className="text-[10px] font-semibold text-aegis-text-muted uppercase tracking-wider mb-1.5">
            Downloads ({downloads.length})
          </div>
          <div className="flex flex-col gap-1.5">
            {downloads.map(d => <HFDownloadRow key={d.key} entry={d} />)}
          </div>
        </div>
      )}
    </div>
  );
}

export default function MarketplaceView() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [dbEngines, setDbEngines] = useState<DBEngine[]>([]);
  const [installedDbs, setInstalledDbs] = useState<InstalledDB[]>([]);
  const [embeddingCatalog, setEmbeddingCatalog] = useState<EmbeddingModelCatalogEntry[]>([]);
  const [installedEmbeddings, setInstalledEmbeddings] = useState<InstalledEmbeddingModel[]>([]);
  const [rerankerCatalog, setRerankerCatalog] = useState<RerankerCatalogEntry[]>([]);
  const [installedRerankers, setInstalledRerankers] = useState<InstalledReranker[]>([]);
  const [ocrEngines, setOcrEngines] = useState<MediaEngine[]>([]);
  const [transcriptionEngines, setTranscriptionEngines] = useState<MediaEngine[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    try {
      const [toolsRes, dbCatalogRes, dbInstalledRes, embedCatalogRes, embedInstalledRes, rerankCatalogRes, rerankInstalledRes, ocrEnginesRes, transcriptionEnginesRes] = await Promise.all([
        fetch(`${API_BASE}/api/marketplace/tools`),
        fetch(`${API_BASE}/api/marketplace/databases/catalog`),
        fetch(`${API_BASE}/api/marketplace/databases`),
        fetch(`${API_BASE}/api/marketplace/embeddings/catalog`),
        fetch(`${API_BASE}/api/marketplace/embeddings`),
        fetch(`${API_BASE}/api/marketplace/rerankers/catalog`),
        fetch(`${API_BASE}/api/marketplace/rerankers`),
        fetch(`${API_BASE}/api/marketplace/media-engines?capability=ocr`),
        fetch(`${API_BASE}/api/marketplace/media-engines?capability=transcription`),
      ]);
      const toolsJson = await toolsRes.json();
      const dbCatalogJson = await dbCatalogRes.json();
      const dbInstalledJson = await dbInstalledRes.json();
      const embedCatalogJson = await embedCatalogRes.json();
      const embedInstalledJson = await embedInstalledRes.json();
      const rerankCatalogJson = await rerankCatalogRes.json();
      const rerankInstalledJson = await rerankInstalledRes.json();
      const ocrEnginesJson = await ocrEnginesRes.json();
      const transcriptionEnginesJson = await transcriptionEnginesRes.json();
      setTools(Array.isArray(toolsJson.tools) ? toolsJson.tools : []);
      setDbEngines(Array.isArray(dbCatalogJson.engines) ? dbCatalogJson.engines : []);
      setInstalledDbs(Array.isArray(dbInstalledJson.databases) ? dbInstalledJson.databases : []);
      setEmbeddingCatalog(Array.isArray(embedCatalogJson.models) ? embedCatalogJson.models : []);
      setInstalledEmbeddings(Array.isArray(embedInstalledJson.models) ? embedInstalledJson.models : []);
      setRerankerCatalog(Array.isArray(rerankCatalogJson.models) ? rerankCatalogJson.models : []);
      setInstalledRerankers(Array.isArray(rerankInstalledJson.models) ? rerankInstalledJson.models : []);
      setOcrEngines(Array.isArray(ocrEnginesJson.engines) ? ocrEnginesJson.engines : []);
      setTranscriptionEngines(Array.isArray(transcriptionEnginesJson.engines) ? transcriptionEnginesJson.engines : []);
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
      !installedRerankers.some(m => m.status === 'downloading') &&
      !ocrEngines.some(e => e.status === 'running') &&
      !transcriptionEngines.some(e => e.status === 'running')
    ) return;
    const t = setInterval(fetchAll, 2500);
    return () => clearInterval(t);
  }, [installedDbs, installedEmbeddings, installedRerankers, ocrEngines, transcriptionEngines, fetchAll]);

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

  // Every custom (Hugging-Face-search-installed) model across all four
  // categories, in one place — see HFDownloadEntry/GlobalHFSearch's own
  // "Downloads" list. "Custom" means not one of that category's own
  // curated catalog entries (embeddings/rerankers) or explicitly flagged
  // custom by the backend (OCR/transcription — see MediaEngine.custom).
  const hfDownloads: HFDownloadEntry[] = [
    ...installedEmbeddings
      .filter(m => !embeddingCatalog.some(c => c.id === m.model_id))
      .map(m => ({
        key: `embeddings:${m.id}`, label: m.display_name, categoryLabel: 'Embedding Model',
        status: m.status, onRemove: async () => { await fetch(`${API_BASE}/api/marketplace/embeddings/${m.id}`, { method: 'DELETE' }); fetchAll(); },
      })),
    ...installedRerankers
      .filter(m => !rerankerCatalog.some(c => c.id === m.model_id))
      .map(m => ({
        key: `rerankers:${m.id}`, label: m.display_name, categoryLabel: 'Reranker',
        status: m.status, onRemove: async () => { await fetch(`${API_BASE}/api/marketplace/rerankers/${m.id}`, { method: 'DELETE' }); fetchAll(); },
      })),
    ...ocrEngines
      .filter(e => e.custom)
      .map(e => ({
        key: `ocr:${e.id}`, label: e.display_name, categoryLabel: 'OCR / Document Extraction',
        status: (e.status === 'running' ? 'downloading' : 'downloaded') as HFDownloadEntry['status'],
        onRemove: async () => { await fetch(`${API_BASE}/api/marketplace/media-engines/ocr/${e.id}`, { method: 'DELETE' }); fetchAll(); },
      })),
    ...transcriptionEngines
      .filter(e => e.custom)
      .map(e => ({
        key: `transcription:${e.id}`, label: e.display_name, categoryLabel: 'Audio/Video Transcription',
        status: (e.status === 'running' ? 'downloading' : 'downloaded') as HFDownloadEntry['status'],
        onRemove: async () => { await fetch(`${API_BASE}/api/marketplace/media-engines/transcription/${e.id}`, { method: 'DELETE' }); fetchAll(); },
      })),
  ];

  // Extraction-category tools (per-format engine choices — see
  // app.core.extraction_engines) get their own grouped section, one
  // subsection per document format, instead of sitting in the flat
  // Automation Tools list — everything else stays there.
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
            <GlobalHFSearch
              onInstalled={fetchAll}
              downloads={hfDownloads}
              categories={[
                {
                  key: 'embeddings', label: 'Embedding Models',
                  searchEndpoint: '/api/marketplace/embeddings/search',
                  downloadEndpoint: '/api/marketplace/embeddings/download',
                  alreadyInstalledIds: installedEmbeddingIds,
                  resultHint: tags => {
                    const license = (tags || []).find(t => t.startsWith('license:'));
                    return license ? license.replace('license:', '') : null;
                  },
                },
                {
                  key: 'rerankers', label: 'Rerankers',
                  searchEndpoint: '/api/marketplace/rerankers/search',
                  downloadEndpoint: '/api/marketplace/rerankers/download',
                  alreadyInstalledIds: installedRerankerIds,
                  resultHint: tags => {
                    const license = (tags || []).find(t => t.startsWith('license:'));
                    return license ? license.replace('license:', '') : null;
                  },
                },
                {
                  key: 'ocr', label: 'OCR / Document Extraction',
                  searchEndpoint: '/api/marketplace/media-engines/ocr/search',
                  downloadEndpoint: '/api/marketplace/media-engines/ocr/install-custom',
                  alreadyInstalledIds: new Set(ocrEngines.filter(e => e.installed).map(e => e.id)),
                  resultHint: tags => {
                    const license = (tags || []).find(t => t.startsWith('license:'));
                    return license ? license.replace('license:', '') : null;
                  },
                },
                {
                  key: 'transcription', label: 'Audio/Video Transcription',
                  searchEndpoint: '/api/marketplace/media-engines/transcription/search',
                  downloadEndpoint: '/api/marketplace/media-engines/transcription/install-custom',
                  alreadyInstalledIds: new Set(transcriptionEngines.filter(e => e.installed).map(e => e.id)),
                  resultHint: tags => {
                    const license = (tags || []).find(t => t.startsWith('license:'));
                    return license ? license.replace('license:', '') : null;
                  },
                },
              ]}
            />

            {automationTools.length > 0 && (
              <section>
                <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Automation Tools</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {automationTools.map(tool => (
                    <ToolCard key={tool.id} tool={tool} onInstalled={fetchAll} />
                  ))}
                </div>
              </section>
            )}

            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Document Extraction</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Every format has its own choice of engine — pick a different one per node in a Workflow's Extract step when the default doesn't give you what you need. PDF and Word work out of the box; everything else installs in seconds when you pick it.
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
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Media Extraction</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Swappable OCR/transcription engines for a Workflow's Media tool nodes (Transcribe media, Extract image text) — pick a different one per node when the bundled default doesn't give you what you need. Includes document-extraction models (e.g. Nougat, Donut — trained specifically to pull text out of PDF/document page images, not just photos) via the search above, same mechanism as any other OCR model. The chat composer's mic transcription always keeps using its own bundled default, unaffected by anything installed here.
              </p>
              <div className="space-y-5">
                <div>
                  <div className="text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wider mb-2">OCR / Document Extraction</div>
                  <div className="flex flex-col gap-2">
                    {ocrEngines.map(engine => (
                      <MediaEngineRow key={engine.id} capability="ocr" engine={engine} onChanged={fetchAll} />
                    ))}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wider mb-2">Audio/Video Transcription</div>
                  <div className="flex flex-col gap-2">
                    {transcriptionEngines.map(engine => (
                      <MediaEngineRow key={engine.id} capability="transcription" engine={engine} onChanged={fetchAll} />
                    ))}
                  </div>
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

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {availableEmbeddings
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
