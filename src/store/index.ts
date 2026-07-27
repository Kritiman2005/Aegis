/**
 * Aegis — Redux Store
 *
 * Root store combining all slices.
 * Persists the session ID to localStorage via a thin subscriber
 * so the same session survives page refreshes.
 */

import { configureStore } from '@reduxjs/toolkit';
import sessionReducer, { selectSessionId } from './sessionSlice';

export const store = configureStore({
  reducer: {
    session: sessionReducer,
  },
});

// ─── Lightweight Persistence Subscriber ──────────────────────────────────────
// Whenever the session ID changes in the store, write it to localStorage.
// This replaces the need for redux-persist.

if (typeof window !== 'undefined') {
  let previousSessionId = selectSessionId(store.getState());

  store.subscribe(() => {
    const currentSessionId = selectSessionId(store.getState());
    if (currentSessionId !== previousSessionId) {
      localStorage.setItem('aegis_session_id', currentSessionId);
      previousSessionId = currentSessionId;
    }
  });
}

// ─── Types ───────────────────────────────────────────────────────────────────

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
