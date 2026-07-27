/**
 * Aegis — Session Redux Slice
 *
 * Manages the active WebSocket session ID in Redux state.
 * Persists the session ID via a lightweight localStorage subscriber
 * (see store/index.ts) — no redux-persist dependency needed.
 */

import { createSlice, PayloadAction } from '@reduxjs/toolkit';

// ─── Helpers ─────────────────────────────────────────────────────────────────

const SESSION_KEY = 'aegis_session_id';

function createSessionId(): string {
  return `session-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}

export function loadSessionId(): string {
  if (typeof window === 'undefined') return createSessionId();
  const stored = localStorage.getItem(SESSION_KEY);
  // Reject old timestamp-based msg-* IDs from before Redux migration
  if (!stored || stored.startsWith('msg-')) {
    const fresh = createSessionId();
    localStorage.setItem(SESSION_KEY, fresh);
    return fresh;
  }
  return stored;
}

// ─── State ───────────────────────────────────────────────────────────────────

export interface SessionState {
  sessionId: string;
}

const initialState: SessionState = {
  sessionId: '', // Hydrate this later on the client to avoid SSR mismatch
};

// ─── Slice ───────────────────────────────────────────────────────────────────

const sessionSlice = createSlice({
  name: 'session',
  initialState,
  reducers: {
    /** Explicitly set a specific session ID (e.g. loading a historical session) */
    setSessionId(state, action: PayloadAction<string>) {
      state.sessionId = action.payload;
    },

    /** Generate and set a brand-new session ID (i.e. "New Chat") */
    generateNewSession(state) {
      state.sessionId = createSessionId();
    },
  },
});

export const { setSessionId, generateNewSession } = sessionSlice.actions;
export const selectSessionId = (state: { session: SessionState }) => state.session.sessionId;
export default sessionSlice.reducer;
