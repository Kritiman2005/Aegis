'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  Download,
  CheckCircle2,
  Loader2,
  ChevronDown,
  ChevronRight,
  ArrowDownToLine,
  Cpu,
} from 'lucide-react';
import { useSocket } from '../hooks/useSocket';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ModelResult {
  id: string;
  author: string;
  downloads: number;
  likes: number;
  tags: string[];
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

// ── File Row ──────────────────────────────────────────────────────────────────

function FileRow({
  file,
  repoId,
  localModels,
  progressData,
  onDownload,
}: {
  file: GGUFFile;
  repoId: string;
  localModels: Record<string, LocalModel>;
  progressData: Record<string, { progress: number; downloaded_bytes: number; total_bytes: number }>;
  onDownload: (repoId: string, filename: string) => void;
}) {
  const key = `${repoId}/${file.filename}`;
  const local = localModels[key];
  const prog = progressData[key];
  const isDownloaded = local?.status === 'downloaded';
  const isDownloading = local?.status === 'downloading' || prog !== undefined;

  return (
    <div className="flex items-center justify-between py-2.5 px-3 bg-gray-50 rounded-xl border border-gray-100 hover:border-gray-200 transition-colors">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold text-gray-800 truncate">{file.filename}</p>
        <p className="text-[11px] text-gray-400 mt-0.5">{formatBytes(file.size)}</p>
      </div>

      <div className="ml-4 flex-shrink-0">
        {isDownloaded ? (
          <span className="flex items-center gap-1.5 text-xs font-semibold text-green-600 bg-green-50 border border-green-100 px-3 py-1.5 rounded-lg">
            <CheckCircle2 className="w-3.5 h-3.5" /> Downloaded
          </span>
        ) : isDownloading ? (
          <div className="flex flex-col items-end gap-1 min-w-[110px]">
            <div className="flex items-center justify-between w-full text-[11px] font-semibold text-[#5B50F0]">
              <span>Downloading...</span>
              <span>{(prog?.progress || 0).toFixed(0)}%</span>
            </div>
            <div className="w-full bg-indigo-100 h-1.5 rounded-full overflow-hidden">
              <div
                className="bg-[#5B50F0] h-full rounded-full transition-all duration-300"
                style={{ width: `${prog?.progress || 0}%` }}
              />
            </div>
            {prog && prog.total_bytes > 0 && (
              <span className="text-[10px] text-gray-400">
                {formatBytes(prog.downloaded_bytes)} / {formatBytes(prog.total_bytes)}
              </span>
            )}
          </div>
        ) : (
          <button
            onClick={() => onDownload(repoId, file.filename)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 hover:border-[#5B50F0] hover:text-[#5B50F0] text-gray-600 text-xs font-semibold rounded-lg transition-all shadow-sm"
          >
            <Download className="w-3.5 h-3.5" /> Download
          </button>
        )}
      </div>
    </div>
  );
}

// ── Model Card ────────────────────────────────────────────────────────────────

function ModelCard({
  model,
  localModels,
  progressData,
  onDownload,
  family,
}: {
  model: ModelResult;
  localModels: Record<string, LocalModel>;
  progressData: Record<string, { progress: number; downloaded_bytes: number; total_bytes: number }>;
  onDownload: (repoId: string, filename: string) => void;
  family: ModelFamily;
}) {
  const [expanded, setExpanded] = useState(false);
  const [files, setFiles] = useState<GGUFFile[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);

  const handleExpand = async () => {
    if (expanded) { setExpanded(false); return; }
    setExpanded(true);
    if (files.length > 0) return;
    setLoadingFiles(true);
    try {
      const res = await fetch(`http://localhost:8000/api/hub/repo/${encodeURIComponent(model.id)}`);
      const data = await res.json();
      setFiles(data.files || []);
    } catch { }
    finally { setLoadingFiles(false); }
  };

  // Check if any file is downloaded
  const hasDownload = Object.keys(localModels).some(k => k.startsWith(model.id + '/') && localModels[k].status === 'downloaded');

  return (
    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden hover:border-gray-300 transition-all hover:shadow-sm">
      <button
        onClick={handleExpand}
        className="w-full flex items-start justify-between p-5 text-left"
      >
        <div className="flex items-start gap-3 flex-1 min-w-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h4 className="text-sm font-bold text-gray-900 truncate">{model.id}</h4>
              {hasDownload && (
                <span className="text-[10px] font-semibold text-green-600 bg-green-50 border border-green-100 px-2 py-0.5 rounded-full">
                  ✓ Available
                </span>
              )}
            </div>
            <p className="text-xs text-gray-400 mt-0.5 flex items-center gap-3">
              <span>by <strong className="text-gray-600">{model.author}</strong></span>
              <span className="flex items-center gap-1">
                <ArrowDownToLine className="w-3 h-3" /> {formatDownloads(model.downloads)}
              </span>
              <span>❤ {formatDownloads(model.likes)}</span>
            </p>
          </div>
        </div>
        <div className="ml-3 flex-shrink-0 mt-0.5">
          {expanded
            ? <ChevronDown className="w-4 h-4 text-gray-400" />
            : <ChevronRight className="w-4 h-4 text-gray-400" />
          }
        </div>
      </button>

      {expanded && (
        <div className="px-5 pb-5 border-t border-gray-100 pt-4 space-y-2">
          {loadingFiles ? (
            <div className="flex items-center gap-2 text-xs text-gray-500 py-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Fetching GGUF files...
            </div>
          ) : files.length === 0 ? (
            <p className="text-xs text-gray-400 py-2">No .gguf files found.</p>
          ) : (
            files.map(f => (
              <FileRow
                key={f.filename}
                file={f}
                repoId={model.id}
                localModels={localModels}
                progressData={progressData}
                onDownload={onDownload}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── Family Section ────────────────────────────────────────────────────────────

function FamilySection({
  family,
  localModels,
  progressData,
  onDownload,
}: {
  family: ModelFamily;
  localModels: Record<string, LocalModel>;
  progressData: Record<string, { progress: number; downloaded_bytes: number; total_bytes: number }>;
  onDownload: (repoId: string, filename: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const [models, setModels] = useState<ModelResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (open && !loaded) {
      setLoading(true);
      fetch(`http://localhost:8000/api/hub/search?q=${encodeURIComponent(family.searchQuery)}&limit=8`)
        .then(r => r.json())
        .then(data => { setModels(data.models || []); setLoaded(true); })
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }, [open, loaded, family.searchQuery]);

  return (
    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
      {/* Family Header */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between p-5 hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-4">
          <div
            className="w-10 h-10 rounded-2xl flex items-center justify-center text-base font-black"
            style={{ background: family.bg, color: family.color }}
          >
            {family.initial}
          </div>
          <div className="text-left">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold text-gray-900">{family.label}</h3>
              {models.length > 0 && (
                <span
                  className="text-[11px] font-bold px-2 py-0.5 rounded-full"
                  style={{ background: family.bg, color: family.color }}
                >
                  {models.length} models
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500 mt-0.5 max-w-lg">{family.description}</p>
          </div>
        </div>
        {open
          ? <ChevronDown className="w-5 h-5 text-gray-400 flex-shrink-0" />
          : <ChevronRight className="w-5 h-5 text-gray-400 flex-shrink-0" />
        }
      </button>

      {/* Models List */}
      {open && (
        <div className="border-t border-gray-100 p-5 space-y-3">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-4 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading {family.label} models...
            </div>
          ) : models.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-4">No models found.</p>
          ) : (
            models.map(model => (
              <ModelCard
                key={model.id}
                model={model}
                localModels={localModels}
                progressData={progressData}
                onDownload={onDownload}
                family={family}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── Main ModelHub ─────────────────────────────────────────────────────────────

export default function ModelHub() {
  const { addMessageHandler } = useSocket();
  const [localModels, setLocalModels] = useState<Record<string, LocalModel>>({});
  const [progressData, setProgressData] = useState<Record<string, { progress: number; downloaded_bytes: number; total_bytes: number }>>({});

  const fetchLocalModels = useCallback(async () => {
    try {
      const res = await fetch('http://localhost:8000/api/hub/downloaded');
      const data = await res.json();
      const mapping: Record<string, LocalModel> = {};
      (data.models || []).forEach((m: LocalModel) => {
        mapping[`${m.repo_id}/${m.filename}`] = m;
      });
      setLocalModels(mapping);
    } catch {}
  }, []);

  useEffect(() => { fetchLocalModels(); }, [fetchLocalModels]);

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
      } else if (type === 'download_complete' || type === 'download_failed') {
        fetchLocalModels();
        setProgressData(prev => { const n = { ...prev }; delete n[key]; return n; });
      }
    });
  }, [addMessageHandler, fetchLocalModels]);

  const startDownload = async (repoId: string, filename: string) => {
    try {
      await fetch('http://localhost:8000/api/hub/download', {
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

  return (
    <div className="flex-1 flex flex-col bg-[#F4F5F7] overflow-hidden">
      {/* Header */}
      <div className="px-8 pt-8 pb-5 bg-[#F4F5F7]">
        <div className="flex items-center gap-3 mb-1">
          <Cpu className="w-6 h-6 text-[#5B50F0]" />
          <h1 className="text-2xl font-bold text-gray-900">LLMs</h1>
        </div>
        <p className="text-sm text-gray-500">Browse and download GGUF models by family. Models run 100% locally.</p>
      </div>

      {/* Family Sections */}
      <div className="flex-1 overflow-y-auto px-8 pb-8 space-y-4">
        {FAMILIES.map(family => (
          <FamilySection
            key={family.key}
            family={family}
            localModels={localModels}
            progressData={progressData}
            onDownload={startDownload}
          />
        ))}
      </div>
    </div>
  );
}
