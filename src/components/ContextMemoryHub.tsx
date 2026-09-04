import React, { useState, useEffect, useRef } from 'react';
import { Cpu, Zap, BrainCircuit, ShieldAlert, AlertTriangle, Play, RefreshCw, HardDrive, Database, CircleSlash, Check, LogOut } from 'lucide-react';
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

  const [hasChanges, setHasChanges] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [downloadedModels, setDownloadedModels] = useState<any[]>([]);
  const [unloading, setUnloading] = useState(false);
  const [loadingModelId, setLoadingModelId] = useState<number | null>(null);

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

  const fetchConfig = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/context-config`);
      if (res.ok) {
        const data = await res.json();
        // Chat's saved values are the shared starting point (it's the one
        // with max_rag_chunks); the Agent side is unified to match on save.
        if (data.chat) setConfig(c => ({ ...c, ...data.chat }));
      }
    } catch (e) {
      console.error(e);
    }
  };

  const saveConfig = async () => {
    setIsSaving(true);
    try {
      const { max_rag_chunks, ...shared } = config;
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/context-config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat: config,
          agent: shared,
        })
      });
      if (res.ok) {
        setHasChanges(false);
      } else {
        toast.error('Failed to save settings.');
      }
    } catch (e) {
      console.error(e);
      toast.error('Network error while saving settings.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleConfigChange = (key: keyof UnifiedConfig, val: number) => {
    setConfig({ ...config, [key]: val });
    setHasChanges(true);
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
            <h1 className="text-xl font-bold text-aegis-text-primary leading-tight">Context & Memory Hub</h1>
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

        {/* Memory & Context Settings */}
        <div className="bg-aegis-raised rounded-xl border border-aegis-border p-4 flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between mb-4 flex-shrink-0">
            <div>
              <h2 className="text-sm font-bold text-aegis-text-primary">Memory & Context Settings</h2>
              <p className="text-[11px] text-aegis-text-muted">Higher values improve recall but increase RAM and latency. Applies to both Chat and the Agent — same underlying LLM.</p>
            </div>
          </div>

          {/* Sliders — 2-column grid keeps all 4 controls visible without scrolling */}
          <div className="grid grid-cols-2 gap-x-6 gap-y-4">
            <div>
              <div className="flex justify-between items-end mb-1.5">
                <span className="text-xs font-bold text-aegis-text-primary">Max Response Length</span>
                <span className="text-xs font-bold text-aegis-primary-light">{config.max_output_tokens.toLocaleString()}</span>
              </div>
              <input
                type="range" min={2048} max={hardware?.max_context || 4096} step={512}
                value={config.max_output_tokens}
                onChange={(e) => handleConfigChange('max_output_tokens', parseInt(e.target.value))}
                className="w-full h-1.5 bg-aegis-overlay rounded-lg appearance-none cursor-pointer accent-aegis-primary focus:outline-none"
              />
              <div className="flex justify-between text-[10px] font-medium text-aegis-text-muted mt-1">
                <span>2,048</span>
                <span className="text-aegis-primary-light font-bold">Model max: {(hardware?.max_context || 4096).toLocaleString()}</span>
              </div>
            </div>

            <div>
              <div className="flex justify-between items-end mb-1.5">
                <span className="text-xs font-bold text-aegis-text-primary">Max History Messages</span>
                <span className="text-xs font-bold text-aegis-primary-light">{config.max_history_messages}</span>
              </div>
              <input
                type="range" min={1} max={20} step={1}
                value={config.max_history_messages}
                onChange={(e) => handleConfigChange('max_history_messages', parseInt(e.target.value))}
                className="w-full h-1.5 bg-aegis-overlay rounded-lg appearance-none cursor-pointer accent-aegis-primary focus:outline-none"
              />
              <div className="flex justify-between text-[10px] font-medium text-aegis-text-muted mt-1">
                <span>1</span>
                <span>20</span>
              </div>
            </div>

            <div>
              <div className="flex justify-between items-end mb-1.5">
                <span className="text-xs font-bold text-aegis-text-primary">Max Characters Per Message</span>
                <span className="text-xs font-bold text-aegis-primary-light">{config.max_msg_chars.toLocaleString()}</span>
              </div>
              <input
                type="range" min={500} max={10000} step={500}
                value={config.max_msg_chars}
                onChange={(e) => handleConfigChange('max_msg_chars', parseInt(e.target.value))}
                className="w-full h-1.5 bg-aegis-overlay rounded-lg appearance-none cursor-pointer accent-aegis-primary focus:outline-none"
              />
              <div className="flex justify-between text-[10px] font-medium text-aegis-text-muted mt-1">
                <span>500</span>
                <span>10.0k</span>
              </div>
            </div>

            <div>
              <div className="flex justify-between items-end mb-1.5">
                <span className="text-xs font-bold text-aegis-text-primary">Tool Result Snippet Size</span>
                <span className="text-xs font-bold text-aegis-primary-light">{config.max_result_snippet.toLocaleString()}</span>
              </div>
              <input
                type="range" min={500} max={10000} step={500}
                value={config.max_result_snippet}
                onChange={(e) => handleConfigChange('max_result_snippet', parseInt(e.target.value))}
                className="w-full h-1.5 bg-aegis-overlay rounded-lg appearance-none cursor-pointer accent-aegis-primary focus:outline-none"
              />
              <div className="flex justify-between text-[10px] font-medium text-aegis-text-muted mt-1">
                <span>500</span>
                <span>10.0k</span>
              </div>
            </div>
          </div>

          <div className="flex-1 flex items-center">
            <div className="w-full bg-aegis-primary/10 p-3 rounded-lg border border-aegis-primary/20 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />
              <p className="text-xs text-aegis-text-secondary">
                <strong className="font-semibold text-aegis-text-primary">Max Response Length</strong> has the largest RAM impact — reflected live in the breakdown above.
                {isLowMemory && ' Available RAM is currently below what the model needs, so responses may be slow.'}
              </p>
            </div>
          </div>

          {/* Save Button */}
          <div className="pt-3 mt-3 border-t border-aegis-border flex justify-end flex-shrink-0">
            <button
              onClick={saveConfig}
              disabled={!hasChanges || isSaving}
              className={`px-5 py-2 rounded-lg text-xs font-semibold transition-all ${
                hasChanges
                  ? 'bg-aegis-primary text-white hover:bg-aegis-primary-dark shadow-sm'
                  : 'bg-aegis-overlay text-aegis-text-muted cursor-not-allowed'
              }`}
            >
              {isSaving ? 'Saving...' : 'Save Settings'}
            </button>
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
                return (
                  <button
                    key={m.id}
                    onClick={() => !m.is_active && handleLoadModel(m.id)}
                    disabled={m.is_active || loadingModelId !== null}
                    className={`w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left transition-colors border-b border-aegis-border last:border-b-0 ${
                      m.is_active ? 'bg-aegis-primary/5' : 'hover:bg-aegis-overlay disabled:hover:bg-transparent'
                    } ${loadingModelId !== null && !isLoadingThis ? 'opacity-50' : ''}`}
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-aegis-text-primary truncate">{cleanName}</p>
                      {m.context_length && (
                        <p className="text-[11px] text-aegis-text-muted mt-0.5">{m.context_length.toLocaleString()}-token context</p>
                      )}
                    </div>
                    {isLoadingThis ? (
                      <RefreshCw className="w-4 h-4 text-aegis-primary-light animate-spin flex-shrink-0" />
                    ) : m.is_active ? (
                      <Check className="w-4 h-4 text-aegis-primary flex-shrink-0" />
                    ) : null}
                  </button>
                );
              })
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
