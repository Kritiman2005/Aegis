'use client';

import { useCallback, useEffect, useState } from 'react';
import { BarChart3 } from 'lucide-react';

interface AnalyticsData {
  tokens_generated: number;
  prompt_tokens_processed: number;
  estimated_saved_usd: number;
  percent_local: number;
  daily: { date: string; tokens: number }[];
  by_model: { model: string; tokens: number }[];
  by_source: { source: string; tokens: number }[];
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

// A small coral/orange-family palette so multi-segment charts stay legible
// while remaining on-brand — no other hues, unlike the reference's mixed palette.
const SEGMENT_COLORS = ['#F4622D', '#FBBF24', '#FF8B5E', '#FCD34D', '#D14A1D', '#F59E0B'];

function formatCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n % 1_000 === 0 ? 0 : 1)}k`;
  return String(n);
}

function StatCard({ label, value, subtitle }: { label: string; value: string; subtitle: string }) {
  return (
    <div className="bg-aegis-raised border border-aegis-border rounded-2xl p-6">
      <p className="text-[11px] font-semibold tracking-wider text-aegis-text-muted uppercase mb-3">{label}</p>
      <p className="text-3xl font-bold text-aegis-text-primary mb-1.5">{value}</p>
      <p className="text-[13px] text-aegis-text-secondary">{subtitle}</p>
    </div>
  );
}

function DailyBarChart({ daily }: { daily: AnalyticsData['daily'] }) {
  const max = Math.max(1, ...daily.map(d => d.tokens));
  return (
    <div className="bg-aegis-raised border border-aegis-border rounded-2xl p-6">
      <p className="text-[11px] font-semibold tracking-wider text-aegis-text-muted uppercase mb-6">Tokens Per Day</p>
      <div className="flex items-end justify-between gap-3 h-48">
        {daily.map(d => {
          const heightPct = Math.max(2, (d.tokens / max) * 100);
          return (
            <div key={d.date} className="flex-1 flex flex-col items-center justify-end h-full gap-2">
              <span className="text-[11px] text-aegis-text-secondary">{d.tokens > 0 ? formatCompact(d.tokens) : ''}</span>
              <div
                className="w-full rounded-t-md bg-aegis-primary transition-all"
                style={{ height: `${heightPct}%` }}
                title={`${d.tokens.toLocaleString()} tokens`}
              />
              <span className="text-[11px] text-aegis-text-muted whitespace-nowrap">
                {new Date(d.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Donut({ segments, total }: { segments: { label: string; tokens: number }[]; total: number }) {
  const size = 140;
  const stroke = 22;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  let cumulative = 0;

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="flex-shrink-0 -rotate-90">
      {total === 0 ? (
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#E4E4E7" strokeWidth={stroke} />
      ) : (
        segments.map((seg, i) => {
          const fraction = seg.tokens / total;
          const dash = fraction * circumference;
          const offset = cumulative;
          cumulative += dash;
          return (
            <circle
              key={seg.label}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={SEGMENT_COLORS[i % SEGMENT_COLORS.length]}
              strokeWidth={stroke}
              strokeDasharray={`${dash} ${circumference - dash}`}
              strokeDashoffset={-offset}
            />
          );
        })
      )}
    </svg>
  );
}

function BreakdownCard({
  title,
  segments,
}: {
  title: string;
  segments: { label: string; tokens: number }[];
}) {
  const total = segments.reduce((sum, s) => sum + s.tokens, 0);
  return (
    <div className="bg-aegis-raised border border-aegis-border rounded-2xl p-6">
      <p className="text-[11px] font-semibold tracking-wider text-aegis-text-muted uppercase mb-6">{title}</p>
      {total === 0 ? (
        <p className="text-[13px] text-aegis-text-muted">No usage yet.</p>
      ) : (
        <div className="flex items-center gap-8">
          <div className="relative flex-shrink-0">
            <Donut segments={segments} total={total} />
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-lg font-bold text-aegis-text-primary">{formatCompact(total)}</span>
              <span className="text-[10px] text-aegis-text-muted uppercase tracking-wide">Tokens</span>
            </div>
          </div>
          <div className="flex-1 min-w-0 space-y-2.5">
            {segments.map((seg, i) => (
              <div key={seg.label} className="flex items-center justify-between gap-3 text-[13px]">
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                    style={{ backgroundColor: SEGMENT_COLORS[i % SEGMENT_COLORS.length] }}
                  />
                  <span className="text-aegis-text-secondary truncate">{seg.label}</span>
                </div>
                <span className="text-aegis-text-primary font-semibold flex-shrink-0">{formatCompact(seg.tokens)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function AnalyticsView() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAnalytics = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/analytics`);
      const json = await res.json();
      setData(json);
    } catch {
      // leave data null — render the empty state
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAnalytics();
    // Usage accrues as you chat elsewhere in the app — refresh periodically
    // rather than requiring a manual tab revisit.
    const interval = setInterval(fetchAnalytics, 15000);
    return () => clearInterval(interval);
  }, [fetchAnalytics]);

  return (
    <div className="flex-1 flex flex-col bg-aegis-base overflow-hidden">
      <div className="px-8 pt-8 pb-5">
        <div className="flex items-center gap-3 mb-1">
          <BarChart3 className="w-6 h-6 text-aegis-primary" />
          <h1 className="text-2xl font-bold text-aegis-text-primary">Analytics</h1>
        </div>
        <p className="text-sm text-aegis-text-secondary">Real token usage, computed from your local model's own tokenizer.</p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8 space-y-4">
        {loading ? (
          <p className="text-sm text-aegis-text-muted">Loading...</p>
        ) : !data ? (
          <p className="text-sm text-aegis-text-muted">Couldn't load analytics. Is the backend running?</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4">
              <StatCard
                label="Tokens Generated"
                value={formatCompact(data.tokens_generated)}
                subtitle="completion tokens, all sessions"
              />
              <StatCard
                label="Prompt Tokens Processed"
                value={formatCompact(data.prompt_tokens_processed)}
                subtitle="input tokens, all sessions"
              />
              <StatCard
                label="Estimated Saved"
                value={`$${data.estimated_saved_usd.toFixed(2)}`}
                subtitle="vs. typical cloud API pricing (est.)"
              />
              <StatCard
                label="Kept Fully Private"
                value={`${data.percent_local}%`}
                subtitle="of tokens processed on-device"
              />
            </div>

            <DailyBarChart daily={data.daily} />

            <div className="grid grid-cols-2 gap-4">
              <BreakdownCard
                title="By Model"
                segments={data.by_model.map(m => ({ label: m.model, tokens: m.tokens }))}
              />
              <BreakdownCard
                title="Tokens By Source"
                segments={data.by_source.map(s => ({ label: s.source, tokens: s.tokens }))}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
