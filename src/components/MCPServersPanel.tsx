import React, { useState, useEffect, useCallback } from 'react';
import { Plug, ShieldAlert, RefreshCw, Trash2, Loader2, CheckCircle2, XCircle, Plus, Download, Store, Eye, EyeOff, ChevronRight, Code2, Search, Globe, Wrench, X, Lock, FileText, Mail } from 'lucide-react';
import { SiGithub } from 'react-icons/si';
import toast from 'react-hot-toast';
import { useSocket } from '../hooks/useSocket';
import { ServiceLogo } from '../lib/serviceIcons';
import { openInBrowser } from '../lib/openInBrowser';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface ServerStatus {
  running: boolean;
  server_info?: { name?: string; version?: string };
  tools_count: number;
  resources_count?: number;
  prompts_count?: number;
}

// resources/list and prompts/get's own MCP shapes — see backend/app/mcp/
// registry.py's list_resources/get_prompt and connectors.py's
// /{server_name}/resources|prompts routes.
interface McpResource {
  uri: string;
  name?: string;
  description?: string;
  mimeType?: string;
}

interface McpPromptArgument {
  name: string;
  description?: string;
  required?: boolean;
}

interface McpPrompt {
  name: string;
  description?: string;
  arguments?: McpPromptArgument[];
}

interface McpPromptMessage {
  role: string;
  content: { type: string; text?: string };
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

// Mirrors app.mcp.catalog's CONNECTORS_CATALOG entry shape. auth_type=="oauth"
// entries only reach the frontend while app.core.feature_flags.CONNECTORS_ENABLED
// is on — Aegis has no hosted OAuth broker, so these need the user's OWN OAuth
// app (client_id/client_secret) pasted in via env_schema, same as an api_key
// connector; the difference is only in what "Connect" does afterward (see
// handleCatalogConnect).
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
  auth_type: 'api_key' | 'path' | 'connection_string' | 'none' | 'oauth';
  env_schema: CatalogField[];
  input_schema: CatalogField[];
  oauth_service?: string;  // matches oauth_service.OAUTH_CONFIGS key; absent for google_* entries
  setup_hint?: string;
  // Present only on a connector from aegisaistudio.online's public catalog
  // (see backend's RemoteConnector / remote_entry_to_catalog_dict) — not on
  // any built-in entry.
  remote?: boolean;
  setup_guide?: string;  // markdown walkthrough for where to get this connector's credential
  needs_reconnect?: boolean;  // the website updated this connector's definition while it was actively connected — see account_auth.py's _resync_catalog_in_background
  locked?: boolean;  // remote entry, but this account's plan isn't "paid" yet — see catalog.py's remote_entry_to_catalog_dict
}

// Mirrors registry_client.py's _extract_install output — a single server
// entry from the public MCP Registry reduced to one thing Aegis can act on
// directly: either a hosted URL (Streamable HTTP) or a local npm/pypi
// package run as a stdio subprocess. `install` is null when neither shape
// was recognized (e.g. docker-only) — nothing to auto-install then.
interface RegistryInstall {
  kind: 'remote' | 'stdio';
  url?: string;
  transport?: string;
  headers?: CatalogField[];
  command?: string[];
  env_schema?: CatalogField[];
}

interface RegistryResult {
  name: string;
  description?: string;
  version?: string;
  repository_url?: string;
  install: RegistryInstall | null;
}

// Mirrors github_installer.py's detect_run_config output.
interface GithubDetectResult {
  repo_dir: string;
  detected: boolean;
  command: string[] | null;
  build_steps: string[][];
  runtime: string | null;
  reason?: string;
}

function RegistryResultCard({ entry, connecting, onInstall }: { entry: RegistryResult; connecting: boolean; onInstall: (values: Record<string, string>) => void }) {
  const install = entry.install;
  const fields = install ? (install.kind === 'remote' ? install.headers : install.env_schema) || [] : [];
  const hasRequiredFields = fields.some(f => f.required);
  const [expanded, setExpanded] = useState(!hasRequiredFields);
  const [values, setValues] = useState<Record<string, string>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});

  const handleClick = () => {
    if (hasRequiredFields && !expanded) { setExpanded(true); return; }
    onInstall(values);
  };

  const handleCancel = () => {
    setExpanded(false);
    setValues({});
    setRevealed({});
  };

  return (
    <div className={`bg-aegis-raised rounded-xl border border-aegis-border shadow-sm px-3.5 py-3 ${expanded && fields.length > 0 ? 'lg:col-span-2' : ''}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-aegis-text-primary truncate">{entry.name}</span>
            {install && (
              <span className="flex-shrink-0 text-[9px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded bg-aegis-overlay text-aegis-text-muted">
                {install.kind === 'remote' ? 'hosted' : 'local'}
              </span>
            )}
            {entry.version && <span className="flex-shrink-0 text-[10px] text-aegis-text-muted">v{entry.version}</span>}
          </div>
          {entry.description && <div className="text-[10px] text-aegis-text-muted leading-relaxed line-clamp-2 mt-0.5">{entry.description}</div>}
        </div>
        {install ? (
          <div className="flex-shrink-0 flex items-center gap-1.5">
            {hasRequiredFields && expanded && !connecting && (
              <button
                onClick={handleCancel}
                title="Cancel"
                className="p-1.5 rounded-lg text-aegis-text-muted hover:bg-aegis-overlay hover:text-aegis-text-secondary transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
            <button
              onClick={handleClick}
              disabled={connecting}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-aegis-primary text-white text-[11px] font-semibold rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {connecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
              {connecting ? 'Connecting…' : expanded || !hasRequiredFields ? 'Install' : 'Configure'}
            </button>
          </div>
        ) : (
          <span className="flex-shrink-0 text-[10px] text-aegis-text-muted">Not auto-installable</span>
        )}
      </div>

      {expanded && fields.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          {fields.map(f => (
            <div key={f.key}>
              <label className="text-[10px] text-aegis-text-muted">{f.label}{f.required && ' *'}</label>
              <div className="relative">
                <input
                  type={f.secret && !revealed[f.key] ? 'password' : 'text'}
                  value={values[f.key] || ''}
                  onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))}
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
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Only ever rendered with unlocked entries — MCPServersPanel filters locked
// ones out into the one "Get prebuilt connectors config" summary card
// instead (see lockedCatalog below), so there's no locked state to handle
// here.
function CatalogCard({ entry, connecting, onConnect }: { entry: CatalogEntry; connecting: boolean; onConnect: (env: Record<string, string>, inputParams: Record<string, string>) => void }) {
  const needsConfiguring = entry.auth_type !== 'none';
  const [expanded, setExpanded] = useState(!needsConfiguring);
  const [values, setValues] = useState<Record<string, string>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const fields = [...entry.env_schema, ...entry.input_schema];

  const handleConnect = () => {
    if (needsConfiguring && !expanded) { setExpanded(true); return; }
    const env: Record<string, string> = {};
    entry.env_schema.forEach(f => { if (values[f.key]) env[f.key] = values[f.key]; });
    const inputParams: Record<string, string> = {};
    entry.input_schema.forEach(f => { if (values[f.key]) inputParams[f.key] = values[f.key]; });
    onConnect(env, inputParams);
  };

  const handleCancel = () => {
    setExpanded(false);
    setValues({});
    setRevealed({});
  };

  return (
    <div
      className={`bg-aegis-raised rounded-xl border border-aegis-border shadow-sm px-3.5 py-3 hover:border-aegis-primary/40 hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 ${expanded && fields.length > 0 ? 'lg:col-span-2' : ''}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <ServiceLogo serviceKey={entry.name} />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-semibold text-aegis-text-primary">{entry.display_name}</span>
              {entry.remote && (
                <span className="text-[9px] font-medium text-aegis-primary-light bg-aegis-primary/10 rounded px-1 py-0.5">
                  aegisaistudio.online
                </span>
              )}
            </div>
            <div className="text-[10px] text-aegis-text-muted leading-relaxed line-clamp-2">{entry.description}</div>
          </div>
        </div>
        <div className="flex-shrink-0 flex items-center gap-1.5">
          {/* Only a real "in-progress configuration" has anything to cancel back
              out of — an auth_type "none" entry never expands into a form. */}
          {needsConfiguring && expanded && !connecting && (
            <button
              onClick={handleCancel}
              title="Cancel"
              className="p-1.5 rounded-lg text-aegis-text-muted hover:bg-aegis-overlay hover:text-aegis-text-secondary transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            onClick={handleConnect}
            disabled={connecting}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-aegis-primary text-white text-[11px] font-semibold rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {connecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
            {connecting ? 'Connecting…' : entry.auth_type === 'none' ? 'Connect' : expanded ? 'Connect' : 'Configure'}
          </button>
        </div>
      </div>

      {entry.needs_reconnect && (
        <p className="mt-2 pl-[52px] text-[10px] text-aegis-warning bg-aegis-warning/10 rounded-md px-2 py-1.5 leading-relaxed">
          This connector was updated on aegisaistudio.online — reconnect to apply the change.
        </p>
      )}

      {expanded && fields.length > 0 && (
        <div className="mt-3 pl-[52px] flex flex-col gap-2">
          {entry.setup_guide && (
            <p className="text-[10px] text-aegis-text-muted bg-aegis-overlay rounded-md px-2 py-1.5 leading-relaxed whitespace-pre-wrap">
              {entry.setup_guide}
            </p>
          )}
          {entry.auth_type === 'oauth' && entry.setup_hint && (
            <p className="text-[10px] text-aegis-text-muted bg-aegis-overlay rounded-md px-2 py-1.5 leading-relaxed">
              {entry.setup_hint}
            </p>
          )}
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

// Renders one resource's fetched content — an MCP resources/read result is
// {"contents": [{uri, mimeType, text?, blob?}, ...]}. An image with real
// bytes (blob, base64) renders as an actual <img>; text renders as-is;
// anything else falls back to a raw JSON dump rather than silently hiding
// it.
function ResourceContentView({ content }: { content: any }) {
  const contents = content?.contents || [];
  if (contents.length === 0) return <p className="text-[10px] text-aegis-text-muted">Empty resource.</p>;
  return (
    <div className="flex flex-col gap-2">
      {contents.map((c: any, i: number) => {
        if (c.mimeType?.startsWith('image/') && c.blob) {
          return <img key={i} src={`data:${c.mimeType};base64,${c.blob}`} alt={c.uri || 'resource'} className="max-w-full rounded-md" />;
        }
        if (typeof c.text === 'string') {
          return <pre key={i} className="text-[10px] text-aegis-text-primary whitespace-pre-wrap break-words font-mono">{c.text}</pre>;
        }
        return <pre key={i} className="text-[10px] text-aegis-text-muted whitespace-pre-wrap break-words font-mono">{JSON.stringify(c, null, 2)}</pre>;
      })}
    </div>
  );
}

// A connected server's resources/prompts — the two MCP primitives beyond
// tools (see backend/app/mcp/registry.py's list_resources/list_prompts).
// Fetched lazily, once, when this section is first expanded — most
// connected servers declare neither, so there's no point fetching this for
// every server up front.
function ServerResourcesPrompts({ serverName }: { serverName: string }) {
  const [resources, setResources] = useState<McpResource[] | null>(null);
  const [prompts, setPrompts] = useState<McpPrompt[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedResource, setExpandedResource] = useState<string | null>(null);
  const [resourceContent, setResourceContent] = useState<Record<string, any>>({});
  const [expandedPrompt, setExpandedPrompt] = useState<string | null>(null);
  const [promptArgs, setPromptArgs] = useState<Record<string, string>>({});
  const [promptResult, setPromptResult] = useState<{ messages: McpPromptMessage[] } | null>(null);
  const [promptLoading, setPromptLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch(`${API_BASE}/api/connectors/${encodeURIComponent(serverName)}/resources`).then(r => (r.ok ? r.json() : { resources: [] })),
      fetch(`${API_BASE}/api/connectors/${encodeURIComponent(serverName)}/prompts`).then(r => (r.ok ? r.json() : { prompts: [] })),
    ])
      .then(([resData, promptData]) => {
        if (cancelled) return;
        setResources(resData.resources || []);
        setPrompts(promptData.prompts || []);
      })
      .catch(() => {
        if (!cancelled) { setResources([]); setPrompts([]); toast.error(`Could not load resources/prompts for ${serverName}.`); }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [serverName]);

  const toggleResource = async (uri: string) => {
    if (expandedResource === uri) { setExpandedResource(null); return; }
    setExpandedResource(uri);
    if (resourceContent[uri]) return; // already fetched
    try {
      const res = await fetch(`${API_BASE}/api/connectors/${encodeURIComponent(serverName)}/resources/read?uri=${encodeURIComponent(uri)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');
      setResourceContent(prev => ({ ...prev, [uri]: data }));
    } catch {
      setResourceContent(prev => ({ ...prev, [uri]: { error: true } }));
    }
  };

  const runPrompt = async (name: string) => {
    setPromptLoading(true);
    setPromptResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/connectors/${encodeURIComponent(serverName)}/prompts/${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ arguments: promptArgs }),
      });
      const data = await res.json();
      if (res.ok) setPromptResult(data);
      else toast.error(data.detail || `Could not get prompt '${name}'.`);
    } catch {
      toast.error(`Could not reach the backend to get prompt '${name}'.`);
    } finally {
      setPromptLoading(false);
    }
  };

  if (loading) return <p className="text-[11px] text-aegis-text-muted pl-[52px] py-1">Loading resources & prompts…</p>;
  if ((resources?.length || 0) === 0 && (prompts?.length || 0) === 0) {
    return <p className="text-[11px] text-aegis-text-muted pl-[52px] py-1">This server doesn't expose any resources or prompts.</p>;
  }

  return (
    <div className="pl-[52px] flex flex-col gap-3 mt-2 mb-1">
      {resources && resources.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-aegis-text-muted uppercase mb-1">Resources ({resources.length})</div>
          <div className="flex flex-col gap-1">
            {resources.map(r => {
              const isOpen = expandedResource === r.uri;
              const content = resourceContent[r.uri];
              return (
                <div key={r.uri} className="border border-aegis-border rounded-md">
                  <button
                    onClick={() => toggleResource(r.uri)}
                    className="w-full flex items-center justify-between gap-2 px-2 py-1.5 text-left hover:bg-aegis-overlay transition-colors"
                  >
                    <div className="min-w-0">
                      <div className="text-[11px] font-medium text-aegis-text-primary truncate">{r.name || r.uri}</div>
                      <div className="text-[10px] text-aegis-text-muted truncate">{r.uri}</div>
                    </div>
                    <ChevronRight className={`w-3 h-3 text-aegis-text-muted flex-shrink-0 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                  </button>
                  {isOpen && (
                    <div className="px-2 pb-2 border-t border-aegis-border pt-2">
                      {!content ? (
                        <Loader2 className="w-3 h-3 animate-spin text-aegis-text-muted" />
                      ) : content.error ? (
                        <p className="text-[10px] text-aegis-error">Could not read this resource.</p>
                      ) : (
                        <ResourceContentView content={content} />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {prompts && prompts.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-aegis-text-muted uppercase mb-1">Prompts ({prompts.length})</div>
          <div className="flex flex-col gap-1">
            {prompts.map(p => {
              const isOpen = expandedPrompt === p.name;
              return (
                <div key={p.name} className="border border-aegis-border rounded-md">
                  <button
                    onClick={() => { setExpandedPrompt(isOpen ? null : p.name); setPromptResult(null); setPromptArgs({}); }}
                    className="w-full flex items-center justify-between gap-2 px-2 py-1.5 text-left hover:bg-aegis-overlay transition-colors"
                  >
                    <div className="min-w-0">
                      <div className="text-[11px] font-medium text-aegis-text-primary truncate">{p.name}</div>
                      {p.description && <div className="text-[10px] text-aegis-text-muted truncate">{p.description}</div>}
                    </div>
                    <ChevronRight className={`w-3 h-3 text-aegis-text-muted flex-shrink-0 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                  </button>
                  {isOpen && (
                    <div className="px-2 pb-2 border-t border-aegis-border pt-2 flex flex-col gap-2">
                      {(p.arguments || []).map(arg => (
                        <div key={arg.name}>
                          <label className="text-[10px] text-aegis-text-muted">{arg.name}{arg.required && ' *'}</label>
                          <input
                            value={promptArgs[arg.name] || ''}
                            onChange={e => setPromptArgs(prev => ({ ...prev, [arg.name]: e.target.value }))}
                            placeholder={arg.description}
                            className="w-full bg-aegis-base border border-aegis-border rounded-md px-2 py-1 text-[11px] text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                          />
                        </div>
                      ))}
                      <button
                        onClick={() => runPrompt(p.name)}
                        disabled={promptLoading}
                        className="self-start flex items-center gap-1 px-2 py-1 bg-aegis-primary text-white text-[10px] font-semibold rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity"
                      >
                        {promptLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                        Get prompt
                      </button>
                      {promptResult && (
                        <div className="flex flex-col gap-1.5 mt-1">
                          {(promptResult.messages || []).map((m, i) => (
                            <div key={i} className="text-[10px] bg-aegis-overlay rounded-md px-2 py-1.5">
                              <span className="font-semibold text-aegis-text-secondary">{m.role}: </span>
                              <span className="text-aegis-text-primary whitespace-pre-wrap">{m.content?.text || JSON.stringify(m.content)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
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
  const [expandedRPServer, setExpandedRPServer] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [customServerOpen, setCustomServerOpen] = useState(false);

  // Registry search
  const [registryOpen, setRegistryOpen] = useState(false);
  const [registryQuery, setRegistryQuery] = useState('');
  const [registryResults, setRegistryResults] = useState<RegistryResult[]>([]);
  const [registrySearching, setRegistrySearching] = useState(false);
  const [registrySearched, setRegistrySearched] = useState(false);

  // Remote (Streamable HTTP) server
  const [remoteOpen, setRemoteOpen] = useState(false);
  const [remoteName, setRemoteName] = useState('');
  const [remoteUrl, setRemoteUrl] = useState('');
  const [remoteHeaderKey, setRemoteHeaderKey] = useState('');
  const [remoteHeaderValue, setRemoteHeaderValue] = useState('');

  // GitHub install
  const [githubOpen, setGithubOpen] = useState(false);
  const [githubRepoUrl, setGithubRepoUrl] = useState('');
  const [githubDetecting, setGithubDetecting] = useState(false);
  const [githubDetected, setGithubDetected] = useState<GithubDetectResult | null>(null);
  const [githubServerName, setGithubServerName] = useState('');
  const [githubCommandText, setGithubCommandText] = useState('');

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
    } catch (e) {
      toast.error('Could not load the connector catalog.');
    }
  }, []);

  // Bypasses the account's normal 6-hour lazy plan/catalog resync
  // (app.api.account_auth's /status) — without this, a user who just paid
  // for the catalog would see it still show as locked here for up to 6
  // hours. Best-effort: no toast on failure (offline, or no Aegis account
  // signed in at all — same "silently no-op" contract the backend's own
  // _resync_*_in_background functions already have), since this fires
  // automatically on every mount, not just an explicit user action.
  const [resyncing, setResyncing] = useState(false);
  const resyncAccount = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/account/resync`, { method: 'POST' });
    } catch (e) {
      // offline, or no Aegis account signed in — fetchCatalog right after
      // this still shows whatever was already cached locally either way.
    }
  }, []);

  useEffect(() => {
    (async () => {
      await resyncAccount();
      fetchCatalog();
    })();
    fetchStatus();
    const interval = setInterval(fetchStatus, 8000);
    return () => clearInterval(interval);
  }, [fetchStatus, fetchCatalog, resyncAccount]);


  const handleOAuthConnect = async (entry: CatalogEntry, env: Record<string, string>) => {
    const clientId = env['OAUTH_CLIENT_ID'];
    const clientSecret = env['OAUTH_CLIENT_SECRET'];
    const isGoogle = entry.name.startsWith('google_');
    // Already configured (fields were left blank because a prior save
    // exists) — just go straight to login.
    if (!clientId || !clientSecret) {
      const loginUrl = isGoogle
        ? `${API_BASE}/auth/google/login?service=${entry.name}`
        : `${API_BASE}/auth/${entry.oauth_service || entry.name}/login`;
      openInBrowser(loginUrl);
      return;
    }
    setPending(prev => ({ ...prev, [entry.name]: { stage: 'connect', message: 'Saving OAuth app…' } }));
    try {
      const configureUrl = isGoogle
        ? `${API_BASE}/auth/google/configure`
        : `${API_BASE}/auth/${entry.oauth_service || entry.name}/configure`;
      const res = await fetch(configureUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.error || data.detail || `Failed to save the OAuth app for '${entry.display_name}'.`);
        return;
      }
      const loginUrl = isGoogle
        ? `${API_BASE}/auth/google/login?service=${entry.name}`
        : `${API_BASE}/auth/${entry.oauth_service || entry.name}/login`;
      openInBrowser(loginUrl);
      toast.success(`OAuth app saved. Finish signing in to ${entry.display_name} in the browser window that just opened.`, { duration: 6000 });
    } catch (e) {
      toast.error('Could not reach the backend.');
    } finally {
      setPending(prev => { const n = { ...prev }; delete n[entry.name]; return n; });
    }
  };

  const handleCatalogConnect = async (entry: CatalogEntry, env: Record<string, string>, inputParams: Record<string, string>) => {
    if (entry.auth_type === 'oauth') {
      await handleOAuthConnect(entry, env);
      return;
    }
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

  const startStdioConnect = async (serverName: string, command: string[], env: Record<string, string> | null) => {
    setPending(prev => ({ ...prev, [serverName]: { stage: 'detect', message: 'Starting…' } }));
    try {
      const res = await fetch(`${API_BASE}/api/connectors/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_name: serverName, command, env }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || `Failed to connect '${serverName}'.`);
        setPending(prev => { const n = { ...prev }; delete n[serverName]; return n; });
      }
    } catch (e) {
      toast.error('Could not reach the backend.');
      setPending(prev => { const n = { ...prev }; delete n[serverName]; return n; });
    }
  };

  const startRemoteConnect = async (serverName: string, url: string, headers: Record<string, string> | null) => {
    setPending(prev => ({ ...prev, [serverName]: { stage: 'connect', message: `Connecting to '${serverName}'…` } }));
    try {
      const res = await fetch(`${API_BASE}/api/connectors/remote/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_name: serverName, url, headers }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || `Failed to connect '${serverName}'.`);
        setPending(prev => { const n = { ...prev }; delete n[serverName]; return n; });
      }
    } catch (e) {
      toast.error('Could not reach the backend.');
      setPending(prev => { const n = { ...prev }; delete n[serverName]; return n; });
    }
  };

  const handleRegistrySearch = async () => {
    if (!registryQuery.trim()) return;
    setRegistrySearching(true);
    setRegistrySearched(true);
    try {
      const res = await fetch(`${API_BASE}/api/connectors/registry/search?q=${encodeURIComponent(registryQuery.trim())}`);
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setRegistryResults(data.results || []);
      } else {
        toast.error(data.detail || 'Registry search failed.');
        setRegistryResults([]);
      }
    } catch (e) {
      toast.error('Could not reach the backend.');
    } finally {
      setRegistrySearching(false);
    }
  };

  const handleRegistryInstall = (entry: RegistryResult, values: Record<string, string>) => {
    if (!entry.install) return;
    const serverName = entry.name.split('/').pop() || entry.name;
    if (entry.install.kind === 'remote' && entry.install.url) {
      startRemoteConnect(serverName, entry.install.url, Object.keys(values).length ? values : null);
    } else if (entry.install.kind === 'stdio' && entry.install.command) {
      startStdioConnect(serverName, entry.install.command, Object.keys(values).length ? values : null);
    }
  };

  const handleRemoteConnect = () => {
    const name = remoteName.trim();
    const url = remoteUrl.trim();
    if (!name || !url) {
      toast.error('Enter a name and a server URL.');
      return;
    }
    const headers = remoteHeaderKey.trim() ? { [remoteHeaderKey.trim()]: remoteHeaderValue } : null;
    startRemoteConnect(name, url, headers);
    setRemoteName(''); setRemoteUrl(''); setRemoteHeaderKey(''); setRemoteHeaderValue('');
  };

  const handleGithubDetect = async () => {
    const repoUrl = githubRepoUrl.trim();
    if (!repoUrl) return;
    setGithubDetecting(true);
    setGithubDetected(null);
    try {
      const res = await fetch(`${API_BASE}/api/connectors/github/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url: repoUrl }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(data.detail || 'Failed to inspect the repo.');
        return;
      }
      setGithubDetected(data);
      setGithubCommandText((data.command || []).join(' '));
      if (!githubServerName.trim()) {
        const guess = repoUrl.replace(/\/$/, '').replace(/\.git$/, '').split('/').pop() || '';
        setGithubServerName(guess);
      }
    } catch (e) {
      toast.error('Could not reach the backend.');
    } finally {
      setGithubDetecting(false);
    }
  };

  const handleGithubInstall = async () => {
    const command = githubCommandText.trim().split(/\s+/).filter(Boolean);
    const serverName = githubServerName.trim();
    if (!serverName || command.length === 0) {
      toast.error('Enter a server name and a run command.');
      return;
    }
    setPending(prev => ({ ...prev, [serverName]: { stage: 'clone', message: 'Starting…' } }));
    try {
      const res = await fetch(`${API_BASE}/api/connectors/github/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_url: githubRepoUrl.trim(),
          server_name: serverName,
          command,
          build_steps: githubDetected?.build_steps || [],
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || `Failed to install '${serverName}'.`);
        setPending(prev => { const n = { ...prev }; delete n[serverName]; return n; });
      } else {
        setGithubRepoUrl(''); setGithubDetected(null); setGithubCommandText(''); setGithubServerName('');
      }
    } catch (e) {
      toast.error('Could not reach the backend.');
      setPending(prev => { const n = { ...prev }; delete n[serverName]; return n; });
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

  // OAuth connects finish in the system browser (google_oauth.py /
  // oauth_routes.py), not the background-task pipeline above — they
  // broadcast `auth_ready`/`error` keyed by `service`, not `server_name`.
  useEffect(() => {
    return addMessageHandler((payload: any) => {
      const { type, service } = payload;
      if (!service) return;
      if (type === 'auth_ready') {
        toast.success(payload.content || `Connected '${service}'.`);
        fetchStatus();
      } else if (type === 'error') {
        toast.error(payload.content || `Could not connect '${service}'.`, { duration: 8000 });
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
  // Locked entries are all "the same card" from the user's point of view —
  // every one leads to the exact same place (pay once, unlock everything).
  // Once a plan unlocks them they're genuinely different tools worth
  // browsing individually, so only collapse them into one summary card
  // while still locked — see the "Unlock the catalog" section below.
  const lockedCatalog = availableCatalog.filter(c => c.locked);
  const unlockedCatalog = availableCatalog.filter(c => !c.locked);
  const catalogByCategory = unlockedCatalog.reduce<Record<string, CatalogEntry[]>>((acc, entry) => {
    (acc[entry.category] ||= []).push(entry);
    return acc;
  }, {});

  return (
    <div className="flex-1 overflow-y-auto bg-aegis-base">
      <div className="px-8 pt-8 pb-5">
        <div className="flex items-center gap-3 mb-1">
          <Plug className="w-6 h-6 text-aegis-primary" />
          <h1 className="text-2xl font-bold text-aegis-text-primary">Connectors</h1>
        </div>
        <p className="text-sm text-aegis-text-secondary">
          Connect any Model Context Protocol server — from the catalog, the public registry, a GitHub repo, or a hosted URL — and give the agent its tools.
        </p>
      </div>

      <div className="px-8 pb-8 space-y-8">
        {/* Custom MCP request */}
        <div className="flex items-center gap-2 bg-aegis-primary/5 border border-aegis-primary/20 rounded-lg px-3 py-2">
          <Mail className="w-3.5 h-3.5 text-aegis-primary flex-shrink-0" />
          <p className="text-[13px] text-aegis-text-secondary">
            Can't find the server you need?{' '}
            <button
              onClick={() => openInBrowser('mailto:kingzkritiman@gmail.com?subject=Custom%20MCP%20server%20request')}
              className="font-semibold text-aegis-primary hover:underline"
            >
              Mail us
            </button>{' '}
            and we'll look into building a custom connector for it.
          </p>
        </div>

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

            {lockedCatalog.length > 0 && (
              <div className="mb-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 rounded-xl border border-aegis-primary/30 bg-gradient-to-br from-aegis-primary/10 to-transparent px-5 py-4">
                <div className="flex items-start gap-3">
                  <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-aegis-primary/15 flex items-center justify-center">
                    <Lock className="w-4 h-4 text-aegis-primary-light" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-aegis-text-primary">Get prebuilt connectors config</p>
                    <p className="text-[12px] text-aegis-text-muted mt-0.5 leading-relaxed">
                      {lockedCatalog.length} pre-built connectors — Slack, Notion, AWS, Google Workspace, and more —
                      unlock with one subscription on aegisaistudio.online.
                    </p>
                  </div>
                </div>
                <div className="flex-shrink-0 flex items-center gap-2">
                  {/* Already paid but this panel hasn't noticed yet (opening
                      it already triggers a resync automatically — this is
                      just a visible, explicit way to ask again, e.g. right
                      after finishing checkout in the browser). */}
                  <button
                    onClick={async () => {
                      setResyncing(true);
                      await resyncAccount();
                      await fetchCatalog();
                      setResyncing(false);
                      toast.success('Refreshed.');
                    }}
                    disabled={resyncing}
                    title="Already paid? Check again"
                    className="flex items-center gap-1.5 px-3 py-2 text-aegis-text-secondary text-[12px] font-medium rounded-lg hover:bg-aegis-overlay transition-colors disabled:opacity-50"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${resyncing ? 'animate-spin' : ''}`} /> Refresh
                  </button>
                  <button
                    // /pricing decides where this visitor actually lands
                    // (the free/paid comparison, or straight through to
                    // checkout, or straight to the connectors dashboard if
                    // they're already on a paid plan — see its own
                    // docstring) rather than this button assuming
                    // "not paid" off its own possibly-stale local cache.
                    onClick={() => openInBrowser('https://aegisaistudio.online/pricing')}
                    className="flex items-center gap-1.5 px-4 py-2 bg-aegis-primary text-white text-[12px] font-semibold rounded-lg hover:opacity-90 transition-opacity"
                  >
                    <Lock className="w-3.5 h-3.5" /> Unlock all connectors
                  </button>
                </div>
              </div>
            )}

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

        {/* Search the public MCP Registry — the community index of published
            servers, so a user can find one by name instead of already
            knowing its package or repo. */}
        <section>
          <div className="bg-aegis-raised rounded-xl border border-aegis-border shadow-sm overflow-hidden">
            <button
              onClick={() => setRegistryOpen(v => !v)}
              className="w-full flex items-center gap-2.5 px-5 py-4 text-left"
            >
              <Search className="w-4 h-4 text-aegis-text-muted flex-shrink-0" />
              <p className="flex-1 min-w-0 text-[13px] text-aegis-text-secondary">Search the public MCP Registry for a server by name.</p>
              <ChevronRight className={`w-4 h-4 text-aegis-text-muted flex-shrink-0 transition-transform duration-200 ${registryOpen ? 'rotate-90' : ''}`} />
            </button>

            {registryOpen && (
              <div className="px-5 pb-5">
                <div className="flex gap-2 mb-3">
                  <input
                    type="text"
                    value={registryQuery}
                    onChange={e => setRegistryQuery(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleRegistrySearch(); }}
                    placeholder="e.g. github, slack, filesystem…"
                    className="flex-1 bg-aegis-overlay border border-aegis-border rounded-lg px-3 py-2 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  />
                  <button
                    onClick={handleRegistrySearch}
                    disabled={registrySearching || !registryQuery.trim()}
                    className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {registrySearching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                    Search
                  </button>
                </div>

                {registrySearched && !registrySearching && registryResults.length === 0 && (
                  <p className="text-[12px] text-aegis-text-muted">No servers found for that search.</p>
                )}

                {registryResults.length > 0 && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 items-start">
                    {registryResults.map(entry => (
                      <RegistryResultCard
                        key={entry.name}
                        entry={entry}
                        connecting={(entry.install ? (entry.name.split('/').pop() || entry.name) : '') in pending}
                        onInstall={values => handleRegistryInstall(entry, values)}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Connect a remote server over Streamable HTTP — a hosted MCP
            endpoint by URL, no local process. No OAuth: only a static
            header value the user already has (an API key, a bearer token
            they generated themselves) — Aegis never runs a login flow. */}
        <section>
          <div className="bg-aegis-raised rounded-xl border border-aegis-border shadow-sm overflow-hidden">
            <button
              onClick={() => setRemoteOpen(v => !v)}
              className="w-full flex items-center gap-2.5 px-5 py-4 text-left"
            >
              <Globe className="w-4 h-4 text-aegis-text-muted flex-shrink-0" />
              <p className="flex-1 min-w-0 text-[13px] text-aegis-text-secondary">Connect a hosted server by URL (Streamable HTTP).</p>
              <ChevronRight className={`w-4 h-4 text-aegis-text-muted flex-shrink-0 transition-transform duration-200 ${remoteOpen ? 'rotate-90' : ''}`} />
            </button>

            {remoteOpen && (
              <div className="px-5 pb-5 flex flex-col gap-3">
                <p className="text-[11px] text-aegis-text-muted">
                  If the server needs a static API key or bearer token (not a login redirect), add it as a header below —
                  Aegis doesn't support servers that require an OAuth sign-in.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-aegis-text-muted">Name</label>
                    <input
                      type="text"
                      value={remoteName}
                      onChange={e => setRemoteName(e.target.value)}
                      placeholder="my-remote-server"
                      className="w-full bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-aegis-text-muted">Server URL</label>
                    <input
                      type="text"
                      value={remoteUrl}
                      onChange={e => setRemoteUrl(e.target.value)}
                      placeholder="https://example.com/mcp"
                      className="w-full bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-aegis-text-muted">Header name (optional)</label>
                    <input
                      type="text"
                      value={remoteHeaderKey}
                      onChange={e => setRemoteHeaderKey(e.target.value)}
                      placeholder="Authorization"
                      className="w-full bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-aegis-text-muted">Header value</label>
                    <input
                      type="password"
                      value={remoteHeaderValue}
                      onChange={e => setRemoteHeaderValue(e.target.value)}
                      placeholder="Bearer …"
                      className="w-full bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                    />
                  </div>
                </div>
                <div className="flex justify-end">
                  <button
                    onClick={handleRemoteConnect}
                    disabled={!remoteName.trim() || !remoteUrl.trim()}
                    className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <Plus className="w-3.5 h-3.5" /> Connect
                  </button>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Install from a GitHub repo — clone, auto-detect a run command,
            let the user review/edit it, then build + connect. */}
        <section>
          <div className="bg-aegis-raised rounded-xl border border-aegis-border shadow-sm overflow-hidden">
            <button
              onClick={() => setGithubOpen(v => !v)}
              className="w-full flex items-center gap-2.5 px-5 py-4 text-left"
            >
              <SiGithub className="w-4 h-4 text-aegis-text-muted flex-shrink-0" />
              <p className="flex-1 min-w-0 text-[13px] text-aegis-text-secondary">Install a server straight from a GitHub repo.</p>
              <ChevronRight className={`w-4 h-4 text-aegis-text-muted flex-shrink-0 transition-transform duration-200 ${githubOpen ? 'rotate-90' : ''}`} />
            </button>

            {githubOpen && (
              <div className="px-5 pb-5 flex flex-col gap-3">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={githubRepoUrl}
                    onChange={e => setGithubRepoUrl(e.target.value)}
                    placeholder="https://github.com/owner/repo"
                    className="flex-1 bg-aegis-overlay border border-aegis-border rounded-lg px-3 py-2 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  />
                  <button
                    onClick={handleGithubDetect}
                    disabled={githubDetecting || !githubRepoUrl.trim()}
                    className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {githubDetecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wrench className="w-3.5 h-3.5" />}
                    {githubDetecting ? 'Inspecting…' : 'Inspect repo'}
                  </button>
                </div>

                {githubDetected && (
                  <div className="flex flex-col gap-2 bg-aegis-overlay rounded-lg p-3">
                    {githubDetected.detected ? (
                      <p className="text-[11px] text-aegis-text-secondary">
                        Detected a {githubDetected.runtime} server{githubDetected.build_steps.length > 0 ? ` — will run ${githubDetected.build_steps.map(s => s.join(' ')).join(' then ')} first` : ''}. Review the command below before installing.
                      </p>
                    ) : (
                      <p className="text-[11px] text-aegis-text-secondary">{githubDetected.reason}</p>
                    )}
                    <div>
                      <label className="text-[10px] text-aegis-text-muted">Name</label>
                      <input
                        type="text"
                        value={githubServerName}
                        onChange={e => setGithubServerName(e.target.value)}
                        className="w-full bg-aegis-base border border-aegis-border rounded-md px-2 py-1.5 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-aegis-text-muted">Run command</label>
                      <input
                        type="text"
                        value={githubCommandText}
                        onChange={e => setGithubCommandText(e.target.value)}
                        placeholder="node /path/to/index.js"
                        spellCheck={false}
                        className="w-full bg-aegis-base border border-aegis-border rounded-md px-2 py-1.5 text-xs font-mono text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                      />
                    </div>
                    <div className="flex justify-end">
                      <button
                        onClick={handleGithubInstall}
                        disabled={!githubServerName.trim() || !githubCommandText.trim()}
                        className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                      >
                        <Plus className="w-3.5 h-3.5" /> Build & connect
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

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
                const rpCount = (s.resources_count || 0) + (s.prompts_count || 0);
                const rpOpen = expandedRPServer === name;
                return (
                  <div key={name} className="bg-aegis-raised border border-aegis-border rounded-xl px-5 py-3">
                    <div className="flex items-center gap-4">
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
                          {rpCount > 0 && ` · ${rpCount} resource${rpCount === 1 ? '' : 's'}/prompt${rpCount === 1 ? '' : 's'}`}
                          {s.server_info?.version ? ` · v${s.server_info.version}` : ''}
                        </p>
                      </div>
                      <div className="flex-shrink-0 flex items-center gap-2">
                        {rpCount > 0 && (
                          <button
                            onClick={() => setExpandedRPServer(rpOpen ? null : name)}
                            title="Resources & prompts"
                            className={`p-1.5 rounded-md hover:bg-aegis-overlay text-aegis-text-muted transition-colors ${rpOpen ? 'bg-aegis-overlay text-aegis-text-secondary' : ''}`}
                          >
                            <FileText className="w-3.5 h-3.5" />
                          </button>
                        )}
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
                    {rpOpen && <ServerResourcesPrompts serverName={name} />}
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
