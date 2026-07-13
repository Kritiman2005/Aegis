'use client';

import { type ChatMessage, type ConnectionStatus } from '@/hooks/useSocket';

interface MessageBubbleProps {
  message: ChatMessage;
}

/** Animated typing indicator shown while the assistant is streaming */
function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 ml-1 align-middle">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="typing-dot inline-block w-1.5 h-1.5 rounded-full bg-aegis-primary-light"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </span>
  );
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';

  const formattedTime = message.timestamp.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });

  // ── System Messages ────────────────────────────────────────────────────────
  if (isSystem) {
    return (
      <div className="flex justify-center animate-fade-in-up px-6 py-1">
        <span className="text-xs text-aegis-warning bg-aegis-warning/10 border border-aegis-warning/20 px-3 py-1 rounded-full">
          {message.content}
        </span>
      </div>
    );
  }

  // ── User / Assistant Messages ──────────────────────────────────────────────
  return (
    <div
      className={`flex items-end gap-3 px-6 animate-fade-in-up ${
        isUser ? 'flex-row-reverse' : 'flex-row'
      }`}
    >
      {/* Avatar */}
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-xl flex items-center justify-center text-sm font-semibold ${
          isUser
            ? 'bg-gradient-to-br from-aegis-primary to-aegis-accent text-white'
            : 'bg-aegis-raised border border-aegis-border text-aegis-primary-light'
        }`}
      >
        {isUser ? 'U' : '⚡'}
      </div>

      {/* Bubble */}
      <div className={`flex flex-col gap-1 max-w-[72%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div
          className={`relative rounded-2xl px-4 py-3 text-sm leading-relaxed ${
            isUser
              ? 'bg-gradient-to-br from-aegis-primary to-aegis-primary-dark text-white rounded-br-sm shadow-lg shadow-aegis-primary/20'
              : 'glass-raised text-aegis-text-primary rounded-bl-sm'
          }`}
        >
          {/* Message text */}
          <p className="whitespace-pre-wrap break-words">
            {message.content}
            {message.isStreaming && <TypingIndicator />}
          </p>
        </div>

        {/* Timestamp */}
        <span className="text-[10px] text-aegis-text-muted px-1">{formattedTime}</span>
      </div>
    </div>
  );
}
