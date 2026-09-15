'use client';

import { useEffect, useRef, useState } from 'react';
import {
  Plug,
  Cpu,
  Database,
  MessageSquarePlus,
  ChevronDown,
  ChevronUp,
  Trash2,
  BarChart3,
  Store,
  LogOut,
  Loader2,
  Workflow,
  Search,
  X,
} from 'lucide-react';
import { AegisMark } from './AegisLogo';
import toast from 'react-hot-toast';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface SearchResult {
  message_id: number;
  conversation_id: string;
  role: string;
  created_at: string;
  snippet: string;
}

// Snippets come straight from stored chat message content (via SQLite's
// snippet()), which can legitimately contain "<", "&", etc. if a user ever
// pasted HTML/code into a chat — escape it before the ** -> <mark> swap so
// dangerouslySetInnerHTML only ever renders tags this function itself adds.
function highlightSnippet(snippet: string): string {
  const escaped = snippet
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
  return escaped.replace(/\*\*(.+?)\*\*/g, '<mark>$1</mark>');
}

export type TabType = 'chat' | 'mcp_servers' | 'workflows' | 'llms' | 'discover' | 'history' | 'model_hub' | 'context' | 'analytics' | 'marketplace';

export interface AccountStatus {
  logged_in: boolean;
  email?: string;
  plan?: string;
}

interface SidebarProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  onNewChat: () => void;
  recentChats?: { id: string; preview: string }[];
  onSelectSession?: (id: string) => void;
  onDeleteSession?: (id: string, e: React.MouseEvent) => void;
  activeSessionId?: string;
  account?: AccountStatus | null;
  onLogout?: () => void;
}

const NAV_ITEMS = [
  { id: 'workflows'    as TabType, label: 'Workflows', icon: Workflow },
  { id: 'mcp_servers'  as TabType, label: 'Connectors', icon: Plug },
  { id: 'marketplace' as TabType, label: 'Marketplace', icon: Store },
  { id: 'llms'        as TabType, label: 'LLMs',        icon: Cpu },
  { id: 'context'     as TabType, label: 'Memory Hub', icon: Database },
  { id: 'analytics'   as TabType, label: 'Analytics',   icon: BarChart3 },
];

export default function Sidebar({
  activeTab,
  setActiveTab,
  onNewChat,
  recentChats = [],
  onSelectSession,
  onDeleteSession,
  activeSessionId,
  account = null,
  onLogout,
}: SidebarProps) {
  const [recentsOpen, setRecentsOpen] = useState(true);

  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);

  // Debounced full-text search over chat history (app.db.crud.search_messages)
  // — replaces the Recents list with matching messages while a query is active.
  useEffect(() => {
    const q = searchQuery.trim();
    if (!q) { setSearchResults([]); setSearching(false); return; }
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/chat/search?q=${encodeURIComponent(q)}`);
        if (res.ok) setSearchResults((await res.json()).results || []);
      } catch (e) {
        // backend not reachable — leave previous results as-is
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [searchQuery]);

  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  // "Erase all my data" is a plain in-card "click again to confirm" toggle,
  // not a native confirm() — that blocks the whole renderer until dismissed
  // (confirmed the hard way building the workflow-delete feature), and this
  // action is far more consequential than that one (wipes every chat,
  // downloaded model, and workflow, not just one graph).
  const [eraseArmed, setEraseArmed] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!accountMenuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(e.target as Node)) {
        setAccountMenuOpen(false);
        setEraseArmed(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [accountMenuOpen]);

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await onLogout?.();
    } finally {
      setLoggingOut(false);
      setAccountMenuOpen(false);
    }
  };

  // Only meaningful inside the packaged Electron app (window.aegis comes
  // from electron/preload.ts's contextBridge) — running the Next.js dev
  // server in a plain browser tab has no main process to ask, so the
  // button below only renders when this is actually available.
  const aegisBridge = typeof window !== 'undefined' ? window.aegis : undefined;

  const handleEraseAllData = () => {
    if (!eraseArmed) {
      setEraseArmed(true);
      return;
    }
    if (!aegisBridge) {
      toast.error('This only works in the installed desktop app.');
      return;
    }
    aegisBridge.send('app:erase-all-data');
    // The main process kills the sidecar, wipes userData, and relaunches
    // the app itself — nothing left to do here, the window is about to close.
  };

  return (
    <aside className="w-48 flex-shrink-0 flex flex-col h-full bg-aegis-sidebar border-r border-aegis-sidebar-border">

      {/* ── Brand ──────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-4 pt-4 pb-1 flex-shrink-0">
        <AegisMark size={22} glow={false} />
        <span className="text-sm font-bold tracking-tight text-white">AEGIS</span>
      </div>

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
                  ? 'bg-aegis-primary text-white'
                  : 'text-aegis-sidebar-text hover:bg-aegis-sidebar-raised hover:text-white'
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
              ? 'bg-aegis-primary text-white'
              : 'text-aegis-sidebar-text hover:bg-aegis-sidebar-raised hover:text-white'
          }`}
        >
          <MessageSquarePlus className="w-4 h-4 flex-shrink-0" />
          New Chat
        </button>

        {/* Search chat history */}
        <div className="pt-2 relative">
          <Search className="w-3.5 h-3.5 text-aegis-sidebar-text-muted absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search chats…"
            className="w-full bg-aegis-sidebar-raised border border-transparent focus:border-aegis-primary/40 rounded-lg pl-7 pr-7 py-1.5 text-[12px] text-aegis-sidebar-text placeholder:text-aegis-sidebar-text-muted focus:outline-none transition-colors"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-aegis-sidebar-text-muted hover:text-aegis-sidebar-text"
            >
              {searching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <X className="w-3.5 h-3.5" />}
            </button>
          )}
        </div>

        {searchQuery.trim() ? (
          <div className="mt-1 space-y-0.5">
            {searchResults.length === 0 ? (
              <p className="px-3 py-2 text-[12px] text-aegis-sidebar-text-muted">{searching ? 'Searching…' : 'No matches.'}</p>
            ) : (
              searchResults.map(r => (
                <button
                  key={r.message_id}
                  onClick={() => { onSelectSession?.(r.conversation_id); setActiveTab('chat'); setSearchQuery(''); }}
                  className="w-full text-left px-3 py-2 rounded-lg hover:bg-aegis-sidebar-raised transition-colors"
                >
                  <div
                    className="text-[12px] text-aegis-sidebar-text-muted truncate [&_mark]:bg-transparent [&_mark]:text-aegis-primary-light [&_mark]:font-semibold"
                    dangerouslySetInnerHTML={{ __html: highlightSnippet(r.snippet) }}
                  />
                </button>
              ))
            )}
          </div>
        ) : (
        /* ── Recents Section ─────────────────────────────────────────── */
        <div className="pt-2">
          <button
            onClick={() => setRecentsOpen(v => !v)}
            className="w-full flex items-center justify-between px-3 py-2 text-[12px] font-semibold text-aegis-sidebar-text-muted hover:text-aegis-sidebar-text transition-colors"
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
                          ? 'bg-aegis-sidebar-raised'
                          : 'hover:bg-aegis-sidebar-raised'
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
                            ? 'text-aegis-primary-light font-semibold'
                            : 'text-aegis-sidebar-text-muted hover:text-aegis-sidebar-text'
                        }`}
                        title={chat.preview}
                      >
                        {chat.preview}
                      </button>

                      {/* Delete button — visible on hover */}
                      {onDeleteSession && (
                        <button
                          onClick={(e) => onDeleteSession(chat.id, e)}
                          className="opacity-0 group-hover:opacity-100 flex-shrink-0 p-1.5 mr-1 rounded-md text-aegis-sidebar-text-muted hover:text-aegis-error hover:bg-aegis-sidebar-raised transition-all"
                          title="Delete this chat"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  );
                })
              ) : (
                <p className="px-3 py-2 text-[12px] text-aegis-sidebar-text-muted">No recent chats</p>
              )}
            </div>
          )}
        </div>
        )}
      </nav>

      {/* ── User Footer ─────────────────────────────────────────────────── */}
      <div className="relative p-3 border-t border-aegis-sidebar-border" ref={accountMenuRef}>
        {accountMenuOpen && (
          <div className="absolute bottom-full left-3 right-3 mb-1.5 rounded-xl bg-aegis-sidebar-raised border border-aegis-sidebar-border shadow-lg overflow-hidden py-1">
            <button
              onClick={handleLogout}
              disabled={loggingOut}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-[12px] font-medium text-aegis-sidebar-text hover:bg-aegis-sidebar-border hover:text-white transition-colors disabled:opacity-50"
            >
              {loggingOut ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <LogOut className="w-3.5 h-3.5" />}
              Log out
            </button>
            <div className="h-px bg-aegis-sidebar-border mx-1" />
            <button
              onClick={handleEraseAllData}
              title="Deletes every chat, workflow, downloaded model, and connector — everything under Aegis's data folder."
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-[12px] font-medium transition-colors ${
                eraseArmed ? 'text-white bg-red-600 hover:bg-red-700' : 'text-red-400 hover:bg-aegis-sidebar-border hover:text-red-300'
              }`}
            >
              <Trash2 className="w-3.5 h-3.5" />
              {eraseArmed ? 'Click again to erase everything' : 'Erase all my data'}
            </button>
          </div>
        )}
        <button
          onClick={() => setAccountMenuOpen(v => !v)}
          className={`w-full flex items-center gap-2.5 px-2 py-2 rounded-lg hover:bg-aegis-sidebar-raised cursor-pointer transition-colors ${accountMenuOpen ? 'bg-aegis-sidebar-raised' : ''}`}
        >
          <div className="w-7 h-7 rounded-full bg-aegis-primary flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
            {account?.logged_in && account.email ? account.email[0].toUpperCase() : '?'}
          </div>
          <div className="min-w-0 text-left">
            <p className="text-[12px] font-semibold text-white leading-tight truncate">
              {account?.logged_in ? account.email : 'Not signed in'}
            </p>
            <p className="text-[11px] text-aegis-sidebar-text-muted leading-tight capitalize">
              {account?.logged_in ? `${account.plan || 'free'} plan` : 'Sign in for cloud features'}
            </p>
          </div>
        </button>
      </div>
    </aside>
  );
}
