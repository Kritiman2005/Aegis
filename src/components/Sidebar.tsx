'use client';

import { 
  PlusCircle, 
  History, 
  Blocks, 
  Settings, 
  User, 
  HelpCircle, 
  Shield 
} from 'lucide-react';

export type TabType = 'chat' | 'connectors' | 'settings' | 'history' | 'sync_detail';

interface SidebarProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  onNewChat: () => void;
}

export default function Sidebar({ activeTab, setActiveTab, onNewChat }: SidebarProps) {
  return (
    <aside className="w-60 flex-shrink-0 flex flex-col h-full bg-[#FAFAFA] border-r border-gray-200">
      {/* ── Brand Logo Header ────────────────────────────────────────────── */}
      <div className="p-6 pb-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-black flex items-center justify-center text-white shadow-md">
            <Shield className="w-5 h-5 fill-white text-black" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-gray-900 tracking-tight leading-tight">
              Aegis
            </h1>
            <p className="text-[11px] text-gray-500 font-medium">Personal AI Assistant</p>
          </div>
        </div>

        {/* Primary Action: + New Chat Button (Large Black Pill) */}
        <button
          onClick={() => {
            onNewChat();
            setActiveTab('chat');
          }}
          className="w-full mt-6 flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-black text-white text-xs font-semibold hover:bg-neutral-800 active:scale-95 transition-all shadow-sm"
        >
          <PlusCircle className="w-4 h-4" />
          New Chat
        </button>
      </div>

      {/* ── Main Navigation Items ───────────────────────────────────────── */}
      <div className="flex-1 px-3 py-2 space-y-1">
        {/* Secondary New Chat */}
        <button
          onClick={() => {
            onNewChat();
            setActiveTab('chat');
          }}
          className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-medium transition-all ${
            activeTab === 'chat'
              ? 'bg-gray-200/60 text-gray-900 font-semibold'
              : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
          }`}
        >
          <PlusCircle className="w-4 h-4 text-gray-500" />
          New Chat
        </button>

        {/* History */}
        <button
          onClick={() => setActiveTab('history')}
          className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-medium transition-all ${
            activeTab === 'history'
              ? 'bg-gray-200/60 text-gray-900 font-semibold'
              : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
          }`}
        >
          <History className="w-4 h-4 text-gray-500" />
          History
        </button>

        {/* Connectors */}
        <button
          onClick={() => setActiveTab('connectors')}
          className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-medium transition-all ${
            activeTab === 'connectors' || activeTab === 'sync_detail'
              ? 'bg-gray-200/80 text-gray-900 font-semibold shadow-sm'
              : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
          }`}
        >
          <Blocks className="w-4 h-4 text-gray-700" />
          Connectors
        </button>

        {/* Settings */}
        <button
          onClick={() => setActiveTab('settings')}
          className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-medium transition-all ${
            activeTab === 'settings'
              ? 'bg-gray-200/60 text-gray-900 font-semibold'
              : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
          }`}
        >
          <Settings className="w-4 h-4 text-gray-500" />
          Settings
        </button>
      </div>

      {/* ── Footer Navigation ───────────────────────────────────────────── */}
      <div className="p-3 border-t border-gray-200 space-y-1">
        <button className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-all">
          <User className="w-4 h-4 text-gray-500" />
          Account
        </button>
        <button className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-all">
          <HelpCircle className="w-4 h-4 text-gray-500" />
          Help
        </button>
      </div>
    </aside>
  );
}
