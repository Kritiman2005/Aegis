'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { CheckCircle2, Loader2, AlertTriangle, Download } from 'lucide-react';

interface SystemStatus {
  sqlite: boolean;
  qdrant: boolean;
  embedding_models: boolean;
  downloaded_models: string[];
}

interface SplashScreenProps {
  onReady: () => void;
  onGoToLLMPanel?: () => void;
}

export default function SplashScreen({ onReady, onGoToLLMPanel }: SplashScreenProps) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [backendReachable, setBackendReachable] = useState(false);
  const [dots, setDots] = useState('');

  // Animate the dots
  useEffect(() => {
    const iv = setInterval(() => setDots(d => d.length >= 3 ? '' : d + '.'), 500);
    return () => clearInterval(iv);
  }, []);

  const poll = useCallback(async () => {
    try {
      const healthRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/health', { cache: 'no-store' });
      if (!healthRes.ok) return;
      setBackendReachable(true);

      const statusRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/status', { cache: 'no-store' });
      if (!statusRes.ok) return;
      const data: SystemStatus = await statusRes.json();
      setStatus(data);

      // Proceed only when core DBs are up
      if (data.sqlite && data.qdrant && data.downloaded_models.length > 0) {
        onReady();
      }
    } catch {
      // Backend not up yet — keep polling
    }
  }, [onReady]);

  useEffect(() => {
    poll();
    const iv = setInterval(poll, 1200);
    return () => clearInterval(iv);
  }, [poll]);

  const checks: { label: string; done: boolean; key: string }[] = [
    { key: 'backend', label: 'Starting backend server', done: backendReachable },
    { key: 'sqlite', label: 'Initializing SQLite database', done: status?.sqlite ?? false },
    { key: 'qdrant', label: 'Starting Qdrant vector store', done: status?.qdrant ?? false },
    { key: 'embed', label: 'Preloading embedding models', done: status?.embedding_models ?? false },
  ];

  const coreReady = status?.sqlite && status?.qdrant;
  const noModel = coreReady && (status?.downloaded_models?.length ?? 0) === 0;

  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center bg-[#080B14] text-white overflow-hidden">
      {/* Background glow */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-blue-600/5 rounded-full blur-3xl" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[300px] h-[300px] bg-purple-600/10 rounded-full blur-2xl" />
      </div>

      <div className="relative z-10 flex flex-col items-center space-y-8 px-8 max-w-sm w-full">
        {/* Spinner / Icon */}
        <div className="relative flex items-center justify-center h-20 w-20">
          {!coreReady ? (
            <>
              <div className="absolute inset-0 rounded-full border-t-2 border-blue-500/60 animate-spin" />
              <div
                className="absolute inset-2 rounded-full border-b-2 border-purple-500/60 animate-spin"
                style={{ animationDuration: '1.5s', animationDirection: 'reverse' }}
              />
            </>
          ) : noModel ? (
            <div className="w-16 h-16 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center">
              <AlertTriangle className="w-8 h-8 text-amber-400" />
            </div>
          ) : (
            <div className="w-16 h-16 rounded-2xl bg-green-500/10 border border-green-500/20 flex items-center justify-center">
              <CheckCircle2 className="w-8 h-8 text-green-400" />
            </div>
          )}
        </div>

        {/* Title */}
        <div className="text-center">
          <h1 className="text-3xl font-bold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-blue-400 via-indigo-300 to-purple-400">
            AEGIS
          </h1>
          <p className="text-gray-500 text-xs mt-1 tracking-widest uppercase">Local AI Platform</p>
        </div>

        {/* Status checklist */}
        <div className="w-full space-y-2.5">
          {checks.map(({ key, label, done }) => (
            <div key={key} className="flex items-center gap-3">
              <div className="flex-shrink-0 w-4 h-4">
                {done ? (
                  <CheckCircle2 className="w-4 h-4 text-green-400" />
                ) : (
                  <Loader2 className="w-4 h-4 text-gray-600 animate-spin" />
                )}
              </div>
              <span className={`text-sm transition-colors ${done ? 'text-gray-300' : 'text-gray-600'}`}>
                {label}{!done && dots}
              </span>
            </div>
          ))}
        </div>

        {/* No model — call to action */}
        {noModel && (
          <div className="w-full bg-amber-500/5 border border-amber-500/20 rounded-2xl p-4 space-y-3">
            <p className="text-amber-300 text-sm font-semibold">No AI model downloaded</p>
            <p className="text-gray-400 text-xs leading-relaxed">
              Aegis is ready, but needs a local model to generate responses.
              Visit the <strong className="text-white">LLM Panel</strong> to download one.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => { onGoToLLMPanel?.(); onReady(); }}
                className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white text-xs font-semibold rounded-xl transition-all shadow-lg shadow-blue-900/30"
              >
                <Download className="w-3.5 h-3.5" />
                Go to LLM Panel
              </button>
              <button
                onClick={onReady}
                className="px-4 py-2 border border-gray-700 text-gray-400 hover:text-gray-300 text-xs rounded-xl transition-all"
              >
                Skip for now
              </button>
            </div>
          </div>
        )}

        {/* Downloaded model badge */}
        {coreReady && (status?.downloaded_models?.length ?? 0) > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-green-500/5 border border-green-500/20 rounded-full">
            <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
            <span className="text-xs text-green-300">
              {status!.downloaded_models[0]} ready
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
