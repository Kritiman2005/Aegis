'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Download,
  CheckCircle2,
  Loader2,
  ArrowDownToLine,
  Cpu,
  Search,
  X,
  Sparkles,
} from 'lucide-react';
import { useSocket } from '../hooks/useSocket';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ModelResult {
  id: string;
  author: string;
  downloads: number;
  likes: number;
  tags: string[];
  familyKey?: string;
}

interface GGUFFile {
  filename: string;
  size: number;
}

interface LocalModel {
  id: number;
  repo_id: string;
  filename: string;
  status: 'downloading' | 'downloaded' | 'failed';
  file_size_bytes: number;
}

interface ModelFamily {
  key: string;
  label: string;
  description: string;
  color: string;
  bg: string;
  initial: string;
  searchQuery: string;
}

// ── Model families ────────────────────────────────────────────────────────────

const FAMILIES: ModelFamily[] = [
  {
    key: 'qwen',
    label: 'Qwen',
    description: "Alibaba's high-performance multilingual models. Excellent reasoning, coding, and long-context tasks.",
    color: '#2563EB',
    bg: '#EFF6FF',
    initial: 'Q',
    searchQuery: 'Qwen2.5 GGUF',
  },
  {
    key: 'llama',
    label: 'Llama',
    description: "Meta's open LLaMA family. Industry standard for general-purpose tasks with broad community support.",
    color: '#7C3AED',
    bg: '#F5F3FF',
    initial: 'L',
    searchQuery: 'Llama-3 GGUF',
  },
  {
    key: 'mistral',
    label: 'Mistral',
    description: 'Efficient, fast, and capable models from Mistral AI. Great balance of quality and speed.',
    color: '#059669',
    bg: '#ECFDF5',
    initial: 'M',
    searchQuery: 'Mistral GGUF',
  },
  {
    key: 'phi',
    label: 'Phi',
    description: "Microsoft's small language models. Exceptionally capable at coding and reasoning for their size.",
    color: '#D97706',
    bg: '#FFFBEB',
    initial: 'P',
    searchQuery: 'Phi-3 GGUF',
  },
];

const FAMILY_BY_KEY: Record<string, ModelFamily> = Object.fromEntries(FAMILIES.map(f => [f.key, f]));

// Best-effort family guess for free-text search results, which don't already
// carry a familyKey — matched against the repo id/author so cards still get
// a color, initial, and description instead of falling back to generic gray.
function guessFamily(model: ModelResult): ModelFamily {
  const hay = `${model.id} ${model.author}`.toLowerCase();
  return FAMILIES.find(f => hay.includes(f.key)) || {
    key: 'other',
    label: model.author || 'Other',
    description: 'A GGUF-quantized model compatible with local inference.',
    color: '#64748B',
    bg: '#F1F5F9',
    initial: (model.author || model.id || '?').charAt(0).toUpperCase(),
    searchQuery: '',
  };
}

// Pull a human parameter-count badge ("7B", "1.5B") out of a repo id — HF
// search results don't return structured param counts, but nearly every
// GGUF repo name encodes it (e.g. "Qwen2.5-7B-Instruct-GGUF").
function parseParamSize(repoId: string): string | null {
  const m = repoId.match(/(\d+(?:\.\d+)?)\s*[Bb](?![a-zA-Z])/);
  return m ? `${m[1]}B params` : null;
}

// Some repos split a large quant (typically fp16) across multiple GGUF
// shards, e.g. "...-fp16-00001-of-00002.gguf". The current single-file
// download endpoint can't assemble shards back into a working model (see
// model_catalog.py's own note on why the curated catalog avoids these), so
// a lone shard downloads successfully but then fails to load — hide them
// from the quant list entirely rather than let a user (or the "Recommended"
// heuristic) pick an unusable file.
const SHARD_FILE_PATTERN = /-\d{5}-of-\d{5}\.gguf$/i;

function cleanTitle(repoId: string): string {
  const name = repoId.split('/').pop() || repoId;
  return name.replace(/-GGUF$/i, '');
}

const formatBytes = (bytes: number) => {
  if (!bytes || bytes === 0) return '—';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const formatDownloads = (n: number) => {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K';
  return String(n);
};

// A quant needs roughly 1.3x its file size in RAM once you account for the
// KV cache and runtime overhead — this mirrors the headroom baked into each
// model_catalog.py tier's min_ram_gb, just applied per-file instead of per
// curated entry. Leaving ~2.5GB for the OS + Electron avoids recommending
// something that technically "fits" but leaves no room to actually run it.
function fitsComfortably(fileSizeBytes: number, ramTotalGb: number): boolean {
  const fileGb = fileSizeBytes / (1024 ** 3);
  return fileGb * 1.3 <= ramTotalGb - 2.5;
}

// ── Quantization info ─────────────────────────────────────────────────────────
// GGUF filenames encode the quantization scheme (Q4_K_M, Q8_0, etc.) but say
// nothing about what that actually trades off — this is the same reference
// table LM Studio effectively shows next to each quant option, so a user can
// tell "smaller and rougher" from "bigger and closer to full quality" without
// already knowing llama.cpp's naming conventions. `quality` is a 1-5 scale
// used to render a simple filled/empty dot bar.
interface QuantInfo {
  bits: string;
  quality: number;
  label: string;
  description: string;
}

const QUANT_INFO: { pattern: RegExp; info: QuantInfo }[] = [
  { pattern: /q2_k/i, info: { bits: '2-bit', quality: 1, label: 'Smallest', description: 'Maximum compression. Quality loss is clearly noticeable — only worth it when RAM is extremely tight.' } },
  { pattern: /q3_k_s/i, info: { bits: '3-bit', quality: 1, label: 'Very small', description: 'Very small and fast, but quality loss is still noticeable in reasoning and coding tasks.' } },
  { pattern: /q3_k_m|q3_k_l|q3_k/i, info: { bits: '3-bit', quality: 2, label: 'Small', description: 'Small file with moderate quality loss — a step up from the smallest quants.' } },
  { pattern: /q4_0|q4_1/i, info: { bits: '4-bit', quality: 2, label: 'Legacy 4-bit', description: 'Older 4-bit format, mostly superseded by the K-quants below — kept for older llama.cpp compatibility.' } },
  { pattern: /q4_k_s/i, info: { bits: '4-bit', quality: 3, label: 'Balanced', description: 'A solid balance of size and quality for everyday use.' } },
  { pattern: /q4_k_m|q4_k/i, info: { bits: '4-bit', quality: 3, label: 'Recommended balance', description: 'The sweet spot most people should start with — strong quality at a moderate file size.' } },
  { pattern: /q5_0|q5_1/i, info: { bits: '5-bit', quality: 3, label: 'Legacy 5-bit', description: 'Older 5-bit format, mostly superseded by the K-quants below.' } },
  { pattern: /q5_k_s|q5_k_m|q5_k/i, info: { bits: '5-bit', quality: 4, label: 'High quality', description: 'Close to the full model\'s quality with a moderate size increase over 4-bit.' } },
  { pattern: /q6_k/i, info: { bits: '6-bit', quality: 4, label: 'Near-lossless', description: 'Very close to full precision, noticeably larger — a good choice if you have RAM to spare.' } },
  { pattern: /q8_0/i, info: { bits: '8-bit', quality: 5, label: 'Highest quality', description: 'The largest practical quantization — virtually indistinguishable from the unquantized model.' } },
  { pattern: /(^|[^a-z0-9])f16($|[^a-z0-9])|fp16/i, info: { bits: '16-bit', quality: 5, label: 'Full precision', description: 'Unquantized half-precision weights — largest file, reference quality, no compression trade-off.' } },
  { pattern: /(^|[^a-z0-9])f32($|[^a-z0-9])|fp32/i, info: { bits: '32-bit', quality: 5, label: 'Full precision', description: 'Full 32-bit precision weights — very large, rarely necessary for local inference.' } },
];

function getQuantInfo(filename: string): QuantInfo | null {
  const match = QUANT_INFO.find(q => q.pattern.test(filename));
  return match ? match.info : null;
}

function QualityDots({ quality }: { quality: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" title={`Quality: ${quality}/5`}>
      {[1, 2, 3, 4, 5].map(i => (
        <span
          key={i}
          className={`w-1.5 h-1.5 rounded-full ${i <= quality ? 'bg-aegis-primary' : 'bg-aegis-border'}`}
        />
      ))}
    </span>
  );
}

// ── File Row ──────────────────────────────────────────────────────────────────

function FileRow({
  file,
  repoId,
  localModels,
  progressData,
  onDownload,
  recommended,
}: {
  file: GGUFFile;
  repoId: string;
  localModels: Record<string, LocalModel>;
  progressData: Record<string, { progress: number; downloaded_bytes: number; total_bytes: number }>;
  onDownload: (repoId: string, filename: string) => void;
  recommended?: boolean;
}) {
  const key = `${repoId}/${file.filename}`;
  const local = localModels[key];
  const prog = progressData[key];
  const isDownloaded = local?.status === 'downloaded';
  const isDownloading = local?.status === 'downloading' || prog !== undefined;
  const quantInfo = getQuantInfo(file.filename);

  return (
    <div className={`py-2.5 px-3 bg-aegis-overlay rounded-xl border transition-colors ${recommended ? 'border-aegis-primary/50' : 'border-aegis-border hover:border-aegis-primary/30'}`}>
      <div className="flex items-center justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-xs font-semibold text-aegis-text-primary truncate">{file.filename}</p>
            {recommended && (
              <span className="flex items-center gap-1 text-[10px] font-semibold text-aegis-primary-light bg-aegis-primary/10 border border-aegis-primary/30 px-1.5 py-0.5 rounded-full flex-shrink-0">
                <Sparkles className="w-2.5 h-2.5" /> Recommended for your RAM
              </span>
            )}
          </div>
          <p className="text-[11px] text-aegis-text-muted mt-0.5">{formatBytes(file.size)}</p>
        </div>

        <div className="ml-4 flex-shrink-0">
          {isDownloaded ? (
            <span className="flex items-center gap-1.5 text-xs font-semibold text-aegis-success bg-aegis-success/10 border border-aegis-success/30 px-3 py-1.5 rounded-lg">
              <CheckCircle2 className="w-3.5 h-3.5" /> Downloaded
            </span>
          ) : isDownloading ? (
            <div className="flex flex-col items-end gap-1 min-w-[110px]">
              <div className="flex items-center justify-between w-full text-[11px] font-semibold text-aegis-primary-light">
                <span>Downloading...</span>
                <span>{(prog?.progress || 0).toFixed(0)}%</span>
              </div>
              <div className="w-full bg-aegis-overlay h-1.5 rounded-full overflow-hidden">
                <div
                  className="bg-aegis-primary h-full rounded-full transition-all duration-300"
                  style={{ width: `${prog?.progress || 0}%` }}
                />
              </div>
              {prog && prog.total_bytes > 0 && (
                <span className="text-[10px] text-aegis-text-muted">
                  {formatBytes(prog.downloaded_bytes)} / {formatBytes(prog.total_bytes)}
                </span>
              )}
            </div>
          ) : (
            <button
              onClick={() => onDownload(repoId, file.filename)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-aegis-raised border border-aegis-border hover:border-aegis-primary hover:text-aegis-primary-light text-aegis-text-secondary text-xs font-semibold rounded-lg transition-all"
            >
              <Download className="w-3.5 h-3.5" /> Download
            </button>
          )}
        </div>
      </div>

      {quantInfo && (
        <div className="mt-2.5 pt-2.5 border-t border-aegis-border/60 flex items-start gap-2 flex-wrap">
          <span className="text-[10px] font-semibold text-aegis-text-secondary bg-aegis-raised border border-aegis-border px-1.5 py-0.5 rounded-full flex-shrink-0">
            {quantInfo.bits}
          </span>
          <span className="text-[10px] font-semibold text-aegis-text-secondary bg-aegis-raised border border-aegis-border px-1.5 py-0.5 rounded-full flex-shrink-0">
            {quantInfo.label}
          </span>
          <QualityDots quality={quantInfo.quality} />
          <p className="text-[11px] text-aegis-text-muted leading-snug flex-1 min-w-[160px]">{quantInfo.description}</p>
        </div>
      )}
    </div>
  );
}

// ── Model Detail Modal ───────────────────────────────────────────────────────
// The per-model "nice description" panel — model identity, a short blurb
// (family blurb + parsed param size), and the full quant list with a
// RAM-aware "Recommended" badge, closer to how LM Studio presents a model's
// available quantizations rather than the old bare filename/size list.

function ModelDetailModal({
  model,
  family,
  ramTotalGb,
  localModels,
  progressData,
  onDownload,
  onClose,
}: {
  model: ModelResult;
  family: ModelFamily;
  ramTotalGb: number | null;
  localModels: Record<string, LocalModel>;
  progressData: Record<string, { progress: number; downloaded_bytes: number; total_bytes: number }>;
  onDownload: (repoId: string, filename: string) => void;
  onClose: () => void;
}) {
  const [files, setFiles] = useState<GGUFFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErrorMsg(null);
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hub/repo/${encodeURIComponent(model.id)}`)
      .then(async r => {
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || 'Failed to load quantizations');
        return data;
      })
      .then(data => setFiles(data.files || []))
      .catch(err => setErrorMsg(err.message || 'Failed to load quantizations'))
      .finally(() => setLoading(false));
  }, [model.id]);

  const visibleFiles = useMemo(() => files.filter(f => !SHARD_FILE_PATTERN.test(f.filename)), [files]);

  // Recommend the largest (best-quality) quant that still comfortably fits
  // this machine's RAM — same "biggest that fits" logic as model_catalog.py's
  // recommend_model, just evaluated per real file size instead of a fixed tier.
  const recommendedFilename = useMemo(() => {
    if (!ramTotalGb || visibleFiles.length === 0) return null;
    const fitting = visibleFiles.filter(f => fitsComfortably(f.size, ramTotalGb));
    if (fitting.length === 0) return null;
    return fitting.reduce((best, f) => (f.size > best.size ? f : best), fitting[0]).filename;
  }, [visibleFiles, ramTotalGb]);

  const paramSize = parseParamSize(model.id);

  return (
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 animate-fade-in" onClick={onClose}>
      <div
        className="bg-aegis-raised rounded-2xl border border-aegis-border shadow-2xl max-w-xl w-full max-h-[85vh] overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="p-6 border-b border-aegis-border flex items-start justify-between flex-shrink-0">
          <div className="flex items-start gap-3 min-w-0">
            <div
              className="w-11 h-11 rounded-2xl flex items-center justify-center text-base font-black flex-shrink-0"
              style={{ background: family.bg, color: family.color }}
            >
              {family.initial}
            </div>
            <div className="min-w-0">
              <h3 className="text-base font-bold text-aegis-text-primary break-words">{cleanTitle(model.id)}</h3>
              <p className="text-xs text-aegis-text-muted mt-0.5">by {model.author}</p>
              <div className="flex items-center gap-2 flex-wrap mt-2">
                <span
                  className="text-[11px] font-bold px-2 py-0.5 rounded-full"
                  style={{ background: family.bg, color: family.color }}
                >
                  {family.label}
                </span>
                {paramSize && (
                  <span className="text-[11px] font-semibold text-aegis-text-secondary bg-aegis-overlay border border-aegis-border px-2 py-0.5 rounded-full">
                    {paramSize}
                  </span>
                )}
                <span className="text-[11px] text-aegis-text-muted flex items-center gap-1">
                  <ArrowDownToLine className="w-3 h-3" /> {formatDownloads(model.downloads)}
                </span>
                <span className="text-[11px] text-aegis-text-muted">❤ {formatDownloads(model.likes)}</span>
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-aegis-text-muted hover:text-aegis-text-primary hover:bg-aegis-overlay transition-colors flex-shrink-0"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 overflow-y-auto space-y-4">
          <p className="text-xs text-aegis-text-secondary leading-relaxed">{family.description}</p>

          <div>
            <p className="text-xs font-semibold text-aegis-text-primary mb-2">Available quantizations</p>
            {loading ? (
              <div className="flex items-center gap-2 text-xs text-aegis-text-secondary py-4 justify-center">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Fetching GGUF files...
              </div>
            ) : errorMsg ? (
              <div className="text-xs text-aegis-error text-center py-4 bg-aegis-error/10 rounded-lg border border-aegis-error/30">
                {errorMsg}
              </div>
            ) : visibleFiles.length === 0 ? (
              <p className="text-xs text-aegis-text-muted py-2">No single-file .gguf quantizations found.</p>
            ) : (
              <div className="space-y-2">
                {visibleFiles.map(f => (
                  <FileRow
                    key={f.filename}
                    file={f}
                    repoId={model.id}
                    localModels={localModels}
                    progressData={progressData}
                    onDownload={onDownload}
                    recommended={f.filename === recommendedFilename}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Model Card (grid item) ───────────────────────────────────────────────────

function ModelCard({
  model,
  family,
  localModels,
  onOpen,
}: {
  model: ModelResult;
  family: ModelFamily;
  localModels: Record<string, LocalModel>;
  onOpen: () => void;
}) {
  const hasDownload = Object.keys(localModels).some(k => k.startsWith(model.id + '/') && localModels[k].status === 'downloaded');
  const paramSize = parseParamSize(model.id);

  return (
    <button
      onClick={onOpen}
      className="text-left bg-aegis-raised rounded-2xl border border-aegis-border hover:border-aegis-primary/40 transition-all p-5 flex flex-col gap-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div
            className="w-10 h-10 rounded-2xl flex items-center justify-center text-base font-black flex-shrink-0"
            style={{ background: family.bg, color: family.color }}
          >
            {family.initial}
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-bold text-aegis-text-primary truncate">{cleanTitle(model.id)}</h4>
            <p className="text-[11px] text-aegis-text-muted truncate">by {model.author}</p>
          </div>
        </div>
        {hasDownload && (
          <span className="flex items-center gap-1 text-[10px] font-semibold text-aegis-success bg-aegis-success/10 border border-aegis-success/30 px-2 py-0.5 rounded-full flex-shrink-0">
            <CheckCircle2 className="w-3 h-3" /> Downloaded
          </span>
        )}
      </div>

      <p className="text-xs text-aegis-text-secondary leading-relaxed line-clamp-2">{family.description}</p>

      <div className="flex items-center justify-between mt-auto pt-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span
            className="text-[10px] font-bold px-2 py-0.5 rounded-full"
            style={{ background: family.bg, color: family.color }}
          >
            {family.label}
          </span>
          {paramSize && (
            <span className="text-[10px] font-semibold text-aegis-text-secondary bg-aegis-overlay border border-aegis-border px-2 py-0.5 rounded-full">
              {paramSize}
            </span>
          )}
        </div>
        <span className="text-[11px] text-aegis-text-muted flex items-center gap-1 flex-shrink-0">
          <ArrowDownToLine className="w-3 h-3" /> {formatDownloads(model.downloads)}
        </span>
      </div>
    </button>
  );
}

// ── Main ModelHub ─────────────────────────────────────────────────────────────

export default function ModelHub() {
  const { addMessageHandler } = useSocket();
  const [localModels, setLocalModels] = useState<Record<string, LocalModel>>({});
  const [progressData, setProgressData] = useState<Record<string, { progress: number; downloaded_bytes: number; total_bytes: number }>>({});
  const [ramTotalGb, setRamTotalGb] = useState<number | null>(null);

  const [activeFamily, setActiveFamily] = useState<string>('all');
  const [familyModels, setFamilyModels] = useState<Record<string, ModelResult[]>>({});
  const [familyLoading, setFamilyLoading] = useState<Record<string, boolean>>({});
  const [familyError, setFamilyError] = useState<Record<string, string | null>>({});

  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<ModelResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [selectedModel, setSelectedModel] = useState<{ model: ModelResult; family: ModelFamily } | null>(null);

  const fetchLocalModels = useCallback(async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hub/downloaded`);
      const data = await res.json();
      const mapping: Record<string, LocalModel> = {};
      (data.models || []).forEach((m: LocalModel) => {
        mapping[`${m.repo_id}/${m.filename}`] = m;
      });
      setLocalModels(mapping);
      return mapping;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => { fetchLocalModels(); }, [fetchLocalModels]);

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hardware/status`)
      .then(r => r.json())
      .then(data => setRamTotalGb(data.ram_total_gb || null))
      .catch(() => {});
  }, []);

  // WebSocket progress
  useEffect(() => {
    return addMessageHandler((payload: any) => {
      const { type, repo_id, filename } = payload;
      const key = `${repo_id}/${filename}`;
      if (type === 'download_progress') {
        setProgressData(prev => ({
          ...prev,
          [key]: { progress: payload.progress, downloaded_bytes: payload.downloaded_bytes, total_bytes: payload.total_bytes },
        }));
      } else if (type === 'download_complete') {
        setProgressData(prev => { const n = { ...prev }; delete n[key]; return n; });
        fetchLocalModels().then(mapping => {
          const model = mapping?.[key];
          if (!model) return;
          fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hardware/load`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_id: model.id }),
          }).catch(() => {});
        });
      } else if (type === 'download_failed') {
        fetchLocalModels();
        setProgressData(prev => { const n = { ...prev }; delete n[key]; return n; });
      }
    });
  }, [addMessageHandler, fetchLocalModels]);

  const startDownload = async (repoId: string, filename: string) => {
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hub/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: repoId, filename }),
      });
      setLocalModels(prev => ({
        ...prev,
        [`${repoId}/${filename}`]: { id: -1, repo_id: repoId, filename, status: 'downloading', file_size_bytes: 0 },
      }));
    } catch {}
  };

  // Fetch a family's model list on first selection (or on first render for
  // "All", which fans out across every family) — cached per family so
  // switching chips back and forth doesn't re-hit the network.
  const loadFamily = useCallback((familyKey: string) => {
    if (familyModels[familyKey] || familyLoading[familyKey]) return;
    const family = FAMILY_BY_KEY[familyKey];
    if (!family) return;
    setFamilyLoading(prev => ({ ...prev, [familyKey]: true }));
    setFamilyError(prev => ({ ...prev, [familyKey]: null }));
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hub/search?q=${encodeURIComponent(family.searchQuery)}&limit=8`)
      .then(async r => {
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || 'API Error');
        return data;
      })
      .then(data => {
        const models: ModelResult[] = (data.models || []).map((m: ModelResult) => ({ ...m, familyKey }));
        setFamilyModels(prev => ({ ...prev, [familyKey]: models }));
      })
      .catch(err => setFamilyError(prev => ({ ...prev, [familyKey]: err.message || 'Failed to fetch models' })))
      .finally(() => setFamilyLoading(prev => ({ ...prev, [familyKey]: false })));
  }, [familyModels, familyLoading]);

  useEffect(() => {
    if (activeFamily === 'all') {
      FAMILIES.forEach(f => loadFamily(f.key));
    } else {
      loadFamily(activeFamily);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeFamily]);

  // Debounced free-text search — takes over the grid whenever non-empty,
  // querying HF directly instead of the fixed per-family queries.
  useEffect(() => {
    const trimmed = searchInput.trim();
    const t = setTimeout(() => setSearchQuery(trimmed), 400);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    if (!searchQuery) { setSearchResults([]); setSearchError(null); return; }
    setSearching(true);
    setSearchError(null);
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hub/search?q=${encodeURIComponent(searchQuery + ' GGUF')}&limit=20`)
      .then(async r => {
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || 'API Error');
        return data;
      })
      .then(data => setSearchResults(data.models || []))
      .catch(err => setSearchError(err.message || 'Search failed'))
      .finally(() => setSearching(false));
  }, [searchQuery]);

  const isSearching = searchQuery.length > 0;

  const displayedModels: ModelResult[] = isSearching
    ? searchResults
    : activeFamily === 'all'
      ? FAMILIES.flatMap(f => familyModels[f.key] || [])
      : (familyModels[activeFamily] || []);

  const loadingGrid = isSearching
    ? searching
    : activeFamily === 'all'
      ? FAMILIES.some(f => familyLoading[f.key])
      : !!familyLoading[activeFamily];

  const gridError = isSearching ? searchError : (activeFamily !== 'all' ? familyError[activeFamily] : null);

  return (
    <div className="flex-1 flex flex-col bg-aegis-base overflow-hidden">
      {/* Header */}
      <div className="px-8 pt-8 pb-5">
        <div className="flex items-center gap-3 mb-1">
          <Cpu className="w-6 h-6 text-aegis-primary" />
          <h1 className="text-2xl font-bold text-aegis-text-primary">LLMs</h1>
        </div>
        <p className="text-sm text-aegis-text-secondary">Browse and download GGUF models by family. Models run 100% locally.</p>
      </div>

      {/* Search */}
      <div className="px-8 pb-5">
        <div className="relative">
          <Search className="w-4 h-4 text-aegis-text-muted absolute left-4 top-1/2 -translate-y-1/2" />
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            placeholder="Search models..."
            className="w-full bg-aegis-raised border border-aegis-border rounded-2xl pl-11 pr-4 py-3 text-sm text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:border-aegis-primary/50 transition-colors"
          />
        </div>
      </div>

      {/* Family filter chips */}
      {!isSearching && (
        <div className="px-8 pb-5 flex items-center gap-2 flex-wrap">
          <button
            onClick={() => setActiveFamily('all')}
            className={`px-4 py-2 rounded-full text-sm font-semibold transition-colors ${activeFamily === 'all' ? 'bg-aegis-primary text-white' : 'bg-aegis-raised border border-aegis-border text-aegis-text-secondary hover:text-aegis-text-primary'}`}
          >
            All families
          </button>
          {FAMILIES.map(f => (
            <button
              key={f.key}
              onClick={() => setActiveFamily(f.key)}
              className={`px-4 py-2 rounded-full text-sm font-semibold transition-colors ${activeFamily === f.key ? 'bg-aegis-primary text-white' : 'bg-aegis-raised border border-aegis-border text-aegis-text-secondary hover:text-aegis-text-primary'}`}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}

      {/* Grid */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {loadingGrid && displayedModels.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-aegis-text-secondary py-12 justify-center">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading models...
          </div>
        ) : gridError ? (
          <div className="text-sm text-aegis-error text-center py-12 bg-aegis-error/10 rounded-lg border border-aegis-error/30">
            <p className="font-semibold">Error loading models</p>
            <p className="text-xs mt-1">{gridError}</p>
          </div>
        ) : displayedModels.length === 0 ? (
          <p className="text-sm text-aegis-text-muted text-center py-12">No models found.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {displayedModels.map(model => {
              const family = model.familyKey ? FAMILY_BY_KEY[model.familyKey] || guessFamily(model) : guessFamily(model);
              return (
                <ModelCard
                  key={model.id}
                  model={model}
                  family={family}
                  localModels={localModels}
                  onOpen={() => setSelectedModel({ model, family })}
                />
              );
            })}
          </div>
        )}
      </div>

      {selectedModel && (
        <ModelDetailModal
          model={selectedModel.model}
          family={selectedModel.family}
          ramTotalGb={ramTotalGb}
          localModels={localModels}
          progressData={progressData}
          onDownload={startDownload}
          onClose={() => setSelectedModel(null)}
        />
      )}
    </div>
  );
}
