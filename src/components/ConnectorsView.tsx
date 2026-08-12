'use client';

import { useState, useEffect, useCallback } from 'react';
import { Search, Settings, Plus, CheckCircle, ChevronDown, X } from 'lucide-react';
import ConfigModal, { CatalogConnector } from './ConfigModal';

// ── Connector definitions (only 5, hardcoded for display) ─────────────────────

const CONNECTOR_DEFS = [
  {
    name: 'google_workspace',
    displayName: 'Google Drive',
    description: 'Search, read, and upload files instantly across your Drive.',
    oauthService: 'google_workspace',
    authType: 'oauth',
    loginUrl: 'http://localhost:8000/auth/google/login',
    icon: (
      <svg viewBox="0 0 24 24" className="w-6 h-6" fill="none">
        <path d="M12 2L2 19h7l3-5.2L15 19h7L12 2z" fill="#4285F4" opacity="0.9"/>
        <path d="M12 2l3 5.2H9L12 2z" fill="#0066DA"/>
        <path d="M9 7.2L2 19h7l3-5.2-3-6.6z" fill="#00AC47" opacity="0.9"/>
        <path d="M15 7.2L12 13.8l3 5.2h7L15 7.2z" fill="#FFBA00" opacity="0.9"/>
      </svg>
    ),
    popularity: 'Most popular',
    rank: 1,
  },
  {
    name: 'gmail',
    displayName: 'Gmail',
    description: 'Draft replies, summarize threads, & search your inbox.',
    oauthService: 'google_workspace',
    authType: 'oauth',
    loginUrl: 'http://localhost:8000/auth/google/login',
    icon: (
      <svg viewBox="0 0 24 24" className="w-6 h-6" fill="none">
        <rect width="20" height="16" x="2" y="4" rx="2" fill="#EA4335" opacity="0.15"/>
        <rect width="20" height="16" x="2" y="4" rx="2" fill="none" stroke="#EA4335" strokeWidth="1.5"/>
        <path d="M2 6l10 7 10-7" stroke="#EA4335" strokeWidth="1.5" strokeLinecap="round"/>
        <text x="12" y="17" textAnchor="middle" fontSize="8" fontWeight="700" fill="#EA4335">M</text>
      </svg>
    ),
    popularity: '#2 popular',
    rank: 2,
  },
  {
    name: 'figma',
    displayName: 'Figma',
    description: 'Read designs, inspect components, and search your Figma workspace.',
    oauthService: null,
    authType: 'api_key',
    loginUrl: null,
    icon: (
      <svg viewBox="0 0 24 24" className="w-6 h-6" fill="none">
        <rect x="8" y="2" width="8" height="8" rx="3" fill="#F24E1E"/>
        <rect x="8" y="10" width="8" height="8" rx="3" fill="#A259FF"/>
        <rect x="2" y="2" width="8" height="8" rx="3" fill="#FF7262"/>
        <rect x="2" y="10" width="8" height="8" rx="3" fill="#1ABCFE"/>
        <circle cx="14" cy="14" r="4" fill="#0ACF83"/>
      </svg>
    ),
    popularity: '#3 popular',
    rank: 3,
  },
  {
    name: 'notion',
    displayName: 'Notion',
    description: 'Read, search, and write to your Notion workspace.',
    oauthService: 'notion',
    authType: 'oauth',
    loginUrl: 'http://localhost:8000/auth/notion/login',
    icon: (
      <svg viewBox="0 0 24 24" className="w-6 h-6" fill="none">
        <rect width="18" height="20" x="3" y="2" rx="3" fill="#191919"/>
        <path d="M6 7h12M6 11h8M6 15h10" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
      </svg>
    ),
    popularity: '#4 popular',
    rank: 4,
  },
  {
    name: 'github',
    displayName: 'GitHub',
    description: 'Browse repos, search commits, list PRs and issues.',
    oauthService: 'github',
    authType: 'oauth',
    loginUrl: 'http://localhost:8000/auth/github/login',
    icon: (
      <svg viewBox="0 0 24 24" className="w-6 h-6" fill="#24292e">
        <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"/>
      </svg>
    ),
    popularity: '#5 popular',
    rank: 5,
  },
];

interface ConnectedServer {
  name: string;
  running: boolean;
  tools_count: number;
}

interface ApiKeyModalProps {
  connector: typeof CONNECTOR_DEFS[0];
  onClose: () => void;
  onConnect: (key: string) => void;
}

function ApiKeyModal({ connector, onClose, onConnect }: ApiKeyModalProps) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const handleSubmit = () => {
    if (!value.trim()) { setError('Please enter a token.'); return; }
    onConnect(value.trim());
    onClose();
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl p-6 w-full max-w-sm mx-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gray-50 border border-gray-200 flex items-center justify-center">
              {connector.icon}
            </div>
            <div>
              <h3 className="text-sm font-bold text-gray-900">{connector.displayName}</h3>
              <p className="text-xs text-gray-500">Enter your access token</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="space-y-3">
          <input
            type="password"
            placeholder="figd_xxxxxxxxxxxxxxxxxxxx"
            value={value}
            onChange={e => { setValue(e.target.value); setError(''); }}
            className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-xl focus:outline-none focus:border-[#5B50F0] focus:ring-2 focus:ring-[#5B50F0]/10 transition-all"
          />
          {error && <p className="text-xs text-red-500">{error}</p>}
          <p className="text-xs text-gray-400">
            Get your token from{' '}
            <a href="https://www.figma.com/settings" target="_blank" rel="noreferrer" className="text-[#5B50F0] hover:underline">
              figma.com/settings
            </a>
            {' '}→ Personal access tokens.
          </p>
          <button
            onClick={handleSubmit}
            className="w-full py-2.5 bg-[#5B50F0] hover:bg-[#4A40E0] text-white text-sm font-semibold rounded-xl transition-colors"
          >
            Connect Figma
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ConnectorsView() {
  const [activeServers, setActiveServers] = useState<Record<string, ConnectedServer>>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState<'all' | 'connected'>('all');
  const [apiKeyConnector, setApiKeyConnector] = useState<typeof CONNECTOR_DEFS[0] | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/connectors');
      if (res.ok) {
        const data = await res.json();
        const map: Record<string, ConnectedServer> = {};
        Object.entries(data.status || {}).forEach(([name, info]: [string, any]) => {
          map[name] = { name, running: info.running, tools_count: info.tools_count || 0 };
        });
        setActiveServers(map);
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchStatus();
    const handler = (e: MessageEvent) => {
      if (e.data === 'auth_success') fetchStatus();
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [fetchStatus]);

  const isConnected = (def: typeof CONNECTOR_DEFS[0]) => {
    // google drive + gmail share google_workspace server
    const serverKey = def.name === 'gmail' ? 'google_workspace' : def.name;
    return activeServers[serverKey]?.running ?? false;
  };

  const handleConnect = (def: typeof CONNECTOR_DEFS[0]) => {
    if (def.authType === 'oauth' && def.loginUrl) {
      window.open(def.loginUrl, '_blank');
    } else if (def.authType === 'api_key') {
      setApiKeyConnector(def);
    }
  };

  const handleFigmaConnect = async (token: string) => {
    try {
      await fetch('http://127.0.0.1:8000/api/connectors/catalog/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_name: 'figma', env: { FIGMA_ACCESS_TOKEN: token }, input_params: {} }),
      });
      await fetchStatus();
    } catch (e) { console.error(e); }
  };

  const displayed = CONNECTOR_DEFS.filter(def => {
    const matchesSearch = !searchQuery ||
      def.displayName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      def.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesTab = activeTab === 'all' || isConnected(def);
    return matchesSearch && matchesTab;
  });

  const popularConnectors = CONNECTOR_DEFS.slice(0, 3);

  return (
    <div className="flex-1 overflow-y-auto bg-[#F4F5F7] p-8">
      <div className="max-w-4xl">
        
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Connectors</h1>
          <p className="text-sm text-gray-500 mt-1">Connect your tools so Aegis can take action across your workspace.</p>
        </div>

        {/* Search Bar */}
        <div className="relative mb-5">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search connectors..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="w-full pl-11 pr-4 py-3 bg-white border border-gray-200 rounded-2xl text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:border-[#5B50F0] focus:ring-2 focus:ring-[#5B50F0]/10 transition-all shadow-sm"
          />
        </div>

        {/* Tab Bar */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveTab('all')}
              className={`px-4 py-1.5 rounded-full text-sm font-semibold transition-colors ${
                activeTab === 'all'
                  ? 'bg-gray-900 text-white'
                  : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50'
              }`}
            >
              All connectors
            </button>
            <button
              onClick={() => setActiveTab('connected')}
              className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
                activeTab === 'connected'
                  ? 'bg-gray-900 text-white font-semibold'
                  : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50'
              }`}
            >
              Connected
            </button>
          </div>
          <div className="flex items-center gap-2">
            <button className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 rounded-full text-sm text-gray-600 hover:bg-gray-50 transition-colors">
              Filter by <ChevronDown className="w-3.5 h-3.5" />
            </button>
            <button className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 rounded-full text-sm text-gray-600 hover:bg-gray-50 transition-colors">
              Sort by <ChevronDown className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Popular Section (only when showing all, no search) */}
        {activeTab === 'all' && !searchQuery && (
          <div className="mb-6">
            <p className="text-xs font-bold uppercase tracking-widest text-gray-400 mb-3">Popular</p>
            <div className="grid grid-cols-3 gap-3">
              {popularConnectors.map(def => {
                const connected = isConnected(def);
                return (
                  <div
                    key={def.name + '-popular'}
                    className="bg-white rounded-2xl border border-gray-200 p-4 flex items-center justify-between shadow-sm hover:shadow-md transition-shadow"
                  >
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-xl bg-gray-50 border border-gray-100 flex items-center justify-center">
                        {def.icon}
                      </div>
                      <span className="text-sm font-semibold text-gray-800">{def.displayName}</span>
                    </div>
                    {connected ? (
                      <Settings className="w-4 h-4 text-gray-400 cursor-pointer hover:text-[#5B50F0] transition-colors" />
                    ) : (
                      <button
                        onClick={() => handleConnect(def)}
                        className="w-7 h-7 rounded-full bg-gray-100 hover:bg-[#5B50F0] hover:text-white text-gray-500 flex items-center justify-center transition-colors"
                      >
                        <Plus className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Main Connectors Grid */}
        <div className="grid grid-cols-2 gap-4">
          {displayed.map(def => {
            const connected = isConnected(def);
            return (
              <div
                key={def.name}
                className="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm hover:shadow-md transition-all"
              >
                {/* Card Header */}
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-gray-50 border border-gray-100 flex items-center justify-center">
                      {def.icon}
                    </div>
                    <div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-bold text-gray-900">{def.displayName}</span>
                        {connected && (
                          <CheckCircle className="w-4 h-4 text-[#5B50F0]" />
                        )}
                      </div>
                      <p className="text-xs text-gray-400">{def.popularity}</p>
                    </div>
                  </div>

                  {connected ? (
                    <button className="p-1.5 text-gray-400 hover:text-[#5B50F0] hover:bg-indigo-50 rounded-lg transition-colors">
                      <Settings className="w-4 h-4" />
                    </button>
                  ) : (
                    <button
                      onClick={() => handleConnect(def)}
                      className="w-7 h-7 rounded-full bg-gray-100 hover:bg-[#5B50F0] hover:text-white text-gray-500 flex items-center justify-center transition-colors"
                    >
                      <Plus className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>

                {/* Description */}
                <p className="text-xs text-gray-600 leading-relaxed mb-3">{def.description}</p>

                {/* Status */}
                {connected ? (
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block" />
                    <span className="text-xs font-semibold text-green-600">Connected</span>
                  </div>
                ) : (
                  <button
                    onClick={() => handleConnect(def)}
                    className="text-xs font-semibold text-[#5B50F0] hover:underline"
                  >
                    Connect →
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Figma API Key Modal */}
      {apiKeyConnector && (
        <ApiKeyModal
          connector={apiKeyConnector}
          onClose={() => setApiKeyConnector(null)}
          onConnect={handleFigmaConnect}
        />
      )}
    </div>
  );
}
