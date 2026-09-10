'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Store, Sparkles, Download, CheckCircle2, Loader2, Trash2, XCircle } from 'lucide-react';
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

interface MarketplaceSkill {
  id: string;
  name: string;
  description: string;
  installed: boolean;
}

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
      <ServiceLogo serviceKey={tool.id} />
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

function SkillCard({ skill, onChanged }: { skill: MarketplaceSkill; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);

  const handleInstall = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/marketplace/skills/${skill.id}/install`, { method: 'POST' });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/marketplace/skills/${skill.id}`, { method: 'DELETE' });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-start gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-4 hover:border-aegis-primary/40 transition-colors">
      <div className="w-10 h-10 rounded-lg bg-aegis-overlay flex items-center justify-center flex-shrink-0">
        <Sparkles className="w-5 h-5 text-aegis-primary-light" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-aegis-text-primary">{skill.name}</p>
        <p className="text-[13px] text-aegis-text-secondary mt-0.5">{skill.description}</p>
      </div>
      <div className="flex-shrink-0">
        {skill.installed ? (
          <button
            onClick={handleRemove}
            disabled={busy}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-text-muted hover:text-aegis-error px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
          >
            <Trash2 className="w-3.5 h-3.5" /> Remove
          </button>
        ) : (
          <button
            onClick={handleInstall}
            disabled={busy}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
            Install
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
        <button onClick={handleDelete} disabled={busy} className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        </button>
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

export default function MarketplaceView() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [skills, setSkills] = useState<MarketplaceSkill[]>([]);
  const [dbEngines, setDbEngines] = useState<DBEngine[]>([]);
  const [installedDbs, setInstalledDbs] = useState<InstalledDB[]>([]);
  const [embeddingCatalog, setEmbeddingCatalog] = useState<EmbeddingModelCatalogEntry[]>([]);
  const [installedEmbeddings, setInstalledEmbeddings] = useState<InstalledEmbeddingModel[]>([]);
  const [embeddingSearch, setEmbeddingSearch] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    try {
      const [toolsRes, skillsRes, dbCatalogRes, dbInstalledRes, embedCatalogRes, embedInstalledRes] = await Promise.all([
        fetch(`${API_BASE}/api/marketplace/tools`),
        fetch(`${API_BASE}/api/marketplace/skills`),
        fetch(`${API_BASE}/api/marketplace/databases/catalog`),
        fetch(`${API_BASE}/api/marketplace/databases`),
        fetch(`${API_BASE}/api/marketplace/embeddings/catalog`),
        fetch(`${API_BASE}/api/marketplace/embeddings`),
      ]);
      const toolsJson = await toolsRes.json();
      const skillsJson = await skillsRes.json();
      const dbCatalogJson = await dbCatalogRes.json();
      const dbInstalledJson = await dbInstalledRes.json();
      const embedCatalogJson = await embedCatalogRes.json();
      const embedInstalledJson = await embedInstalledRes.json();
      setTools(Array.isArray(toolsJson.tools) ? toolsJson.tools : []);
      setSkills(Array.isArray(skillsJson.skills) ? skillsJson.skills : []);
      setDbEngines(Array.isArray(dbCatalogJson.engines) ? dbCatalogJson.engines : []);
      setInstalledDbs(Array.isArray(dbInstalledJson.databases) ? dbInstalledJson.databases : []);
      setEmbeddingCatalog(Array.isArray(embedCatalogJson.models) ? embedCatalogJson.models : []);
      setInstalledEmbeddings(Array.isArray(embedInstalledJson.models) ? embedInstalledJson.models : []);
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
    if (!installedDbs.some(d => d.status === 'installing') && !installedEmbeddings.some(m => m.status === 'downloading')) return;
    const t = setInterval(fetchAll, 2500);
    return () => clearInterval(t);
  }, [installedDbs, installedEmbeddings, fetchAll]);

  const relationalEngines = dbEngines.filter(e => e.category === 'relational');
  const vectorEngines = dbEngines.filter(e => e.category === 'vector');
  const installedEmbeddingIds = new Set(installedEmbeddings.filter(m => m.status !== 'failed').map(m => m.model_id));
  const availableEmbeddings = embeddingCatalog.filter(m => !installedEmbeddingIds.has(m.id));

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
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Databases</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Install a local database or vector store to use from a workflow's Database/Vector node. Relational engines run your own table schema as a migration; vector engines store embeddings for you to search.
              </p>

              {installedDbs.length > 0 && (
                <div className="space-y-2 mb-4">
                  {installedDbs.map(db => (
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
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Skills</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Pre-built guidance the chat agent draws on automatically when relevant — no setup needed once installed.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {skills.map(skill => (
                  <SkillCard key={skill.id} skill={skill} onChanged={fetchAll} />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
