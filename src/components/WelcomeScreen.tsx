'use client';

import { useEffect, useState } from 'react';
import { Check, Loader2 } from 'lucide-react';

interface HardwareDetect {
  platform: string;
  is_apple_silicon: boolean;
  chip_name: string | null;
  total_cores: number;
  performance_cores: number | null;
  efficiency_cores: number | null;
  gpu_backend: string;
  ram_total_gb: number;
  gpu_offload_layers: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface WelcomeScreenProps {
  onContinue: () => void;
}

// Shown exactly once, ever — the first time this install reaches the main
// app. A real hardware read (see /api/hardware/detect), not a copy of any
// other product's fabricated specs or branding.
export default function WelcomeScreen({ onContinue }: WelcomeScreenProps) {
  const [hw, setHw] = useState<HardwareDetect | null>(null);
  const [continuing, setContinuing] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/hardware/detect`, { cache: 'no-store' })
      .then(res => res.json())
      .then(setHw)
      .catch(() => {});
  }, []);

  const deviceLabel = hw?.platform === 'Darwin' ? 'Mac' : hw?.platform === 'Windows' ? 'PC' : 'machine';

  const items: string[] = [];
  if (hw) {
    if (hw.chip_name) items.push(`Chip detected: ${hw.chip_name}`);
    items.push(
      hw.performance_cores != null && hw.efficiency_cores != null
        ? `${hw.total_cores}-core CPU (${hw.performance_cores} performance + ${hw.efficiency_cores} efficiency)`
        : `${hw.total_cores}-core CPU`
    );
    items.push(`${hw.gpu_backend} inference ready`);
    items.push(`${hw.ram_total_gb} GB ${hw.is_apple_silicon ? 'unified' : 'system'} memory`);
    items.push(
      hw.gpu_offload_layers === -1
        ? 'All model layers offloaded to GPU'
        : `${hw.gpu_offload_layers} model layers offloaded to GPU`
    );
  }

  const handleContinue = async () => {
    setContinuing(true);
    try {
      await fetch(`${API_BASE}/api/onboarding/welcome-seen`, { method: 'POST' });
    } finally {
      onContinue();
    }
  };

  return (
    <div className="relative flex h-screen w-screen flex-col items-center justify-center bg-aegis-base text-aegis-text-primary overflow-hidden">
      {/* Background glow — matches the auth/splash screens' treatment */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[700px] bg-aegis-primary/[0.03] rounded-full blur-3xl animate-[pulse-glow_4s_ease-in-out_infinite]" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[340px] h-[340px] bg-aegis-accent/[0.04] rounded-full blur-2xl" />
      </div>

      <div className="relative z-10 w-full max-w-md px-8 animate-fade-in-up">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-2 h-2 rounded-sm bg-aegis-primary flex-shrink-0" />
          <span className="text-[11px] font-bold tracking-widest text-aegis-text-muted uppercase">Local Setup</span>
        </div>
        <h1 className="text-3xl font-bold text-aegis-text-primary mb-6">
          Your {deviceLabel} is ready.
        </h1>

        <div className="bg-aegis-raised rounded-2xl border border-aegis-border overflow-hidden mb-4">
          {!hw ? (
            <div className="flex items-center gap-2 px-4 py-6 justify-center text-aegis-text-muted text-sm">
              <Loader2 className="w-4 h-4 animate-spin" /> Checking your hardware…
            </div>
          ) : (
            items.map((label, i) => (
              <div key={i} className={`flex items-center gap-3 px-4 py-3 ${i > 0 ? 'border-t border-aegis-border' : ''}`}>
                <Check className="w-4 h-4 text-aegis-success flex-shrink-0" />
                <span className="text-sm text-aegis-text-primary">{label}</span>
              </div>
            ))
          )}
        </div>

        <p className="text-xs text-aegis-text-muted leading-relaxed mb-6">
          Models run at full speed, entirely on-device and entirely private.
        </p>

        <button
          onClick={handleContinue}
          disabled={continuing || !hw}
          className="w-full flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-aegis-primary text-white text-sm font-semibold hover:opacity-90 transition-all disabled:opacity-50"
        >
          {continuing && <Loader2 className="w-4 h-4 animate-spin" />}
          Start Using Aegis
        </button>
      </div>
    </div>
  );
}
