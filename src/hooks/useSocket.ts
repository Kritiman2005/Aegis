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
export type MessageType = 'message' | 'thought' | 'tool_call';

export interface Attachment {
  document_id: number;
  filename: string;
  file_type: string;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  msgType?: MessageType;
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  attachments?: Attachment[];
}

export type ConnectionStatus =
  | 'connecting'
  | 'connected'
  | 'disconnected'
  | 'reconnecting'
  | 'error';

interface ServerPayload {
  type: 'connected' | 'token' | 'done' | 'error' | 'pong' | 'history' | 'toast' | 'status' | 'step_result' | 'document_progress';
  content?: string;
  connection_id?: string;
  history?: Array<{ role: string; content: string; attachments?: Attachment[] }>;
  agent_state?: string;
  node_id?: string;
  status?: string;
  tool?: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://127.0.0.1:8000/ws';
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
  // The backend agent's own state for this conversation (IDLE unless a
  // plan/cookie/pagination prompt is paused mid-flight) — reported once on
  // the "history" message so the UI can tell whether the conversation it's
  // opening was actually left in Agent Mode with something still pending.
  const [agentState, setAgentState] = useState<string>('IDLE');
  // A single transient "what's happening right now" line (e.g. "Searching
  // your documents...") — replaced in place as each stage reports in, never
  // appended as its own permanent message. Cleared the moment real content
  // starts streaming or the turn ends. Claude-style: one line, not a stack
  // of separate status bubbles.
  const [statusText, setStatusText] = useState<string | null>(null);
  const statusClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Chat-turn stages clear statusText themselves the moment a real event
  // (token/step_result/done/error) arrives. A document/scrape upload has no
  // such follow-up event once it reaches a terminal state ("✅ ready" / "❌
  // failed"), so that line would otherwise sit there forever — this debounced
  // timer clears it a few seconds after the *last* update, giving the reader
  // enough time to see the terminal message without it becoming a permanent
  // fixture in the UI.
  useEffect(() => {
    if (statusClearTimerRef.current) clearTimeout(statusClearTimerRef.current);
    if (statusText) {
      statusClearTimerRef.current = setTimeout(() => setStatusText(null), 4000);
    }
    return () => {
      if (statusClearTimerRef.current) clearTimeout(statusClearTimerRef.current);
    };
  }, [statusText]);

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

  const finalizeStreamingMessage = useCallback((forceMsgType?: MessageType) => {
    if (!streamingIdRef.current) return;
    const finalContent = (streamingContentRef.current + bufferRef.current).trim();
    
    if (finalContent) {
      appendMessage({
        id: streamingIdRef.current,
        role: 'assistant',
        msgType: forceMsgType || 'thought',
        content: finalContent,
        timestamp: new Date(),
        isStreaming: false,
      });
    }
    
    streamingIdRef.current = null;
    bufferRef.current = "";
    streamingContentRef.current = "";
    setStreamingContent("");
    setIsStreaming(false);
    rafPending.current = false;
  }, [appendMessage]);

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
              attachments: m.attachments,
              msgType: m.msg_type as MessageType | undefined,
            })));
            setAgentState(payload.agent_state || 'IDLE');
          }
          break;

        case 'token':
          // Real content is arriving — the "Analyzing.../Generating..."
          // status line has served its purpose.
          setStatusText(null);

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

        case 'step_result':
          // A single tool has finished executing. Flush any current streaming
          // content and immediately render the result as its own message so the
          // user sees live progress without waiting for the full plan to complete.
          setStatusText(null);
          if (streamingContentRef.current || bufferRef.current) {
            finalizeRef.current('thought');
          }
          if (payload.content) {
            appendMessageRef.current({
              id: generateId(),
              role: 'assistant',
              msgType: 'tool_call',
              content: payload.content,
              timestamp: new Date(),
              isStreaming: false,
            });
          }
          // Mark the node as completed in the WorkflowCanvas DAG
          if (payload.node_id) {
            setCompletedNodeIds(prev => new Set(prev).add(payload.node_id as string));
            setActiveNodeId(null);
          }
          break;

        case 'done':
          setStatusText(null);
          finalizeRef.current('message');
          break;

        case 'status':
        case 'document_progress':
          // A transient "what's happening right now" update — Chat Mode's
          // status_callback ("Analyzing request...", "Searching your
          // documents...") or a document/scrape lifecycle stage ("Ingesting
          // X...", "✅ ready", "❌ failed"). Replaces the single status line
          // in place rather than becoming its own permanent message, so a
          // turn (or an upload) with several stages doesn't leave a stack of
          // pill bubbles behind. The Files panel is the durable record of
          // an upload's outcome — the chat transcript doesn't need its own
          // permanent copy of the same status.
          setStatusText(payload.content ?? null);
          break;

        case 'toast':
          // Genuine one-off notifications (job scheduled, action expired,
          // agent busy) — these ARE meant to be a persistent, visible
          // record, unlike the transient line above.
          appendMessageRef.current({
            id: generateId(),
            role: 'system',
            content: payload.content ?? '',
            timestamp: new Date(),
          });
          break;

        case 'error':
          setStatusText(null);
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
    (content: string, msgType: string = 'message', mode: string = 'chat', userPrompt?: string, attachments?: Attachment[], exportFormat?: string): boolean => {
      const trimmed = content.trim();
      // Claude-style: a message can be attachments alone with no typed text.
      if (!trimmed && !(attachments && attachments.length > 0)) return false;
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
          attachments,
        });
        // Turn the animated response indicator on immediately, not only once
        // the first 'token' event arrives — that event only fires after the
        // backend's whole pre-generation phase (RAG search, skills, status
        // updates) completes, which can take a real, visible amount of time.
        // Without this, that entire wait shows nothing at all if a status
        // update is slow, dropped, or the connection hiccups — exactly the
        // blank-screen gap this was reported against. Claude's own "thinking"
        // indicator appears the instant you hit send, not after a round trip.
        setIsStreaming(true);
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
        JSON.stringify({ type: msgType, content: trimmed, mode, user_prompt: userPrompt, attachments, export_format: exportFormat })
      );
      return true;
    },
    [isStreaming, appendMessage]
  );

  const cancelGeneration = useCallback(() => {
    if (socketRef.current?.readyState === WebSocket.OPEN && isStreaming) {
      socketRef.current.send(JSON.stringify({ type: 'cancel' }));
      // We don't immediately clear isStreaming here; the backend will send a done event 
      // which handles state cleanup predictably.
    }
  }, [isStreaming]);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setStatusText(null);
  }, []);

  const switchSession = useCallback((newSessionId?: string) => {
    // Signal that this close is intentional — don't trigger reconnect
    isSessionSwitchRef.current = true;
    // Reset history flag so the new session's history gets loaded
    historyLoadedRef.current = false;
    setStatusText(null);
    // A fresh/empty conversation never gets a "history" message at all (see
    // the backend's `if full_history:` guard), so without this reset a new
    // chat would inherit whatever agentState the *previous* conversation
    // last reported — stale, and specifically the wrong direction to leak
    // (turning Agent Mode's confirmation UI back on for an unrelated chat).
    setAgentState('IDLE');

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
    statusText,
    agentState,
    activeNodeId,
    completedNodeIds,
    failedNodeIds,
    sendMessage,
    cancelGeneration,
    clearMessages,
    switchSession,
    addMessageHandler,
  };
}
