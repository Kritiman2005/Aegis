'use client';

import { useState, useCallback, useEffect } from 'react';
import Sidebar, { TabType } from '@/components/Sidebar';
import ChatView from '@/components/ChatView';
import ConnectorsView from '@/components/ConnectorsView';
import ModelHub from '@/components/ModelHub';
import { useSocket } from '@/hooks/useSocket';
import { useAppSelector } from '@/hooks/useStore';
import { selectSessionId } from '@/store/sessionSlice';

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
    switchSession,
  } = useSocket();

  const currentSessionId = useAppSelector(selectSessionId);

  const [sessions, setSessions] = useState<any[]>([]);

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('http://localhost:8000/api/chat/sessions');
      const data = await res.json();
      setSessions(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  // Refresh sessions list on mount and whenever a message is sent
  useEffect(() => { fetchSessions(); }, [fetchSessions]);
  useEffect(() => {
    if (messages.length > 0) fetchSessions();
  }, [messages.length, fetchSessions]);

  const handleNewChat = useCallback(() => {
    switchSession();
    setActiveTab('chat');
  }, [switchSession]);

  const handleSelectSession = useCallback((sessionId: string) => {
    switchSession(sessionId);
    setActiveTab('chat');
  }, [switchSession]);

  const handleDeleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this chat? This cannot be undone.')) return;
    try {
      const res = await fetch(
        `http://localhost:8000/api/chat/sessions/${encodeURIComponent(sessionId)}`,
        { method: 'DELETE' }
      );
      if (res.ok) {
        setSessions(prev => prev.filter(s => s.id !== sessionId));
        // If we deleted the currently open session, start a new chat
        if (currentSessionId === sessionId) {
          switchSession();
          setActiveTab('chat');
        }
      }
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  }, [currentSessionId, switchSession]);

  const recentChats = sessions.map(s => ({
    id: s.id,
    preview: s.preview || 'Untitled chat',
  }));

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#F4F5F7] text-gray-900 font-sans antialiased">
      {/* Sidebar */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onNewChat={handleNewChat}
        recentChats={recentChats}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        activeSessionId={currentSessionId ?? undefined}
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
