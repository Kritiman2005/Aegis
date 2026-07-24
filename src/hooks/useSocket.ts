/**
 * Aegis — useSocket
 *
 * A robust WebSocket hook that manages:
 *  - Persistent connection to ws://127.0.0.1:8000/ws
 *  - Automatic reconnection with exponential backoff
 *  - Streaming token accumulation into assistant messages
 *  - Connection status reporting
 *  - Heartbeat ping to detect stale connections
 */

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

// ─── Types ───────────────────────────────────────────────────────────────────

export type MessageRole = 'user' | 'assistant' | 'system';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
}

export type ConnectionStatus =
  | 'connecting'
  | 'connected'
  | 'disconnected'
  | 'reconnecting'
  | 'error';

interface ServerPayload {
  type: 'connected' | 'token' | 'done' | 'error' | 'pong';
  content?: string;
  connection_id?: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const WS_URL = 'ws://127.0.0.1:8000/ws';
const MAX_RECONNECT_DELAY_MS = 30_000;
const BASE_RECONNECT_DELAY_MS = 1_000;
const PING_INTERVAL_MS = 25_000;
const PING_TIMEOUT_MS = 5_000;

// ─── ID Generator ────────────────────────────────────────────────────────────

let _msgCounter = 0;
function generateId(): string {
  return `msg-${Date.now()}-${++_msgCounter}`;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useSocket() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [isStreaming, setIsStreaming] = useState(false);

  const socketRef = useRef<WebSocket | null>(null);
  const connectionIdRef = useRef<string>(generateId()); // Stable client ID across reconnects
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const pongTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const streamingIdRef = useRef<string | null>(null);
  const isUnmountedRef = useRef(false);

  // ── Helpers ─────────────────────────────────────────────────────────────────

  const appendMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateLastAssistantMessage = useCallback((updater: (prev: string) => string) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role !== 'assistant') return prev;
      return [
        ...prev.slice(0, -1),
        { ...last, content: updater(last.content) },
      ];
    });
  }, []);

  const finalizeStreamingMessage = useCallback(() => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role !== 'assistant') return prev;
      return [...prev.slice(0, -1), { ...last, isStreaming: false }];
    });
    streamingIdRef.current = null;
    setIsStreaming(false);
  }, []);

  // ── Ping / Keepalive ─────────────────────────────────────────────────────────

  const startPingLoop = useCallback(() => {
    pingTimerRef.current = setInterval(() => {
      if (socketRef.current?.readyState !== WebSocket.OPEN) return;

      socketRef.current.send(JSON.stringify({ type: 'ping' }));

      // If pong not received within timeout, consider connection dead
      pongTimeoutRef.current = setTimeout(() => {
        console.warn('[useSocket] Ping timed out — closing stale connection');
        socketRef.current?.close(4000, 'Ping timeout');
      }, PING_TIMEOUT_MS);
    }, PING_INTERVAL_MS);
  }, []);

  const stopPingLoop = useCallback(() => {
    clearInterval(pingTimerRef.current);
    clearTimeout(pongTimeoutRef.current);
  }, []);

  // ── Connect ──────────────────────────────────────────────────────────────────

  const connect = useCallback(() => {
    if (isUnmountedRef.current) return;

    const wsUrl = `${WS_URL}?client_id=${connectionIdRef.current}`;
    console.log(`[useSocket] Connecting to ${wsUrl}`);

    const ws = new WebSocket(wsUrl);
    socketRef.current = ws;

    ws.onopen = () => {
      if (isUnmountedRef.current) return;
      console.log('[useSocket] Connected');
      setStatus('connected');
      reconnectAttemptsRef.current = 0;
      startPingLoop();
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      if (isUnmountedRef.current) return;

      let payload: ServerPayload;
      try {
        payload = JSON.parse(event.data) as ServerPayload;
      } catch {
        console.warn('[useSocket] Non-JSON message received:', event.data);
        return;
      }

      switch (payload.type) {
        case 'connected':
          // Server confirmed connection and echoed back its connection_id
          break;

        case 'token':
          // Accumulate streaming token into the current assistant message
          if (!streamingIdRef.current) {
            // First token — create the assistant placeholder message
            const msgId = generateId();
            streamingIdRef.current = msgId;
            setIsStreaming(true);
            appendMessage({
              id: msgId,
              role: 'assistant',
              content: payload.content ?? '',
              timestamp: new Date(),
              isStreaming: true,
            });
          } else {
            updateLastAssistantMessage((prev) => prev + (payload.content ?? ''));
          }
          break;

        case 'done':
          finalizeStreamingMessage();
          break;

        case 'error':
          finalizeStreamingMessage();
          appendMessage({
            id: generateId(),
            role: 'system',
            content: `⚠ Backend error: ${payload.content ?? 'Unknown error'}`,
            timestamp: new Date(),
          });
          break;

        case 'pong':
          clearTimeout(pongTimeoutRef.current);
          break;
      }
    };

    ws.onclose = (event) => {
      if (isUnmountedRef.current) return;
      stopPingLoop();
      console.log(`[useSocket] Closed — code: ${event.code}, reason: ${event.reason}`);

      if (event.code === 1000) {
        // Clean close — no reconnect
        setStatus('disconnected');
        return;
      }

      // Exponential backoff reconnect
      const delay = Math.min(
        BASE_RECONNECT_DELAY_MS * 2 ** reconnectAttemptsRef.current,
        MAX_RECONNECT_DELAY_MS
      );
      reconnectAttemptsRef.current++;
      setStatus('reconnecting');

      console.log(`[useSocket] Reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current})`);
      reconnectTimerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = (err) => {
      console.error('[useSocket] WebSocket error:', err);
      setStatus('error');
      // onclose will fire after onerror, triggering reconnect
    };
  }, [appendMessage, updateLastAssistantMessage, finalizeStreamingMessage, startPingLoop, stopPingLoop]);

  // ── Lifecycle ────────────────────────────────────────────────────────────────

  useEffect(() => {
    isUnmountedRef.current = false;
    connect();

    return () => {
      isUnmountedRef.current = true;
      clearTimeout(reconnectTimerRef.current);
      stopPingLoop();
      // Close cleanly with code 1000 to prevent reconnect loop
      socketRef.current?.close(1000, 'Component unmounted');
    };
  }, [connect, stopPingLoop]);

  // ── Public API ───────────────────────────────────────────────────────────────

  const sendMessage = useCallback(
    (content: string, msgType: string = 'message', mode: string = 'chat', userPrompt?: string): boolean => {
      const trimmed = content.trim();
      if (!trimmed) return false;
      if (socketRef.current?.readyState !== WebSocket.OPEN) {
        console.warn('[useSocket] Cannot send — socket not open');
        return false;
      }
      if (isStreaming) {
        console.warn('[useSocket] Cannot send — currently streaming');
        return false;
      }

      if (msgType === 'message') {
        // Optimistically add user message only for standard messages
        appendMessage({
          id: generateId(),
          role: 'user',
          content: trimmed,
          timestamp: new Date(),
        });
      }

      socketRef.current.send(
        JSON.stringify({ type: msgType, content: trimmed, mode, user_prompt: userPrompt })
      );
      return true;
    },
    [isStreaming, appendMessage]
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  return {
    messages,
    status,
    isStreaming,
    sendMessage,
    clearMessages,
  };
}
