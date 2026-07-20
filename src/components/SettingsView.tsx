'use client';

import { useState } from 'react';
import { 
  Sun, 
  Moon, 
  Monitor, 
  Cpu, 
  Bell, 
  Lock, 
  Key, 
  Copy, 
  Trash2, 
  Plus,
  Check
} from 'lucide-react';

export default function SettingsView() {
  const [activeTab, setActiveTab] = useState<'general' | 'profile' | 'billing' | 'advanced'>('general');
  const [theme, setTheme] = useState<'light' | 'dark' | 'system'>('light');
  const [aiModel, setAiModel] = useState<'pro' | 'lite'>('pro');
  const [desktopAlerts, setDesktopAlerts] = useState(true);
  const [emailDigest, setEmailDigest] = useState(false);
  const [copiedKey, setCopiedKey] = useState(false);

  const copyApiKey = () => {
    navigator.clipboard.writeText('sk_live_aegis_8834920194821');
    setCopiedKey(true);
    setTimeout(() => setCopiedKey(false), 2000);
  };

  return (
    <div className="flex-1 overflow-y-auto bg-[#F8F9FA] p-8 space-y-8">
      {/* Title Header */}
      <div>
        <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Settings</h2>
        <div className="flex items-center gap-6 border-b border-gray-200 mt-4">
          {(['general', 'profile', 'billing', 'advanced'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-3 text-xs font-semibold capitalize transition-all relative ${
                activeTab === tab
                  ? 'text-gray-900 border-b-2 border-black'
                  : 'text-gray-500 hover:text-gray-900'
              }`}
            >
              {tab === 'general' ? 'General Preferences' : tab}
            </button>
          ))}
        </div>
      </div>

      <div className="max-w-4xl space-y-8">
        {/* Section 1: Top Row Grid (Interface Theme & AI Intelligence) */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Interface Theme Card (Matching Image 4) */}
          <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4 shadow-sm">
            <div className="flex items-center gap-2">
              <Sun className="w-4 h-4 text-gray-700" />
              <h3 className="text-sm font-bold text-gray-900">Interface Theme</h3>
            </div>

            <div className="grid grid-cols-3 gap-3 pt-2">
              <button
                onClick={() => setTheme('light')}
                className={`p-3 rounded-xl border flex flex-col items-center justify-center gap-2 transition-all ${
                  theme === 'light'
                    ? 'border-black bg-gray-50 ring-1 ring-black'
                    : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <Sun className="w-5 h-5 text-gray-700" />
                <span className="text-xs font-semibold text-gray-900">Light</span>
              </button>

              <button
                onClick={() => setTheme('dark')}
                className={`p-3 rounded-xl border flex flex-col items-center justify-center gap-2 transition-all ${
                  theme === 'dark'
                    ? 'border-black bg-gray-900 text-white'
                    : 'border-gray-200 bg-gray-100/50 hover:bg-gray-100'
                }`}
              >
                <Moon className="w-5 h-5 text-gray-500" />
                <span className="text-xs font-semibold text-gray-700">Dark</span>
              </button>

              <button
                onClick={() => setTheme('system')}
                className={`p-3 rounded-xl border flex flex-col items-center justify-center gap-2 transition-all ${
                  theme === 'system'
                    ? 'border-black bg-gray-50 ring-1 ring-black'
                    : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <Monitor className="w-5 h-5 text-gray-500" />
                <span className="text-xs font-semibold text-gray-700">System</span>
              </button>
            </div>
          </div>

          {/* AI Intelligence Card (Matching Image 4) */}
          <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4 shadow-sm">
            <div className="flex items-center gap-2">
              <Cpu className="w-4 h-4 text-purple-600" />
              <h3 className="text-sm font-bold text-gray-900">AI Intelligence</h3>
            </div>

            <div className="space-y-3 pt-1">
              <label
                onClick={() => setAiModel('pro')}
                className={`flex items-start justify-between p-3 rounded-xl border cursor-pointer transition-all ${
                  aiModel === 'pro' ? 'border-purple-600 bg-purple-50/40 ring-1 ring-purple-600' : 'border-gray-200'
                }`}
              >
                <div>
                  <p className="text-xs font-bold text-gray-900">Aegis Pro (Qwen2.5-3B-Instruct)</p>
                  <p className="text-[11px] text-gray-500">Enhanced reasoning & structured tool planning</p>
                </div>
                <input
                  type="radio"
                  name="model"
                  checked={aiModel === 'pro'}
                  onChange={() => setAiModel('pro')}
                  className="accent-purple-600 mt-1"
                />
              </label>

              <label
                onClick={() => setAiModel('lite')}
                className={`flex items-start justify-between p-3 rounded-xl border cursor-pointer transition-all ${
                  aiModel === 'lite' ? 'border-purple-600 bg-purple-50/40 ring-1 ring-purple-600' : 'border-gray-200'
                }`}
              >
                <div>
                  <p className="text-xs font-bold text-gray-900">Aegis Lite</p>
                  <p className="text-[11px] text-gray-500">Fast & efficient for simple tasks</p>
                </div>
                <input
                  type="radio"
                  name="model"
                  checked={aiModel === 'lite'}
                  onChange={() => setAiModel('lite')}
                  className="accent-purple-600 mt-1"
                />
              </label>
            </div>
          </div>
        </div>

        {/* Section 2: Notifications (Matching Image 4) */}
        <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4 shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-100 pb-3">
            <div className="flex items-center gap-2">
              <Bell className="w-4 h-4 text-gray-700" />
              <h3 className="text-sm font-bold text-gray-900">Notifications</h3>
            </div>
            <button className="text-xs text-indigo-600 hover:underline font-medium">Reset to Default</button>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-bold text-gray-900">Desktop Alerts</p>
                <p className="text-[11px] text-gray-500">Receive system notifications for MCP tool completion.</p>
              </div>
              <button
                onClick={() => setDesktopAlerts(!desktopAlerts)}
                className={`w-11 h-6 rounded-full transition-all relative ${desktopAlerts ? 'bg-black' : 'bg-gray-200'}`}
              >
                <span className={`w-5 h-5 rounded-full bg-white absolute top-0.5 transition-all ${desktopAlerts ? 'left-5' : 'left-0.5'}`} />
              </button>
            </div>

            <div className="flex items-center justify-between pt-2">
              <div>
                <p className="text-xs font-bold text-gray-900">Email Digest</p>
                <p className="text-[11px] text-gray-500">Weekly summary of your workspace activity.</p>
              </div>
              <button
                onClick={() => setEmailDigest(!emailDigest)}
                className={`w-11 h-6 rounded-full transition-all relative ${emailDigest ? 'bg-black' : 'bg-gray-200'}`}
              >
                <span className={`w-5 h-5 rounded-full bg-white absolute top-0.5 transition-all ${emailDigest ? 'left-5' : 'left-0.5'}`} />
              </button>
            </div>
          </div>
        </div>

        {/* Section 3: Bottom Grid (Account Security & API Access) */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Account Security Card (Matching Image 4) */}
          <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4 shadow-sm">
            <div className="flex items-center gap-2">
              <Lock className="w-4 h-4 text-gray-700" />
              <h3 className="text-sm font-bold text-gray-900">Account Security</h3>
            </div>

            <div className="space-y-3 pt-1">
              <button className="w-full p-3 rounded-xl border border-gray-200 flex items-center justify-between text-xs font-medium text-gray-800 hover:bg-gray-50 transition-all">
                <span>Change Password</span>
                <span className="text-gray-400">›</span>
              </button>

              <div className="p-3 rounded-xl border border-gray-200 flex items-center justify-between text-xs font-medium text-gray-800">
                <span>Two-Factor Authentication</span>
                <span className="text-red-500 font-semibold">Disabled</span>
              </div>
            </div>
          </div>

          {/* API Access Card (Matching Image 4) */}
          <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Key className="w-4 h-4 text-indigo-600" />
                <h3 className="text-sm font-bold text-gray-900">API Access</h3>
              </div>
              <button className="text-xs text-indigo-600 font-semibold hover:underline flex items-center gap-1">
                <Plus className="w-3.5 h-3.5" /> Create Key
              </button>
            </div>

            <div className="p-3.5 rounded-xl bg-gray-50 border border-gray-200 flex items-center justify-between">
              <div>
                <p className="text-xs font-bold text-gray-900">Workspace_Main</p>
                <p className="text-xs font-mono text-gray-500 mt-0.5">sk_live_••••••••2481</p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={copyApiKey}
                  className="p-1.5 text-gray-500 hover:text-gray-900 hover:bg-gray-200 rounded-lg transition-colors"
                  title="Copy Key"
                >
                  {copiedKey ? <Check className="w-4 h-4 text-green-600" /> : <Copy className="w-4 h-4" />}
                </button>
                <button className="p-1.5 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>

            <p className="text-[11px] text-gray-400 leading-relaxed">
              API keys grant full access to your account data. Keep them secret and never share them in public repositories.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
