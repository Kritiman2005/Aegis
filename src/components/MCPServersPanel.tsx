import React, { useState, useEffect, useCallback } from 'react';
import { Plug, ShieldAlert, RefreshCw, Trash2, Loader2, CheckCircle2, XCircle, Plus, Download, Store, Eye, EyeOff, ChevronRight, Code2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useSocket } from '../hooks/useSocket';
import { ServiceLogo } from '../lib/serviceIcons';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface ServerStatus {
  running: boolean;
  server_info?: { name?: string; version?: string };
  tools_count: number;
}

interface ToolDef {
  name: string;
  description?: string;
}

// Mirrors the backend's runtime_manager progress events (stage: detect |
// download | extract | runtime | connect, status: running | ready | error).
interface ConnectProgress {
  stage: string;
  message: string;
  progress?: number; // 0-100, only present during an active download
}

// Same `mcpServers` JSON shape used by AnythingLLM/Claude Desktop/etc., so a
// config a user already has for another tool can usually be pasted here
// verbatim. Only the stdio (`command`) shape is wired to a working backend
// today — an `url`-based (SSE/streamable) entry is recognized and reported
// back per-entry as not yet supported rather than silently dropped.
interface PastedServerEntry {
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  type?: string;
}

const PLACEHOLDER = `{
  "mcpServers": {
    "mcp-youtube": {
      "command": "uvx",
      "args": ["mcp-youtube"]
    }
  }
}`;

// Mirrors app.mcp.catalog's CONNECTORS_CATALOG entry shape — only
// auth_type != "oauth" entries ever reach the frontend (the backend
// filters oauth ones out while app.core.feature_flags.CONNECTORS_ENABLED
// is off, since those need a hosted broker Aegis doesn't have yet).
interface CatalogField {
  key: string;
  label: string;
  placeholder?: string;
  required?: boolean;
  secret?: boolean;
  help_url?: string;
}

interface CatalogEntry {
  name: string;
  display_name: string;
  category: string;
  description: string;
  auth_type: 'api_key' | 'path' | 'connection_string' | 'none';
  env_schema: CatalogField[];
  input_schema: CatalogField[];
}

function CatalogCard({ entry, connecting, onConnect }: { entry: CatalogEntry; connecting: boolean; onConnect: (env: Record<string, string>, inputParams: Record<string, string>) => void }) {
  const [expanded, setExpanded] = useState(entry.auth_type === 'none');
  const [values, setValues] = useState<Record<string, string>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const fields = [...entry.env_schema, ...entry.input_schema];

  const handleConnect = () => {
    if (entry.auth_type !== 'none' && !expanded) { setExpanded(true); return; }
    const env: Record<string, string> = {};
    entry.env_schema.forEach(f => { if (values[f.key]) env[f.key] = values[f.key]; });
    const inputParams: Record<string, string> = {};
    entry.input_schema.forEach(f => { if (values[f.key]) inputParams[f.key] = values[f.key]; });
    onConnect(env, inputParams);
  };

  return (
    <div
      className={`bg-aegis-raised rounded-xl border border-aegis-border shadow-sm px-3.5 py-3 hover:border-aegis-primary/40 hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 ${expanded && fields.length > 0 ? 'lg:col-span-2' : ''}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <ServiceLogo serviceKey={entry.name} />
          <div className="min-w-0">
            <div className="text-xs font-semibold text-aegis-text-primary">{entry.display_name}</div>
            <div className="text-[10px] text-aegis-text-muted leading-relaxed line-clamp-2">{entry.description}</div>
          </div>
        </div>
        <button
          onClick={handleConnect}
          disabled={connecting}
          className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 bg-aegis-primary text-white text-[11px] font-semibold rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {connecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
          {connecting ? 'Connecting…' : entry.auth_type === 'none' ? 'Connect' : expanded ? 'Connect' : 'Configure'}
        </button>
      </div>

      {expanded && fields.length > 0 && (
        <div className="mt-3 pl-[52px] flex flex-col gap-2">
          {fields.map(f => (
            <div key={f.key}>
              <label className="text-[10px] text-aegis-text-muted">{f.label}{f.required && ' *'}</label>
              <div className="relative">
                <input
                  type={f.secret && !revealed[f.key] ? 'password' : 'text'}
                  value={values[f.key] || ''}
                  onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  className="w-full bg-aegis-base border border-aegis-border rounded-md px-2 py-1.5 pr-8 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                />
                {f.secret && (
                  <button
                    type="button"
                    onClick={() => setRevealed(r => ({ ...r, [f.key]: !r[f.key] }))}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-aegis-text-muted hover:text-aegis-text-secondary"
                  >
                    {revealed[f.key] ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                  </button>
                )}
              </div>
              {f.help_url && (
                <a href={f.help_url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-aegis-primary-light hover:underline">
                  Where do I find this?
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function MCPServersPanel() {
  const { addMessageHandler } = useSocket();
  const [statusMap, setStatusMap] = useState<Record<string, ServerStatus>>({});
  const [tools, setTools] = useState<ToolDef[]>([]);
  const [jsonInput, setJsonInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [pending, setPending] = useState<Record<string, ConnectProgress>>({});
  const [busyServer, setBusyServer] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [customServerOpen, setCustomServerOpen] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/connectors`);
      if (res.ok) {
        const data = await res.json();
        setStatusMap(data.status || {});
        setTools(data.tools || []);
      }
    } catch (e) {
      // Backend not reachable yet — next interval tick retries.
    } finally {
      setLoaded(true);
    }
  }, []);

  const fetchCatalog = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/connectors/catalog`);
      if (res.ok) setCatalog((await res.json()).catalog || []);
    } catch (e) {}
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchCatalog();
    const interval = setInterval(fetchStatus, 8000);
    return () => clearInterval(interval);
  }, [fetchStatus, fetchCatalog]);

  const handleCatalogConnect = async (entry: CatalogEntry, env: Record<string, string>, inputParams: Record<string, string>) => {
    setPending(prev => ({ ...prev, [entry.name]: { stage: 'detect', message: 'Starting…' } }));
    try {
      const res = await fetch(`${API_BASE}/api/connectors/catalog/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_name: entry.name, env, input_params: inputParams }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || `Failed to connect '${entry.display_name}'.`);
        setPending(prev => { const n = { ...prev }; delete n[entry.name]; return n; });
      }
    } catch (e) {
      toast.error('Could not reach the backend.');
      setPending(prev => { const n = { ...prev }; delete n[entry.name]; return n; });
    }
  };

  // Live progress for in-flight connects — the backend downloads whatever
  // runtime the pasted command needs (Node for npx, uv for uvx) in the
  // background and broadcasts each step over the same WebSocket chat uses,
  // rather than one long blocking spinner. See connectors.py's
  // _connect_custom_task and runtime_manager.ensure_runtime.
  useEffect(() => {
    return addMessageHandler((payload: any) => {
      const { type, server_name } = payload;
      if (!server_name) return;

      if (type === 'mcp_connect_progress') {
        setPending(prev => ({
          ...prev,
          [server_name]: { stage: payload.stage, message: payload.message, progress: payload.progress },
        }));
      } else if (type === 'mcp_connect_complete') {
        setPending(prev => { const n = { ...prev }; delete n[server_name]; return n; });
        toast.success(`Connected '${server_name}' — ${payload.tools_count} tool${payload.tools_count === 1 ? '' : 's'} found.`);
        fetchStatus();
      } else if (type === 'mcp_connect_failed') {
        setPending(prev => { const n = { ...prev }; delete n[server_name]; return n; });
        toast.error(`${server_name}: ${payload.message}`, { duration: 8000 });
      }
    });
  }, [addMessageHandler, fetchStatus]);

  const handleAdd = async () => {
    let parsed: any;
    try {
      parsed = JSON.parse(jsonInput);
    } catch (e) {
      toast.error('Not valid JSON — check for a trailing comma or missing quote.');
      return;
    }

    const entries: Record<string, PastedServerEntry> = parsed.mcpServers || parsed;
    if (!entries || typeof entries !== 'object' || Array.isArray(entries) || Object.keys(entries).length === 0) {
      toast.error('Expected a "mcpServers" object with at least one server entry.');
      return;
    }

    setSubmitting(true);
    let startedCount = 0;
    const errors: string[] = [];

    for (const [serverName, entry] of Object.entries(entries)) {
      if (entry.url) {
        errors.push(`${serverName}: SSE/streamable ("url") servers aren't supported yet — only "command"-based (stdio) servers.`);
        continue;
      }
      if (!entry.command) {
        errors.push(`${serverName}: missing "command".`);
        continue;
      }
      try {
        setPending(prev => ({ ...prev, [serverName]: { stage: 'detect', message: 'Starting…' } }));
        const res = await fetch(`${API_BASE}/api/connectors/connect`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            server_name: serverName,
            command: [entry.command, ...(entry.args || [])],
            env: entry.env || null,
          }),
        });
        if (res.ok) {
          startedCount++;
        } else {
          const data = await res.json().catch(() => ({}));
          errors.push(`${serverName}: ${data.detail || 'failed to start connecting'}`);
          setPending(prev => { const n = { ...prev }; delete n[serverName]; return n; });
        }
      } catch (e) {
        errors.push(`${serverName}: could not reach the backend.`);
        setPending(prev => { const n = { ...prev }; delete n[serverName]; return n; });
      }
    }

    setSubmitting(false);
    if (startedCount > 0) {
      setJsonInput('');
    }
    errors.forEach(e => toast.error(e, { duration: 6000 }));
  };

  const handleReload = async (name: string) => {
    setBusyServer(name);
    try {
      const res = await fetch(`${API_BASE}/api/connectors/${encodeURIComponent(name)}/reload`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        toast.success(`Reloaded ${name}.`);
      } else {
        toast.error(data.detail || `Failed to reload ${name}.`);
      }
    } catch (e) {
      toast.error(`Could not reach the backend.`);
    } finally {
      setBusyServer(null);
      fetchStatus();
    }
  };

  const handleDisconnect = async (name: string) => {
    setBusyServer(name);
    try {
      const res = await fetch(`${API_BASE}/api/connectors/${encodeURIComponent(name)}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success(`Disconnected ${name}.`);
      } else {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || `Failed to disconnect ${name}.`);
      }
    } catch (e) {
      toast.error(`Could not reach the backend.`);
    } finally {
      setBusyServer(null);
      fetchStatus();
    }
  };

  const serverNames = Object.keys(statusMap);
  const pendingNames = Object.keys(pending);

  const availableCatalog = catalog.filter(c => !(c.name in statusMap));
  const catalogByCategory = availableCatalog.reduce<Record<string, CatalogEntry[]>>((acc, entry) => {
    (acc[entry.category] ||= []).push(entry);
    return acc;
  }, {});

  return (
    <div className="flex-1 overflow-y-auto bg-aegis-base">
      <div className="px-8 pt-8 pb-5">
        <div className="flex items-center gap-3 mb-1">
          <Plug className="w-6 h-6 text-aegis-primary" />
          <h1 className="text-2xl font-bold text-aegis-text-primary">MCP Servers</h1>
        </div>
        <p className="text-sm text-aegis-text-secondary">
          Connect any Model Context Protocol server and give the agent its tools.
        </p>
      </div>

      <div className="px-8 pb-8 space-y-8">
        {/* Safety warning */}
        <div className="flex items-center gap-2 bg-aegis-error/5 border border-aegis-error/20 rounded-lg px-3 py-2">
          <ShieldAlert className="w-3.5 h-3.5 text-aegis-error flex-shrink-0" />
          <p className="text-[13px] text-aegis-text-secondary">
            <span className="font-semibold text-aegis-error">Only connect servers you trust</span> — a connected server runs as a real local process; Aegis doesn't sandbox third-party ones.
          </p>
        </div>

        {/* Catalog */}
        {availableCatalog.length > 0 && (
          <section>
            <h2 className="text-sm font-semibold text-aegis-text-primary mb-3 flex items-center gap-1.5">
              <Store className="w-4 h-4 text-aegis-primary-light" /> Browse catalog
            </h2>
            <p className="text-[13px] text-aegis-text-muted mb-3">
              Pre-configured servers — just an API key, folder, or nothing at all to get going.
            </p>
            <div className="space-y-5">
              {Object.entries(catalogByCategory).map(([category, entries]) => (
                <div key={category}>
                  <div className="text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wider mb-2">{category}</div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 items-start">
                    {entries.map(entry => (
                      <CatalogCard
                        key={entry.name}
                        entry={entry}
                        connecting={entry.name in pending}
                        onConnect={(env, inputParams) => handleCatalogConnect(entry, env, inputParams)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Add a custom server — collapsed by default so a wall of raw JSON
            doesn't compete for attention with the friendly catalog above;
            this is the power-user path, not the primary one. */}
        <section>
          <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Add a custom server</h2>
          <div className="bg-aegis-raised rounded-xl border border-aegis-border shadow-sm overflow-hidden">
            <button
              onClick={() => setCustomServerOpen(v => !v)}
              className="w-full flex items-center gap-2.5 px-5 py-4 text-left"
            >
              <Code2 className="w-4 h-4 text-aegis-text-muted flex-shrink-0" />
              <p className="flex-1 min-w-0 text-[13px] text-aegis-text-secondary">Not in the catalog? Paste an mcpServers JSON config instead.</p>
              <ChevronRight className={`w-4 h-4 text-aegis-text-muted flex-shrink-0 transition-transform duration-200 ${customServerOpen ? 'rotate-90' : ''}`} />
            </button>

            {customServerOpen && (
              <div className="px-5 pb-5">
                <p className="text-[11px] text-aegis-text-muted mb-3">
                  The same <code className="text-aegis-primary-light">mcpServers</code> JSON config other MCP clients use.
                  If the command is <code>npx</code> or <code>uvx</code> and isn't already installed, Aegis downloads a portable
                  copy for you automatically — anything else needs to already be on your system's PATH.
                </p>
                <textarea
                  value={jsonInput}
                  onChange={(e) => setJsonInput(e.target.value)}
                  placeholder={PLACEHOLDER}
                  spellCheck={false}
                  rows={8}
                  className="w-full bg-aegis-overlay border border-aegis-border rounded-lg p-3 text-xs font-mono text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary resize-y"
                />
                <div className="flex justify-end mt-3">
                  <button
                    onClick={handleAdd}
                    disabled={submitting || !jsonInput.trim()}
                    className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                    {submitting ? 'Starting…' : 'Connect'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Live progress for in-flight connects */}
        {pendingNames.length > 0 && (
          <section>
            <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Connecting…</h2>
            <div className="space-y-2">
              {pendingNames.map((name) => {
                const p = pending[name];
                const isDownload = p.stage === 'download' && typeof p.progress === 'number';
                return (
                  <div key={name} className="bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
                    <div className="flex items-center gap-2.5">
                      {isDownload ? (
                        <Download className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />
                      ) : (
                        <Loader2 className="w-4 h-4 text-aegis-primary-light flex-shrink-0 animate-spin" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-semibold text-aegis-text-primary truncate">{name}</div>
                        <div className="text-[11px] text-aegis-text-muted truncate">{p.message}</div>
                      </div>
                    </div>
                    {isDownload && (
                      <div className="w-full h-1 rounded-full bg-aegis-base overflow-hidden mt-2">
                        <div
                          className="h-full bg-aegis-primary transition-all duration-200"
                          style={{ width: `${p.progress}%` }}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* Connected servers — plain section header + card rows, riding the
            single page-level scroll like every other section, instead of
            being boxed inside its own fixed card. */}
        <section>
          <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">
            Connected servers
            {serverNames.length > 0 && (
              <span className="text-aegis-text-muted font-normal"> ({serverNames.length} server{serverNames.length === 1 ? '' : 's'}, {tools.length} tool{tools.length === 1 ? '' : 's'} total)</span>
            )}
          </h2>

          {!loaded ? (
            <p className="text-sm text-aegis-text-muted">Loading...</p>
          ) : serverNames.length === 0 ? (
            <p className="text-sm text-aegis-text-muted">No MCP servers connected yet.</p>
          ) : (
            <div className="space-y-2">
              {serverNames.map((name) => {
                const s = statusMap[name];
                const busy = busyServer === name;
                return (
                  <div key={name} className="flex items-center gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
                    <div className="relative flex-shrink-0">
                      <ServiceLogo serviceKey={name} size="sm" />
                      {s.running ? (
                        <CheckCircle2 className="w-3 h-3 text-aegis-success absolute -bottom-0.5 -right-0.5 bg-aegis-raised rounded-full" />
                      ) : (
                        <XCircle className="w-3 h-3 text-aegis-error absolute -bottom-0.5 -right-0.5 bg-aegis-raised rounded-full" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] font-semibold text-aegis-text-primary truncate">{name}</p>
                      <p className="text-[11px] text-aegis-text-muted">
                        {s.running ? `Running` : 'Not running'} · {s.tools_count} tool{s.tools_count === 1 ? '' : 's'}
                        {s.server_info?.version ? ` · v${s.server_info.version}` : ''}
                      </p>
                    </div>
                    <div className="flex-shrink-0 flex items-center gap-2">
                      <button
                        onClick={() => handleReload(name)}
                        disabled={busy}
                        title="Reload"
                        className="p-1.5 rounded-md hover:bg-aegis-overlay text-aegis-text-muted disabled:opacity-40 transition-colors"
                      >
                        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                      </button>
                      <button
                        onClick={() => handleDisconnect(name)}
                        disabled={busy}
                        title="Disconnect"
                        className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
