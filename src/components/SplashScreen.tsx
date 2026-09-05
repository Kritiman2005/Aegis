'use client';

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { CheckCircle2, Circle, Download, Loader2, Cpu } from 'lucide-react';
import { AegisMark } from './AegisLogo';

interface SystemStatus {
  sqlite: boolean;
  qdrant: boolean;
  embedding_models: boolean;
  downloaded_models: string[];
}

interface Recommendation {
  ram_total_gb: number;
  model: {
    key: string;
    display_name: string;
    repo_id: string;
    filename: string;
    approx_download_gb: number;
    description: string;
  };
  already_downloaded: boolean;
}

interface SplashScreenProps {
  onReady: () => void;
  onGoToLLMPanel?: () => void;
  // Fires once, the moment the backend answers /api/health at all — well
  // before onReady (which waits for Qdrant + embedding preload + a
  // downloaded model too). SQLite is already up by then (it's initialized
  // synchronously before the server accepts any requests), so this is the
  // earliest point account status can actually be checked — used to show
  // the sign-in screen without making an unauthenticated visitor sit
  // through the rest of the local-AI boot sequence first.
  onBackendReachable?: () => void;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export default function SplashScreen({ onReady, onGoToLLMPanel, onBackendReachable }: SplashScreenProps) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [backendReachable, setBackendReachable] = useState(false);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [downloadState, setDownloadState] = useState<'idle' | 'downloading' | 'failed'>('idle');
  const downloadPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Dedicated logo-build moment before anything else on the splash screen
  // appears — the mark's 8 blocks pop in one by one (see AegisMark's
  // `animated` prop: ~2s for all 8 at 160ms apart, each taking 0.9s to
  // settle), holds briefly so the fully-assembled mark actually registers,
  // then crossfades into the real splash content (progress bar, checklist,
  // etc.) below.
  const INTRO_HOLD_MS = 2600;
  const INTRO_EXIT_MS = 500;
  const [introPhase, setIntroPhase] = useState<'building' | 'exiting' | 'done'>('building');
  useEffect(() => {
    const t1 = setTimeout(() => setIntroPhase('exiting'), INTRO_HOLD_MS);
    const t2 = setTimeout(() => setIntroPhase('done'), INTRO_HOLD_MS + INTRO_EXIT_MS);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, []);

  // Backend readiness and the logo intro are tracked separately — see the
  // effect below that only calls onReady() once both have finished, so a
  // backend that's already warm (common in dev, or a fast relaunch) can't
  // cut the dedicated logo-build animation short by yanking this screen
  // away mid-flight.
  const [backendFullyReady, setBackendFullyReady] = useState(false);

  const poll = useCallback(async () => {
    try {
      const healthRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/health`, { cache: 'no-store' });
      if (!healthRes.ok) return;
      setBackendReachable(true);

      const statusRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/status`, { cache: 'no-store' });
      if (!statusRes.ok) return;
      const data: SystemStatus = await statusRes.json();
      setStatus(data);

      // Proceed only when core DBs are up
      if (data.sqlite && data.qdrant && data.downloaded_models.length > 0) {
        setBackendFullyReady(true);
      }
    } catch {
      // Backend not up yet — keep polling
    }
  }, []);

  useEffect(() => {
    poll();
    const iv = setInterval(poll, 1200);
    return () => clearInterval(iv);
  }, [poll]);

  useEffect(() => {
    // Also waits for the logo intro to finish, same as onReady below — so an
    // unauthenticated visitor still gets the full logo-build moment before
    // being handed to AuthScreen, instead of that animation getting cut off
    // mid-flight the instant the backend answers.
    if (backendReachable && introPhase === 'done') onBackendReachable?.();
    // Fire once, the instant both conditions are met — not on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendReachable, introPhase]);

  useEffect(() => {
    if (backendFullyReady && introPhase === 'done') {
      onReady();
    }
  }, [backendFullyReady, introPhase, onReady]);

  const checks: { label: string; done: boolean; key: string }[] = [
    { key: 'backend', label: 'Starting backend server', done: backendReachable },
    { key: 'sqlite', label: 'Initializing SQLite database', done: status?.sqlite ?? false },
    { key: 'qdrant', label: 'Starting Qdrant vector store', done: status?.qdrant ?? false },
    { key: 'embed', label: 'Preloading embedding models', done: status?.embedding_models ?? false },
  ];

  const coreReady = status?.sqlite && status?.qdrant;
  const noModel = coreReady && (status?.downloaded_models?.length ?? 0) === 0;

  // Fetch a model recommendation for this machine as soon as we know there's
  // nothing downloaded yet — mirrors AnythingLLM's setup flow: suggest one
  // concrete model that fits the hardware, rather than an empty search box.
  useEffect(() => {
    if (!noModel || recommendation) return;
    fetch(`${API_BASE}/api/hub/recommendation`, { cache: 'no-store' })
      .then(res => res.json())
      .then(setRecommendation)
      .catch(() => {});
  }, [noModel, recommendation]);

  useEffect(() => () => { if (downloadPollRef.current) clearInterval(downloadPollRef.current); }, []);

  const handleDownloadRecommended = useCallback(() => {
    if (!recommendation) return;
    const { repo_id, filename } = recommendation.model;
    setDownloadState('downloading');

    fetch(`${API_BASE}/api/hub/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_id, filename }),
    }).catch(() => {});

    // No websocket connection exists yet at this point in the boot sequence —
    // poll the same downloaded-models list the rest of the app uses instead of
    // wiring in useSocket just for this one screen.
    downloadPollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/hub/downloaded`, { cache: 'no-store' });
        const data = await res.json();
        const match = (data.models || []).find(
          (m: any) => m.repo_id === repo_id && m.filename === filename
        );
        if (match?.status === 'downloaded') {
          if (downloadPollRef.current) clearInterval(downloadPollRef.current);
          onReady();
        } else if (match?.status === 'failed') {
          if (downloadPollRef.current) clearInterval(downloadPollRef.current);
          setDownloadState('failed');
        }
      } catch {
        // backend hiccup mid-download — keep polling
      }
    }, 1500);
  }, [recommendation, onReady]);
  const progressPct = Math.round((checks.filter(c => c.done).length / checks.length) * 100);

  return (
    <div className="relative flex h-screen w-screen flex-col items-center justify-center bg-aegis-base text-aegis-text-primary overflow-hidden">
      {/* Background glow — slow breathing pulse behind the mark. Much lower
          opacity than the dark-theme version: a light background shows any
          colored blur as a visible tint across the whole page, not just a
          subtle depth cue. */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[700px] bg-aegis-primary/[0.03] rounded-full blur-3xl animate-[pulse-glow_4s_ease-in-out_infinite]" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[340px] h-[340px] bg-aegis-accent/[0.04] rounded-full blur-2xl" />
      </div>

      <div className="relative z-10 flex flex-col items-center space-y-9 px-8 max-w-sm w-full">
        {/* The logo already had its dedicated moment in the intro overlay —
            this phase is pure initialization status (DBs, models), so it
            deliberately doesn't repeat the mark or wordmark here. */}

        {/* Progress bar — delayed to start exactly when the intro overlay
            begins fading (see INTRO_HOLD_MS below), not at mount. It used
            to fire immediately: at 0.25s per fade-in-up, it (and the rest
            of this real content) had always finished and gone fully
            static well before the 2.6s overlay hold ended, so the overlay
            fading away just revealed an already-settled screen instead of
            a second thing animating in — reads as one continuous handoff
            now instead of a flat "curtain drop". */}
        <div className="w-full space-y-2 animate-fade-in-up" style={{ animationDelay: `${INTRO_HOLD_MS}ms`, animationFillMode: 'backwards' }}>
          <div className="h-1 w-full bg-aegis-overlay rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-aegis-primary to-aegis-accent transition-all duration-700 ease-out"
              style={{ width: `${Math.max(6, progressPct)}%` }}
            />
          </div>
          <div className="flex justify-between text-[10px] text-aegis-text-muted tracking-wide">
            <span>{coreReady ? 'Core systems online' : 'Booting local stack'}</span>
            <span>{progressPct}%</span>
          </div>
        </div>

        {/* Status checklist — same re-sync: staggered off INTRO_HOLD_MS
            instead of 0, so the stagger plays out as the logo fades away. */}
        <div className="w-full space-y-3">
          {checks.map(({ key, label, done }, i) => (
            <div
              key={key}
              className="flex items-center gap-3 animate-fade-in-up"
              style={{ animationDelay: `${INTRO_HOLD_MS + i * 80}ms`, animationFillMode: 'backwards' }}
            >
              <div className="flex-shrink-0 w-4 h-4 relative">
                {done ? (
                  <CheckCircle2 className="w-4 h-4 text-aegis-success" />
                ) : (
                  <Circle className="w-4 h-4 text-aegis-border" strokeWidth={2} />
                )}
              </div>
              <span className={`text-sm transition-colors duration-300 ${done ? 'text-aegis-text-secondary' : 'text-aegis-text-muted'}`}>
                {label}
              </span>
            </div>
          ))}
        </div>

        {/* No model — recommend one for this machine. Styled to match
            WelcomeScreen's "Your Mac is ready" language: eyebrow label,
            bold headline, a clean bordered card instead of a warning-tinted
            box, one solid primary CTA, lighter secondary text actions. */}
        {noModel && (
          <div className="w-full animate-fade-in-up">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-2 h-2 rounded-sm bg-aegis-primary flex-shrink-0" />
              <span className="text-[11px] font-bold tracking-widest text-aegis-text-muted uppercase">Model Setup</span>
            </div>
            <h2 className="text-2xl font-bold text-aegis-text-primary mb-4">
              {downloadState === 'downloading' ? 'Downloading your model…' : 'Choose a model to get started.'}
            </h2>

            <div className="bg-aegis-raised rounded-2xl border border-aegis-border overflow-hidden mb-4">
              {downloadState === 'downloading' ? (
                <div className="flex items-center gap-3 px-4 py-4">
                  <Loader2 className="w-4 h-4 animate-spin text-aegis-primary-light flex-shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-aegis-text-primary truncate">{recommendation?.model.display_name}</p>
                    <p className="text-xs text-aegis-text-muted mt-0.5">This can take a few minutes depending on your connection.</p>
                  </div>
                </div>
              ) : recommendation ? (
                <div className="px-4 py-4 space-y-1.5">
                  <div className="flex items-center gap-2 text-xs text-aegis-text-muted">
                    <Cpu className="w-3.5 h-3.5 text-aegis-primary-light flex-shrink-0" />
                    Recommended for your {recommendation.ram_total_gb}GB machine
                  </div>
                  <p className="text-sm font-semibold text-aegis-text-primary">{recommendation.model.display_name}</p>
                  <p className="text-xs text-aegis-text-muted leading-relaxed">{recommendation.model.description}</p>
                  <p className="text-[11px] text-aegis-text-muted">~{recommendation.model.approx_download_gb} GB download</p>
                </div>
              ) : (
                <div className="px-4 py-5 text-center text-xs text-aegis-text-muted">
                  Aegis needs a local model to generate responses.
                </div>
              )}
            </div>

            {downloadState === 'failed' && (
              <p className="text-aegis-error text-xs font-medium mb-3">Download failed — check your connection and try again.</p>
            )}

            {downloadState !== 'downloading' && (
              <>
                {recommendation && !recommendation.already_downloaded && (
                  <button
                    onClick={handleDownloadRecommended}
                    className="w-full flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-aegis-primary text-white text-sm font-semibold hover:opacity-90 transition-all mb-3"
                  >
                    <Download className="w-4 h-4" />
                    Download This Model
                  </button>
                )}
                <div className="flex items-center justify-center gap-4">
                  <button
                    onClick={() => { onGoToLLMPanel?.(); onReady(); }}
                    className="text-xs font-semibold text-aegis-text-secondary hover:text-aegis-text-primary transition-colors"
                  >
                    Browse other models
                  </button>
                  <button
                    onClick={onReady}
                    className="text-xs font-semibold text-aegis-text-muted hover:text-aegis-text-secondary transition-colors"
                  >
                    Skip for now
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {/* Downloaded model badge */}
        {coreReady && (status?.downloaded_models?.length ?? 0) > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-aegis-success/5 border border-aegis-success/20 rounded-full animate-fade-in-up">
            <CheckCircle2 className="w-3.5 h-3.5 text-aegis-success" />
            <span className="text-xs text-aegis-success">
              {status!.downloaded_models[0]} ready
            </span>
          </div>
        )}
      </div>

      {/* Intro overlay — the dedicated logo-build moment. Sits opaque on top
          of the real content (which is already mounted and animating in
          underneath) until the mark finishes assembling, then fades out. */}
      {introPhase !== 'done' && (
        <div
          className={`absolute inset-0 z-20 flex items-center justify-center bg-aegis-base transition-opacity duration-500 ${
            introPhase === 'exiting' ? 'opacity-0' : 'opacity-100'
          }`}
        >
          <AegisMark size={120} animated />
        </div>
      )}
    </div>
  );
}
