'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { SiApple, SiGoogle } from 'react-icons/si';
import { AegisMark } from './AegisLogo';
import { openInBrowser } from '@/lib/openInBrowser';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
// The Aegis website — real login/sign-up lives there (2FA, password
// managers, social login all work) so the desktop app never shows its own
// password field. Hardcoded rather than env-driven, same reasoning as
// SUPABASE_URL in the backend's supabase_client.py: every install (dev or
// packaged) needs to reach the same website, and .env isn't bundled into a
// packaged build.
const WEBAPP_BASE = 'https://aegisaistudio.online';

interface AuthScreenProps {
  onAuthenticated: () => void;
}

export default function AuthScreen({ onAuthenticated }: AuthScreenProps) {
  const [mode, setMode] = useState<'idle' | 'waiting'>('idle');
  const [lastUrl, setLastUrl] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Login and sign-up are the same action now that the website is
  // OAuth-only (Supabase creates the account automatically on a first
  // sign-in) — always land on /auth/login, and `provider` deep-links
  // straight into that provider's redirect so opening the browser doesn't
  // make the user pick again on the page that opens.
  const startAuth = useCallback((provider: 'google' | 'apple') => {
    const next = encodeURIComponent('/desktop/callback');
    const url = `${WEBAPP_BASE}/auth/login?next=${next}&provider=${provider}`;
    setLastUrl(url);
    openInBrowser(url);
    setMode('waiting');
  }, []);

  useEffect(() => {
    if (mode !== 'waiting') return;
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/account/status`, { cache: 'no-store' });
        const data = await res.json();
        if (data.logged_in) {
          if (pollRef.current) clearInterval(pollRef.current);
          onAuthenticated();
        }
      } catch {
        // backend hiccup — keep polling
      }
    }, 1500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [mode, onAuthenticated]);

  return (
    <div className="relative flex h-screen w-screen flex-col items-center justify-center bg-aegis-base text-aegis-text-primary overflow-hidden">
      {/* Background glow — matches the splash screen's treatment */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[700px] bg-aegis-primary/[0.03] rounded-full blur-3xl animate-[pulse-glow_4s_ease-in-out_infinite]" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[340px] h-[340px] bg-aegis-accent/[0.04] rounded-full blur-2xl" />
      </div>

      <div className="relative z-10 flex flex-col items-center gap-8 px-8 max-w-sm w-full animate-fade-in-up">
        <div className="flex items-center gap-3">
          <AegisMark size={40} glow={false} />
          <span className="font-bold tracking-tight text-2xl text-aegis-text-primary">AEGIS</span>
        </div>

        <div className="w-full bg-aegis-raised rounded-2xl border border-aegis-border p-6 space-y-4 shadow-sm">
          {mode === 'idle' ? (
            <>
              <div className="text-center space-y-1">
                <h3 className="text-sm font-bold text-aegis-text-primary">Sign in to Aegis</h3>
                <p className="text-[11px] text-aegis-text-secondary">
                  An Aegis account is required to continue. You'll sign in through your browser.
                </p>
              </div>

              <button
                onClick={() => startAuth('google')}
                className="w-full flex items-center justify-center gap-2 p-3 rounded-xl bg-aegis-primary text-white text-xs font-semibold hover:opacity-90 transition-all"
              >
                <SiGoogle className="w-3.5 h-3.5" />
                Continue with Google
              </button>
              <button
                onClick={() => startAuth('apple')}
                className="w-full flex items-center justify-center gap-2 p-3 rounded-xl bg-aegis-overlay border border-aegis-border text-aegis-text-primary text-xs font-semibold hover:bg-aegis-sidebar-raised transition-all"
              >
                <SiApple className="w-3.5 h-3.5" />
                Continue with Apple
              </button>
            </>
          ) : (
            <div className="text-center space-y-4 py-2">
              <Loader2 className="w-5 h-5 animate-spin text-aegis-primary mx-auto" />
              <div className="space-y-1">
                <h3 className="text-sm font-bold text-aegis-text-primary">Waiting for you to finish in your browser…</h3>
                <p className="text-[11px] text-aegis-text-secondary">
                  Complete sign-in there — this screen updates automatically.
                </p>
              </div>
              <div className="flex items-center justify-center gap-4 pt-1">
                <button
                  onClick={() => lastUrl && openInBrowser(lastUrl)}
                  className="text-[11px] font-semibold text-aegis-primary-light hover:underline"
                >
                  Reopen browser
                </button>
                <button
                  onClick={() => setMode('idle')}
                  className="text-[11px] font-semibold text-aegis-text-secondary hover:underline"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
