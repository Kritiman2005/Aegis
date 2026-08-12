'use client';

import { useState, useCallback, useEffect } from 'react';
import Sidebar, { TabType } from '@/components/Sidebar';
import ChatView from '@/components/ChatView';
import ConnectorsView from '@/components/ConnectorsView';
import ModelHub from '@/components/ModelHub';
import { useSocket } from '@/hooks/useSocket';

export default function Home() {
  const [activeTab, setActiveTab] = useState<TabType>('connectors');
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
    fetch('http://localhost:8000/api/chat/sessions')
      .then(res => res.json())
      .then(data => setSessions(data))
      .catch(() => {});
  }, [messages.length]); // refresh when new messages come in

  const handleNewChat = useCallback(() => {
    switchSession();
    setActiveTab('chat');
  }, [switchSession]);

  const recentChats = sessions.slice(0, 6).map(s => ({
    id: s.id,
    preview: s.preview || 'Chat session',
  }));

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#F4F5F7] text-gray-900 font-sans antialiased">
      {/* Sidebar */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onNewChat={handleNewChat}
        recentChats={recentChats}
      />

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 h-full overflow-hidden">
        {activeTab === 'chat' && (
          <ChatView
            messages={messages}
            status={status}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            onSendMessage={sendMessage}
            onClearMessages={clearMessages}
            activeNodeId={activeNodeId}
            completedNodeIds={completedNodeIds}
            failedNodeIds={failedNodeIds}
          />
        )}
        {(activeTab === 'connectors' || activeTab === 'sync_detail') && (
          <ConnectorsView />
        )}
        {(activeTab === 'llms' || activeTab === 'model_hub') && (
          <ModelHub />
        )}
        {activeTab === 'discover' && (
          <div className="flex-1 flex items-center justify-center bg-[#F4F5F7]">
            <div className="text-center">
              <p className="text-2xl font-bold text-gray-900 mb-2">Discover</p>
              <p className="text-sm text-gray-400">Coming soon — explore community workflows and agent templates.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

