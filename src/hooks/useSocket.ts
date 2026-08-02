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
import { useAppDispatch, useAppSelector } from '@/hooks/useStore';
import { setSessionId, generateNewSession, selectSessionId } from '@/store/sessionSlice';

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
  type: 'connected' | 'token' | 'done' | 'error' | 'pong' | 'history';
  content?: string;
  connection_id?: string;
  history?: Array<{ role: string; content: string }>;
  node_id?: string;
  status?: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const WS_URL = 'ws://127.0.0.1:8000/ws';
const MAX_RECONNECT_DELAY_MS = 30_000;
const BASE_RECONNECT_DELAY_MS = 1_000;
const PING_INTERVAL_MS = 60_000;
const PING_TIMEOUT_MS = 120_000;

// ─── ID Generator ────────────────────────────────────────────────────────────

let _msgCounter = 0;
function generateId(): string {
  return `msg-${Date.now()}-${++_msgCounter}`;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useSocket() {
  const dispatch = useAppDispatch();
  const sessionId = useAppSelector(selectSessionId);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');
  const bufferRef = useRef("");
  const streamingContentRef = useRef("");
  const rafPending = useRef(false);

  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [completedNodeIds, setCompletedNodeIds] = useState<Set<string>>(new Set());
  const [failedNodeIds, setFailedNodeIds] = useState<Set<string>>(new Set());

  const socketRef = useRef<WebSocket | null>(null);
  // Mirror the Redux session ID into a ref for stable access inside WebSocket callbacks
  const connectionIdRef = useRef<string>(sessionId);
  const historyLoadedRef = useRef<boolean>(false); // Only load history once per session
  const isSessionSwitchRef = useRef<boolean>(false); // Prevent reconnect loop during session switch

  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const pongTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const streamingIdRef = useRef<string | null>(null);
  const isUnmountedRef = useRef(false);
  
  // Custom external handlers (e.g. for components listening to raw WebSocket events)
  const externalHandlersRef = useRef<Array<(payload: any) => void>>([]);

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
    const finalContent = streamingContentRef.current + bufferRef.current;
    
    bufferRef.current = '';
    streamingContentRef.current = '';
    setStreamingContent('');
    
    if (finalContent.trim()) {
      setMessages((prev) => [
        ...prev,
        {
          id: streamingIdRef.current || generateId(),
          role: 'assistant',
          content: finalContent,
          timestamp: new Date(),
          isStreaming: false
        }
      ]);
    }
    
    streamingIdRef.current = null;
    setIsStreaming(false);
    setActiveNodeId(null);
  }, []);

  // ── Stable callback refs (prevents connect from being recreated on every render) ──
  const appendMessageRef = useRef(appendMessage);
  const updateLastRef = useRef(updateLastAssistantMessage);
  const finalizeRef = useRef(finalizeStreamingMessage);
  useEffect(() => { appendMessageRef.current = appendMessage; }, [appendMessage]);
  useEffect(() => { updateLastRef.current = updateLastAssistantMessage; }, [updateLastAssistantMessage]);
  useEffect(() => { finalizeRef.current = finalizeStreamingMessage; }, [finalizeStreamingMessage]);

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

  // Stable refs for ping so connect doesn't depend on them
  const startPingLoopRef = useRef(startPingLoop);
  const stopPingLoopRef = useRef(stopPingLoop);
  useEffect(() => { startPingLoopRef.current = startPingLoop; }, [startPingLoop]);
  useEffect(() => { stopPingLoopRef.current = stopPingLoop; }, [stopPingLoop]);

  // ── Connect ──────────────────────────────────────────────────────────────────

  // connect is intentionally stable (no deps) so that the useEffect below never
  // re-fires mid-inference and closes the socket. All internal callbacks are
  // accessed via refs which are kept in sync via their own useEffects above.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const connect = useCallback(() => {
    if (isUnmountedRef.current) return;
    if (!connectionIdRef.current) {
      console.log('[useSocket] Waiting for sessionId hydration...');
      return;
    }

    const wsUrl = `${WS_URL}?client_id=${connectionIdRef.current}`;
    console.log(`[useSocket] Connecting to ${wsUrl}`);

    const ws = new WebSocket(wsUrl);
    socketRef.current = ws;

    ws.onopen = () => {
      if (isUnmountedRef.current || socketRef.current !== ws) return;
      console.log('[useSocket] Connected');
      setStatus('connected');
      reconnectAttemptsRef.current = 0;
      startPingLoopRef.current();
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      if (isUnmountedRef.current || socketRef.current !== ws) return;

      let payload: ServerPayload;
      try {
        payload = JSON.parse(event.data) as ServerPayload;
      } catch {
        console.warn('[useSocket] Non-JSON message received:', event.data);
        return;
      }
      
      // Dispatch to external listeners
      externalHandlersRef.current.forEach(handler => handler(payload));

      switch (payload.type) {
        case 'connected':
          break;

        case 'history':
          if (!historyLoadedRef.current && payload.history && payload.history.length > 0) {
            historyLoadedRef.current = true;
            setMessages(payload.history.map((m: any) => ({
              id: generateId(),
              role: m.role as MessageRole,
              content: m.content,
              timestamp: new Date(),
            })));
          }
          break;

        case 'token':
          if (payload.node_id) {
            if (payload.status === 'running') {
              setActiveNodeId(payload.node_id);
            } else if (payload.status === 'completed') {
              setCompletedNodeIds(prev => new Set(prev).add(payload.node_id as string));
              setActiveNodeId(null);
            } else if (payload.status === 'failed') {
              setFailedNodeIds(prev => new Set(prev).add(payload.node_id as string));
              setActiveNodeId(null);
            }
          }

          if (!streamingIdRef.current) {
            streamingIdRef.current = generateId();
            setIsStreaming(true);
            
            if (!payload.node_id) {
              setCompletedNodeIds(new Set());
              setFailedNodeIds(new Set());
            }
          }

          if (payload.content) {
            bufferRef.current += payload.content;
            if (!rafPending.current) {
              rafPending.current = true;
              requestAnimationFrame(() => {
                streamingContentRef.current += bufferRef.current;
                setStreamingContent(streamingContentRef.current);
                bufferRef.current = "";
                rafPending.current = false;
              });
            }
          }
          break;

        case 'done':
          finalizeRef.current();
          break;

        case 'toast':
          appendMessageRef.current({
            id: generateId(),
            role: 'system',
            content: payload.content ?? '',
            timestamp: new Date(),
          });
          break;

        case 'error':
          finalizeRef.current();
          appendMessageRef.current({
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
      if (isUnmountedRef.current || socketRef.current !== ws) return;
      stopPingLoopRef.current();
      console.log(`[useSocket] Closed — code: ${event.code}, reason: ${event.reason}`);

      if (event.code === 1000) {
        setStatus('disconnected');
        return;
      }

      if (isSessionSwitchRef.current) {
        isSessionSwitchRef.current = false;
        return;
      }

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
      if (socketRef.current !== ws) return;
      console.error('[useSocket] WebSocket error:', err);
      setStatus('error');
    };
  }, []);

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
    // connect is stable (empty deps) so this effect only fires once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the ref in sync whenever the Redux session ID changes
  useEffect(() => {
    connectionIdRef.current = sessionId;
    if (sessionId && (!socketRef.current || socketRef.current.readyState === WebSocket.CLOSED)) {
      connect();
    }
  }, [sessionId, connect]);

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
      } else if (msgType === 'toast') {
        // Add a local system message for visual feedback
        appendMessage({
          id: generateId(),
          role: 'assistant', // Render as assistant so it looks like system message
          content: trimmed,
          timestamp: new Date(),
        });
        // We do not need to send purely local toasts to the backend
        return true;
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

  const switchSession = useCallback((newSessionId?: string) => {
    // Signal that this close is intentional — don't trigger reconnect
    isSessionSwitchRef.current = true;
    // Reset history flag so the new session's history gets loaded
    historyLoadedRef.current = false;

    if (newSessionId) {
      // Load a historical session — dispatch to Redux (store subscriber writes to localStorage)
      dispatch(setSessionId(newSessionId));
      connectionIdRef.current = newSessionId;
    } else {
      // Start a fresh session — dispatch generates a new ID and persists it
      dispatch(generateNewSession());
      // The new ID will be set in connectionIdRef via the useEffect above,
      // but we need it immediately for the reconnect below, so read from store.
      // The store.getState() call handles this synchronously.
      const { store } = require('@/store');
      connectionIdRef.current = store.getState().session.sessionId;
    }

    clearMessages();
    socketRef.current?.close(1000, 'Switching session');
    setTimeout(() => {
      connect();
    }, 150);
  }, [dispatch, clearMessages, connect]);

  const addMessageHandler = useCallback((handler: (payload: any) => void) => {
    externalHandlersRef.current.push(handler);
    return () => {
      externalHandlersRef.current = externalHandlersRef.current.filter(h => h !== handler);
    };
  }, []);

  return {
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
    addMessageHandler,
  };
}
