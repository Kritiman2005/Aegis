'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Store, Globe, Sparkles, Download, CheckCircle2, Loader2, Trash2, XCircle } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface Tool {
  id: string;
  name: string;
  description: string;
  category: string;
  size_estimate: string;
  status_endpoint: string;
  installed: boolean;
}

const TOOL_ICONS: Record<string, typeof Globe> = {
  playwright_scraper: Globe,
};

interface MarketplaceSkill {
  id: string;
  name: string;
  description: string;
  installed: boolean;
}

type ToolStatus = 'not_installed' | 'installing' | 'ready' | 'failed';

function ToolCard({ tool, onInstalled }: { tool: Tool; onInstalled: () => void }) {
  const [status, setStatus] = useState<ToolStatus>(tool.installed ? 'ready' : 'not_installed');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setStatus(tool.installed ? 'ready' : 'not_installed');
  }, [tool.installed]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const handleInstall = async () => {
    setStatus('installing');
    try {
      await fetch(`${API_BASE}/api/marketplace/tools/${tool.id}/install`, { method: 'POST' });
    } catch {
      setStatus('failed');
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}${tool.status_endpoint}`);
        const json = await res.json();
        if (json.status === 'ready') {
          if (pollRef.current) clearInterval(pollRef.current);
          setStatus('ready');
          onInstalled();
        } else if (json.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current);
          setStatus('failed');
        }
      } catch {
        // transient — keep polling
      }
    }, 2500);
  };

  const Icon = TOOL_ICONS[tool.id] || Globe;

  return (
    <div className="flex items-start gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-4 hover:border-aegis-primary/40 transition-colors">
      <div className="w-10 h-10 rounded-lg bg-aegis-overlay flex items-center justify-center flex-shrink-0">
        <Icon className="w-5 h-5 text-aegis-primary-light" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-[13px] font-semibold text-aegis-text-primary">{tool.name}</p>
          <span className="text-[11px] text-aegis-text-muted">· {tool.size_estimate}</span>
        </div>
        <p className="text-[13px] text-aegis-text-secondary mt-0.5">{tool.description}</p>
      </div>
      <div className="flex-shrink-0">
        {status === 'ready' && (
          <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-success px-3 py-1.5">
            <CheckCircle2 className="w-3.5 h-3.5" /> Installed
          </span>
        )}
        {status === 'not_installed' && (
          <button
            onClick={handleInstall}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors"
          >
            <Download className="w-3.5 h-3.5" /> Install
          </button>
        )}
        {status === 'installing' && (
          <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-warning px-3 py-1.5">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Installing...
          </span>
        )}
        {status === 'failed' && (
          <button
            onClick={handleInstall}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-error px-3.5 py-1.5 rounded-lg border border-aegis-error/30 hover:bg-aegis-error/10 transition-colors"
          >
            <XCircle className="w-3.5 h-3.5" /> Retry
          </button>
        )}
      </div>
    </div>
  );
}

function SkillCard({ skill, onChanged }: { skill: MarketplaceSkill; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);

  const handleInstall = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/marketplace/skills/${skill.id}/install`, { method: 'POST' });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/api/marketplace/skills/${skill.id}`, { method: 'DELETE' });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-start gap-4 bg-aegis-raised border border-aegis-border rounded-xl px-5 py-4 hover:border-aegis-primary/40 transition-colors">
      <div className="w-10 h-10 rounded-lg bg-aegis-overlay flex items-center justify-center flex-shrink-0">
        <Sparkles className="w-5 h-5 text-aegis-primary-light" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-aegis-text-primary">{skill.name}</p>
        <p className="text-[13px] text-aegis-text-secondary mt-0.5">{skill.description}</p>
      </div>
      <div className="flex-shrink-0">
        {skill.installed ? (
          <button
            onClick={handleRemove}
            disabled={busy}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-aegis-text-muted hover:text-aegis-error px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
          >
            <Trash2 className="w-3.5 h-3.5" /> Remove
          </button>
        ) : (
          <button
            onClick={handleInstall}
            disabled={busy}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
            Install
          </button>
        )}
      </div>
    </div>
  );
}

export default function MarketplaceView() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [skills, setSkills] = useState<MarketplaceSkill[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    try {
      const [toolsRes, skillsRes] = await Promise.all([
        fetch(`${API_BASE}/api/marketplace/tools`),
        fetch(`${API_BASE}/api/marketplace/skills`),
      ]);
      const toolsJson = await toolsRes.json();
      const skillsJson = await skillsRes.json();
      setTools(Array.isArray(toolsJson.tools) ? toolsJson.tools : []);
      setSkills(Array.isArray(skillsJson.skills) ? skillsJson.skills : []);
    } catch {
      // leave state as-is
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  return (
    <div className="flex-1 flex flex-col bg-aegis-base overflow-hidden">
      <div className="px-8 pt-8 pb-5">
        <div className="flex items-center gap-3 mb-1">
          <Store className="w-6 h-6 text-aegis-primary" />
          <h1 className="text-2xl font-bold text-aegis-text-primary">Marketplace</h1>
        </div>
        <p className="text-sm text-aegis-text-secondary">
          Choose what Aegis can do. Nothing downloads or activates until you install it here.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8 space-y-8">
        {loading ? (
          <p className="text-sm text-aegis-text-muted">Loading...</p>
        ) : (
          <>
            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Automation Tools</h2>
              <div className="space-y-2.5">
                {tools.map(tool => (
                  <ToolCard key={tool.id} tool={tool} onInstalled={fetchAll} />
                ))}
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-aegis-text-primary mb-3">Skills</h2>
              <p className="text-[13px] text-aegis-text-muted mb-3">
                Pre-built guidance the chat agent draws on automatically when relevant — no setup needed once installed.
              </p>
              <div className="space-y-2.5">
                {skills.map(skill => (
                  <SkillCard key={skill.id} skill={skill} onChanged={fetchAll} />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
