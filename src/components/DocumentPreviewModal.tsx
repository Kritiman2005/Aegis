'use client';

import { useEffect, useState } from 'react';
import { X, Loader2, AlertCircle, Download } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface DocumentPreviewModalProps {
  documentId: number;
  filename: string;
  fileType: string;
  onClose: () => void;
}

// Claude/ChatGPT-style click-to-preview for an attached file: native browser
// rendering for what it can handle (PDF via <iframe>, images via <img>),
// extracted-text fallback (via /text) for everything else we support
// (DOCX/XLSX/PPTX/CSV/TXT/MD) — same extract_text() the RAG pipeline uses,
// so what's shown here is exactly what the model can actually search over.
// Deliberately doesn't take a status/errorMessage prop — /text already
// returns a clean 409/422 with the real reason for a not-ready or failed
// document, so there's no need to duplicate that state from the caller.
export default function DocumentPreviewModal({
  documentId,
  filename,
  fileType,
  onClose,
}: DocumentPreviewModalProps) {
  const ext = fileType.toLowerCase();
  const isImage = ['png', 'jpg', 'jpeg'].includes(ext);
  const isPdf = ext === 'pdf';
  const needsTextFetch = !isImage && !isPdf;

  const [text, setText] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(needsTextFetch);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (!needsTextFetch) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${API_BASE}/api/documents/${documentId}/text`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail || `Couldn't load preview (${res.status}).`);
        }
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        setText(data.text ?? '');
        setTruncated(!!data.truncated);
      })
      .catch((err: Error) => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [documentId, needsTextFetch]);

  const rawUrl = `${API_BASE}/api/documents/${documentId}/raw`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Preview of ${filename}`}
    >
      <div
        className="bg-aegis-raised border border-aegis-border rounded-2xl shadow-2xl w-full max-w-3xl h-[85vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-aegis-border flex-shrink-0">
          <div className="min-w-0">
            <p className="text-sm font-medium text-aegis-text-primary truncate">{filename}</p>
            <p className="text-[11px] text-aegis-text-muted uppercase tracking-wide">{ext}</p>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            <a
              href={`${rawUrl}?download=true`}
              download={filename}
              className="flex items-center gap-1.5 text-xs text-aegis-text-secondary hover:text-aegis-text-primary px-2.5 py-1.5 rounded-lg hover:bg-aegis-overlay transition-colors"
              title="Download the original file"
            >
              <Download className="w-3.5 h-3.5" />
              Download
            </a>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-aegis-overlay text-aegis-text-muted hover:text-aegis-text-primary transition-colors"
              title="Close preview"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-hidden bg-aegis-base">
          {isImage ? (
            <div className="w-full h-full flex items-center justify-center overflow-auto p-4">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={rawUrl} alt={filename} className="max-w-full max-h-full object-contain" />
            </div>
          ) : isPdf ? (
            <iframe src={rawUrl} title={filename} className="w-full h-full border-0" />
          ) : loading ? (
            <div className="w-full h-full flex items-center justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-aegis-text-muted" />
            </div>
          ) : error ? (
            <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-center px-6">
              <AlertCircle className="w-5 h-5 text-aegis-error flex-shrink-0" />
              <p className="text-sm text-aegis-text-secondary">{error}</p>
            </div>
          ) : (
            <div className="w-full h-full overflow-auto p-4">
              <pre className="text-xs text-aegis-text-primary whitespace-pre-wrap font-mono leading-relaxed">{text}</pre>
              {truncated && (
                <p className="text-[11px] text-aegis-text-muted mt-3 pt-3 border-t border-aegis-border">
                  Preview truncated — download the file to see the rest.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
