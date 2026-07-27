'use client';

import { useState, useCallback } from 'react';
import Sidebar, { TabType } from '@/components/Sidebar';
import Header from '@/components/Header';
import ChatView from '@/components/ChatView';
import ConnectorsView from '@/components/ConnectorsView';
import SettingsView from '@/components/SettingsView';
import MemoryViewer from '@/components/MemoryViewer';
import { useSocket } from '@/hooks/useSocket';

export default function Home() {
  const [activeTab, setActiveTab] = useState<TabType>('connectors'); // Default to Connectors view matching Stitch Image 1
  const [activeConnectorName, setActiveConnectorName] = useState('GitHub');
  const [isMemoryOpen, setIsMemoryOpen] = useState(false);
  const { 
    messages, 
    status, 
    isStreaming, 
    activeNodeId,
    completedNodeIds,
    failedNodeIds,
    sendMessage, 
    clearMessages 
  } = useSocket();

  const handleNewChat = useCallback(() => {
    clearMessages();
    setActiveTab('chat');
  }, [clearMessages]);

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

        {activeTab === 'history' && (
          <div className="flex-1 p-8 overflow-y-auto">
            <h2 className="text-2xl font-bold text-gray-900 mb-4">Chat History</h2>
            <div className="bg-white rounded-2xl border border-gray-200 p-6 shadow-sm">
              <p className="text-xs text-gray-500">
                Your past conversation threads are stored locally in Aegis SQLite database.
              </p>
              <button
                onClick={() => setActiveTab('chat')}
                className="mt-4 px-4 py-2 bg-black text-white text-xs font-semibold rounded-xl hover:bg-neutral-800 transition-all"
              >
                Start New Chat
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
