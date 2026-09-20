'use client';

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { CheckCircle2, Circle, Download, Loader2, Cpu } from 'lucide-react';
import { AegisMark } from './AegisLogo';

interface SystemStatus {
  sqlite: boolean;
  qdrant: boolean;
  embedding_models: boolean;
  embedding_stage: 'pending' | 'dense' | 'sparse' | 'reranker' | 'done' | 'failed';
  embedding_progress: number;
  llm_ready: boolean;
  llm_stage: 'pending' | 'none' | 'loading' | 'done' | 'failed';
  llm_progress: number;
  llm_model_name: string | null;
  downloaded_models: string[];
}

// "dense"/"sparse"/"reranker" are dead states now — main.py's on_startup no
// longer eagerly preloads any of these (they're all lazy, first-real-use
// initialized instead — see its own comment there on why: a fresh install
// downloading BAAI/bge-base-en-v1.5 + a sparse model + a reranker on every
// boot was unwanted default weight for a pipeline that, by default, never
// touches any of them). embedding_stage only ever actually arrives as
// "pending" (briefly, before the first status poll) or "done" (within a
// fraction of a second after) in a real run — worded honestly here rather
// than implying a multi-step load that doesn't happen.
const EMBEDDING_STAGE_LABEL: Record<SystemStatus['embedding_stage'], string> = {
  pending: 'Checking embedding models',
  dense: 'Checking embedding models',
  sparse: 'Checking embedding models',
  reranker: 'Checking embedding models',
  done: 'Embedding models ready',
  failed: 'Embedding models will load on first use',
};

// Rotates while the reranker/LLM stages are in flight — this is the part of
// the wait that's actually visible now (see the cold-start investigation:
// sentence_transformers' own import graph alone is ~11s), so it gets a
// little texture instead of sitting on a static spinner.
const LOADING_TIPS = [
  'Everything runs locally — nothing you type ever leaves this device.',
  'The reranker re-scores search results for accuracy after retrieval.',
  'You can swap the language model anytime from the Model Hub.',
  'First boot is the slowest — these models stay warm in RAM after this.',
];

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

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

export default function SplashScreen({ onReady, onGoToLLMPanel, onBackendReachable }: SplashScreenProps) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [backendReachable, setBackendReachable] = useState(false);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [downloadState, setDownloadState] = useState<'idle' | 'downloading' | 'failed'>('idle');
  const [downloadProgress, setDownloadProgress] = useState<{ percent: number; downloadedBytes: number; totalBytes: number } | null>(null);
  const downloadPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const downloadSocketRef = useRef<WebSocket | null>(null);

  // Dedicated logo-build moment before anything else on the splash screen
  // appears — the mark's 8 blocks pop in one by one (see AegisMark's
  // `animated` prop: ~2s for all 8 at 160ms apart, each taking 0.9s to
  // settle), holds briefly so the fully-assembled mark actually registers,
  // then crossfades into the real splash content (progress bar, checklist,
  // etc.) below.
  const INTRO_HOLD_MS = 2600;
  const INTRO_EXIT_MS = 500;
  // Real content used to start its own fade-in at INTRO_HOLD_MS — the exact
  // instant the overlay BEGINS fading, not once it's actually gone. Since
  // the overlay's mark and this content are both centered on the same
  // spot on screen, that meant a ~500ms window where the mark (fading out)
  // and the progress bar/checklist (fading in) visibly overlapped each
  // other. Waiting for the overlay to fully finish before anything real
  // starts appearing removes that window entirely — a clean handoff
  // instead of a collision.
  const CONTENT_REVEAL_MS = INTRO_HOLD_MS + INTRO_EXIT_MS;
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

      // Proceed once core DBs are up AND both background preloads
      // (embedding models + the active LLM) have actually finished — the
      // splash screen is the whole point of showing real progress for the
      // slow part of boot, so it shouldn't hand off to the app while the
      // reranker/LLM are still silently loading behind it.
      if (data.sqlite && data.qdrant && data.downloaded_models.length > 0 && data.embedding_models && data.llm_ready) {
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

  const embeddingStage = status?.embedding_stage ?? 'pending';
  const embeddingPct = status?.embedding_progress ?? 0;
  const llmStage = status?.llm_stage ?? 'pending';
  const llmPct = status?.llm_progress ?? 0;
  const llmDone = llmStage === 'done' || llmStage === 'none' || llmStage === 'failed';

  const checks: { key: string; label: string; done: boolean; pct?: number }[] = [
    { key: 'backend', label: 'Starting backend server', done: backendReachable },
    { key: 'sqlite', label: 'Initializing SQLite database', done: status?.sqlite ?? false },
    { key: 'qdrant', label: 'Starting Qdrant vector store', done: status?.qdrant ?? false },
    {
      key: 'embed',
      label: EMBEDDING_STAGE_LABEL[embeddingStage],
      done: status?.embedding_models ?? false,
      // No real percentage to show anymore — embeddingStage resolves
      // pending -> done in a fraction of a second, with nothing in
      // between (see EMBEDDING_STAGE_LABEL's own comment).
      pct: undefined,
    },
    ...(llmStage !== 'pending' && llmStage !== 'none'
      ? [{
          key: 'llm',
          label: llmStage === 'loading'
            ? `Loading language model${status?.llm_model_name ? ` — ${status.llm_model_name}` : ''}`
            : llmStage === 'failed'
            ? 'Language model will load on first message'
            : 'Language model ready',
          done: llmDone,
          pct: llmStage === 'loading' ? llmPct : undefined,
        }]
      : []),
  ];

  // Weighted overall percentage — the two heavy background preloads
  // (embedding models, LLM) dominate the real wait, so they dominate the
  // bar too, instead of every checklist item counting equally the way a
  // flat done-count would.
  const progressPct = Math.round(
    (backendReachable ? 10 : 0) +
    (status?.sqlite ? 5 : 0) +
    (status?.qdrant ? 5 : 0) +
    40 * (embeddingPct / 100) +
    40 * (llmDone ? 1 : llmPct / 100)
  );

  // embeddingStage can never actually be 'reranker' anymore (see
  // EMBEDDING_STAGE_LABEL's own comment) — the LLM download/load is the
  // only real multi-second wait left to show rotating tips during.
  const isLoadingHeavyStage = llmStage === 'loading';
  const [tipIndex, setTipIndex] = useState(0);
  useEffect(() => {
    if (!isLoadingHeavyStage) return;
    const t = setInterval(() => setTipIndex(i => (i + 1) % LOADING_TIPS.length), 4000);
    return () => clearInterval(t);
  }, [isLoadingHeavyStage]);

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

  useEffect(() => () => {
    if (downloadPollRef.current) clearInterval(downloadPollRef.current);
    downloadSocketRef.current?.close();
  }, []);

  const handleDownloadRecommended = useCallback(() => {
    if (!recommendation) return;
    const { repo_id, filename } = recommendation.model;
    setDownloadState('downloading');
    setDownloadProgress(null);

    fetch(`${API_BASE}/api/hub/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_id, filename }),
    }).catch(() => {});

    // Real progress — the backend already broadcasts download_progress
    // (percent + byte counts) over the same WebSocket every chat connection
    // uses; a plain throwaway client_id here (not useSocket, which also
    // pulls in a Redux session and would mint a real chat session this
    // early in boot) is enough to receive it. One reconnect attempt if it
    // drops mid-download — the point of showing this at all is a slow/flaky
    // connection, exactly where a socket is likeliest to hiccup — but the
    // poll below is the actual source of truth for completion either way,
    // so a permanently-dead socket just means the percentage stops moving,
    // never a stuck screen.
    let reconnected = false;
    const connect = () => {
      const wsUrl = (process.env.NEXT_PUBLIC_WS_URL || 'ws://127.0.0.1:8000/ws') + `?client_id=splash-${Date.now()}`;
      const ws = new WebSocket(wsUrl);
      downloadSocketRef.current = ws;
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.repo_id !== repo_id || msg.filename !== filename) return;
          if (msg.type === 'download_progress') {
            setDownloadProgress({ percent: msg.progress ?? 0, downloadedBytes: msg.downloaded_bytes ?? 0, totalBytes: msg.total_bytes ?? 0 });
          } else if (msg.type === 'download_complete') {
            setDownloadProgress(p => p ? { ...p, percent: 100 } : p);
          }
        } catch {
          // not JSON, or not a shape we care about — ignore
        }
      };
      ws.onclose = () => {
        if (!reconnected && downloadPollRef.current) {
          reconnected = true;
          setTimeout(connect, 2000);
        }
      };
    };
    connect();

    // Source of truth for completion/failure — unchanged from before.
    downloadPollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/hub/downloaded`, { cache: 'no-store' });
        const data = await res.json();
        const match = (data.models || []).find(
          (m: any) => m.repo_id === repo_id && m.filename === filename
        );
        if (match?.status === 'downloaded') {
          if (downloadPollRef.current) clearInterval(downloadPollRef.current);
          downloadSocketRef.current?.close();
          onReady();
        } else if (match?.status === 'failed') {
          if (downloadPollRef.current) clearInterval(downloadPollRef.current);
          downloadSocketRef.current?.close();
          setDownloadState('failed');
        }
      } catch {
        // backend hiccup mid-download — keep polling
      }
    }, 1500);
  }, [recommendation, onReady]);

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

        {/* Progress bar — delayed until the intro overlay has fully
            finished fading (CONTENT_REVEAL_MS above), not until it merely
            *starts* fading and not at mount either. Starting it at mount
            meant this (and the rest of the real content) had always
            finished animating and gone fully static well before the 2.6s
            overlay hold even ended, so the overlay fading away just
            revealed an already-settled screen. Starting it when the fade
            merely *began* was hardly better: the overlay's mark and this
            content are both centered on the same spot on screen, so for
            the ~500ms fade duration the outgoing mark and the incoming
            progress bar/checklist were visibly overlapping each other.
            Waiting for the overlay to be fully gone avoids both — one
            clean handoff, nothing on screen twice at once. */}
        <div className="w-full space-y-2 animate-fade-in-up" style={{ animationDelay: `${CONTENT_REVEAL_MS}ms`, animationFillMode: 'backwards' }}>
          <div className="h-1 w-full bg-aegis-overlay rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-aegis-primary to-aegis-accent transition-all duration-700 ease-out"
              style={{ width: `${Math.max(6, progressPct)}%` }}
            />
          </div>
          <div className="flex justify-between text-[10px] text-aegis-text-muted tracking-wide">
            <span>{coreReady ? 'Core systems online' : 'Booting local stack'}</span>
            <span className="tabular-nums">{progressPct}%</span>
          </div>
        </div>

        {/* Status checklist — same re-sync: staggered off CONTENT_REVEAL_MS
            instead of 0, so the stagger plays out only once the logo is
            fully gone, not overlapping it mid-fade. Items with a live `pct`
            (the reranker/LLM stages — the two that actually take real time,
            see the cold-start investigation) get their own mini progress
            bar and a moving percentage instead of just a spinner, so the
            wait has something concrete to look at. */}
        <div className="w-full space-y-3">
          {checks.map(({ key, label, done, pct }, i) => (
            <div
              key={key}
              className="animate-fade-in-up"
              style={{ animationDelay: `${CONTENT_REVEAL_MS + i * 80}ms`, animationFillMode: 'backwards' }}
            >
              <div className="flex items-center gap-3">
                <div className="flex-shrink-0 w-4 h-4 relative">
                  {done ? (
                    <CheckCircle2 className="w-4 h-4 text-aegis-success" />
                  ) : pct !== undefined ? (
                    <Loader2 className="w-4 h-4 animate-spin text-aegis-primary-light" />
                  ) : (
                    <Circle className="w-4 h-4 text-aegis-border" strokeWidth={2} />
                  )}
                </div>
                <span className={`text-sm transition-colors duration-300 flex-1 ${done ? 'text-aegis-text-secondary' : pct !== undefined ? 'text-aegis-text-primary font-medium' : 'text-aegis-text-muted'}`}>
                  {label}
                </span>
                {pct !== undefined && (
                  <span className="text-xs font-semibold text-aegis-primary-light tabular-nums flex-shrink-0">{pct}%</span>
                )}
              </div>
              {pct !== undefined && (
                <div className="mt-1.5 ml-7 h-[3px] w-[calc(100%-1.75rem)] bg-aegis-overlay rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full bg-aegis-primary-light transition-all duration-500 ease-out"
                    style={{ width: `${Math.max(3, pct)}%` }}
                  />
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Rotating tip — only while a genuinely slow stage is in flight
            (reranker import, LLM load), so it doesn't flash in for the
            sub-second sqlite/qdrant checks. */}
        {isLoadingHeavyStage && (
          <p
            key={tipIndex}
            className="text-xs text-aegis-text-muted text-center leading-relaxed animate-fade-in-up max-w-[280px]"
            style={{ animationFillMode: 'backwards' }}
          >
            {LOADING_TIPS[tipIndex]}
          </p>
        )}

        {/* No model — recommend one for this machine. Styled to match
            WelcomeScreen's "Your Mac is ready" language: eyebrow label,
            bold headline, a clean bordered card instead of a warning-tinted
            box, one solid primary CTA, lighter secondary text actions. */}
        {noModel && (
          <div
            className="w-full animate-fade-in-up"
            style={{ animationDelay: `${CONTENT_REVEAL_MS + checks.length * 80}ms`, animationFillMode: 'backwards' }}
          >
            <div className="flex items-center gap-2 mb-3">
              <span className="w-2 h-2 rounded-sm bg-aegis-primary flex-shrink-0" />
              <span className="text-[11px] font-bold tracking-widest text-aegis-text-muted uppercase">Model Setup</span>
            </div>
            <h2 className="text-2xl font-bold text-aegis-text-primary mb-4">
              {downloadState === 'downloading' ? 'Downloading your model…' : 'Choose a model to get started.'}
            </h2>

            <div className="bg-aegis-raised rounded-2xl border border-aegis-border overflow-hidden mb-4">
              {downloadState === 'downloading' ? (
                <div className="px-4 py-4">
                  <div className="flex items-center gap-3">
                    {downloadProgress ? (
                      <span className="text-xs font-bold text-aegis-primary-light flex-shrink-0 w-9 text-right tabular-nums">
                        {Math.round(downloadProgress.percent)}%
                      </span>
                    ) : (
                      <Loader2 className="w-4 h-4 animate-spin text-aegis-primary-light flex-shrink-0" />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-aegis-text-primary truncate">{recommendation?.model.display_name}</p>
                      <p className="text-xs text-aegis-text-muted mt-0.5">
                        {downloadProgress && downloadProgress.totalBytes > 0
                          ? `${formatBytes(downloadProgress.downloadedBytes)} of ${formatBytes(downloadProgress.totalBytes)}`
                          : 'This can take a few minutes depending on your connection.'}
                      </p>
                    </div>
                  </div>
                  {downloadProgress && (
                    <div className="mt-3 h-1.5 w-full rounded-full bg-aegis-border overflow-hidden">
                      <div
                        className="h-full rounded-full bg-aegis-primary transition-[width] duration-300 ease-out"
                        style={{ width: `${Math.min(100, Math.max(0, downloadProgress.percent))}%` }}
                      />
                    </div>
                  )}
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
          <div
            className="flex items-center gap-2 px-3 py-1.5 bg-aegis-success/5 border border-aegis-success/20 rounded-full animate-fade-in-up"
            style={{ animationDelay: `${CONTENT_REVEAL_MS + checks.length * 80}ms`, animationFillMode: 'backwards' }}
          >
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
