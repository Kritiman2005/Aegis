'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { HardDriveDownload, CheckCircle2, Loader2, Trash2, Download, ScrollText, RefreshCw, Package, Search, ChevronDown, Sparkles } from 'lucide-react';
import toast from 'react-hot-toast';
import { useSocket } from '../hooks/useSocket';
import { openInBrowser } from '../lib/openInBrowser';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface UpdateCheckResult {
  update_available: boolean;
  current_version: string;
  latest_version?: string | null;
  release_url?: string | null;
  download_url?: string | null;
  release_notes?: string;
  error?: string;
}

// What Aegis itself ships with (backend/app/core/optional_deps.py's
// list_bundled — read from requirements.txt, paired with the real
// installed version). Read-only: these are frozen into the app at build
// time (see backend/main.spec), not something to install/remove from
// here — only a new Aegis release changes this list.
interface BundledPackage {
  name: string;
  version: string | null;
}

// A package installed via the free-text pip install box below — see
// backend/app/core/optional_deps.py's own module docstring for the full
// story (a general-purpose escape hatch for a future dependency nothing
// in Aegis anticipated, not a curated catalog — that earlier design,
// making torch itself an opt-in download, was tried and reverted after
// real Hugging-Face-ecosystem compatibility problems).
interface CustomPackage {
  name: string;
  spec: string;
  status?: 'running';
  message?: string;
}

function PackageRow({ pkg, onChanged }: { pkg: CustomPackage; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const downloading = pkg.status === 'running';

  const handleDelete = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/optional-deps/${pkg.name}`, { method: 'DELETE' });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-3 bg-aegis-raised border border-aegis-border rounded-xl px-4 py-3">
      <div className="min-w-0">
        <p className="text-[13px] font-mono font-medium text-aegis-text-primary truncate">{pkg.spec}</p>
      </div>
      <div className="flex-shrink-0 flex items-center gap-2">
        {downloading ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-aegis-warning">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> {pkg.message || 'Installing…'}
          </span>
        ) : (
          <>
            <span className="inline-flex items-center gap-1 text-[11px] text-aegis-success"><CheckCircle2 className="w-3.5 h-3.5" /> Installed</span>
            <button onClick={handleDelete} disabled={busy} className="p-1.5 rounded-md hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error disabled:opacity-40 transition-colors">
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

interface LogEntry {
  timestamp: string;
  level: string;
  logger: string;
  category: string;
  message: string;
}

function levelColor(level: string): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'text-aegis-error';
  if (level === 'WARNING') return 'text-aegis-warning';
  return 'text-aegis-text-secondary';
}

export default function DependenciesPanel() {
  const { addMessageHandler } = useSocket();
  const [packages, setPackages] = useState<CustomPackage[]>([]);
  const [loading, setLoading] = useState(true);
  const [spec, setSpec] = useState('');
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);

  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logsOpen, setLogsOpen] = useState(true);

  const [bundled, setBundled] = useState<BundledPackage[]>([]);
  const [bundledLoading, setBundledLoading] = useState(true);
  const [bundledOpen, setBundledOpen] = useState(true);
  const [bundledSearch, setBundledSearch] = useState('');

  // Only meaningful inside the packaged Electron app — window.aegis doesn't
  // exist in a plain browser tab (dev-in-Chrome), same guard openInBrowser
  // itself uses. No update section renders at all when it's absent, rather
  // than showing a version check that can never actually resolve.
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckResult | null>(null);
  const [updateChecking, setUpdateChecking] = useState(false);

  const checkForUpdate = useCallback(async () => {
    const aegis = typeof window !== 'undefined' ? (window as any).aegis : undefined;
    if (!aegis?.app?.getVersion) return;
    setUpdateChecking(true);
    try {
      const version = await aegis.app.getVersion();
      const res = await fetch(`${API_BASE}/api/updates/check?current_version=${encodeURIComponent(version)}`);
      if (res.ok) setUpdateCheck(await res.json());
    } catch {
      // offline, or GitHub unreachable — leave whatever was there (or
      // nothing) rather than show an error for a background check.
    } finally {
      setUpdateChecking(false);
    }
  }, []);

  const fetchBundled = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/optional-deps/bundled`);
      if (res.ok) {
        const data = await res.json();
        setBundled(Array.isArray(data.packages) ? data.packages : []);
      }
    } catch {
      // leave state as-is
    } finally {
      setBundledLoading(false);
    }
  }, []);

  const filteredBundled = useMemo(() => {
    const q = bundledSearch.trim().toLowerCase();
    if (!q) return bundled;
    return bundled.filter(p => p.name.toLowerCase().includes(q));
  }, [bundled, bundledSearch]);

  const fetchPackages = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/optional-deps`);
      if (res.ok) {
        const data = await res.json();
        setPackages(Array.isArray(data.packages) ? data.packages : []);
      }
    } catch {
      // leave state as-is
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/optional-deps/logs?limit=200`);
      if (res.ok) {
        const data = await res.json();
        setLogs(Array.isArray(data.logs) ? data.logs : []);
      }
    } catch {
      // leave state as-is
    }
  }, []);

  useEffect(() => { fetchPackages(); fetchLogs(); fetchBundled(); checkForUpdate(); }, [fetchPackages, fetchLogs, fetchBundled, checkForUpdate]);

  // A failed install disappears from the polled /api/optional-deps list
  // with no trace (it never gets a real, persisted "failed" row — see
  // backend/app/core/optional_deps.py's install_custom_sync, which only
  // ever writes the .installed marker on success) — the real error is
  // always in the log viewer below, but a toast surfaces it immediately
  // too, instead of making the user go dig for it.
  useEffect(() => {
    return addMessageHandler((payload: any) => {
      if (payload.type === 'optional_dep_install_failed') {
        toast.error(`Couldn't install ${payload.spec}: ${payload.message}`);
        fetchPackages();
        fetchLogs();
      } else if (payload.type === 'optional_dep_install_complete') {
        toast.success(`Installed ${payload.spec}`);
        fetchPackages();
      }
    });
  }, [addMessageHandler, fetchPackages, fetchLogs]);

  // Same "poll while anything's installing" pattern MarketplaceView
  // already uses — pip gives no byte-level progress to hook (see
  // backend/app/api/optional_deps.py's own note), so this is the only way
  // this panel ever learns a background install finished.
  useEffect(() => {
    if (!packages.some(p => p.status === 'running')) return;
    const t = setInterval(fetchPackages, 2000);
    return () => clearInterval(t);
  }, [packages, fetchPackages]);

  const handleInstall = async () => {
    const trimmed = spec.trim();
    if (!trimmed) return;
    setInstalling(true);
    setInstallError(null);
    try {
      const res = await fetch(`${API_BASE}/api/optional-deps/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec: trimmed }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Install failed to start.');
      }
      setSpec('');
      fetchPackages();
    } catch (e: any) {
      setInstallError(e.message || 'Could not reach the backend.');
    } finally {
      setInstalling(false);
    }
  };

  const handleClearLogs = async () => {
    await fetch(`${API_BASE}/api/optional-deps/logs/clear`, { method: 'DELETE' });
    setLogs([]);
  };

  return (
    <div className="flex-1 overflow-y-auto bg-aegis-base">
      <div className="px-8 pt-8 pb-5">
        <div className="flex items-center gap-3 mb-1">
          <HardDriveDownload className="w-6 h-6 text-aegis-primary" />
          <h1 className="text-2xl font-bold text-aegis-text-primary">Dependencies</h1>
        </div>
        <p className="text-sm text-aegis-text-secondary">
          See what Aegis already ships with, install any package it doesn't, and see what's actually going wrong when something fails — no waiting on a new release for a dependency Aegis didn't anticipate.
        </p>
      </div>

      <div className="px-8 pb-8 space-y-8">
        {updateCheck && (
          <section className={`rounded-xl border p-4 flex items-center gap-4 ${updateCheck.update_available ? 'border-aegis-primary/40 bg-aegis-primary/5' : 'border-aegis-border'}`}>
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${updateCheck.update_available ? 'bg-aegis-primary/15' : 'bg-aegis-overlay'}`}>
              {updateCheck.update_available
                ? <Sparkles className="w-4 h-4 text-aegis-primary-light" />
                : <CheckCircle2 className="w-4 h-4 text-aegis-text-muted" />}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-aegis-text-primary">
                {updateCheck.update_available
                  ? `Aegis ${updateCheck.latest_version} is available`
                  : `Aegis ${updateCheck.current_version} — up to date`}
              </p>
              {updateCheck.update_available && updateCheck.release_notes && (
                <p className="text-[12px] text-aegis-text-muted mt-0.5 line-clamp-2">{updateCheck.release_notes}</p>
              )}
              {!updateCheck.update_available && (
                <p className="text-[11px] text-aegis-text-muted mt-0.5">Checked against the latest GitHub release.</p>
              )}
            </div>
            {updateCheck.update_available ? (
              <button
                onClick={() => updateCheck.download_url && openInBrowser(updateCheck.download_url)}
                className="flex-shrink-0 flex items-center gap-1.5 px-4 py-2 bg-aegis-primary text-white text-[12px] font-semibold rounded-lg hover:opacity-90 transition-colors"
              >
                <Download className="w-3.5 h-3.5" /> Download
              </button>
            ) : (
              <button
                onClick={checkForUpdate}
                disabled={updateChecking}
                title="Check again"
                className="flex-shrink-0 p-2 text-aegis-text-muted hover:text-aegis-text-secondary rounded-lg hover:bg-aegis-overlay transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${updateChecking ? 'animate-spin' : ''}`} />
              </button>
            )}
          </section>
        )}

        <section>
          <button onClick={() => setBundledOpen(v => !v)} className="w-full flex items-center gap-2.5 mb-3 text-left">
            <Package className="w-4 h-4 text-aegis-primary flex-shrink-0" />
            <div className="flex-1">
              <h2 className="text-sm font-semibold text-aegis-text-primary">App dependencies</h2>
              <p className="text-[11px] text-aegis-text-muted mt-0.5">What Aegis itself ships with — read-only, built into the app.</p>
            </div>
            <span className="text-[11px] text-aegis-text-muted tabular-nums">{bundled.length}</span>
            <ChevronDown className={`w-4 h-4 text-aegis-text-muted transition-transform ${bundledOpen ? 'rotate-180' : ''}`} />
          </button>

          {bundledOpen && (
            bundledLoading ? (
              <p className="text-sm text-aegis-text-muted">Loading...</p>
            ) : (
              <>
                <div className="relative mb-2">
                  <Search className="w-3.5 h-3.5 text-aegis-text-muted absolute left-2.5 top-1/2 -translate-y-1/2" />
                  <input
                    value={bundledSearch}
                    onChange={e => setBundledSearch(e.target.value)}
                    placeholder="Filter..."
                    className="w-full bg-aegis-raised border border-aegis-border rounded-lg pl-8 pr-3 py-1.5 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
                  />
                </div>
                <div className="bg-aegis-raised border border-aegis-border rounded-xl max-h-[280px] overflow-y-auto divide-y divide-aegis-border">
                  {filteredBundled.length === 0 ? (
                    <p className="text-xs text-aegis-text-muted p-3">No match.</p>
                  ) : (
                    filteredBundled.map(p => (
                      <div key={p.name} className="flex items-center justify-between gap-3 px-3 py-1.5">
                        <span className="text-[13px] font-mono text-aegis-text-primary truncate">{p.name}</span>
                        <span className="text-[11px] font-mono text-aegis-text-muted flex-shrink-0">{p.version ?? '—'}</span>
                      </div>
                    ))
                  )}
                </div>
              </>
            )
          )}
        </section>

        <section>
          <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Install a package</h2>
          <div className="flex gap-2">
            <input
              value={spec}
              onChange={e => setSpec(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleInstall(); }}
              placeholder="Package spec, e.g. requests==2.31.0"
              className="flex-1 bg-aegis-raised border border-aegis-border rounded-lg px-3 py-2 text-xs font-mono text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:ring-1 focus:ring-aegis-primary"
            />
            <button
              onClick={handleInstall}
              disabled={installing || !spec.trim()}
              className="flex-shrink-0 inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-2 rounded-lg transition-colors disabled:opacity-50"
            >
              {installing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
              Install
            </button>
          </div>
          {installError && <p className="text-[12px] text-aegis-error mt-2">{installError}</p>}
          <p className="text-[10px] text-aegis-text-muted mt-2 leading-relaxed">
            Runs a real <span className="font-mono">pip install</span> — anything installable from PyPI works, exactly as typed (version pins, extras like <span className="font-mono">package[extra]</span>, all supported). Installs into Aegis's own data folder, not the system Python.
          </p>

          {loading ? (
            <p className="text-sm text-aegis-text-muted mt-4">Loading...</p>
          ) : packages.length > 0 ? (
            <div className="flex flex-col gap-2 mt-4">
              {packages.map(p => <PackageRow key={p.name} pkg={p} onChanged={fetchPackages} />)}
            </div>
          ) : null}
        </section>

        <section>
          {/* A real <button> wrapping the Refresh/Clear buttons below is
              invalid HTML (button-in-button) and was causing a React
              hydration error — a plain clickable div with the same
              role/keyboard handling avoids that while keeping the whole
              row clickable to toggle. */}
          <div
            role="button"
            tabIndex={0}
            onClick={() => setLogsOpen(v => !v)}
            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setLogsOpen(v => !v); } }}
            className="w-full flex items-center gap-2.5 mb-3 text-left cursor-pointer"
          >
            <ScrollText className="w-4 h-4 text-aegis-primary flex-shrink-0" />
            <div className="flex-1">
              <h2 className="text-sm font-semibold text-aegis-text-primary">Install problems</h2>
              <p className="text-[11px] text-aegis-text-muted mt-0.5">Why a package couldn't be installed — not general app errors.</p>
            </div>
            <button
              onClick={e => { e.stopPropagation(); fetchLogs(); }}
              title="Refresh"
              className="p-1.5 rounded-md hover:bg-aegis-overlay text-aegis-text-muted hover:text-aegis-text-primary transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={e => { e.stopPropagation(); handleClearLogs(); }}
              className="text-[11px] font-medium text-aegis-text-muted hover:text-aegis-error transition-colors"
            >
              Clear
            </button>
          </div>

          {logsOpen && (
            logs.length === 0 ? (
              <p className="text-[13px] text-aegis-text-muted">Nothing logged yet — that's a good sign.</p>
            ) : (
              <div className="bg-aegis-raised border border-aegis-border rounded-xl p-3 max-h-[480px] overflow-y-auto font-mono text-[11px] leading-relaxed">
                {logs.slice().reverse().map((l, i) => (
                  <div key={i} className="py-1.5 border-b border-aegis-border last:border-0">
                    <span className="text-aegis-text-muted">{new Date(l.timestamp).toLocaleTimeString()}</span>
                    {' '}
                    <span className={`font-semibold ${levelColor(l.level)}`}>{l.level === 'ERROR' || l.level === 'CRITICAL' ? 'Failed' : 'Warning'}</span>
                    {' — '}
                    <span className="text-aegis-text-primary whitespace-pre-wrap break-words font-sans">{l.message}</span>
                  </div>
                ))}
              </div>
            )
          )}
        </section>
      </div>
    </div>
  );
}
