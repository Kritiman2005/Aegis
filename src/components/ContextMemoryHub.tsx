import React, { useState, useEffect, useRef } from 'react';
import { Cpu, Zap, BrainCircuit, ShieldAlert, AlertTriangle, Play, RefreshCw, HardDrive, Database } from 'lucide-react';
import toast from 'react-hot-toast';

interface HardwareStatus {
  active_model: string;
  max_context: number;
  ram_total_gb: number;
  ram_used_gb: number;
  ram_percent: number;
}

interface ChatConfig {
  max_history_messages: number;
  max_msg_chars: number;
  max_output_tokens: number;
  max_result_snippet: number;
}

interface AgentConfig {
  max_history_messages: number;
  max_msg_chars: number;
  max_result_snippet: number;
  max_output_tokens: number;
}

export default function ContextMemoryHub() {
  const [hardware, setHardware] = useState<HardwareStatus | null>(null);
  const [mode, setMode] = useState<'chat' | 'agent'>('chat');
  const [chatConfig, setChatConfig] = useState<ChatConfig>({
    max_history_messages: 20,
    max_msg_chars: 4000,
    max_output_tokens: 5120,
    max_result_snippet: 2000
  });
  const [agentConfig, setAgentConfig] = useState<AgentConfig>({
    max_history_messages: 6,
    max_msg_chars: 2000,
    max_result_snippet: 2000,
    max_output_tokens: 5120
  });
  
  const [hasChanges, setHasChanges] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [downloadedModels, setDownloadedModels] = useState<any[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<number | ''>('');
  const [unloading, setUnloading] = useState(false);
  const [loadingModel, setLoadingModel] = useState(false);

  useEffect(() => {
    fetchHardware();
    fetchConfig();
    fetchDownloadedModels();
    const interval = setInterval(fetchHardware, 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchDownloadedModels = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/hub/downloaded');
      if (res.ok) {
        const data = await res.json();
        setDownloadedModels(data.models || []);
      }
    } catch (e) {}
  };

  const fetchHardware = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/hardware/status');
      if (res.ok) {
        setHardware(await res.json());
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchConfig = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/context-config');
      if (res.ok) {
        const data = await res.json();
        if (data.chat) setChatConfig({ ...chatConfig, ...data.chat });
        if (data.planner) setAgentConfig({ ...agentConfig, ...data.planner }); // It comes back as planner in the backend structure currently
      }
    } catch (e) {
      console.error(e);
    }
  };

  const saveConfig = async () => {
    setIsSaving(true);
    try {
      await fetch('http://127.0.0.1:8000/api/context-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat: chatConfig,
          agent: agentConfig 
        })
      });
      setHasChanges(false);
    } catch (e) {
      console.error(e);
    } finally {
      setIsSaving(false);
    }
  };

  const handleChatChange = (key: keyof ChatConfig, val: number) => {
    setChatConfig({ ...chatConfig, [key]: val });
    setHasChanges(true);
  };

  const handleAgentChange = (key: keyof AgentConfig, val: number) => {
    setAgentConfig({ ...agentConfig, [key]: val });
    setHasChanges(true);
  };

  const handleUnloadModel = async () => {
    setUnloading(true);
    try {
      const res = await fetch('http://127.0.0.1:8000/api/hardware/unload', { method: 'POST' });
      if (res.ok) {
        toast.success("Model ejected from RAM.");
        fetchHardware();
      } else {
        toast.error("Failed to eject model.");
      }
    } catch (e) {
      toast.error("Network error.");
    } finally {
      setUnloading(false);
    }
  };

  const handleLoadModel = async () => {
    if (!selectedModelId) return;
    setLoadingModel(true);
    try {
      const res = await fetch('http://127.0.0.1:8000/api/hardware/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: selectedModelId })
      });
      if (res.ok) {
        toast.success("Model set as active and RAM cleared.");
        fetchHardware();
      } else {
        toast.error("Failed to load model.");
      }
    } catch (e) {
      toast.error("Network error.");
    } finally {
      setLoadingModel(false);
    }
  };

  // === Dynamic RAM & Latency Estimation ===
  const modelMaxContext = hardware?.max_context || 4096;
  const activeTokens = mode === 'chat' ? chatConfig.max_output_tokens : agentConfig.max_output_tokens;

  // KV cache: each token costs ~0.5 MB for a typical 3B model (2 layers * 2 (K+V) * hidden_dim * bytes)
  // Scale it by ratio of requested context vs model max context
  const contextRatio = activeTokens / modelMaxContext;
  const baseKvGb = modelMaxContext * 0.000125;  // approx at model max
  const extraKvGb = baseKvGb * contextRatio;     // scales proportionally

  const otherAppsGb = hardware ? (hardware.ram_used_gb > 2 ? hardware.ram_used_gb - 2 : hardware.ram_used_gb) : 0;
  const modelBaseGb = (hardware && hardware.active_model !== 'None') ? 2.0 : 0; // approx weights only
  const modelEstimatedGb = modelBaseGb + extraKvGb;
  const availableGb = hardware ? Math.max(0, hardware.ram_total_gb - hardware.ram_used_gb - extraKvGb) : 0;
  const isLowMemory = availableGb < 2;

  // Latency tier based on context ratio
  const latencyLabel = contextRatio > 0.75 ? 'High' : contextRatio > 0.4 ? 'Medium' : 'Low';
  const latencyColor = contextRatio > 0.75 ? 'text-red-500' : contextRatio > 0.4 ? 'text-amber-500' : 'text-green-600';

  // Chart circle geometry
  const radius = 60;
  const stroke = 12;
  const normalizedRadius = radius - stroke * 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const strokeDashoffset = circumference - ((availableGb > 0 ? (availableGb / (hardware?.ram_total_gb || 16)) : 0.05) * circumference);

  return (
    <div className="flex-1 overflow-y-auto bg-[#F4F5F7] p-8 font-sans">
      <div className="max-w-3xl mx-auto space-y-6">
        
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center text-white shadow-sm">
            <Database className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Context & Memory Hub</h1>
            <p className="text-sm text-gray-500">Configure how the local AI remembers context — and see the real-time impact on your system.</p>
          </div>
        </div>

        {/* Live RAM Analysis Card */}
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
          <div className="flex justify-between items-start mb-6">
            <div>
              <h2 className="text-base font-bold text-gray-900">Live RAM Analysis</h2>
              <p className="text-xs text-gray-400">Reflects your current configuration</p>
            </div>
            {isLowMemory && (
              <div className="flex items-center gap-1.5 px-3 py-1 bg-red-50 text-red-600 border border-red-100 rounded-full text-xs font-semibold">
                <AlertTriangle className="w-3.5 h-3.5" />
                Low Memory
              </div>
            )}
          </div>

          <div className="flex items-center gap-10">
            {/* Donut Chart */}
            <div className="relative w-40 h-40 flex-shrink-0 flex items-center justify-center">
              <svg height={radius * 2} width={radius * 2} className="-rotate-90">
                <circle stroke="#E5E7EB" fill="transparent" strokeWidth={stroke} r={normalizedRadius} cx={radius} cy={radius} />
                <circle 
                  stroke={isLowMemory ? "#EF4444" : "#6366F1"} 
                  fill="transparent" 
                  strokeWidth={stroke} 
                  strokeDasharray={circumference + ' ' + circumference} 
                  style={{ strokeDashoffset }} 
                  r={normalizedRadius} cx={radius} cy={radius} 
                  className="transition-all duration-1000 ease-in-out"
                />
              </svg>
              <div className="absolute flex flex-col items-center justify-center text-center">
                <span className="text-[10px] font-bold text-gray-400 tracking-wider">AVAILABLE</span>
                <span className="text-2xl font-black text-gray-900 leading-none">{availableGb.toFixed(1)}</span>
                <span className="text-xs font-semibold text-gray-400">GB</span>
              </div>
            </div>

            {/* Legend & Stats */}
            <div className="flex-1 space-y-4">
              <div className="space-y-2">
                <div className="flex justify-between text-xs font-medium">
                  <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded bg-gray-300"/> <span className="text-gray-500">Other Apps</span></div>
                  <span className="text-gray-900">{otherAppsGb.toFixed(1)} GB</span>
                </div>
                <div className="flex justify-between text-xs font-medium">
                  <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded bg-red-500"/> <span className="text-gray-500">Model (estimated)</span></div>
                  <span className="text-gray-900">{modelEstimatedGb.toFixed(1)} GB</span>
                </div>
                <div className="flex justify-between text-xs font-medium">
                  <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded bg-blue-200"/> <span className="text-gray-500">Free</span></div>
                  <span className="text-gray-900">0.0 GB</span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-2">
                <div className="bg-gray-50 p-3 rounded-xl">
                  <div className="text-[9px] font-bold text-gray-400 tracking-wider uppercase mb-0.5">Total RAM</div>
                  <div className="text-sm font-bold text-gray-900">{hardware ? hardware.ram_total_gb.toFixed(0) : 16} GB</div>
                </div>
                <div className="bg-gray-50 p-3 rounded-xl">
                  <div className="text-[9px] font-bold text-gray-400 tracking-wider uppercase mb-0.5">Available</div>
                  <div className="text-sm font-bold text-blue-600">{availableGb.toFixed(1)} GB</div>
                </div>
                <div className="bg-gray-50 p-3 rounded-xl">
                  <div className="text-[9px] font-bold text-gray-400 tracking-wider uppercase mb-0.5">Model Required</div>
                  <div className="text-sm font-bold text-gray-900">{modelEstimatedGb.toFixed(1)} GB</div>
                </div>
                <div className="bg-gray-50 p-3 rounded-xl">
                  <div className="text-[9px] font-bold text-gray-400 tracking-wider uppercase mb-0.5">Latency Est.</div>
                  <div className={`text-sm font-bold ${latencyColor}`}>
                    {latencyLabel}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {isLowMemory && (
            <div className="mt-6 bg-red-50 text-red-600 text-xs p-3 rounded-lg border border-red-100 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <p>
                <strong>Low Memory:</strong> Available RAM is less than required. The model will run, but response times will be severely impacted. Consider closing other apps or lowering context limits.
              </p>
            </div>
          )}
        </div>

        {/* Mode Toggle */}
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-2 flex items-center justify-between">
          <div className="flex items-center gap-3 px-2">
            <div 
              className={`w-12 h-6 rounded-full p-1 cursor-pointer transition-colors ${mode === 'agent' ? 'bg-indigo-600' : 'bg-gray-200'}`}
              onClick={() => setMode(mode === 'chat' ? 'agent' : 'chat')}
            >
              <div className={`w-4 h-4 bg-white rounded-full shadow-sm transition-transform ${mode === 'agent' ? 'translate-x-6' : 'translate-x-0'}`} />
            </div>
            <div>
              <div className="text-sm font-bold text-gray-900">{mode === 'agent' ? 'Agent Mode' : 'Chat Mode'}</div>
              <div className="text-[10px] text-gray-400">{mode === 'agent' ? 'Multi-step autonomous execution' : 'Normal chat mode — single-turn responses'}</div>
            </div>
          </div>
          <div className="flex gap-1 p-1 bg-gray-50 rounded-xl">
            <button onClick={() => setMode('chat')} className={`px-4 py-1.5 text-xs font-semibold rounded-lg transition-all ${mode === 'chat' ? 'bg-white shadow-sm text-gray-900' : 'text-gray-400'}`}>Chat</button>
            <button onClick={() => setMode('agent')} className={`px-4 py-1.5 text-xs font-semibold rounded-lg transition-all ${mode === 'agent' ? 'bg-white shadow-sm text-indigo-600' : 'text-gray-400'}`}>Agent</button>
          </div>
        </div>

        {/* Sliders Area */}
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6 space-y-8">
          <div>
            <h2 className="text-base font-bold text-gray-900">Memory & Context Settings</h2>
            <p className="text-xs text-gray-400">Higher values improve AI recall but increase RAM consumption and latency.</p>
          </div>

          {mode === 'chat' ? (
            <div className="space-y-8">
              {/* Context Window */}
              <div>
                <div className="flex justify-between items-end mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-900">Context Window</span>
                    <span className="text-[10px] text-gray-400">tokens</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 bg-amber-50 text-amber-600 rounded text-[9px] font-bold tracking-wide">Latency {latencyLabel}</span>
                    <span className="px-1.5 py-0.5 bg-indigo-50 text-indigo-500 rounded text-[9px] font-bold tracking-wide">RAM ↑</span>
                    <span className="text-sm font-bold text-indigo-600">{chatConfig.max_output_tokens.toLocaleString()}</span>
                  </div>
                </div>
                <input 
                  type="range" min={2048} max={hardware?.max_context || 4096} step={512}
                  value={chatConfig.max_output_tokens}
                  onChange={(e) => handleChatChange('max_output_tokens', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 focus:outline-none"
                />
                <div className="flex justify-between text-[10px] font-medium text-gray-400 mt-2">
                  <span>2,048</span>
                  <span className="text-indigo-500 font-bold">Model max: {(hardware?.max_context || 4096).toLocaleString()}</span>
                </div>
              </div>

              {/* Max History Messages */}
              <div>
                <div className="flex justify-between items-end mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-900">Max History Messages</span>
                    <span className="text-[10px] text-gray-400">messages back</span>
                  </div>
                  <span className="text-sm font-bold text-indigo-600">{chatConfig.max_history_messages}</span>
                </div>
                <input 
                  type="range" min={1} max={50} step={1}
                  value={chatConfig.max_history_messages}
                  onChange={(e) => handleChatChange('max_history_messages', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 focus:outline-none"
                />
                <div className="flex justify-between text-[10px] font-medium text-gray-400 mt-2">
                  <span>1</span>
                  <span>50</span>
                </div>
              </div>

              {/* Max Characters Per Message */}
              <div>
                <div className="flex justify-between items-end mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-900">Max Characters Per Message</span>
                    <span className="text-[10px] text-gray-400">chars</span>
                  </div>
                  <span className="text-sm font-bold text-indigo-600">{chatConfig.max_msg_chars.toLocaleString()}</span>
                </div>
                <input 
                  type="range" min={500} max={8000} step={500}
                  value={chatConfig.max_msg_chars}
                  onChange={(e) => handleChatChange('max_msg_chars', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 focus:outline-none"
                />
                <div className="flex justify-between text-[10px] font-medium text-gray-400 mt-2">
                  <span>500</span>
                  <span>8.0k</span>
                </div>
              </div>

              {/* Chat: Max Result Snippet */}
              <div>
                <div className="flex justify-between items-end mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-900">Tool Result Snippet Size</span>
                  </div>
                  <span className="text-sm font-bold text-indigo-600">{chatConfig.max_result_snippet.toLocaleString()}</span>
                </div>
                <input 
                  type="range" min={500} max={10000} step={500}
                  value={chatConfig.max_result_snippet}
                  onChange={(e) => handleChatChange('max_result_snippet', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 focus:outline-none"
                />
                <div className="flex justify-between text-[10px] font-medium text-gray-400 mt-2">
                  <span>500</span>
                  <span>10.0k</span>
                </div>
              </div>

              <div className="bg-indigo-50 p-3 rounded-lg border border-indigo-100 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-indigo-400 flex-shrink-0" />
                <p className="text-xs text-indigo-800">
                  Adjusting <strong className="font-semibold text-indigo-900">Context Window</strong> has the largest RAM impact — reflected live in the chart above.
                </p>
              </div>

            </div>
          ) : (
            <div className="space-y-8">
              {/* Agent Context Window */}
              <div>
                <div className="flex justify-between items-end mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-900">Context Window</span>
                    <span className="text-[10px] text-gray-400">tokens</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 bg-amber-50 text-amber-600 rounded text-[9px] font-bold tracking-wide">Latency {latencyLabel}</span>
                    <span className="px-1.5 py-0.5 bg-indigo-50 text-indigo-500 rounded text-[9px] font-bold tracking-wide">RAM ↑</span>
                    <span className="text-sm font-bold text-indigo-600">{agentConfig.max_output_tokens.toLocaleString()}</span>
                  </div>
                </div>
                <input 
                  type="range" min={2048} max={hardware?.max_context || 4096} step={512}
                  value={agentConfig.max_output_tokens}
                  onChange={(e) => handleAgentChange('max_output_tokens', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 focus:outline-none"
                />
                <div className="flex justify-between text-[10px] font-medium text-gray-400 mt-2">
                  <span>2,048</span>
                  <span className="text-indigo-500 font-bold">Model max: {(hardware?.max_context || 4096).toLocaleString()}</span>
                </div>
              </div>

              {/* Agent: Max History Messages */}
              <div>
                <div className="flex justify-between items-end mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-900">Agent Max History</span>
                    <span className="text-[10px] text-gray-400">messages back</span>
                  </div>
                  <span className="text-sm font-bold text-indigo-600">{agentConfig.max_history_messages}</span>
                </div>
                <input 
                  type="range" min={1} max={20} step={1}
                  value={agentConfig.max_history_messages}
                  onChange={(e) => handleAgentChange('max_history_messages', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 focus:outline-none"
                />
                <div className="flex justify-between text-[10px] font-medium text-gray-400 mt-2">
                  <span>1</span>
                  <span>20</span>
                </div>
              </div>

              {/* Agent: Max Chars */}
              <div>
                <div className="flex justify-between items-end mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-900">Max Characters Per Message</span>
                  </div>
                  <span className="text-sm font-bold text-indigo-600">{agentConfig.max_msg_chars.toLocaleString()}</span>
                </div>
                <input 
                  type="range" min={500} max={10000} step={500}
                  value={agentConfig.max_msg_chars}
                  onChange={(e) => handleAgentChange('max_msg_chars', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 focus:outline-none"
                />
                <div className="flex justify-between text-[10px] font-medium text-gray-400 mt-2">
                  <span>500</span>
                  <span>10.0k</span>
                </div>
              </div>
              
              {/* Agent: Max Result Snippet */}
              <div>
                <div className="flex justify-between items-end mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-900">Tool Result Snippet Size</span>
                  </div>
                  <span className="text-sm font-bold text-indigo-600">{agentConfig.max_result_snippet.toLocaleString()}</span>
                </div>
                <input 
                  type="range" min={500} max={10000} step={500}
                  value={agentConfig.max_result_snippet}
                  onChange={(e) => handleAgentChange('max_result_snippet', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 focus:outline-none"
                />
                <div className="flex justify-between text-[10px] font-medium text-gray-400 mt-2">
                  <span>500</span>
                  <span>10.0k</span>
                </div>
              </div>
            </div>
          )}

          {/* Save Button */}
          <div className="pt-4 border-t border-gray-100 flex justify-end">
            <button
              onClick={saveConfig}
              disabled={!hasChanges || isSaving}
              className={`px-6 py-2.5 rounded-xl text-sm font-semibold transition-all ${
                hasChanges 
                  ? 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-sm' 
                  : 'bg-gray-100 text-gray-400 cursor-not-allowed'
              }`}
            >
              {isSaving ? 'Saving...' : 'Save Settings'}
            </button>
          </div>
        </div>

      {/* Model Management Section */}
        <div className="bg-white rounded-3xl border border-gray-100 shadow-sm p-6 flex flex-col gap-4">
          <div className="flex items-center justify-between border-b border-gray-100 pb-4">
            <div>
              <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                <HardDrive className="w-5 h-5 text-indigo-500" />
                Model Management
              </h2>
              <p className="text-xs text-gray-500 mt-1">Eject the current model from RAM or switch to a different downloaded model.</p>
            </div>
            
            <button
              onClick={handleUnloadModel}
              disabled={unloading}
              className="flex items-center gap-2 px-5 py-2.5 bg-red-50 text-red-600 text-sm font-semibold rounded-xl hover:bg-red-100 disabled:opacity-50 transition-all"
            >
              {unloading ? (
                <><RefreshCw className="w-4 h-4 animate-spin" /> Ejecting...</>
              ) : (
                <>Eject Active Model</>
              )}
            </button>
          </div>
          
          <div className="flex items-end gap-4 mt-2">
            <div className="flex-1">
              <label className="block text-xs font-semibold text-gray-700 mb-2">Switch Active Model</label>
              <select 
                value={selectedModelId}
                onChange={(e) => setSelectedModelId(e.target.value ? parseInt(e.target.value) : '')}
                className="w-full bg-gray-50 border border-gray-200 text-gray-900 text-sm rounded-xl px-4 py-2.5 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
              >
                <option value="">-- Select a downloaded model --</option>
                {downloadedModels.map((m) => (
                  <option key={m.id} value={m.id}>{m.repo_id || m.name}</option>
                ))}
              </select>
            </div>
            <button
              onClick={handleLoadModel}
              disabled={!selectedModelId || loadingModel}
              className="flex items-center justify-center gap-2 px-6 py-2.5 bg-indigo-600 text-white text-sm font-semibold rounded-xl hover:bg-indigo-700 disabled:opacity-50 transition-all h-[42px]"
            >
              {loadingModel ? (
                <RefreshCw className="w-4 h-4 animate-spin" />
              ) : (
                'Set Active'
              )}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
