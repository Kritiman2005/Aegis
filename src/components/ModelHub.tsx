'use client';

import React, { useState, useEffect } from 'react';
import { Search, Download, CheckCircle2, XCircle, ArrowDownToLine, Loader2, HardDrive, FileText, Activity } from 'lucide-react';
import { useSocket } from '../hooks/useSocket';

// --- Types ---
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

export default function ModelHub() {
  const [searchQuery, setSearchQuery] = useState('qwen');
  const [isSearching, setIsSearching] = useState(false);
  const [models, setModels] = useState<ModelResult[]>([]);
  
  // Local models tracking
  const [localModels, setLocalModels] = useState<Record<string, LocalModel>>({});
  const { status, addMessageHandler } = useSocket();

  // Selected model for files
  const [selectedRepo, setSelectedRepo] = useState<string | null>(null);
  const [repoFiles, setRepoFiles] = useState<GGUFFile[]>([]);
  const [isLoadingFiles, setIsLoadingFiles] = useState(false);

  // Download progress tracking (from WebSocket)
  const [progressData, setProgressData] = useState<Record<string, { progress: number, downloaded_bytes: number, total_bytes: number }>>({});

  // 1. Initial Load of Local Models
  useEffect(() => {
    fetchLocalModels();
  }, []);

  const fetchLocalModels = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/hub/downloaded');
      const data = await res.json();
      const mapping: Record<string, LocalModel> = {};
      data.models.forEach((m: LocalModel) => {
        mapping[`${m.repo_id}/${m.filename}`] = m;
      });
      setLocalModels(mapping);
    } catch (err) {
      console.error("Failed to fetch local models:", err);
    }
  };

  // 2. Initial Search
  useEffect(() => {
    handleSearch();
  }, []);

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setIsSearching(true);
    try {
      const res = await fetch(`http://localhost:8000/api/hub/search?q=${encodeURIComponent(searchQuery)}&limit=15`);
      const data = await res.json();
      setModels(data.models || []);
    } catch (err) {
      console.error(err);
    } finally {
      setIsSearching(false);
    }
  };

  // 3. WebSocket Listener for Progress
  useEffect(() => {
    const handler = (payload: any) => {
      const { type, repo_id, filename } = payload;
      const key = `${repo_id}/${filename}`;

      if (type === 'download_progress') {
        setProgressData((prev) => ({
          ...prev,
          [key]: {
            progress: payload.progress,
            downloaded_bytes: payload.downloaded_bytes,
            total_bytes: payload.total_bytes,
          }
        }));
      } else if (type === 'download_complete') {
        fetchLocalModels(); // Refresh status
        setProgressData((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
      } else if (type === 'download_failed') {
        fetchLocalModels();
        setProgressData((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
      }
    };

    const cleanup = addMessageHandler(handler);
    return cleanup;
  }, [addMessageHandler]);

  // 4. Fetch Files for Repo
  const handleSelectModel = async (repoId: string) => {
    if (selectedRepo === repoId) {
      setSelectedRepo(null);
      return;
    }
    setSelectedRepo(repoId);
    setRepoFiles([]);
    setIsLoadingFiles(true);
    try {
      const res = await fetch(`http://localhost:8000/api/hub/repo/${encodeURIComponent(repoId)}`);
      const data = await res.json();
      setRepoFiles(data.files || []);
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoadingFiles(false);
    }
  };

  // 5. Start Download
  const startDownload = async (repoId: string, filename: string) => {
    try {
      await fetch('http://localhost:8000/api/hub/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: repoId, filename })
      });
      // Optimistically update local models to show "downloading"
      setLocalModels(prev => ({
        ...prev,
        [`${repoId}/${filename}`]: {
          id: -1,
          repo_id: repoId,
          filename,
          status: 'downloading',
          file_size_bytes: 0
        }
      }));
    } catch (err) {
      console.error(err);
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  return (
    <div className="flex-1 flex flex-col bg-white overflow-hidden h-full">
      {/* Header */}
      <div className="p-8 pb-6 border-b border-gray-100 flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 tracking-tight flex items-center gap-2">
            <HardDrive className="w-6 h-6 text-indigo-600" />
            Model Hub
          </h2>
          <p className="text-sm text-gray-500 mt-1">Discover and download GGUF models from Hugging Face.</p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-8 space-y-8 bg-[#FDFDFD]">
        
        {/* Search Bar */}
        <div className="max-w-2xl">
          <div className="relative">
            <input
              type="text"
              placeholder="Search models (e.g. Llama-3, Qwen, Mistral)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="w-full pl-11 pr-4 py-3 bg-white border border-gray-200 rounded-2xl text-sm outline-none focus:border-indigo-500 focus:ring-4 focus:ring-indigo-50 transition-all shadow-sm"
            />
            <Search className="absolute left-4 top-3.5 w-4 h-4 text-gray-400" />
            <button 
              onClick={handleSearch}
              className="absolute right-2 top-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-1.5 rounded-xl text-xs font-semibold transition-all shadow-sm"
            >
              Search
            </button>
          </div>
        </div>

        {/* Loading State */}
        {isSearching && (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400 gap-3">
            <Loader2 className="w-8 h-8 animate-spin" />
            <span className="text-sm font-medium">Searching Hugging Face...</span>
          </div>
        )}

        {/* Results Grid */}
        {!isSearching && models.length > 0 && (
          <div className="grid grid-cols-1 gap-4 max-w-4xl">
            {models.map((model) => (
              <div key={model.id} className="bg-white border border-gray-200 rounded-2xl p-5 hover:border-gray-300 hover:shadow-md transition-all">
                <div className="flex justify-between items-start">
                  <div>
                    <h3 className="text-sm font-bold text-gray-900">{model.id}</h3>
                    <p className="text-xs text-gray-500 mt-1 flex items-center gap-4">
                      <span>By <strong className="text-gray-700">{model.author}</strong></span>
                      <span className="flex items-center gap-1"><ArrowDownToLine className="w-3 h-3"/> {model.downloads.toLocaleString()} downloads</span>
                      <span className="flex items-center gap-1"><Activity className="w-3 h-3"/> {model.likes.toLocaleString()} likes</span>
                    </p>
                  </div>
                  <button 
                    onClick={() => handleSelectModel(model.id)}
                    className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-semibold rounded-xl transition-all"
                  >
                    {selectedRepo === model.id ? 'Close Files' : 'View Files'}
                  </button>
                </div>

                {/* Expanded Files View */}
                {selectedRepo === model.id && (
                  <div className="mt-5 border-t border-gray-100 pt-5">
                    {isLoadingFiles ? (
                      <div className="flex items-center gap-2 text-xs text-gray-500">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" /> Fetching available GGUF files...
                      </div>
                    ) : repoFiles.length === 0 ? (
                      <p className="text-xs text-gray-500">No .gguf files found in this repository.</p>
                    ) : (
                      <div className="space-y-2">
                        {repoFiles.map((file) => {
                          const key = `${model.id}/${file.filename}`;
                          const local = localModels[key];
                          const prog = progressData[key];
                          
                          const isDownloaded = local?.status === 'downloaded';
                          const isDownloading = local?.status === 'downloading' || prog !== undefined;

                          return (
                            <div key={file.filename} className="flex items-center justify-between p-3 bg-gray-50 rounded-xl border border-gray-100">
                              <div className="flex items-center gap-3">
                                <FileText className="w-4 h-4 text-gray-400" />
                                <div>
                                  <p className="text-xs font-bold text-gray-800">{file.filename}</p>
                                  <p className="text-[10px] text-gray-500">{formatBytes(file.size)}</p>
                                </div>
                              </div>
                              
                              <div className="flex items-center gap-3 min-w-[150px] justify-end">
                                {isDownloaded ? (
                                  <span className="flex items-center gap-1 text-xs font-semibold text-teal-600 bg-teal-50 px-3 py-1.5 rounded-lg border border-teal-100">
                                    <CheckCircle2 className="w-3.5 h-3.5" /> Downloaded
                                  </span>
                                ) : isDownloading ? (
                                  <div className="flex flex-col items-end gap-1 w-32">
                                    <div className="flex items-center justify-between w-full text-[10px] font-semibold text-indigo-600">
                                      <span>Downloading...</span>
                                      <span>{prog?.progress.toFixed(1) || 0}%</span>
                                    </div>
                                    <div className="w-full bg-indigo-100 h-1.5 rounded-full overflow-hidden">
                                      <div 
                                        className="bg-indigo-600 h-full rounded-full transition-all duration-300"
                                        style={{ width: `${prog?.progress || 0}%` }}
                                      />
                                    </div>
                                    {prog && prog.total_bytes > 0 && (
                                      <span className="text-[9px] text-gray-400">
                                        {formatBytes(prog.downloaded_bytes)} / {formatBytes(prog.total_bytes)}
                                      </span>
                                    )}
                                  </div>
                                ) : (
                                  <button
                                    onClick={() => startDownload(model.id, file.filename)}
                                    className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 hover:border-indigo-400 hover:text-indigo-600 text-gray-600 text-xs font-semibold rounded-lg transition-all shadow-sm"
                                  >
                                    <Download className="w-3.5 h-3.5" /> Download
                                  </button>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
