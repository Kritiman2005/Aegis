'use client';

import { useCallback } from 'react';
import { useSocket } from '@/hooks/useSocket';
import Sidebar from '@/components/Sidebar';
import ChatWindow from '@/components/ChatWindow';
import ChatInput from '@/components/ChatInput';

export default function Home() {
  const { messages, status, isStreaming, sendMessage, clearMessages } = useSocket();

  const handleNewChat = useCallback(() => {
    clearMessages();
  }, [clearMessages]);

  return (
    <main className="flex h-full w-full overflow-hidden bg-[#080B14]">
      {/* Background gradient orbs */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-40 -left-40 w-80 h-80 rounded-full bg-aegis-primary/5 blur-3xl" />
        <div className="absolute top-1/3 -right-20 w-60 h-60 rounded-full bg-aegis-accent/5 blur-3xl" />
        <div className="absolute -bottom-20 left-1/3 w-72 h-72 rounded-full bg-aegis-primary/4 blur-3xl" />
      </div>

      {/* Sidebar */}
      <Sidebar onNewChat={handleNewChat} />

      {/* Main Chat Area */}
      <section className="flex flex-col flex-1 min-w-0 h-full relative">
        {/* Top bar */}
        <header className="flex items-center justify-between px-6 py-3 border-b border-aegis-border glass flex-shrink-0">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-semibold text-aegis-text-primary">
              Chat
            </h2>
            {messages.length > 0 && (
              <span className="text-xs text-aegis-text-muted bg-aegis-raised px-2 py-0.5 rounded-full border border-aegis-border">
                {messages.filter((m) => m.role === 'user').length} messages
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            {/* Connection status badge */}
            <div className="flex items-center gap-1.5">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  status === 'connected'
                    ? 'bg-aegis-success'
                    : status === 'error'
                    ? 'bg-aegis-error animate-pulse'
                    : 'bg-aegis-warning animate-pulse'
                }`}
              />
              <span className="text-xs text-aegis-text-muted capitalize">{status}</span>
            </div>

            {/* Clear button */}
            {messages.length > 0 && (
              <button
                onClick={handleNewChat}
                className="text-xs text-aegis-text-muted hover:text-aegis-error transition-colors px-2 py-1 rounded-lg hover:bg-aegis-error/10"
              >
                Clear
              </button>
            )}
          </div>
        </header>

        {/* Messages */}
        <ChatWindow messages={messages} isStreaming={isStreaming} />

        {/* Input */}
        <ChatInput
          onSend={sendMessage}
          isStreaming={isStreaming}
          status={status}
        />
      </section>
    </main>
  );
}
