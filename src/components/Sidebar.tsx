'use client';

import { useState } from 'react';
import { 
  Link2,
  Cpu,
  Compass,
  MessageSquarePlus,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';

export type TabType = 'chat' | 'connectors' | 'llms' | 'discover' | 'history' | 'sync_detail' | 'model_hub' | 'context';

interface SidebarProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  onNewChat: () => void;
  recentChats?: { id: string; preview: string }[];
}

const NAV_ITEMS = [
  { id: 'connectors' as TabType, label: 'Connectors', icon: Link2 },
  { id: 'llms'       as TabType, label: 'LLMs',        icon: Cpu },
  { id: 'discover'   as TabType, label: 'Discover',    icon: Compass },
];

export default function Sidebar({ activeTab, setActiveTab, onNewChat, recentChats = [] }: SidebarProps) {
  const [recentsOpen, setRecentsOpen] = useState(true);

  return (
    <aside className="w-48 flex-shrink-0 flex flex-col h-full bg-white border-r border-[#E8EAED]">
      
      {/* ── Nav Items ──────────────────────────────────────────────────── */}
      <nav className="flex-1 pt-3 px-2 space-y-0.5">
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => {
          const isActive = activeTab === id;
          return (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] font-medium transition-colors ${
                isActive
                  ? 'bg-[#5B50F0] text-white'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {label}
            </button>
          );
        })}

        {/* New Chat */}
        <button
          onClick={() => { onNewChat(); setActiveTab('chat'); }}
          className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] font-medium transition-colors ${
            activeTab === 'chat'
              ? 'bg-[#5B50F0] text-white'
              : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
          }`}
        >
          <MessageSquarePlus className="w-4 h-4 flex-shrink-0" />
          New Chat
        </button>

        {/* ── Recents Section ─────────────────────────────────────────── */}
        <div className="pt-2">
          <button
            onClick={() => setRecentsOpen(v => !v)}
            className="w-full flex items-center justify-between px-3 py-2 text-[12px] font-semibold text-gray-500 hover:text-gray-700 transition-colors"
          >
            <span>Recents</span>
            {recentsOpen
              ? <ChevronUp className="w-3.5 h-3.5" />
              : <ChevronDown className="w-3.5 h-3.5" />
            }
          </button>

          {recentsOpen && (
            <div className="mt-0.5 space-y-0.5">
              {recentChats.length > 0 ? (
                recentChats.slice(0, 6).map(chat => (
                  <button
                    key={chat.id}
                    className="w-full flex items-center px-3 py-2 rounded-lg text-[12px] text-gray-500 hover:bg-gray-100 hover:text-gray-800 transition-colors truncate text-left"
                  >
                    <span className="truncate">{chat.preview}</span>
                  </button>
                ))
              ) : (
                <p className="px-3 py-2 text-[12px] text-gray-400">No recent chats</p>
              )}
            </div>
          )}
        </div>
      </nav>

      {/* ── User Footer ─────────────────────────────────────────────────── */}
      <div className="p-3 border-t border-[#E8EAED]">
        <div className="flex items-center gap-2.5 px-2 py-2 rounded-lg hover:bg-gray-100 cursor-pointer transition-colors">
          <div className="w-7 h-7 rounded-full bg-blue-500 flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
            K
          </div>
          <div className="min-w-0">
            <p className="text-[12px] font-semibold text-gray-800 leading-tight">Kritiman</p>
            <p className="text-[11px] text-gray-400 leading-tight">Free plan</p>
          </div>
        </div>
      </div>
    </aside>
  );
}
