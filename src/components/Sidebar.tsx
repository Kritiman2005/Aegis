'use client';

import { useState } from 'react';
import { 
  Link2,
  Cpu,
  Database,
  MessageSquarePlus,
  ChevronDown,
  ChevronUp,
  Trash2,
} from 'lucide-react';

export type TabType = 'chat' | 'connectors' | 'llms' | 'discover' | 'history' | 'sync_detail' | 'model_hub' | 'context';

interface SidebarProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  onNewChat: () => void;
  recentChats?: { id: string; preview: string }[];
  onSelectSession?: (id: string) => void;
  onDeleteSession?: (id: string, e: React.MouseEvent) => void;
  activeSessionId?: string;
}

const NAV_ITEMS = [
  { id: 'connectors' as TabType, label: 'Connectors', icon: Link2 },
  { id: 'llms'       as TabType, label: 'LLMs',        icon: Cpu },
  { id: 'context'    as TabType, label: 'Context & Memory', icon: Database },
];

export default function Sidebar({
  activeTab,
  setActiveTab,
  onNewChat,
  recentChats = [],
  onSelectSession,
  onDeleteSession,
  activeSessionId,
}: SidebarProps) {
  const [recentsOpen, setRecentsOpen] = useState(true);

  return (
    <aside className="w-48 flex-shrink-0 flex flex-col h-full bg-white border-r border-[#E8EAED]">
      
      {/* ── Nav Items ──────────────────────────────────────────────────── */}
      <nav className="flex-1 pt-3 px-2 space-y-0.5 overflow-y-auto">
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
            activeTab === 'chat' && !activeSessionId
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
                recentChats.slice(0, 8).map(chat => {
                  const isActiveChat = activeSessionId === chat.id && activeTab === 'chat';
                  return (
                    <div
                      key={chat.id}
                      className={`group relative flex items-center rounded-lg transition-colors ${
                        isActiveChat
                          ? 'bg-indigo-50'
                          : 'hover:bg-gray-100'
                      }`}
                    >
                      {/* Session title — click to open */}
                      <button
                        onClick={() => {
                          onSelectSession?.(chat.id);
                          setActiveTab('chat');
                        }}
                        className={`flex-1 min-w-0 text-left px-3 py-2 text-[12px] truncate transition-colors ${
                          isActiveChat
                            ? 'text-[#5B50F0] font-semibold'
                            : 'text-gray-500 hover:text-gray-800'
                        }`}
                        title={chat.preview}
                      >
                        {chat.preview}
                      </button>

                      {/* Delete button — visible on hover */}
                      {onDeleteSession && (
                        <button
                          onClick={(e) => onDeleteSession(chat.id, e)}
                          className="opacity-0 group-hover:opacity-100 flex-shrink-0 p-1.5 mr-1 rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 transition-all"
                          title="Delete this chat"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  );
                })
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
