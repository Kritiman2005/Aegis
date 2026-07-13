'use client';

import { useRef, useEffect } from 'react';
import { type ChatMessage } from '@/hooks/useSocket';
import MessageBubble from './MessageBubble';

interface ChatWindowProps {
  messages: ChatMessage[];
  isStreaming: boolean;
}

/** Shown when there are no messages yet */
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 text-center px-8">
      {/* Animated glyph */}
      <div className="relative">
        <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-aegis-primary/20 to-aegis-accent/20 border border-aegis-primary/30 flex items-center justify-center text-4xl glow-primary">
          ⚡
        </div>
        {/* Pulse rings */}
        <span className="absolute inset-0 rounded-2xl border border-aegis-primary/40 animate-ping opacity-30" />
      </div>

      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-gradient">
          Aegis is ready
        </h2>
        <p className="text-sm text-aegis-text-secondary max-w-xs leading-relaxed">
          Your local AI agent is running on-device. All conversations are private and never leave your machine.
        </p>
      </div>

      {/* Suggestion chips */}
      <div className="flex flex-wrap gap-2 justify-center">
        {[
          'Summarize a document',
          'Write some code',
          'Analyze data',
          'Research a topic',
        ].map((suggestion) => (
          <span
            key={suggestion}
            className="text-xs px-3 py-1.5 rounded-full border border-aegis-border text-aegis-text-secondary hover:border-aegis-primary hover:text-aegis-primary-light transition-colors cursor-default"
          >
            {suggestion}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function ChatWindow({ messages, isStreaming }: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new messages arrive or streaming updates
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto py-6 space-y-4 scroll-smooth"
    >
      {messages.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}

          {/* Streaming indicator shown between last message and bottom */}
          {isStreaming && messages[messages.length - 1]?.role !== 'assistant' && (
            <div className="flex items-end gap-3 px-6 animate-fade-in-up">
              <div className="w-8 h-8 rounded-xl flex items-center justify-center text-sm bg-aegis-raised border border-aegis-border text-aegis-primary-light">
                ⚡
              </div>
              <div className="glass-raised rounded-2xl rounded-bl-sm px-4 py-3">
                <span className="inline-flex items-center gap-1">
                  {[0, 1, 2].map((i) => (
                    <span
                      key={i}
                      className="typing-dot inline-block w-2 h-2 rounded-full bg-aegis-primary"
                      style={{ animationDelay: `${i * 0.15}s` }}
                    />
                  ))}
                </span>
              </div>
            </div>
          )}

          <div ref={bottomRef} className="h-1" />
        </>
      )}
    </div>
  );
}
