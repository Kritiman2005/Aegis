import React, { useState, useEffect, useRef } from 'react';
import { Cpu, Zap, BrainCircuit, ShieldAlert, AlertTriangle, Play, RefreshCw, HardDrive, Database, CircleSlash, Check, LogOut, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';

interface HardwareStatus {
  active_model: string;
  active_model_display?: string;
  max_context: number;
  ram_total_gb: number;
  ram_used_gb: number;
  ram_percent: number;
}

// One shared config for both Chat and the Agent — same underlying LLM, so
// there's no reason to make the user tune identical knobs twice. Chat's
// backend schema still carries `max_rag_chunks` (Agent's doesn't use RAG),
// which this UI has no control for — it's just carried through unedited on
// save so persisting the shared values never fails Chat's validation.
interface UnifiedConfig {
  max_history_messages: number;
  max_msg_chars: number;
  max_output_tokens: number;
  max_result_snippet: number;
  max_rag_chunks: number;
}

export default function ContextMemoryHub() {
  const [hardware, setHardware] = useState<HardwareStatus | null>(null);
  const [config, setConfig] = useState<UnifiedConfig>({
    max_history_messages: 20,
    max_msg_chars: 4000,
    max_output_tokens: 5120,
    max_result_snippet: 2000,
    max_rag_chunks: 5,
  });

  const [downloadedModels, setDownloadedModels] = useState<any[]>([]);
  const [unloading, setUnloading] = useState(false);
  const [loadingModelId, setLoadingModelId] = useState<number | null>(null);
  const [deletingModelId, setDeletingModelId] = useState<number | null>(null);
  // Deleting a model is consequential (re-downloading can mean gigabytes
  // again) but a native confirm() blocks the whole renderer until
  // dismissed — same reasoning as the workflow-list delete button — so
  // this is a plain "click again to confirm" arm instead of a dialog.
  const [armedDeleteId, setArmedDeleteId] = useState<number | null>(null);

  useEffect(() => {
    fetchHardware();
    fetchConfig();
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
    } catch (e) {}
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

  // Read-only here now — Max Response Length feeds the RAM/latency estimate
  // below, but editing it (along with the rest of the Memory & Context
  // settings) moved to the reply-generation "llm" node's own config panel
  // (WorkflowsView.tsx's MemorySettingsPanel), which posts to this same
  // /api/context-config endpoint.
  const fetchConfig = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/context-config`);
      if (res.ok) {
        const data = await res.json();
        if (data.chat) setConfig(c => ({ ...c, ...data.chat }));
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleUnloadModel = async () => {
    setUnloading(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hardware/unload`, { method: 'POST' });
      if (res.ok) {
        toast.success("Model ejected from RAM.");
        fetchHardware();
        fetchDownloadedModels();
      } else {
        toast.error("Failed to eject model.");
      }
    } catch (e) {
      toast.error("Network error.");
    } finally {
      setUnloading(false);
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

  // KV cache: each token costs ~0.5 MB for a typical 3B model (2 layers * 2 (K+V) * hidden_dim * bytes)
  // Scale it by ratio of requested context vs model max context
  const contextRatio = config.max_output_tokens / modelMaxContext;
  const baseKvGb = modelMaxContext * 0.000125;  // approx at model max
  const extraKvGb = baseKvGb * contextRatio;     // scales proportionally

  const otherAppsGb = hardware ? (hardware.ram_used_gb > 2 ? hardware.ram_used_gb - 2 : hardware.ram_used_gb) : 0;
  const modelBaseGb = (hardware && hardware.active_model !== 'None') ? 2.0 : 0; // approx weights only
  const modelEstimatedGb = modelBaseGb + extraKvGb;
  const availableGb = hardware ? Math.max(0, hardware.ram_total_gb - hardware.ram_used_gb - extraKvGb) : 0;
  const isLowMemory = availableGb < 2;

  // Latency tier based on context ratio
  const latencyLabel = contextRatio > 0.75 ? 'High' : contextRatio > 0.4 ? 'Medium' : 'Low';
  const latencyColor = contextRatio > 0.75 ? 'text-aegis-error' : contextRatio > 0.4 ? 'text-aegis-warning' : 'text-aegis-success';

  // RAM breakdown as a compact horizontal stacked bar (replaces the donut —
  // same information, a fraction of the vertical space).
  const ramTotal = hardware?.ram_total_gb || 16;
  const otherPct = Math.max(0, Math.min(100, (otherAppsGb / ramTotal) * 100));
  const modelPct = Math.max(0, Math.min(100 - otherPct, (modelEstimatedGb / ramTotal) * 100));
  const freePct = Math.max(0, 100 - otherPct - modelPct);

  const activeModelName = hardware?.active_model && hardware.active_model !== 'None' ? hardware.active_model : null;
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
            <div className="text-[10px] text-aegis-text-muted mt-0.5">at current context</div>
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
            {activeModelName && (
              <button
                onClick={handleUnloadModel}
                disabled={unloading}
                className="flex items-center gap-1.5 text-xs font-semibold text-aegis-error hover:underline disabled:opacity-50 transition-all"
              >
                {unloading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <LogOut className="w-3.5 h-3.5" />}
                Eject
              </button>
            )}
          </div>

          <div className="max-h-48 overflow-y-auto">
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
                return (
                  <div
                    key={m.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => !m.is_active && handleLoadModel(m.id)}
                    onKeyDown={e => { if (e.key === 'Enter' && !m.is_active) handleLoadModel(m.id); }}
                    aria-disabled={m.is_active || loadingModelId !== null}
                    className={`w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left transition-colors border-b border-aegis-border last:border-b-0 cursor-pointer ${
                      m.is_active ? 'bg-aegis-primary/5' : 'hover:bg-aegis-overlay'
                    } ${loadingModelId !== null && !isLoadingThis ? 'opacity-50 pointer-events-none' : ''}`}
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-aegis-text-primary truncate">{cleanName}</p>
                      {m.context_length && (
                        <p className="text-[11px] text-aegis-text-muted mt-0.5">{m.context_length.toLocaleString()}-token context</p>
                      )}
                    </div>
                    <div className="flex-shrink-0 flex items-center gap-2">
                      {isLoadingThis ? (
                        <RefreshCw className="w-4 h-4 text-aegis-primary-light animate-spin flex-shrink-0" />
                      ) : m.is_active ? (
                        <Check className="w-4 h-4 text-aegis-primary flex-shrink-0" />
                      ) : null}
                      {/* Can't delete the model currently loaded in RAM —
                          eject it first (same rule the backend enforces). */}
                      {!m.is_active && (
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
                );
              })
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
