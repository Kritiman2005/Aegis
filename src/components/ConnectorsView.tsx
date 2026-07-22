'use client';

import { useState, useEffect } from 'react';
import { 
  Search, 
  Plus, 
  CheckCircle2, 
  RefreshCw, 
  Sliders, 
  ArrowLeft, 
  ExternalLink,
  ShieldCheck,
  Clock,
  AlertCircle,
  Mail,
  MessageSquare,
  FileText,
  Users,
  Database,
  Box
} from 'lucide-react';
import ConfigModal, { CatalogConnector } from './ConfigModal';

interface ConnectedServer {
  name: string;
  status: string;
  tools_count: number;
  active: boolean;
}

interface ConnectorsViewProps {
  onSelectConnector?: (connectorName: string) => void;
}

const getConnectorIcon = (name: string) => {
  const n = name.toLowerCase();
  if (n.includes('google')) return <Mail className="w-6 h-6" />;
  if (n.includes('slack')) return <MessageSquare className="w-6 h-6" />;
  if (n.includes('notion')) return <FileText className="w-6 h-6" />;
  if (n.includes('hubspot') || n.includes('salesforce')) return <Users className="w-6 h-6" />;
  if (n.includes('airtable')) return <Database className="w-6 h-6" />;
  return <Box className="w-6 h-6" />;
};

export default function ConnectorsView({ onSelectConnector }: ConnectorsViewProps) {
  const [catalog, setCatalog] = useState<CatalogConnector[]>([]);
  const [activeServers, setActiveServers] = useState<Record<string, ConnectedServer>>({});
  const [activeCategory, setActiveCategory] = useState<string>('All');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedModalConnector, setSelectedModalConnector] = useState<CatalogConnector | null>(null);
  const [selectedDetailConnector, setSelectedDetailConnector] = useState<CatalogConnector | null>(null);
  const [loading, setLoading] = useState(true);

  // Fetch catalog & active connectors status from backend
  const fetchData = async () => {
    try {
      setLoading(true);
      const [catRes, actRes] = await Promise.all([
        fetch('http://127.0.0.1:8000/api/connectors/catalog'),
        fetch('http://127.0.0.1:8000/api/connectors')
      ]);

      if (catRes.ok) {
        const catData = await catRes.json();
        setCatalog(catData.catalog || []);
      }
      if (actRes.ok) {
        const actData = await actRes.json();
        const serverMap: Record<string, ConnectedServer> = {};
        (actData.servers || []).forEach((s: ConnectedServer) => {
          serverMap[s.name] = s;
        });
        setActiveServers(serverMap);
      }
    } catch (err) {
      console.error('Failed to load catalog:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const handleMessage = (event: MessageEvent) => {
      if (event.data === 'auth_success') {
        fetchData();
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  // Handle Connect Click
  const handleConnectClick = (connector: CatalogConnector) => {
    if (connector.auth_type === 'oauth') {
      // 1-Click OAuth flow: trigger browser login
      const service = connector.oauth_service || connector.name;
      const targetUrl = service === 'google_workspace' 
        ? 'http://localhost:8000/auth/google/login' 
        : `http://localhost:8000/auth/${service}/login`;
      window.open(targetUrl, '_blank');
    } else if (connector.auth_type === 'none') {
      // Direct connection with no auth required
      handleManualConnect(connector.name, {}, {});
    } else {
      // Open modal for API Key / Connection String / Path inputs
      setSelectedModalConnector(connector);
    }
  };

  // Submit manual connector connection
  const handleManualConnect = async (
    serverName: string,
    env: Record<string, string>,
    inputParams: Record<string, string>
  ) => {
    const res = await fetch('http://127.0.0.1:8000/api/connectors/catalog/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        server_name: serverName,
        env,
        input_params: inputParams,
      }),
    });

    if (!res.ok) {
      const errorData = await res.json();
      throw new Error(errorData.detail || 'Connection failed');
    }

    await fetchData();
  };

  // Filter categories
  const filteredConnectors = catalog.filter((c) => {
    return c.display_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
           c.description.toLowerCase().includes(searchQuery.toLowerCase());
  });

  // ── Sync / Detail View (Matching Image 3) ───────────────────────────────────
  if (selectedDetailConnector) {
    const activeInfo = activeServers[selectedDetailConnector.name];
    const isConnected = activeInfo?.active ?? false;

    return (
      <div className="flex-1 overflow-y-auto bg-[#F8F9FA] p-8">
        {/* Breadcrumb Navigation */}
        <div className="flex items-center gap-2 text-xs text-gray-500 mb-6">
          <button
            onClick={() => setSelectedDetailConnector(null)}
            className="flex items-center gap-1 hover:text-gray-900 transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" /> Connectors
          </button>
          <span>/</span>
          <span className="font-semibold text-gray-900">{selectedDetailConnector.display_name}</span>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 max-w-6xl">
          {/* Main Sync Config Column (2 Cols) */}
          <div className="lg:col-span-2 space-y-6">
            {/* Connector Banner Card */}
            <div className="bg-white rounded-2xl border border-gray-200 p-6 flex items-center justify-between shadow-sm">
              <div className="flex items-center gap-4">
                <div className="w-14 h-14 rounded-2xl bg-gray-900 flex items-center justify-center text-white text-2xl font-bold">
                  {getConnectorIcon(selectedDetailConnector.name)}
                </div>
                <div>
                  <h2 className="text-xl font-bold text-gray-900">{selectedDetailConnector.display_name}</h2>
                  <div className="flex items-center gap-2 mt-1">
                    <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-teal-500' : 'bg-gray-400'}`} />
                    <span className="text-xs font-semibold tracking-wide uppercase text-teal-600">
                      {isConnected ? 'Connected & Active' : 'Disconnected'}
                    </span>
                    <span className="text-xs text-gray-400">• Last full sync 14 minutes ago</span>
                  </div>
                </div>
              </div>
              <button
                onClick={() => handleConnectClick(selectedDetailConnector)}
                className="px-5 py-2.5 rounded-xl bg-black text-white text-xs font-semibold hover:bg-neutral-800 transition-all shadow-sm"
              >
                {isConnected ? 'Re-authenticate' : 'Connect'}
              </button>
            </div>

            {/* Sync Configuration Card */}
            <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-6 shadow-sm">
              <h3 className="text-sm font-bold text-gray-900 tracking-tight">Sync Configuration</h3>

              {/* Sync Frequency Selector */}
              <div>
                <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500 block mb-2">
                  Sync Frequency
                </label>
                <div className="grid grid-cols-4 gap-3">
                  {['Real-time', 'Hourly', 'Daily', 'Manual'].map((freq, idx) => (
                    <button
                      key={freq}
                      className={`py-2.5 px-3 rounded-xl border text-xs font-medium transition-all ${
                        idx === 1
                          ? 'bg-black text-white border-black font-semibold'
                          : 'bg-gray-50 border-gray-200 text-gray-700 hover:bg-gray-100'
                      }`}
                    >
                      {freq}
                    </button>
                  ))}
                </div>
                <p className="text-[11px] text-gray-400 mt-2 italic">
                  Changes will be indexed within 60 minutes of appearing on {selectedDetailConnector.display_name}.
                </p>
              </div>

              {/* Scope & Permissions */}
              <div>
                <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500 block mb-2">
                  Permissions Scope
                </label>
                <div className="p-4 rounded-xl bg-gray-50 border border-gray-200 space-y-3">
                  <div className="flex items-start gap-3">
                    <ShieldCheck className="w-5 h-5 text-teal-600 flex-shrink-0 mt-0.5" />
                    <div>
                      <p className="text-xs font-bold text-gray-900">Read & Access Scope</p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        Allows indexing and query execution for context-aware assistance in Aegis local agent workflow.
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Right Sync Log Column (1 Col - Matching Image 3) */}
          <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-6 shadow-sm">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-gray-900 tracking-tight">Sync Log</h3>
              <button onClick={fetchData} className="p-1 text-gray-400 hover:text-gray-900 transition-colors">
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>

            {/* Timeline Items */}
            <div className="space-y-6 relative before:absolute before:left-3.5 before:top-2 before:bottom-2 before:w-0.5 before:bg-gray-100">
              <div className="flex items-start gap-4 relative">
                <div className="w-7 h-7 rounded-full bg-purple-100 border border-purple-200 flex items-center justify-center text-purple-600 flex-shrink-0 z-10">
                  <RefreshCw className="w-3.5 h-3.5" />
                </div>
                <div>
                  <p className="text-xs font-bold text-gray-900 uppercase tracking-wide">Sync Completed</p>
                  <p className="text-xs text-gray-600 mt-0.5">Updated index for {selectedDetailConnector.display_name}. Scanned tools & active process.</p>
                  <span className="text-[10px] text-gray-400 mt-1 block">14 minutes ago</span>
                </div>
              </div>

              <div className="flex items-start gap-4 relative">
                <div className="w-7 h-7 rounded-full bg-teal-100 border border-teal-200 flex items-center justify-center text-teal-600 flex-shrink-0 z-10">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                </div>
                <div>
                  <p className="text-xs font-bold text-gray-900 uppercase tracking-wide">Stdio Session Ready</p>
                  <p className="text-xs text-gray-600 mt-0.5">JSON-RPC 2.0 subprocess initialized and tools registered.</p>
                  <span className="text-[10px] text-gray-400 mt-1 block">2 hours ago</span>
                </div>
              </div>

              <div className="flex items-start gap-4 relative">
                <div className="w-7 h-7 rounded-full bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-500 flex-shrink-0 z-10">
                  <Clock className="w-3.5 h-3.5" />
                </div>
                <div>
                  <p className="text-xs font-bold text-gray-900 uppercase tracking-wide">Scheduled Sync</p>
                  <p className="text-xs text-gray-600 mt-0.5">Full workspace health check completed. All indexed data valid.</p>
                  <span className="text-[10px] text-gray-400 mt-1 block">Yesterday, 11:30 PM</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── Main Connectors Gallery View (Matching Image 1) ─────────────────────────
  return (
    <div className="flex-1 overflow-y-auto bg-[#F8F9FA] p-8 space-y-8">
      {/* Title Header */}
      <div>
        <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Connectors</h2>
        <p className="text-xs text-gray-500 mt-1">Supercharge Aegis with your favorite tools.</p>
      </div>

      {/* Filter & Search Bar (Matching Image 1) */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        {/* Left spacer to push search to right if needed, or just let search take full width */}
        <div className="flex-1"></div>

        {/* Right Search Input */}
        <div className="relative w-full sm:w-72">
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search connectors..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-white border border-gray-200 rounded-2xl pl-10 pr-4 py-2 text-xs text-gray-900 placeholder:text-gray-400 focus:outline-none focus:border-black transition-all shadow-sm"
          />
        </div>
      </div>

      {/* Connectors Grid (Matching Image 1) */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="h-48 rounded-2xl bg-gray-100 animate-pulse border border-gray-200" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredConnectors.map((connector) => {
            const activeInfo = activeServers[connector.name];
            const isConnected = activeInfo?.active ?? false;

            return (
              <div
                key={connector.name}
                className="bg-white rounded-2xl border border-gray-200 p-6 flex flex-col justify-between card-shadow card-hover-shadow transition-all relative group"
              >
                <div>
                  {/* Top Card Row: Icon + Connect Button / Toggle */}
                  <div className="flex items-start justify-between mb-4">
                    <div className="w-12 h-12 rounded-xl bg-gray-900 flex items-center justify-center text-white text-xl font-bold shadow-sm">
                      {getConnectorIcon(connector.name)}
                    </div>

                    {/* Connect Button or Active Toggle */}
                    {isConnected ? (
                      <div className="flex items-center gap-1.5 px-3 py-1 bg-teal-50 border border-teal-200 rounded-full text-xs font-semibold text-teal-700">
                        <span className="w-1.5 h-1.5 rounded-full bg-teal-500" />
                        <span>Active</span>
                      </div>
                    ) : (
                      <button
                        onClick={() => handleConnectClick(connector)}
                        className="px-4 py-1.5 rounded-xl bg-black text-white text-xs font-semibold hover:bg-neutral-800 active:scale-95 transition-all shadow-sm"
                      >
                        Connect
                      </button>
                    )}
                  </div>

                  {/* Title & Description */}
                  <h3 className="text-base font-bold text-gray-900 tracking-tight">{connector.display_name}</h3>
                  <p className="text-xs text-gray-500 mt-1.5 line-clamp-2 leading-relaxed">
                    {connector.description}
                  </p>
                </div>

                {/* Card Footer: Category Badge + Status Link */}
                <div className="pt-6 mt-4 border-t border-gray-100 flex items-center justify-between">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-teal-700 bg-teal-50 border border-teal-200 px-2 py-0.5 rounded-md">
                    {connector.category}
                  </span>

                  <button
                    onClick={() => setSelectedDetailConnector(connector)}
                    className="text-xs text-gray-500 hover:text-gray-900 font-medium transition-colors"
                  >
                    {isConnected ? 'View Settings' : 'Disconnected'}
                  </button>
                </div>
              </div>
            );
          })}

          {/* Custom API Card at End (Matching Image 1) */}
          <div className="border-2 border-dashed border-gray-200 rounded-2xl p-6 flex flex-col items-center justify-center text-center hover:border-gray-400 transition-all bg-gray-50/50">
            <div className="w-12 h-12 rounded-xl bg-white border border-gray-200 flex items-center justify-center text-gray-700 text-xl font-bold mb-3 shadow-sm">
              <Plus className="w-6 h-6" />
            </div>
            <h3 className="text-sm font-bold text-gray-900">Custom API</h3>
            <p className="text-xs text-gray-500 mt-1 max-w-xs">
              Build your own custom connector using our developer SDK.
            </p>
            <button className="mt-4 text-xs font-semibold text-black hover:underline">
              Documentation →
            </button>
          </div>
        </div>
      )}

      {/* Config Modal for API Key / Input Schema Connectors */}
      <ConfigModal
        connector={selectedModalConnector}
        isOpen={!!selectedModalConnector}
        onClose={() => setSelectedModalConnector(null)}
        onConnect={handleManualConnect}
      />
    </div>
  );
}
