'use client';

/**
 * Aegis — Redux Provider Wrapper
 *
 * A thin 'use client' wrapper around the Redux store Provider.
 * Required because Next.js App Router root layout is a Server Component
 * and cannot directly use client-side context providers.
 */

import { Provider } from 'react-redux';
import { useEffect } from 'react';
import { store } from '@/store';
import { setSessionId, loadSessionId } from '@/store/sessionSlice';

export default function ReduxProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    store.dispatch(setSessionId(loadSessionId()));
  }, []);

  return <Provider store={store}>{children}</Provider>;
}
