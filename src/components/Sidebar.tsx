'use client';

import { useState } from 'react';

// ─── Smart Node Definitions ───────────────────────────────────────────────────

interface SmartNode {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: 'tools' | 'memory' | 'data' | 'system';
  enabled: boolean;
}

const DEFAULT_NODES: SmartNode[] = [
  {
    id: 'web-search',
    name: 'Web Search',
    description: 'Search the internet for real-time information',
    icon: '🔍',
    category: 'tools',
    enabled: true,
  },
  {
    id: 'file-system',
    name: 'File System',
    description: 'Read and write files on your local machine',
    icon: '📁',
    category: 'tools',
    enabled: true,
  },
  {
    id: 'code-runner',
    name: 'Code Runner',
    description: 'Execute Python, JS, and shell scripts safely',
    icon: '💻',
    category: 'tools',
    enabled: false,
  },
  {
    id: 'memory-store',
    name: 'Memory Store',
    description: 'Persistent vector memory across sessions',
    icon: '🧠',
    category: 'memory',
    enabled: true,
  },
  {
    id: 'browser-agent',
    name: 'Browser Agent',
    description: 'Autonomously browse and interact with web pages',
    icon: '🌐',
    category: 'tools',
    enabled: false,
  },
  {
    id: 'data-analysis',
    name: 'Data Analysis',
    description: 'Parse CSV, JSON, and tabular data with charts',
    icon: '📊',
    category: 'data',
    enabled: false,
  },
  {
    id: 'local-rag',
    name: 'Local RAG',
    description: 'Index and query your private document library',
    icon: '📚',
    category: 'memory',
    enabled: false,
  },
  {
    id: 'system-info',
    name: 'System Monitor',
    description: 'Access CPU, memory, and process information',
    icon: '⚙️',
    category: 'system',
    enabled: false,
  },
];

const CATEGORY_LABELS: Record<SmartNode['category'], string> = {
  tools: 'Tools',
  memory: 'Memory',
  data: 'Data',
  system: 'System',
};

// ─── Node Toggle ──────────────────────────────────────────────────────────────

interface NodeItemProps {
  node: SmartNode;
  onToggle: (id: string) => void;
}

function NodeItem({ node, onToggle }: NodeItemProps) {
  return (
    <button
      onClick={() => onToggle(node.id)}
      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all duration-200 group text-left ${
        node.enabled
          ? 'bg-aegis-primary/10 border border-aegis-primary/25 hover:bg-aegis-primary/15'
          : 'bg-transparent border border-transparent hover:bg-aegis-raised hover:border-aegis-border'
      }`}
      title={node.description}
    >
      {/* Icon */}
      <span className="text-base w-7 h-7 flex-shrink-0 flex items-center justify-center rounded-lg bg-aegis-raised border border-aegis-border">
        {node.icon}
      </span>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <p
          className={`text-xs font-medium truncate ${
            node.enabled ? 'text-aegis-text-primary' : 'text-aegis-text-secondary'
          }`}
        >
          {node.name}
        </p>
      </div>

      {/* Toggle dot */}
      <div className="flex-shrink-0">
        <div
          className={`w-7 h-4 rounded-full border transition-colors duration-200 flex items-center ${
            node.enabled
              ? 'bg-aegis-primary border-aegis-primary justify-end pr-0.5'
              : 'bg-transparent border-aegis-border justify-start pl-0.5'
          }`}
        >
          <div className="w-3 h-3 rounded-full bg-white/90" />
        </div>
      </div>
    </button>
  );
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

interface SidebarProps {
  onNewChat: () => void;
}

export default function Sidebar({ onNewChat }: SidebarProps) {
  const [nodes, setNodes] = useState<SmartNode[]>(DEFAULT_NODES);
  const [activeCategory, setActiveCategory] = useState<SmartNode['category'] | 'all'>('all');

  const toggleNode = (id: string) => {
    setNodes((prev) =>
      prev.map((n) => (n.id === id ? { ...n, enabled: !n.enabled } : n))
    );
  };

  const enabledCount = nodes.filter((n) => n.enabled).length;

  const filteredNodes =
    activeCategory === 'all'
      ? nodes
      : nodes.filter((n) => n.category === activeCategory);

  const categories: Array<SmartNode['category'] | 'all'> = [
    'all',
    'tools',
    'memory',
    'data',
    'system',
  ];

  return (
    <aside className="w-64 flex-shrink-0 flex flex-col h-full glass border-r border-aegis-border animate-slide-in-left">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="px-4 pt-6 pb-4 border-b border-aegis-border">
        {/* Logo */}
        <div className="flex items-center gap-2.5 mb-5">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-aegis-primary to-aegis-accent flex items-center justify-center text-white font-bold text-sm glow-primary">
            ⚡
          </div>
          <div>
            <h1 className="text-sm font-bold text-aegis-text-primary tracking-wide">AEGIS</h1>
            <p className="text-[10px] text-aegis-text-muted">Local AI Platform</p>
          </div>
        </div>

        {/* New Chat Button */}
        <button
          onClick={onNewChat}
          className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-xl bg-gradient-to-r from-aegis-primary to-aegis-accent text-white text-xs font-medium hover:opacity-90 active:scale-95 transition-all duration-150 shadow-lg shadow-aegis-primary/25"
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="8" y1="2" x2="8" y2="14" />
            <line x1="2" y1="8" x2="14" y2="8" />
          </svg>
          New Chat
        </button>
      </div>

      {/* ── Smart Nodes ─────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-3 py-4">
        {/* Section title */}
        <div className="flex items-center justify-between mb-3 px-1">
          <p className="text-[10px] font-semibold text-aegis-text-muted uppercase tracking-widest">
            Smart Nodes
          </p>
          <span className="text-[10px] text-aegis-primary bg-aegis-primary/10 px-1.5 py-0.5 rounded-full">
            {enabledCount}/{nodes.length}
          </span>
        </div>

        {/* Category filter pills */}
        <div className="flex flex-wrap gap-1 mb-3">
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat as SmartNode['category'] | 'all')}
              className={`text-[10px] px-2 py-1 rounded-full transition-colors duration-150 ${
                activeCategory === cat
                  ? 'bg-aegis-primary text-white'
                  : 'bg-aegis-raised text-aegis-text-muted hover:text-aegis-text-secondary border border-aegis-border'
              }`}
            >
              {cat === 'all' ? 'All' : CATEGORY_LABELS[cat as SmartNode['category']]}
            </button>
          ))}
        </div>

        {/* Node list */}
        <div className="space-y-1">
          {filteredNodes.map((node) => (
            <NodeItem key={node.id} node={node} onToggle={toggleNode} />
          ))}
        </div>
      </div>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <div className="px-4 py-4 border-t border-aegis-border space-y-2">
        {/* Model indicator */}
        <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-aegis-raised border border-aegis-border">
          <span className="w-2 h-2 rounded-full bg-aegis-success flex-shrink-0" />
          <div className="min-w-0">
            <p className="text-[10px] text-aegis-text-muted">Active Model</p>
            <p className="text-xs font-medium text-aegis-text-secondary truncate">
              Local · On-Device
            </p>
          </div>
        </div>

        {/* Settings / Version */}
        <div className="flex items-center justify-between px-1">
          <span className="text-[10px] text-aegis-text-muted">v0.1.0</span>
          <button className="text-[10px] text-aegis-text-muted hover:text-aegis-primary transition-colors">
            Settings
          </button>
        </div>
      </div>
    </aside>
  );
}
