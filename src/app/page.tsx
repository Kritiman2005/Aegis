'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import Sidebar, { TabType, AccountStatus } from '@/components/Sidebar';
import ChatView from '@/components/ChatView';
import ContextMemoryHub from '@/components/ContextMemoryHub';
import ConnectorsView from '@/components/ConnectorsView';
import MCPServersPanel from '@/components/MCPServersPanel';
import WorkflowsView from '@/components/WorkflowsView';
import ModelHub from '@/components/ModelHub';
import SplashScreen from '@/components/SplashScreen';
import AuthScreen from '@/components/AuthScreen';
import WelcomeScreen from '@/components/WelcomeScreen';
import AnalyticsView from '@/components/AnalyticsView';
import MarketplaceView from '@/components/MarketplaceView';
import { useSocket } from '@/hooks/useSocket';
import { useAppSelector } from '@/hooks/useStore';
import { selectSessionId } from '@/store/sessionSlice';

export default function Home() {
  // Connectors is temporarily disabled (see backend/app/core/feature_flags.py)
  // — 'connectors' is no longer a valid landing tab while it's hidden.
  const [activeTab, setActiveTab] = useState<TabType>('chat');
  const [isBackendReady, setIsBackendReady] = useState(false);
  // Fires much earlier than isBackendReady — as soon as the backend answers
  // at all, well before Qdrant/embedding preload/model checks finish. SQLite
  // (and so the account row) is already up by then, so this is what gates
  // the account check now, instead of waiting for full readiness.
  const [backendReachable, setBackendReachable] = useState(false);
  const [connectorsEnabled, setConnectorsEnabled] = useState(false);

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/feature-flags`)
      .then(res => res.json())
      .then(data => setConnectorsEnabled(!!data.connectors_enabled))
      .catch(() => {});
  }, []);

  const [account, setAccount] = useState<AccountStatus | null>(null);
  const [accountChecked, setAccountChecked] = useState(false);
  // Single attempt: null means "couldn't get a trustworthy answer" — either
  // the fetch itself failed, or the response wasn't a 2xx with a real
  // {logged_in: boolean} body (e.g. a transient 500 while SQLite is still
  // settling right after a restart). Previously any of that was silently
  // treated as "logged out", which is wrong — it just means we don't know.
  const tryFetchAccountStatus = useCallback(async (): Promise<AccountStatus | null> => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/account/status`, { cache: 'no-store' });
      if (!res.ok) return null;
      const data = await res.json();
      if (typeof data?.logged_in !== 'boolean') return null;
      return data;
    } catch {
      return null;
    }
  }, []);

  // React StrictMode (on by default in `next dev`) double-invokes effects,
  // so refreshAccount can end up called twice back-to-back on mount — two
  // genuinely concurrent retry loops. Without a guard, whichever one
  // happens to finish LAST wins, even if it's the redundant one and even if
  // it ran out of retries and concluded "logged out" while the other had
  // already succeeded. This token makes only the most recently *started*
  // call allowed to ever commit state, so a stale call's result — success
  // or failure — is silently dropped once a newer call has begun.
  const refreshCallIdRef = useRef(0);
  const refreshAccount = useCallback(async () => {
    const callId = ++refreshCallIdRef.current;
    // A few retries with backoff absorb a brief post-restart hiccup (backend
    // reachable for /api/health but not yet ready to serve every route, or
    // a transient DB error) — only conclude "logged out" once we're sure.
    for (let attempt = 0; attempt < 4; attempt++) {
      const data = await tryFetchAccountStatus();
      if (refreshCallIdRef.current !== callId) return; // superseded — drop this result
      if (data) {
        setAccount(data);
        setAccountChecked(true);
        return;
      }
      if (attempt < 3) await new Promise(r => setTimeout(r, 800));
    }
    if (refreshCallIdRef.current !== callId) return;
    setAccount({ logged_in: false });
    setAccountChecked(true);
  }, [tryFetchAccountStatus]);

  // Wait for backendReachable (not the full isBackendReady) before ever
  // calling this — firing on mount unconditionally raced the backend
  // actually being reachable: right after a restart, this could hit a
  // connection error while the Python process was still binding its port,
  // and (before the retry logic above) that would permanently mark the user
  // logged out with no retry, even though the real session was sitting in
  // SQLite the whole time and the backend became reachable moments later.
  // Deliberately doesn't wait for the *full* boot sequence (Qdrant, embedding
  // preload, model checks) — SQLite is already up as soon as the backend
  // answers at all, so there's no reason to make an unauthenticated visitor
  // sit through the rest of that just to reach the sign-in screen.
  useEffect(() => {
    if (backendReachable) refreshAccount();
  }, [backendReachable, refreshAccount]);

  // The "Your Mac is ready" hardware screen — shown exactly once, ever, the
  // first time this install reaches the main app (not once per launch).
  // Defaults to true (already seen) so a flaky status fetch fails toward
  // never re-showing it, rather than risking an annoying repeat.
  const [welcomeSeen, setWelcomeSeen] = useState(true);
  const [welcomeChecked, setWelcomeChecked] = useState(false);
  // Gated on backendReachable, not the full isBackendReady — same reasoning
  // as the account check above (SQLite, which /api/onboarding/status reads,
  // is already up as soon as the backend answers at all) — and this is what
  // lets WelcomeScreen render before SplashScreen's boot/model-download
  // sequence rather than after it (see the render order below): the
  // hardware read it shows (/api/hardware/detect) has no SQLite/Qdrant/model
  // dependency either, so there's no real reason a first-time user should
  // ever have sat through model download before seeing it in the first place.
  useEffect(() => {
    if (!backendReachable || !account?.logged_in) return;
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/onboarding/status`, { cache: 'no-store' })
      .then(res => res.json())
      .then(data => setWelcomeSeen(!!data.welcome_seen))
      .catch(() => {})
      .finally(() => setWelcomeChecked(true));
  }, [backendReachable, account?.logged_in]);

  const handleLogout = useCallback(async () => {
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/account/logout`, { method: 'POST' });
    } finally {
      refreshAccount();
    }
  }, [refreshAccount]);

  const {
    messages,
    status,
    isStreaming,
    streamingContent,
    statusText,
    sendMessage,
    cancelGeneration,
    clearMessages,
    switchSession,
  } = useSocket();

  const currentSessionId = useAppSelector(selectSessionId);
  const [sessions, setSessions] = useState<any[]>([]);

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/chat/sessions`);
      const data = await res.json();
      setSessions(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  // Refresh sessions list on mount and whenever a message is sent
  useEffect(() => { fetchSessions(); }, [fetchSessions]);
  useEffect(() => {
    if (messages.length > 0) fetchSessions();
  }, [messages.length, fetchSessions]);

  const handleNewChat = useCallback(() => {
    switchSession();
    setActiveTab('chat');
  }, [switchSession]);

  const handleSelectSession = useCallback((sessionId: string) => {
    switchSession(sessionId);
    setActiveTab('chat');
  }, [switchSession]);

  const handleDeleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this chat? This cannot be undone.')) return;
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/chat/sessions/${encodeURIComponent(sessionId)}`,
        { method: 'DELETE' }
      );
      if (res.ok) {
        setSessions(prev => prev.filter(s => s.id !== sessionId));
        // If we deleted the currently open session, start a new chat
        if (currentSessionId === sessionId) {
          switchSession();
          setActiveTab('chat');
        }
      }
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  }, [currentSessionId, switchSession]);

  const recentChats = sessions.map(s => ({
    id: s.id,
    preview: s.preview || 'Untitled chat',
  }));

  // Account status is resolved before full backend readiness: as soon as we
  // know for sure the visitor isn't signed in, show AuthScreen immediately
  // rather than making them wait through Qdrant/embedding preload/model
  // checks first — none of that matters until they can actually use the
  // app. An already-authenticated visitor sees no difference: this check
  // doesn't fire faster than before for them, since they need to wait for
  // full readiness regardless.
  if (backendReachable && accountChecked && !account?.logged_in) {
    return <AuthScreen onAuthenticated={refreshAccount} />;
  }

  // Account check still in flight — brief blank beat before we know
  // whether to show AuthScreen, WelcomeScreen, or move on to SplashScreen.
  if (backendReachable && !accountChecked) {
    return <div className="h-screen w-screen bg-aegis-base" />;
  }

  // Hardware-detection welcome screen, shown before the boot/model-download
  // sequence (SplashScreen below) rather than after — it only needs the
  // backend reachable (see the effect above), so a first-time user should
  // see "here's what we found on your machine" first, and only then move
  // on to picking/downloading a model. Still shown at most once ever.
  if (backendReachable && accountChecked && account?.logged_in && welcomeChecked && !welcomeSeen) {
    return <WelcomeScreen onContinue={() => setWelcomeSeen(true)} />;
  }

  if (!isBackendReady) {
    return (
      <SplashScreen
        onReady={() => setIsBackendReady(true)}
        onGoToLLMPanel={() => { setIsBackendReady(true); setActiveTab('llms'); }}
        onBackendReachable={() => setBackendReachable(true)}
      />
    );
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-aegis-base text-aegis-text-primary font-sans antialiased">
      {/* Sidebar */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onNewChat={handleNewChat}
        recentChats={recentChats}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        activeSessionId={currentSessionId ?? undefined}
        connectorsEnabled={connectorsEnabled}
        account={account}
        onLogout={handleLogout}
      />

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 h-full overflow-hidden">
        {/* Kept mounted and just hidden (rather than conditionally
            rendered like every other tab below) when navigating away —
            ChatView carries a lot of its own local state (the composer's
            in-progress text, scroll position, pending attachments, the
            doc-status polling effect) that a full unmount/remount would
            silently wipe every time, plus the cost of re-rendering the
            entire message history and re-running every mount effect from
            scratch. That combination is what was showing up as "laggy,
            and I can't type" when coming back to chat after visiting
            another tab — display:none preserves all of it instead of
            tearing the component down and rebuilding it. */}
        <div className={activeTab === 'chat' ? 'contents' : 'hidden'}>
          <ChatView
            messages={messages}
            status={status}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            statusText={statusText}
            onSendMessage={sendMessage}
            onCancelGeneration={cancelGeneration}
            onClearMessages={clearMessages}
            onOpenLLMPanel={() => setActiveTab('llms')}
            onOpenContextPanel={() => setActiveTab('context')}
            onOpenMarketplace={() => setActiveTab('marketplace')}
          />
        </div>
        {(activeTab === 'connectors' || activeTab === 'sync_detail') && (
          <ConnectorsView />
        )}
        {activeTab === 'mcp_servers' && (
          <MCPServersPanel />
        )}
        {activeTab === 'workflows' && (
          <WorkflowsView />
        )}
        {(activeTab === 'llms' || activeTab === 'model_hub') && (
          <ModelHub />
        )}
        {activeTab === 'context' && (
          <ContextMemoryHub />
        )}
        {activeTab === 'analytics' && (
          <AnalyticsView />
        )}
        {activeTab === 'marketplace' && (
          <MarketplaceView />
        )}
      </div>
    </div>
  );
}
