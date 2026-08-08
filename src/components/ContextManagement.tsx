'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  SlidersHorizontal,
  MessageSquare,
  GitBranch,
  Wrench,
  Cpu,
  RotateCcw,
  Save,
  CheckCircle2,
  AlertTriangle,
  Info,
  Zap,
  Lock,
  HardDrive,
  Activity,
  AlertOctagon,
  RefreshCw
} from 'lucide-react';
import toast from 'react-hot-toast';

// ── Types ──────────────────────────────────────────────────────────────────────

interface ChatConfig {
  max_history_messages: number;
  max_msg_chars: number;
  max_rag_chunks: number;
}

interface PlannerConfig {
  max_history_messages: number;
  max_msg_chars: number;
  max_result_snippet: number;
}

interface ExtractorConfig {
  max_tokens: number;
}

interface AdvancedConfig {
  rag_confidence_threshold: number;
}

interface HardwareConfig {
  n_gpu_layers: number;
  n_threads: number;
}

interface ContextConfig {
  chat: ChatConfig;
  planner: PlannerConfig;
  executor: { description: string };
  extractor: ExtractorConfig;
  advanced: AdvancedConfig;
  hardware: HardwareConfig;
}

const TOTAL_CTX = 6144; // n_ctx of the loaded Qwen model

// ── Token estimation (rough: 1 token ≈ 4 chars) ──────────────────────────────
function estimatePlannerTokens(cfg: PlannerConfig): number {
  const sysPrompt = 900;
  const historyTokens = (cfg.max_history_messages * cfg.max_msg_chars) / 4;
  const resultTokens = cfg.max_result_snippet / 4;
  return Math.round(sysPrompt + historyTokens + resultTokens);
}

function estimateChatTokens(cfg: ChatConfig): number {
  const sysPrompt = 600;
  const historyTokens = (cfg.max_history_messages * cfg.max_msg_chars) / 4;
  const ragTokens = cfg.max_rag_chunks * 200;
  return Math.round(sysPrompt + historyTokens + ragTokens);
}

function estimateExtractorTokens(cfg: ExtractorConfig): number {
  return Math.round(250 + cfg.max_tokens);
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function TokenBar({ used, total }: { used: number; total: number }) {
  const pct = Math.min((used / total) * 100, 100);
  const color =
    pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-amber-400' : 'bg-emerald-500';
  const textColor =
    pct > 90 ? 'text-red-600' : pct > 70 ? 'text-amber-600' : 'text-emerald-600';

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[11px] font-medium">
        <span className="text-gray-500">Estimated context usage</span>
        <span className={`font-bold tabular-nums ${textColor}`}>
          ~{used.toLocaleString()} / {total.toLocaleString()} tokens ({pct.toFixed(0)}%)
        </span>
      </div>
      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {pct > 90 && (
        <p className="text-[10px] text-red-500 flex items-center gap-1">
          <AlertTriangle className="w-3 h-3" /> Exceeds safe limit — reduce parameters to prevent truncation.
        </p>
      )}
    </div>
  );
}

function ParamSlider({
  label,
  description,
  value,
  min,
  max,
  step,
  unit,
  onChange,
  disabled = false,
}: {
  label: string;
  description: string;
  value: number | string;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange?: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className={`space-y-2 ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold text-gray-800">{label}</p>
          <p className="text-[11px] text-gray-400 leading-snug">{description}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <input
            type="number"
            value={value}
            min={min}
            max={max}
            step={step}
            disabled={disabled}
            onChange={(e) => {
              if (!onChange) return;
              const v = Number(e.target.value);
              if (v >= min && v <= max) onChange(v);
            }}
            className="w-16 text-right text-xs font-bold text-gray-900 bg-gray-50 border border-gray-200 rounded-lg px-2 py-1 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 tabular-nums disabled:cursor-not-allowed"
          />
          <span className="text-[11px] text-gray-400 font-medium">{unit}</span>
        </div>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange && onChange(Number(e.target.value))}
        className="w-full h-1.5 bg-gray-200 rounded-full appearance-none cursor-pointer accent-indigo-600 disabled:cursor-not-allowed"
      />
      <div className="flex justify-between text-[10px] text-gray-300 tabular-nums">
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function ContextManagement() {
  const [config, setConfig] = useState<ContextConfig | null>(null);
  const [originalConfig, setOriginalConfig] = useState<ContextConfig | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [loading, setLoading] = useState(true);
  
  const [activeTab, setActiveTab] = useState<'A' | 'B' | 'C'>('A');
  const [unloading, setUnloading] = useState(false);

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch('http://localhost:8000/api/context-config');
      if (!res.ok) throw new Error('Failed to fetch');
      const data = await res.json();
      setConfig(data);
      setOriginalConfig(JSON.parse(JSON.stringify(data)));
    } catch (e) {
      console.error('Failed to load context config', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const updateConfig = (section: keyof ContextConfig, key: string, value: number) => {
    if (!config) return;
    setConfig({
      ...config,
      [section]: {
        ...(config[section] as any),
        [key]: value
      }
    });
    setDirty(true);
    setSaveStatus('idle');
  };

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      const res = await fetch('http://localhost:8000/api/context-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat: config.chat,
          planner: config.planner,
          extractor: config.extractor,
          advanced: config.advanced,
          hardware: config.hardware
        }),
      });
      
      const data = await res.json();
      
      if (!res.ok) {
        if (res.status === 409) {
            toast.error("Cannot change hardware settings while a generation is in progress. Please wait.", { duration: 4000 });
            if (originalConfig) {
                setConfig(JSON.parse(JSON.stringify(originalConfig)));
                setDirty(false);
            }
        }
        throw new Error(data.detail || 'Save failed');
      }
      
      setOriginalConfig(JSON.parse(JSON.stringify(data.config)));
      setSaveStatus('success');
      setDirty(false);
      setTimeout(() => setSaveStatus('idle'), 3000);
      
      if (activeTab === 'C') {
        toast.success("Settings saved. Model is reloading in background.");
      }
    } catch (e) {
      console.error(e);
      setSaveStatus('error');
      setTimeout(() => setSaveStatus('idle'), 3000);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!confirm('Reset all agents to default context window settings?')) return;
    setSaving(true);
    try {
      const res = await fetch('http://localhost:8000/api/context-config/reset', { method: 'POST' });
      if (!res.ok) throw new Error('Reset failed');
      const data = await res.json();
      setConfig(data.config);
      setOriginalConfig(JSON.parse(JSON.stringify(data.config)));
      setDirty(false);
      setSaveStatus('success');
      setTimeout(() => setSaveStatus('idle'), 3000);
    } catch (e) {
      setSaveStatus('error');
    } finally {
      setSaving(false);
    }
  };

  const handleUnloadModel = async () => {
    setUnloading(true);
    try {
      const res = await fetch('http://localhost:8000/api/hardware/unload', { method: 'POST' });
      const data = await res.json();
      
      if (!res.ok) {
          if (res.status === 409) {
              toast.error("Cannot unload model while a generation is in progress. Please wait.", { duration: 4000 });
          }
          throw new Error(data.detail || 'Unload failed');
      }
      
      toast.success(data.message || "Model unloaded from RAM.");
    } catch (e) {
      console.error(e);
    } finally {
      setUnloading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!config) {
    return (
      <div className="flex-1 flex items-center justify-center p-12 text-center">
        <div>
          <AlertTriangle className="w-8 h-8 text-amber-400 mx-auto mb-3" />
          <p className="text-sm font-semibold text-gray-700">Could not connect to backend</p>
          <p className="text-xs text-gray-400 mt-1">Make sure Uvicorn is running on port 8000.</p>
        </div>
      </div>
    );
  }

  const chatTokens = estimateChatTokens(config.chat);
  const plannerTokens = estimatePlannerTokens(config.planner);
  const extractorTokens = estimateExtractorTokens(config.extractor);

  return (
    <div className="flex-1 overflow-y-auto bg-[#F8F9FA]">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">

        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2.5 mb-1.5">
              <div className="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center shadow-sm">
                <SlidersHorizontal className="w-4 h-4 text-white" />
              </div>
              <h1 className="text-xl font-bold text-gray-900 tracking-tight">Settings & Controls</h1>
            </div>
            <p className="text-xs text-gray-500 leading-relaxed max-w-lg">
              Manage LLM context windows, advanced heuristics, and local hardware utilization.
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <button
              onClick={handleReset}
              disabled={saving}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold text-gray-600 border border-gray-200 bg-white hover:bg-gray-50 transition-all disabled:opacity-50"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Reset All
            </button>
            <button
              onClick={handleSave}
              disabled={!dirty || saving}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold transition-all shadow-sm ${
                saveStatus === 'success'
                  ? 'bg-emerald-500 text-white'
                  : saveStatus === 'error'
                  ? 'bg-red-500 text-white'
                  : dirty
                  ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                  : 'bg-gray-200 text-gray-400 cursor-not-allowed'
              }`}
            >
              {saveStatus === 'success' ? (
                <><CheckCircle2 className="w-3.5 h-3.5" /> Saved</>
              ) : saving ? (
                <><div className="w-3.5 h-3.5 border-2 border-white/40 border-t-white rounded-full animate-spin" /> Saving...</>
              ) : (
                <><Save className="w-3.5 h-3.5" /> Save Changes</>
              )}
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-6 border-b border-gray-200">
          <button
            onClick={() => setActiveTab('A')}
            className={`pb-2.5 px-1 text-sm font-semibold transition-colors ${
              activeTab === 'A' ? 'border-b-2 border-indigo-600 text-indigo-600' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Tier A (Tunable)
          </button>
          <button
            onClick={() => setActiveTab('B')}
            className={`pb-2.5 px-1 text-sm font-semibold transition-colors ${
              activeTab === 'B' ? 'border-b-2 border-indigo-600 text-indigo-600' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Tier B (Advanced)
          </button>
          <button
            onClick={() => setActiveTab('C')}
            className={`pb-2.5 px-1 text-sm font-semibold transition-colors ${
              activeTab === 'C' ? 'border-b-2 border-indigo-600 text-indigo-600' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Tier C (Hardware)
          </button>
        </div>

        {activeTab === 'A' && (
          <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
            {/* Global context window overview */}
            <div className="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm">
              <div className="flex items-center gap-2 mb-4">
                <Zap className="w-4 h-4 text-indigo-500" />
                <h2 className="text-sm font-bold text-gray-900">Context Window Overview</h2>
                <span className="ml-auto text-[11px] text-gray-400 font-medium">Model: Qwen 2.5 3B · n_ctx = 6,144</span>
              </div>
              <div className="grid grid-cols-3 gap-3">
                {[
                  { label: 'Chat Agent', tokens: chatTokens, color: chatTokens / TOTAL_CTX > 0.9 ? 'text-red-600' : 'text-indigo-600' },
                  { label: 'Planner Agent', tokens: plannerTokens, color: plannerTokens / TOTAL_CTX > 0.9 ? 'text-red-600' : 'text-indigo-600' },
                  { label: 'Extractor Agent', tokens: extractorTokens, color: 'text-indigo-600' },
                ].map(({ label, tokens, color }) => (
                  <div key={label} className="bg-gray-50 rounded-xl p-3 text-center border border-gray-100">
                    <p className="text-[11px] text-gray-500 font-medium mb-1">{label}</p>
                    <p className={`text-lg font-bold tabular-nums ${color}`}>~{tokens.toLocaleString()}</p>
                    <p className="text-[10px] text-gray-400">tokens</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Chat Agent Card */}
            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
              <div className="px-5 pt-5 pb-4 border-b border-gray-100">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl bg-rose-50 flex items-center justify-center border border-rose-100">
                    <MessageSquare className="w-4.5 h-4.5 text-rose-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-bold text-gray-900">Chat Agent</h2>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-rose-50 text-rose-600 border border-rose-100">
                        High Risk of Overflow
                      </span>
                    </div>
                    <p className="text-[11px] text-gray-400 mt-0.5">
                      Conversational LLM. Receives the full chat history and RAG document chunks.
                    </p>
                  </div>
                </div>
              </div>
              <div className="p-5 space-y-5">
                <TokenBar used={chatTokens} total={TOTAL_CTX} />
                <div className="h-px bg-gray-100" />
                <ParamSlider
                  label="Max History Messages"
                  description="Number of past chat turns sent to the Chat LLM"
                  value={config.chat.max_history_messages}
                  min={2}
                  max={50}
                  step={1}
                  unit="turns"
                  onChange={(v) => updateConfig('chat', 'max_history_messages', v)}
                />
                <ParamSlider
                  label="Max Chars per Message"
                  description="Long messages are truncated at this limit before being passed to LLM"
                  value={config.chat.max_msg_chars}
                  min={500}
                  max={10000}
                  step={100}
                  unit="chars"
                  onChange={(v) => updateConfig('chat', 'max_msg_chars', v)}
                />
                <ParamSlider
                  label="Max RAG Document Chunks"
                  description="Number of relevant document excerpts injected into context"
                  value={config.chat.max_rag_chunks}
                  min={0}
                  max={15}
                  step={1}
                  unit="chunks"
                  onChange={(v) => updateConfig('chat', 'max_rag_chunks', v)}
                />
              </div>
            </div>

            {/* Planner Agent Card */}
            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
              <div className="px-5 pt-5 pb-4 border-b border-gray-100">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl bg-indigo-50 flex items-center justify-center border border-indigo-100">
                    <GitBranch className="w-4.5 h-4.5 text-indigo-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-bold text-gray-900">Planner Agent</h2>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-100">
                        Safe
                      </span>
                    </div>
                    <p className="text-[11px] text-gray-400 mt-0.5">
                      JSON plan generator. History and tool result snippets are hard-capped.
                    </p>
                  </div>
                </div>
              </div>
              <div className="p-5 space-y-5">
                <TokenBar used={plannerTokens} total={TOTAL_CTX} />
                <div className="h-px bg-gray-100" />
                <ParamSlider
                  label="Max History Messages"
                  description="Number of past chat turns sent to the Planner LLM"
                  value={config.planner.max_history_messages}
                  min={1}
                  max={15}
                  step={1}
                  unit="turns"
                  onChange={(v) => updateConfig('planner', 'max_history_messages', v)}
                />
                <ParamSlider
                  label="Max Result Snippet Size"
                  description="Character cap for recent tool result block injected into Planner"
                  value={config.planner.max_result_snippet}
                  min={200}
                  max={6000}
                  step={100}
                  unit="chars"
                  onChange={(v) => updateConfig('planner', 'max_result_snippet', v)}
                />
              </div>
            </div>
            
            {/* Extractor Agent Card */}
            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
              <div className="px-5 pt-5 pb-4 border-b border-gray-100">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl bg-violet-50 flex items-center justify-center border border-violet-100">
                    <Cpu className="w-4.5 h-4.5 text-violet-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-bold text-gray-900">Extractor Agent</h2>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-100">
                        Variable
                      </span>
                    </div>
                    <p className="text-[11px] text-gray-400 mt-0.5">
                      Entity memory extractor. Reads tool results and extracts key IDs.
                    </p>
                  </div>
                </div>
              </div>
              <div className="p-5 space-y-5">
                <TokenBar used={extractorTokens} total={TOTAL_CTX} />
                <div className="h-px bg-gray-100" />
                <ParamSlider
                  label="Max Output Tokens"
                  description="Maximum tokens the Extractor LLM is allowed to generate"
                  value={config.extractor.max_tokens}
                  min={64}
                  max={2048}
                  step={64}
                  unit="tokens"
                  onChange={(v) => updateConfig('extractor', 'max_tokens', v)}
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === 'B' && (
          <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="bg-white rounded-2xl border border-amber-200 shadow-sm overflow-hidden">
              <div className="px-5 pt-5 pb-4 border-b border-amber-100 bg-amber-50/30">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl bg-amber-100 flex items-center justify-center border border-amber-200">
                    <AlertOctagon className="w-4.5 h-4.5 text-amber-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-bold text-gray-900">Advanced Calibrated Settings</h2>
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      These parameters were empirically validated during testing against real distributions. 
                      Changing them alters the core behavior of the hybrid search and pipeline.
                    </p>
                  </div>
                </div>
              </div>
              <div className="p-5 space-y-5">
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-semibold text-gray-500">Validated Default: 0.1</span>
                  <button
                    onClick={() => {
                        if(confirm("Restore validated default for RAG threshold?")) {
                            updateConfig('advanced', 'rag_confidence_threshold', 0.1);
                        }
                    }}
                    className="text-[11px] font-semibold text-indigo-600 hover:text-indigo-700 bg-indigo-50 px-2.5 py-1 rounded-md transition-colors"
                  >
                    Restore validated default
                  </button>
                </div>
                
                <ParamSlider
                  label="RAG Confidence Threshold"
                  description="Lowering this will surface more tool matches, including possibly irrelevant ones. Raising it will make the agent stricter but might miss matches."
                  value={config.advanced.rag_confidence_threshold}
                  min={0.0}
                  max={1.0}
                  step={0.05}
                  unit="score"
                  onChange={(v) => {
                      if(confirm("Are you sure you want to change the RAG threshold? Lowering this will surface more tool matches, including possibly irrelevant ones.")) {
                          updateConfig('advanced', 'rag_confidence_threshold', v);
                      }
                  }}
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === 'C' && (
          <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
             
             {/* RAM Guard / Unload Button */}
             <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                    <Activity className="w-4 h-4 text-emerald-500" />
                    Memory Management
                  </h2>
                  <p className="text-[11px] text-gray-500 mt-1">
                    Free up system RAM on demand by explicitly unloading the active model. 
                    The model will automatically reload upon the next chat turn.
                  </p>
                </div>
                <button
                  onClick={handleUnloadModel}
                  disabled={unloading}
                  className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 text-xs font-semibold rounded-xl shadow-sm hover:bg-gray-50 disabled:opacity-50 transition-all"
                >
                  {unloading ? (
                    <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Unloading...</>
                  ) : (
                    <><HardDrive className="w-3.5 h-3.5" /> Unload Model</>
                  )}
                </button>
             </div>

            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
              <div className="px-5 pt-5 pb-4 border-b border-gray-100">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl bg-slate-50 flex items-center justify-center border border-slate-200">
                    <Cpu className="w-4.5 h-4.5 text-slate-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-bold text-gray-900">Inference Hardware</h2>
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      Adjust how the LLM executes on your local hardware. Note: Changing these requires a model reload to take effect.
                    </p>
                  </div>
                </div>
              </div>
              <div className="p-5 space-y-6">
                
                <ParamSlider
                  label="GPU Offload Layers (n_gpu_layers)"
                  description="Number of model layers to run on the GPU. Set to -1 to offload all layers."
                  value={config.hardware.n_gpu_layers}
                  min={-1}
                  max={40}
                  step={1}
                  unit="layers"
                  onChange={(v) => updateConfig('hardware', 'n_gpu_layers', v)}
                />
                
                <ParamSlider
                  label="CPU Threads (n_threads)"
                  description="Number of threads for CPU-based inference. If heavily offloading to GPU, this matters less."
                  value={config.hardware.n_threads}
                  min={1}
                  max={16}
                  step={1}
                  unit="threads"
                  onChange={(v) => updateConfig('hardware', 'n_threads', v)}
                />

                <div className="h-px bg-gray-100" />
                
                {/* Locked llm_executor display */}
                <div className="bg-gray-50 rounded-xl p-4 border border-gray-200 relative group">
                    <ParamSlider
                    label="LLM Max Workers"
                    description="Maximum concurrent executions allowed in the LLM thread pool."
                    value={1}
                    min={1}
                    max={1}
                    step={1}
                    unit="workers"
                    disabled={true}
                    />
                    <div className="absolute top-4 right-4 flex items-center gap-1.5 text-[10px] font-bold text-gray-500 bg-white px-2 py-1 rounded-md border border-gray-200 shadow-sm">
                        <Lock className="w-3 h-3" /> Locked
                    </div>
                    <p className="text-[11px] text-gray-500 font-medium mt-3 bg-white px-3 py-2 rounded-lg border border-gray-200">
                        <span className="font-bold text-gray-700">Why is this locked?</span> This must remain 1 to prevent severe GPU kernel crashes (Metal) or heavy VRAM contention (CUDA). Consumer-grade local inference requires strict serialization.
                    </p>
                </div>

              </div>
            </div>
          </div>
        )}

        {/* Bottom padding */}
        <div className="h-4" />
      </div>
    </div>
  );
}
