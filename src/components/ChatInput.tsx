'use client';

import { useState, useRef, useCallback, KeyboardEvent } from 'react';
import { type ConnectionStatus } from '@/hooks/useSocket';

interface ChatInputProps {
  onSend: (message: string) => boolean;
  isStreaming: boolean;
  status: ConnectionStatus;
}

const STATUS_CONFIG: Record<ConnectionStatus, { label: string; color: string; dot: string }> = {
  connected:    { label: 'Connected',    color: 'text-aegis-success',        dot: 'bg-aegis-success' },
  connecting:   { label: 'Connecting…',  color: 'text-aegis-warning',        dot: 'bg-aegis-warning animate-pulse' },
  reconnecting: { label: 'Reconnecting…',color: 'text-aegis-warning',        dot: 'bg-aegis-warning animate-pulse' },
  disconnected: { label: 'Disconnected', color: 'text-aegis-text-muted',     dot: 'bg-aegis-text-muted' },
  error:        { label: 'Error',        color: 'text-aegis-error',          dot: 'bg-aegis-error animate-pulse' },
};

export default function ChatInput({ onSend, isStreaming, status }: ChatInputProps) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const statusCfg = STATUS_CONFIG[status];
  const canSend = value.trim().length > 0 && !isStreaming && status === 'connected';

  // Auto-resize textarea
  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  const handleSend = useCallback(() => {
    if (!canSend) return;
    const sent = onSend(value);
    if (sent) {
      setValue('');
      // Reset textarea height
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }
  }, [canSend, onSend, value]);

  // Send on Enter (Shift+Enter for newline)
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  return (
    <div className="px-4 pb-4 pt-2">
      {/* Input container */}
      <div
        className={`relative glass rounded-2xl transition-all duration-200 ${
          canSend
            ? 'border-aegis-primary/40 shadow-lg shadow-aegis-primary/10'
            : 'border-aegis-border'
        }`}
        style={{ border: '1px solid' }}
      >
        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            handleInput();
          }}
          onKeyDown={handleKeyDown}
          placeholder={
            status === 'connected'
              ? 'Message Aegis… (Enter to send, Shift+Enter for newline)'
              : `${statusCfg.label} — waiting for backend…`
          }
          disabled={status !== 'connected'}
          rows={1}
          className="w-full bg-transparent text-aegis-text-primary placeholder:text-aegis-text-muted text-sm resize-none rounded-2xl px-4 pt-4 pb-12 focus:outline-none disabled:opacity-40 leading-relaxed"
          style={{ minHeight: '56px', maxHeight: '200px' }}
        />

        {/* Bottom toolbar */}
        <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between px-4 py-3">
          {/* Status indicator */}
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusCfg.dot}`} />
            <span className={`text-xs ${statusCfg.color}`}>{statusCfg.label}</span>
          </div>

          <div className="flex items-center gap-2">
            {/* Character hint */}
            {value.length > 0 && (
              <span className="text-xs text-aegis-text-muted">
                Shift+↵ newline
              </span>
            )}

            {/* Send button */}
            <button
              onClick={handleSend}
              disabled={!canSend}
              aria-label="Send message"
              className={`w-8 h-8 rounded-xl flex items-center justify-center transition-all duration-150 ${
                canSend
                  ? 'bg-gradient-to-br from-aegis-primary to-aegis-accent text-white hover:opacity-90 hover:scale-105 active:scale-95 shadow-md shadow-aegis-primary/30'
                  : 'bg-aegis-raised text-aegis-text-muted cursor-not-allowed'
              }`}
            >
              {isStreaming ? (
                /* Stop icon during streaming */
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                  <rect x="3" y="3" width="10" height="10" rx="2" />
                </svg>
              ) : (
                /* Send arrow */
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="2" y1="14" x2="14" y2="2" />
                  <polyline points="5,2 14,2 14,11" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Footer disclaimer */}
      <p className="text-center text-[10px] text-aegis-text-muted mt-2">
        Aegis runs entirely on your device. No data leaves your machine.
      </p>
    </div>
  );
}
