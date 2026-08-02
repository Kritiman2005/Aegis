'use client';

import { useState, useCallback, useEffect } from 'react';
import Sidebar, { TabType } from '@/components/Sidebar';
import Header from '@/components/Header';
import ChatView from '@/components/ChatView';
import ConnectorsView from '@/components/ConnectorsView';
import SettingsView from '@/components/SettingsView';
import MemoryViewer from '@/components/MemoryViewer';
import ModelHub from '@/components/ModelHub';
import ContextManagement from '@/components/ContextManagement';
import { useSocket } from '@/hooks/useSocket';
import { Trash2 } from 'lucide-react';

export default function Home() {
  const [activeTab, setActiveTab] = useState<TabType>('connectors'); // Default to Connectors view matching Stitch Image 1
  const [activeConnectorName, setActiveConnectorName] = useState('GitHub');
  const [isMemoryOpen, setIsMemoryOpen] = useState(false);
  const { 
    messages, 
    status, 
    isStreaming,
    streamingContent, 
    activeNodeId,
    completedNodeIds,
    failedNodeIds,
    sendMessage, 
    clearMessages,
    switchSession
  } = useSocket();

  const [sessions, setSessions] = useState<any[]>([]);

  useEffect(() => {
    if (activeTab === 'history') {
      fetch('http://localhost:8000/api/chat/sessions')
        .then(res => res.json())
        .then(data => setSessions(data))
        .catch(err => console.error("Failed to fetch sessions", err));
    }
  }, [activeTab]);

  const handleNewChat = useCallback(() => {
    switchSession();
    setActiveTab('chat');
  }, [switchSession]);

  const handleDeleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation(); // Don't trigger card click
    if (!confirm('Delete this chat session? This cannot be undone.')) return;
    try {
      const res = await fetch(`http://localhost:8000/api/chat/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setSessions(prev => prev.filter(s => s.id !== sessionId));
      }
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#F8F9FA] text-gray-900 font-sans antialiased">
      {/* ── Left Sidebar Navigation (Fixed width matching stitch) ───────── */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onNewChat={handleNewChat}
      />

      {/* ── Main Content Area ───────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 h-full overflow-hidden">
        {/* Top Header Bar */}
        <Header
          activeConnectorName={activeConnectorName}
          searchPlaceholder={
            activeTab === 'connectors'
              ? 'Search connectors...'
              : activeTab === 'settings'
              ? 'Search settings...'
              : 'Search Workspace...'
          }
          onOpenMemory={() => setIsMemoryOpen(true)}
        />

        {isMemoryOpen && <MemoryViewer onClose={() => setIsMemoryOpen(false)} />}

        {/* Tab Views */}
        {activeTab === 'chat' && (
          <ChatView
            messages={messages}
            status={status}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            onSendMessage={sendMessage}
            onClearMessages={clearMessages}
            activeConnectorName={activeConnectorName}
            activeNodeId={activeNodeId}
            completedNodeIds={completedNodeIds}
            failedNodeIds={failedNodeIds}
          />
        )}

        {(activeTab === 'connectors' || activeTab === 'sync_detail') && (
          <ConnectorsView
            onSelectConnector={(connectorName) => {
              setActiveConnectorName(connectorName);
            }}
          />
        )}

        {activeTab === 'settings' && <SettingsView />}

        {activeTab === 'model_hub' && <ModelHub />}

        {activeTab === 'context' && <ContextManagement />}

        {activeTab === 'history' && (
          <div className="flex-1 p-8 overflow-y-auto">
            <h2 className="text-2xl font-bold text-gray-900 mb-4">Chat History</h2>
            <div className="bg-white rounded-2xl border border-gray-200 p-6 shadow-sm mb-6">
              <p className="text-xs text-gray-500">
                Your past conversation threads are stored locally in Aegis SQLite database.
              </p>
              <button
                onClick={handleNewChat}
                className="mt-4 px-4 py-2 bg-black text-white text-xs font-semibold rounded-xl hover:bg-neutral-800 transition-all"
              >
                Start New Chat
              </button>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {sessions.map((session) => (
                <div 
                  key={session.id} 
                  onClick={() => {
                    switchSession(session.id);
                    setActiveTab('chat');
                  }}
                  className="bg-white border border-gray-200 rounded-2xl p-5 hover:shadow-md hover:border-gray-300 transition-all cursor-pointer flex flex-col gap-2 group"
                >
                  <p className="text-xs text-gray-400 font-medium">
                    {session.created_at ? new Date(session.created_at).toLocaleString() : 'Unknown Time'}
                  </p>
                  <p className="text-sm text-gray-800 font-medium line-clamp-3">
                    {session.preview}
                  </p>
                  <div className="mt-auto pt-2 flex items-center justify-between">
                    <span className="text-[11px] px-2 py-1 bg-gray-100 text-gray-600 rounded-lg font-medium">
                      {session.message_count} messages
                    </span>
                    <button
                      onClick={(e) => handleDeleteSession(session.id, e)}
                      className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-all"
                      title="Delete session"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
              
              {sessions.length === 0 && (
                <div className="col-span-full py-12 text-center text-gray-500 text-sm">
                  No previous sessions found.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
