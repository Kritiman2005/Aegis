import React, { useState, useEffect } from 'react';
import { Cpu, AlertTriangle, RefreshCw, HardDrive, Database, CircleSlash, Check, LogOut, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';

interface OtherLoadedModel {
  name: string;
  display_name: string;
  max_context: number;
  used_by_workflows: string[];
}

interface HardwareStatus {
  active_model: string;
  active_model_display?: string;
  max_context: number;
  other_loaded_models?: OtherLoadedModel[];
  ram_total_gb: number;
  ram_used_gb: number;
  ram_percent: number;
}

export default function ContextMemoryHub() {
  const [hardware, setHardware] = useState<HardwareStatus | null>(null);

  const [downloadedModels, setDownloadedModels] = useState<any[]>([]);
  const [unloadingModelId, setUnloadingModelId] = useState<number | null>(null);
  const [loadingModelId, setLoadingModelId] = useState<number | null>(null);
  const [deletingModelId, setDeletingModelId] = useState<number | null>(null);
  // Deleting a model is consequential (re-downloading can mean gigabytes
  // again) but a native confirm() blocks the whole renderer until
  // dismissed — same reasoning as the workflow-list delete button — so
  // this is a plain "click again to confirm" arm instead of a dialog.
  const [armedDeleteId, setArmedDeleteId] = useState<number | null>(null);

  useEffect(() => {
    fetchHardware();
    fetchDownloadedModels();
    const interval = setInterval(fetchHardware, 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchDownloadedModels = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hub/downloaded`);
      if (res.ok) {
        const data = await res.json();
        setDownloadedModels(data.models || []);
      }
    } catch (e) { toast.error('Could not load downloaded models.'); }
  };

  const fetchHardware = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hardware/status`);
      if (res.ok) {
        setHardware(await res.json());
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleUnloadModel = async (modelId: number, displayName: string) => {
    setUnloadingModelId(modelId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hardware/unload/${modelId}`, { method: 'POST' });
      if (res.ok) {
        toast.success(`Ejected '${displayName}' from RAM.`);
        fetchHardware();
        fetchDownloadedModels();
      } else {
        toast.error('Failed to eject model.');
      }
    } catch (e) {
      toast.error('Network error.');
    } finally {
      setUnloadingModelId(null);
    }
  };

  const handleLoadModel = async (modelId: number) => {
    setLoadingModelId(modelId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hardware/load`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId })
      });
      if (res.ok) {
        toast.success("Model set as active and RAM cleared.");
        fetchHardware();
        fetchDownloadedModels();
      } else {
        toast.error("Failed to load model.");
      }
    } catch (e) {
      toast.error("Network error.");
    } finally {
      setLoadingModelId(null);
    }
  };

  const handleDeleteModel = async (modelId: number, displayName: string) => {
    if (armedDeleteId !== modelId) {
      setArmedDeleteId(modelId);
      return;
    }
    setArmedDeleteId(null);
    setDeletingModelId(modelId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hub/${modelId}`, { method: 'DELETE' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        toast.success(`Deleted '${displayName}'.`);
        fetchDownloadedModels();
      } else {
        toast.error(data.detail || 'Failed to delete model.');
      }
    } catch (e) {
      toast.error('Network error.');
    } finally {
      setDeletingModelId(null);
    }
  };

  // === Dynamic RAM & Latency Estimation ===
  const modelMaxContext = hardware?.max_context || 4096;
  const isModelActive = !!(hardware && hardware.active_model !== 'None');

  // KV cache: each token costs ~0.5 MB for a typical 3B model (2 layers * 2
  // (K+V) * hidden_dim * bytes). Estimated at the model's full context
  // window (worst case) rather than scaled by a configured response
  // length — that per-turn tuning now lives in the Workflow's memory node
  // (WorkflowsView.tsx's MemorySettingsPanel), out of scope for this page.
  const extraKvGb = isModelActive ? modelMaxContext * 0.000125 : 0;

  const otherAppsGb = hardware ? (hardware.ram_used_gb > 2 ? hardware.ram_used_gb - 2 : hardware.ram_used_gb) : 0;
  const modelBaseGb = isModelActive ? 2.0 : 0; // approx weights only
  const modelEstimatedGb = modelBaseGb + extraKvGb;
  const availableGb = hardware ? Math.max(0, hardware.ram_total_gb - hardware.ram_used_gb - extraKvGb) : 0;
  const isLowMemory = availableGb < 2;

  // Latency tier based on free RAM headroom, not context/token usage —
  // matches how little free memory drives real slowdowns (swapping,
  // memory pressure) regardless of what a workflow does with context.
  const latencyLabel = availableGb < 1 ? 'High' : availableGb < 3 ? 'Medium' : 'Low';
  const latencyColor = availableGb < 1 ? 'text-aegis-error' : availableGb < 3 ? 'text-aegis-warning' : 'text-aegis-success';

  // RAM breakdown as a compact horizontal stacked bar (replaces the donut —
  // same information, a fraction of the vertical space).
  const ramTotal = hardware?.ram_total_gb || 16;
  const otherPct = Math.max(0, Math.min(100, (otherAppsGb / ramTotal) * 100));
  const modelPct = Math.max(0, Math.min(100 - otherPct, (modelEstimatedGb / ramTotal) * 100));
  const freePct = Math.max(0, 100 - otherPct - modelPct);

  const activeModelName = isModelActive ? hardware!.active_model : null;
  const activeModelShort = activeModelName
    ? (hardware?.active_model_display && hardware.active_model_display !== 'None'
        ? hardware.active_model_display.replace(/\s*\([^)]*\)\s*$/, '').trim()
        : (activeModelName.includes('/') ? activeModelName.split('/').pop() : activeModelName))
    : null;

  return (
    <div className="flex-1 overflow-hidden bg-aegis-base p-6 font-sans">
      <div className="max-w-3xl mx-auto h-full flex flex-col gap-4">

        {/* Header */}
        <div className="flex items-center gap-3 flex-shrink-0">
          <div className="w-9 h-9 rounded-xl bg-aegis-primary flex items-center justify-center text-white shadow-sm flex-shrink-0">
            <Database className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-aegis-text-primary leading-tight">Memory Hub</h1>
            <p className="text-xs text-aegis-text-secondary truncate">How the local AI remembers context, and its real-time system impact.</p>
          </div>
        </div>

        {/* Top stat grid — Active Model, Total RAM, Available RAM, Latency */}
        <div className="grid grid-cols-4 gap-3 flex-shrink-0">
          <div className="bg-aegis-raised rounded-xl border border-aegis-border p-3.5">
            <div className="text-[9px] font-bold text-aegis-text-muted tracking-wider uppercase mb-1 flex items-center gap-1">
              <Cpu className="w-3 h-3" /> Active Model
            </div>
            {activeModelShort ? (
              <div className="text-sm font-bold text-aegis-text-primary truncate" title={activeModelName || ''}>{activeModelShort}</div>
            ) : (
              <div className="text-sm font-bold text-aegis-text-muted flex items-center gap-1">
                <CircleSlash className="w-3.5 h-3.5" /> None loaded
              </div>
            )}
            <div className="text-[10px] text-aegis-text-muted mt-0.5">{(hardware?.max_context || 4096).toLocaleString()}-token context window</div>
          </div>
          <div className="bg-aegis-raised rounded-xl border border-aegis-border p-3.5">
            <div className="text-[9px] font-bold text-aegis-text-muted tracking-wider uppercase mb-1">Total RAM</div>
            <div className="text-lg font-bold text-aegis-text-primary">{hardware ? hardware.ram_total_gb.toFixed(0) : 16} GB</div>
            <div className="text-[10px] text-aegis-text-muted mt-0.5">system total</div>
          </div>
          <div className="bg-aegis-raised rounded-xl border border-aegis-border p-3.5">
            <div className="text-[9px] font-bold text-aegis-text-muted tracking-wider uppercase mb-1">Available</div>
            <div className="text-lg font-bold text-aegis-primary-light">{availableGb.toFixed(1)} GB</div>
            <div className="text-[10px] text-aegis-text-muted mt-0.5">after model load</div>
          </div>
          <div className="bg-aegis-raised rounded-xl border border-aegis-border p-3.5">
            <div className="text-[9px] font-bold text-aegis-text-muted tracking-wider uppercase mb-1">Latency Est.</div>
            <div className={`text-lg font-bold ${latencyColor}`}>{latencyLabel}</div>
            <div className="text-[10px] text-aegis-text-muted mt-0.5">from free RAM</div>
          </div>
        </div>

        {/* RAM breakdown bar */}
        <div className="bg-aegis-raised rounded-xl border border-aegis-border p-4 flex-shrink-0">
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-xs font-semibold text-aegis-text-secondary">RAM Breakdown</span>
            {isLowMemory && (
              <div className="flex items-center gap-1 px-2 py-0.5 bg-aegis-error/10 text-aegis-error border border-aegis-error/30 rounded-full text-[10px] font-semibold">
                <AlertTriangle className="w-3 h-3" /> Low Memory
              </div>
            )}
          </div>
          <div className="w-full h-2.5 rounded-full overflow-hidden flex bg-aegis-overlay">
            <div className="h-full bg-aegis-text-muted transition-all duration-700" style={{ width: `${otherPct}%` }} />
            <div className={`h-full transition-all duration-700 ${isLowMemory ? 'bg-aegis-error' : 'bg-aegis-primary'}`} style={{ width: `${modelPct}%` }} />
            <div className="h-full bg-blue-400 transition-all duration-700" style={{ width: `${freePct}%` }} />
          </div>
          <div className="flex items-center justify-between mt-2 text-[11px]">
            <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-aegis-text-muted" /><span className="text-aegis-text-secondary">Other {otherAppsGb.toFixed(1)}GB</span></div>
            <div className="flex items-center gap-1.5"><span className={`w-2 h-2 rounded-sm ${isLowMemory ? 'bg-aegis-error' : 'bg-aegis-primary'}`} /><span className="text-aegis-text-secondary">Model {modelEstimatedGb.toFixed(1)}GB</span></div>
            <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-blue-400" /><span className="text-aegis-text-secondary">Free {availableGb.toFixed(1)}GB</span></div>
          </div>
        </div>

        {/* Model Management — a clickable list, Claude-style: click a model to
            make it active, a checkmark shows the one that's currently loaded,
            instead of a raw <select> plus separate "Set Active"/"Eject" buttons. */}
        <div className="bg-aegis-raised rounded-xl border border-aegis-border flex-shrink-0 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-aegis-border">
            <span className="text-[11px] font-semibold text-aegis-text-secondary flex items-center gap-1.5">
              <HardDrive className="w-3.5 h-3.5 text-aegis-primary-light" /> Downloaded Models
            </span>
          </div>

          <div className="max-h-[28rem] overflow-y-auto">
            {downloadedModels.filter(m => m.status === 'downloaded').length === 0 ? (
              <p className="px-4 py-5 text-center text-xs text-aegis-text-muted">
                No downloaded models yet — visit the LLMs tab to download one.
              </p>
            ) : (
              downloadedModels.filter(m => m.status === 'downloaded').map((m) => {
                const cleanName = (m.display_name || m.repo_id || m.name || 'Unknown model')
                  .replace(/\s*\([^)]*\)\s*$/, '').trim();
                const isLoadingThis = loadingModelId === m.id;
                const isDeletingThis = deletingModelId === m.id;
                const isArmed = armedDeleteId === m.id;
                // A workflow node can load a different model than whichever
                // one is explicitly "active" here — surface that it's
                // sitting in RAM too, not just silently invisible.
                const loadedByWorkflow = hardware?.other_loaded_models?.find(o => o.name === m.name);
                // Ground truth for "is this genuinely sitting in RAM right
                // now" — m.is_active (from GET /api/hub/downloaded) is a
                // DB preference for which model to preload on next launch,
                // NOT live state: it's only ever set by this panel's own
                // "Set Active" click, so a model loaded any other way (most
                // commonly, a Workflow "llm" node with its own model
                // picked) is really loaded but m.is_active stays false for
                // it — hiding its Eject button and leaving it stuck in RAM
                // with no way to free it from here. hardware.active_model/
                // other_loaded_models come straight from
                // LLMManager.loaded_models, so they can't drift the same way.
                const isGenuinelyLoaded = hardware?.active_model === m.name || !!loadedByWorkflow;
                const rowDisabled = isGenuinelyLoaded || loadingModelId !== null;
                const isCapped = m.effective_context_length && m.context_length && m.effective_context_length < m.context_length;
                return (
                  <div key={m.id} className={`border-b border-aegis-border last:border-b-0 ${isGenuinelyLoaded ? 'bg-aegis-primary/5' : ''}`}>
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => !rowDisabled && handleLoadModel(m.id)}
                      onKeyDown={e => { if (e.key === 'Enter' && !rowDisabled) handleLoadModel(m.id); }}
                      aria-disabled={rowDisabled}
                      className={`w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left transition-colors cursor-pointer ${
                        m.is_active ? '' : 'hover:bg-aegis-overlay'
                      } ${loadingModelId !== null && !isLoadingThis ? 'opacity-50 pointer-events-none' : ''}`}
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-aegis-text-primary truncate">{cleanName}</p>
                        {(m.effective_context_length || m.context_length) && (
                          <p className="text-[11px] text-aegis-text-muted mt-0.5">
                            {(m.effective_context_length ?? m.context_length).toLocaleString()}-token context
                            {isCapped && ` (${m.context_length.toLocaleString()} native, capped)`}
                          </p>
                        )}
                        {m.used_by_workflows?.length > 0 && (
                          <p className="text-[11px] text-aegis-primary-light mt-0.5 truncate">
                            Used by: {m.used_by_workflows.join(', ')}
                          </p>
                        )}
                      </div>
                      <div className="flex-shrink-0 flex items-center gap-2">
                        {isLoadingThis ? (
                          <RefreshCw className="w-4 h-4 text-aegis-primary-light animate-spin flex-shrink-0" />
                        ) : hardware?.active_model === m.name ? (
                          <Check className="w-4 h-4 text-aegis-primary flex-shrink-0" />
                        ) : loadedByWorkflow ? (
                          <span
                            title={`Loaded in RAM by: ${loadedByWorkflow.used_by_workflows.join(', ') || 'a workflow'}`}
                            className="text-[9px] font-semibold text-aegis-primary-light bg-aegis-primary/10 border border-aegis-primary/30 px-1.5 py-0.5 rounded-full flex-shrink-0"
                          >
                            Loaded (workflow)
                          </span>
                        ) : null}
                        {/* Per-model eject — shown for any row actually
                            resident in RAM right now (isGenuinelyLoaded —
                            live state, not the DB's is_active preference),
                            whether it's the explicit "active" one or one a
                            workflow loaded on its own. Ejects only this
                            model, never the others (see POST
                            /api/hardware/unload/{id}). */}
                        {isGenuinelyLoaded && (
                          <button
                            onClick={e => { e.stopPropagation(); handleUnloadModel(m.id, cleanName); }}
                            disabled={unloadingModelId === m.id}
                            title={`Eject ${cleanName} from RAM`}
                            className="p-1 rounded-md text-aegis-text-muted hover:bg-aegis-error/10 hover:text-aegis-error transition-colors disabled:opacity-50"
                          >
                            {unloadingModelId === m.id ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <LogOut className="w-3.5 h-3.5" />}
                          </button>
                        )}
                        {/* Can't delete the model currently loaded in RAM —
                            eject it first (same rule the backend enforces). */}
                        {!isGenuinelyLoaded && (
                          <button
                            onClick={e => { e.stopPropagation(); handleDeleteModel(m.id, cleanName); }}
                            disabled={isDeletingThis}
                            title={isArmed ? 'Click again to delete' : `Delete ${cleanName}`}
                            className={`p-1 rounded-md transition-colors disabled:opacity-50 ${
                              isArmed ? 'bg-aegis-error/10 text-aegis-error' : 'text-aegis-text-muted hover:bg-aegis-error/10 hover:text-aegis-error'
                            }`}
                          >
                            {isDeletingThis ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                          </button>
                        )}
                      </div>
                    </div>
                    <ModelContextCapControl model={m} onSaved={() => { fetchDownloadedModels(); fetchHardware(); }} />
                  </div>
                );
              })
            )}
          </div>
        </div>

      </div>
    </div>
  );
}

// One per downloaded model, not a single app-wide value — different
// models legitimately want different caps (see backend
// llm_manager.resolve_effective_n_ctx and POST /api/hub/{id}/context-cap).
// Lives outside the row's own onClick-to-load handler (stops propagation
// throughout) so dragging the slider or hitting Apply never triggers
// loading the model.
function ModelContextCapControl({ model, onSaved }: { model: any; onSaved: () => void }) {
  const DEFAULT_N_CTX = 8192;
  const nativeMax = model.context_length || DEFAULT_N_CTX;
  const ceiling = Math.max(nativeMax, 2048);
  const effective = model.effective_context_length || Math.min(DEFAULT_N_CTX, ceiling);
  const [draft, setDraft] = useState<number>(effective);
  const [saving, setSaving] = useState(false);

  // Re-sync whenever the backend's own effective value changes (after a
  // save, or a fresh fetch) rather than freezing on whatever was true when
  // this row first mounted.
  useEffect(() => {
    setDraft(effective);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model.id, effective]);

  const ramDeltaGb = ((draft - DEFAULT_N_CTX) / 1024) * 0.20;
  const hasChanges = draft !== effective;

  const handleApply = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setSaving(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hub/${model.id}/context-cap`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ n_ctx: draft }),
      });
      if (res.ok) {
        toast.success(`Context cap set to ${draft.toLocaleString()} tokens — applies next time this model loads.`);
        onSaved();
      } else if (res.status === 409) {
        toast.error('Cannot change the context cap while a generation is in progress.');
      } else {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || 'Failed to save context cap.');
      }
    } catch (e) {
      toast.error('Network error.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="px-4 pb-3 pt-1" onClick={e => e.stopPropagation()}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] font-semibold text-aegis-text-muted uppercase">Context Cap</span>
        <span className="text-[11px] font-bold text-aegis-primary-light">{draft.toLocaleString()} tokens</span>
      </div>
      <input
        type="range"
        min={2048}
        max={ceiling}
        step={512}
        value={draft}
        onChange={e => setDraft(parseInt(e.target.value))}
        className="w-full h-1 bg-aegis-overlay rounded-lg appearance-none cursor-pointer accent-aegis-primary focus:outline-none"
      />
      <div className="flex items-center justify-between mt-2">
        <span className={`text-[10px] ${ramDeltaGb > 0 ? 'text-aegis-warning' : 'text-aegis-text-muted'}`}>
          {ramDeltaGb > 0
            ? `≈ +${ramDeltaGb.toFixed(1)} GB vs. default · native max ${nativeMax.toLocaleString()}`
            : `Native max: ${nativeMax.toLocaleString()}`}
        </span>
        <button
          onClick={handleApply}
          disabled={saving || !hasChanges}
          className="px-2.5 py-1 rounded-md text-[10px] font-semibold bg-aegis-primary text-white hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {saving ? 'Saving…' : 'Apply'}
        </button>
      </div>
    </div>
  );
}
