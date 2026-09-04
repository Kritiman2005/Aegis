'use client';

import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import {
  Send,
  Paperclip,
  CheckCircle2,
  Info,
  Save,
  Check,
  X,
  Bookmark,
  Loader2,
  Plus,
  Globe,
  Store,
  Sparkles,
  FileText,
  XCircle,
  ChevronDown,
  ChevronRight,
  Mic,
  Square,
} from 'lucide-react';
import { type ChatMessage, type ConnectionStatus, type Attachment } from '@/hooks/useSocket';
import { useAppSelector } from '@/hooks/useStore';
import { selectSessionId } from '@/store/sessionSlice';
import AgentThinking from './AgentThinking';
import MarkdownContent from './MarkdownContent';
import PlanCard, { parsePlanMarkdown } from './PlanCard';
import { useMemo } from 'react';

function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onChange(!checked); }}
      className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${checked ? 'bg-aegis-primary' : 'bg-aegis-overlay border border-aegis-border'}`}
      title={checked ? 'On for this chat' : 'Off for this chat'}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-4' : 'translate-x-0'}`}
      />
    </button>
  );
}

type DocStatus = 'processing' | 'ready' | 'failed';

function AttachmentChip({ attachment, status }: { attachment: Attachment; status?: DocStatus }) {
  const ext = attachment.file_type.toLowerCase();
  const isImage = ['png', 'jpg', 'jpeg'].includes(ext);
  return (
    <div className="flex items-center gap-2.5 bg-aegis-raised border border-aegis-border rounded-xl px-3.5 py-2.5 max-w-xs">
      <div className="w-8 h-8 rounded-lg bg-aegis-overlay flex items-center justify-center flex-shrink-0">
        <FileText className="w-4 h-4 text-aegis-primary-light" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[12px] font-medium text-aegis-text-primary truncate">{attachment.filename}</p>
        <p className="text-[11px] text-aegis-text-muted uppercase">{ext}</p>
      </div>
      {status === 'failed' ? (
        <XCircle className="w-4 h-4 text-aegis-error flex-shrink-0" />
      ) : status === 'ready' ? (
        <CheckCircle2 className="w-4 h-4 text-aegis-success flex-shrink-0" />
      ) : (
        <Loader2 className="w-4 h-4 text-aegis-text-muted animate-spin flex-shrink-0" />
      )}
    </div>
  );
}

interface ChatViewProps {
  messages: ChatMessage[];
  status: ConnectionStatus;
  isStreaming: boolean;
  onSendMessage: (msg: string, msgType?: string, mode?: string, userPrompt?: string, attachments?: Attachment[]) => boolean;
  onCancelGeneration?: () => void;
  onClearMessages: () => void;
  activeConnectorName?: string;
  activeNodeId?: string | null;
  completedNodeIds?: Set<string>;
  failedNodeIds?: Set<string>;
  streamingContent?: string;
  statusText?: string | null;
  agentState?: string;
  onOpenLLMPanel?: () => void;
  onOpenContextPanel?: () => void;
  onOpenMarketplace?: () => void;
}

export default function ChatView({
  messages,
  status,
  isStreaming,
  onSendMessage,
  onCancelGeneration,
  onClearMessages,
  activeConnectorName = 'GitHub',
  activeNodeId,
  completedNodeIds,
  failedNodeIds,
  streamingContent,
  statusText,
  agentState,
  onOpenLLMPanel,
  onOpenContextPanel,
  onOpenMarketplace,
}: ChatViewProps) {
  const sessionId = useAppSelector(selectSessionId);
  const [inputVal, setInputVal] = useState('');
  const [savingMsgId, setSavingMsgId] = useState<string | null>(null);
  const [savedMsgId, setSavedMsgId] = useState<string | null>(null);
  const [chatMode, setChatMode] = useState<'chat' | 'agent'>('chat');

  // chatMode is otherwise a pure UI toggle with no memory of which mode a
  // given conversation was actually in — switching away and back (or a full
  // reload) always restarted it at 'chat', silently stripping the
  // interactive Yes/No buttons off a plan (or a cookie-input prompt) that
  // was still genuinely waiting on the user, even though the backend agent
  // itself was untouched and still paused. Only ever corrects *into* Agent
  // Mode when there's positive evidence (a non-IDLE backend state) that
  // something is pending — never forces back to 'chat', which would stomp
  // a normal manual mode switch in an otherwise-idle conversation.
  useEffect(() => {
    if (agentState && agentState !== 'IDLE') {
      setChatMode('agent');
    }
  }, [agentState, sessionId]);
  const [hardwareStatus, setHardwareStatus] = useState<{active_model: string, active_model_display?: string, max_context?: number, ram_percent: number} | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement>(null);

  // Live status (processing/ready/failed) for each attached document's chip,
  // resolved by polling — attachment messages themselves never change once
  // created, so this is the only way a chip's spinner turns into a check/x.
  const [docStatuses, setDocStatuses] = useState<Record<number, DocStatus>>({});

  useEffect(() => {
    const pendingIds = messages
      .flatMap(m => m.attachments || [])
      .map(a => a.document_id)
      .filter(id => docStatuses[id] === undefined || docStatuses[id] === 'processing');
    if (pendingIds.length === 0 || !sessionId) return;

    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/documents?conversation_id=${encodeURIComponent(sessionId)}`);
        const docs = await res.json();
        if (cancelled || !Array.isArray(docs)) return;
        let anyPending = false;
        setDocStatuses(prev => {
          const next = { ...prev };
          for (const d of docs) {
            if (d.status === 'ready' || d.status === 'failed') next[d.id] = d.status;
            else { next[d.id] = 'processing'; anyPending = true; }
          }
          return next;
        });
        if (!anyPending && interval) clearInterval(interval);
      } catch {
        // leave statuses as-is — next poll tick will retry
      }
    };
    poll();
    interval = setInterval(poll, 2000);
    return () => { cancelled = true; if (interval) clearInterval(interval); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, sessionId]);

  // Claude-style plain-text "Thinking…" / "Thought for Xs" — no icons, no
  // dots, just an elapsed-seconds counter that ticks while waiting for the
  // first token, then freezes into a static summary once content starts
  // arriving (matching Claude's own "Thought for 53s" label).
  const [thinkingSeconds, setThinkingSeconds] = useState(0);
  const [thoughtDurationSec, setThoughtDurationSec] = useState<number | null>(null);
  const preContentPhase = isStreaming && chatMode === 'chat' && !streamingContent;

  useEffect(() => {
    if (!isStreaming) return;
    // A fresh turn started — reset the counter and the frozen summary from
    // whatever the previous turn left behind.
    setThinkingSeconds(0);
    setThoughtDurationSec(null);
  }, [isStreaming]);

  useEffect(() => {
    if (!preContentPhase) return;
    const interval = setInterval(() => setThinkingSeconds(s => s + 1), 1000);
    return () => clearInterval(interval);
  }, [preContentPhase]);

  useEffect(() => {
    // First token just arrived — freeze the elapsed time as the summary,
    // the same way Claude's live "Thinking…" becomes a static "Thought for
    // Xs" once the answer starts.
    if (chatMode === 'chat' && streamingContent && thoughtDurationSec === null) {
      setThoughtDurationSec(thinkingSeconds);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamingContent]);

  useEffect(() => {
    const fetchHardware = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/hardware/status`);
        const data = await res.json();
        setHardwareStatus(data);
      } catch (e) {
        // Silently ignore fetch errors so Next.js doesn't pop up a red error overlay
        // if the user restarts the backend during a polling cycle.
      }
    };
    fetchHardware();
    const interval = setInterval(fetchHardware, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!modelMenuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target as Node)) {
        setModelMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [modelMenuOpen]);

  // Schedule state
  const [schedulingPlanId, setSchedulingPlanId] = useState<string | null>(null);
  const [scheduleCron, setScheduleCron] = useState<string>('every_1_hour');
  

  
  // Plan Editing State
  const [editingPlanId, setEditingPlanId] = useState<string | null>(null);
  const [planEditContent, setPlanEditContent] = useState('');
  

  const groupedMessages = useMemo(() => {
    const groups: Array<{ type: 'message' | 'system' | 'thinking', content: ChatMessage[], id: string }> = [];
    
    messages.forEach(msg => {
      if (msg.role === 'system') {
        groups.push({ type: 'system', content: [msg], id: msg.id });
      } else if (msg.msgType === 'thought' || msg.msgType === 'tool_call') {
        const lastGroup = groups[groups.length - 1];
        if (lastGroup && lastGroup.type === 'thinking') {
          lastGroup.content.push(msg);
        } else {
          groups.push({ type: 'thinking', content: [msg], id: `thinking-${msg.id}` });
        }
      } else {
        groups.push({ type: 'message', content: [msg], id: msg.id });
      }
    });

    return groups;
  }, [messages]);

  // Document Upload State
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Uploaded-but-not-yet-sent attachments, shown as removable chips above
  // the composer — Claude-style: attach, optionally type text, then send.
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);

  // "+" attach menu: Upload Document / Tools / Skills
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [scrapeBarOpen, setScrapeBarOpen] = useState(false);
  const [scrapeUrl, setScrapeUrl] = useState('');
  const [isScraping, setIsScraping] = useState(false);
  const skillFileInputRef = useRef<HTMLInputElement>(null);
  const [isUploadingSkill, setIsUploadingSkill] = useState(false);

  // Installed Tools/Skills for THIS conversation, with their per-chat
  // on/off state — the "+" menu's Claude-Desktop-style toggle list.
  const [capTools, setCapTools] = useState<{ id: string; name: string; active: boolean }[]>([]);
  const [capSkills, setCapSkills] = useState<{ id: string; name: string; description: string; active: boolean }[]>([]);

  const fetchCapabilities = () => {
    if (!sessionId) return;
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/conversations/${sessionId}/capabilities`)
      .then(res => res.json())
      .then(data => {
        setCapTools(Array.isArray(data.tools) ? data.tools : []);
        setCapSkills(Array.isArray(data.skills) ? data.skills : []);
      })
      .catch(() => {});
  };

  useEffect(() => {
    if (!attachMenuOpen) return;
    fetchCapabilities();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachMenuOpen, sessionId]);

  const toggleCapability = async (type: 'tool' | 'skill', id: string, active: boolean) => {
    if (!sessionId) return;
    if (type === 'tool') setCapTools(prev => prev.map(t => (t.id === id ? { ...t, active } : t)));
    else setCapSkills(prev => prev.map(s => (s.id === id ? { ...s, active } : s)));
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/conversations/${sessionId}/capabilities/${type}/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active }),
      });
    } catch {
      // best-effort — a stale toggle just means the setting reverts next menu open
    }
  };

  // Clicking a Tool row itself (not its toggle) runs that tool's manual
  // one-off action — only "web_scrape"/playwright_scraper exists today.
  const handleToolClick = (toolId: string) => {
    setAttachMenuOpen(false);
    if (toolId === 'playwright_scraper') {
      setScrapeBarOpen(true);
    }
  };

  const handleSkillFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploadingSkill(true);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/skills/upload`, {
        method: 'POST',
        body: formData,
      });
      if (res.ok) {
        fetchCapabilities();
      } else {
        const err = await res.json().catch(() => ({}));
        onSendMessage(`[System] Couldn't add that skill: ${err.detail || 'invalid file'}`, 'toast', chatMode);
      }
    } catch {
      onSendMessage('[System] Failed to upload the skill file.', 'toast', chatMode);
    } finally {
      setIsUploadingSkill(false);
      if (skillFileInputRef.current) skillFileInputRef.current.value = '';
    }
  };

  const handleScrapeSubmit = async () => {
    const url = scrapeUrl.trim();
    if (!url || !sessionId) return;
    setIsScraping(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/scrape`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, conversation_id: sessionId }),
      });
      if (!res.ok) {
        onSendMessage('[System] Failed to start scraping that page.', 'toast', chatMode);
      }
      // Same deal as document upload: the backend broadcasts real progress
      // ("Opening..." -> "✅ Scraped..." / "❌ ...") over the websocket, so we
      // don't post an assumed-success message here.
    } catch (err) {
      console.error(err);
      onSendMessage('[System] Failed to connect to the scraping endpoint.', 'toast', chatMode);
    } finally {
      setIsScraping(false);
      setScrapeUrl('');
      setScrapeBarOpen(false);
    }
  };

  // Voice input (mic button) — local speech-to-text via faster-whisper.
  // The model ships inside the app bundle itself (see
  // app/core/transcription.py's module docstring), so there's no install
  // flow here — 'ready' vs 'unavailable' is checked once on mount and only
  // ever flips to 'unavailable' if the build is genuinely missing the model.
  type VoiceStatus = 'checking' | 'unavailable' | 'ready';
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>('checking');
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  // Live transcription while recording: re-transcribes the growing clip
  // every second so text appears as you talk instead of only after you stop
  // (faster-whisper has no true incremental/streaming decode mode, so this
  // re-runs on the whole clip so far — cheap enough for the small int8
  // model at chat-composer lengths). baseTextRef freezes whatever was
  // already in the composer when recording started, so live/final updates
  // only ever replace the voice-dictated portion, never text typed before.
  const baseTextRef = useRef('');
  const partialInFlightRef = useRef(false);
  const stoppingRef = useRef(false);
  const recordingSessionRef = useRef(0);

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/voice/status`)
      .then(res => res.json())
      .then(data => setVoiceStatus(data.status === 'ready' ? 'ready' : 'unavailable'))
      .catch(() => setVoiceStatus('unavailable'));
  }, []);

  const stopRecording = () => {
    stoppingRef.current = true;
    mediaRecorderRef.current?.stop();
  };

  // Fires on every ~2s chunk while recording. Transcribes everything
  // captured so far and writes it straight into the composer as a live
  // preview. Guarded by partialInFlightRef so a slow response can't pile up
  // behind the next tick's request, and by comparing `session` against
  // recordingSessionRef so a response that lands after recording has
  // stopped (or a new recording has started) is dropped instead of
  // clobbering the final/newer text.
  const sendPartialTranscript = async (session: number) => {
    if (partialInFlightRef.current || audioChunksRef.current.length === 0) return;
    partialInFlightRef.current = true;
    try {
      const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
      const formData = new FormData();
      formData.append('file', audioBlob, 'clip.webm');
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/voice/transcribe`, {
        method: 'POST',
        body: formData,
      });
      if (res.ok && session === recordingSessionRef.current) {
        const data = await res.json();
        const text = (data.text || '').trim();
        setInputVal(baseTextRef.current && text ? `${baseTextRef.current} ${text}` : (text || baseTextRef.current));
      }
    } catch {
      // transient — the next chunk's tick will retry
    } finally {
      partialInFlightRef.current = false;
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      audioChunksRef.current = [];
      stoppingRef.current = false;
      recordingSessionRef.current += 1;
      const session = recordingSessionRef.current;
      baseTextRef.current = inputVal.trim();

      recorder.ondataavailable = (e) => {
        if (e.data.size === 0) return;
        audioChunksRef.current.push(e.data);
        // Skip the trailing chunk MediaRecorder flushes right before onstop
        // fires — that clip gets the authoritative final transcription below.
        if (!stoppingRef.current) sendPartialTranscript(session);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        setIsTranscribing(true);
        try {
          const formData = new FormData();
          formData.append('file', audioBlob, 'clip.webm');
          const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/voice/transcribe`, {
            method: 'POST',
            body: formData,
          });
          if (res.ok) {
            const data = await res.json();
            const text = (data.text || '').trim();
            setInputVal(baseTextRef.current && text ? `${baseTextRef.current} ${text}` : (text || baseTextRef.current));
            textareaRef.current?.focus();
          } else {
            onSendMessage('[System] Voice transcription failed.', 'toast', chatMode);
          }
        } catch {
          onSendMessage('[System] Failed to connect to the voice transcription endpoint.', 'toast', chatMode);
        } finally {
          setIsTranscribing(false);
        }
      };
      mediaRecorderRef.current = recorder;
      // 1s timeslice — makes ondataavailable fire every second during
      // recording (not just once at the end), which is what drives the
      // live preview above.
      recorder.start(1000);
      setIsRecording(true);
    } catch {
      onSendMessage('[System] Microphone access is required for voice input.', 'toast', chatMode);
    }
  };

  const handleMicClick = () => {
    if (voiceStatus !== 'ready' || isTranscribing) return;
    if (isRecording) {
      setIsRecording(false);
      stopRecording();
    } else {
      startRecording();
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !sessionId) return;

    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('conversation_id', sessionId);

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'}/api/documents/upload`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        onSendMessage(`[System] Failed to upload document.`, 'toast', chatMode);
        return;
      }
      // Uploading only starts ingestion in the background — it does NOT send
      // a message on its own. The file sits as a pending attachment in the
      // composer (Claude-style: attach, optionally type something, then hit
      // send) until the user actually sends; that's when it becomes part of
      // the conversation. Ingestion status resolves separately once it's
      // actually part of a sent message, via the docStatuses poll.
      const data = await res.json();
      setPendingAttachments(prev => [...prev, {
        document_id: data.document_id,
        filename: data.filename,
        file_type: data.file_type,
      }]);
    } catch (err) {
      console.error(err);
      onSendMessage(`[System] Failed to connect to upload endpoint.`, 'toast', chatMode);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);



  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const [isNearBottom, setIsNearBottom] = useState(true);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    const distanceToBottom = scrollHeight - scrollTop - clientHeight;
    setIsNearBottom(distanceToBottom < 100);
  };

  useEffect(() => {
    if (isNearBottom) {
      scrollToBottom();
    }
  }, [messages, streamingContent]);

  const handleSend = () => {
    if ((!inputVal.trim() && pendingAttachments.length === 0) || isStreaming || status !== 'connected') return;
    const sent = onSendMessage(inputVal, 'message', chatMode, undefined, pendingAttachments.length > 0 ? pendingAttachments : undefined);
    if (sent) {
      setInputVal('');
      setPendingAttachments([]);
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-aegis-base overflow-hidden">
      {/* ── Chat Messages Thread (Matching Image 2) ─────────────────────────── */}
      <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-6" onScroll={handleScroll}>
        {messages.length === 0 && !streamingContent && !isStreaming ? (
          /* ── Landing Page (matching reference image 1) ──────────────────── */
          <div className="h-full flex flex-col items-center justify-center text-center px-8">
            <h2 className="text-2xl font-bold text-aegis-text-primary mb-2">
              Where shall we begin?
            </h2>
            <p className="text-sm text-aegis-text-muted">
              Aegis is ready to help — ask anything.
            </p>
          </div>
        ) : (
          groupedMessages.map((group, index) => {
            if (group.type === 'system') {
              const msg = group.content[0];
              return (
                <div key={group.id} className="flex justify-center my-4">
                  <div className="px-4 py-1.5 bg-aegis-overlay text-aegis-text-secondary text-xs font-medium rounded-full shadow-sm border border-aegis-border">
                    {msg.content}
                  </div>
                </div>
              );
            }

            if (group.type === 'thinking') {
              const isLast = index === groupedMessages.length - 1;
              const shouldPassStream = isLast && isStreaming && chatMode === 'agent';
              
              return (
                <AgentThinking 
                  key={group.id} 
                  messages={group.content} 
                  isStreaming={shouldPassStream}
                  streamingContent={shouldPassStream ? streamingContent : undefined}
                />
              );
            }

            const msg = group.content[0];
            const isUser = msg.role === 'user';

            // No avatars, no "You"/"Aegis" labels — Claude-style: the user's
            // turn is a plain card, the assistant's turn is plain text with
            // no card at all. Both flush to the same width, no gutter for an
            // icon that isn't there.
            return (
              <div key={msg.id} className={`max-w-4xl mx-auto flex min-w-0 ${isUser ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-2xl w-full min-w-0 ${isUser ? 'flex flex-col items-end' : ''}`}>
                  {isUser ? (
                    <div className="space-y-2 flex flex-col items-end min-w-0 w-full">
                      {msg.attachments && msg.attachments.length > 0 && (
                        <div className="flex flex-col gap-2 items-end">
                          {msg.attachments.map(a => (
                            <AttachmentChip key={a.document_id} attachment={a} status={docStatuses[a.document_id]} />
                          ))}
                        </div>
                      )}
                      {msg.content && (
                        // break-words (not just wrapping normal text) is the
                        // fix that actually matters here: a pasted cookie
                        // value or long token has no spaces at all, so with
                        // nothing telling the browser it may break mid-word,
                        // the bubble just grows to fit it instead of
                        // wrapping — which is exactly what pushed this card
                        // wider than the rest of the chat.
                        <div className="bg-aegis-overlay text-aegis-text-primary rounded-2xl px-4 py-3 text-[13px] leading-relaxed break-words max-w-full">
                          {msg.content}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-[13px] leading-relaxed min-w-0 group">
                      {!msg.isStreaming && parsePlanMarkdown(msg.content) ? (
                        <PlanCard
                          content={msg.content}
                          interactive={index === groupedMessages.length - 1 && !isStreaming && chatMode === 'agent' && status === 'connected'}
                          onApprove={() => onSendMessage('yes', 'message', chatMode)}
                          onEdit={() => textareaRef.current?.focus()}
                        />
                      ) : (
                        <MarkdownContent content={msg.content} />
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}

        {/* ── Streaming Bubble: show when actively streaming OR waiting for first token ──
             Claude-style: no avatar, no card — plain text, with a "Thinking…" /
             "Thought for Xs" label exactly like the real Claude UI. Always
             just "Thinking…" here, deliberately — Claude never surfaces its
             own backend substeps ("searching documents", "generating"…) to
             the user, so this doesn't either; that stays reserved for the
             standalone status line below (uploads/scrapes with no chat turn). */}
        {isStreaming && chatMode === 'chat' && (
          <div className="max-w-4xl mx-auto">
            <div className="max-w-2xl w-full space-y-2">
              {!streamingContent && (
                <span className="text-[13px] text-shimmer animate-shimmer">
                  Thinking…
                </span>
              )}
              {streamingContent && (
                <div className="space-y-2">
                  {thoughtDurationSec !== null && (
                    <div className="text-[13px] text-aegis-text-muted">
                      Thought for {thoughtDurationSec}s
                    </div>
                  )}
                  <div className="text-[13px] text-aegis-text-primary leading-relaxed">
                    <MarkdownContent content={streamingContent} />
                    <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-aegis-text-muted animate-pulse align-middle" />
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* If in agent mode and the last group is not thinking, we need a fresh AgentThinking for the stream */}
        {isStreaming && chatMode === 'agent' && (!groupedMessages.length || groupedMessages[groupedMessages.length - 1].type !== 'thinking') && (
          <AgentThinking
            messages={[]}
            isStreaming={true}
            streamingContent={streamingContent}
          />
        )}

        {/* Standalone status line — covers cases with no response bubble to
            show it in yet: a document/scrape upload (no chat turn at all),
            or Agent Mode before its first token arrives. During an active
            Chat Mode turn this is already shown inline in the response
            bubble's header, so it's suppressed here to avoid a duplicate. */}
        {statusText && !(isStreaming && chatMode === 'chat') && (
          <div className="max-w-4xl mx-auto">
            <span className="text-[13px] text-aegis-text-muted">{statusText}</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ── Bottom Input Bar ─────────────────────────────── */}
      <div className="p-6 pt-2 bg-aegis-base max-w-4xl w-full mx-auto space-y-3">
        {/* Mode Toggle — a real sliding segmented switch, not two separate buttons */}
        <div className="flex justify-center">
          <div className={`relative bg-aegis-overlay/60 p-1 rounded-lg flex items-center w-[180px] ${
            isStreaming || !!activeNodeId ? 'opacity-50 pointer-events-none' : ''
          }`}>
            <div
              className="absolute top-1 bottom-1 left-1 w-[calc(50%-4px)] bg-aegis-raised rounded-md shadow-sm transition-transform duration-200 ease-out"
              style={{ transform: chatMode === 'agent' ? 'translateX(calc(100% + 8px))' : 'translateX(0)' }}
            />
            <button
              onClick={() => {
                if (chatMode === 'agent') onSendMessage('__system_mode_switch__', 'system');
                setChatMode('chat');
              }}
              className={`relative z-10 flex-1 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                chatMode === 'chat' ? 'text-aegis-text-primary' : 'text-aegis-text-secondary'
              }`}
            >
              Chat
            </button>
            <button
              onClick={() => {
                if (chatMode === 'chat') onSendMessage('__system_mode_switch__', 'system');
                setChatMode('agent');
              }}
              className={`relative z-10 flex-1 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                chatMode === 'agent' ? 'text-aegis-primary-light' : 'text-aegis-text-secondary'
              }`}
            >
              Agent
            </button>
          </div>
        </div>

        {pendingAttachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {pendingAttachments.map(a => (
              <div
                key={a.document_id}
                className="flex items-center gap-2 bg-aegis-raised border border-aegis-border rounded-xl pl-3 pr-2 py-2"
              >
                <FileText className="w-3.5 h-3.5 text-aegis-primary-light flex-shrink-0" />
                <span className="text-[12px] text-aegis-text-primary truncate max-w-[160px]">{a.filename}</span>
                <button
                  onClick={() => setPendingAttachments(prev => prev.filter(p => p.document_id !== a.document_id))}
                  className="text-aegis-text-muted hover:text-aegis-error p-0.5"
                  title="Remove attachment"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        {scrapeBarOpen && (
          <div className="flex items-center gap-2 mb-2 bg-aegis-raised border border-aegis-border rounded-xl px-3 py-2">
            <Globe className="w-4 h-4 text-aegis-primary-light flex-shrink-0" />
            <input
              autoFocus
              type="url"
              value={scrapeUrl}
              onChange={(e) => setScrapeUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleScrapeSubmit(); if (e.key === 'Escape') setScrapeBarOpen(false); }}
              placeholder="https://example.com — paste a URL to scrape"
              disabled={isScraping}
              className="flex-1 bg-transparent text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none"
            />
            <button
              onClick={handleScrapeSubmit}
              disabled={isScraping || !scrapeUrl.trim()}
              className="text-[12px] font-medium text-white bg-aegis-primary hover:bg-aegis-primary-dark px-3 py-1 rounded-lg transition-colors disabled:opacity-50"
            >
              {isScraping ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Scrape'}
            </button>
            <button
              onClick={() => setScrapeBarOpen(false)}
              className="text-aegis-text-muted hover:text-aegis-text-secondary p-1"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        <div className={`relative bg-aegis-raised border rounded-2xl shadow-sm focus-within:ring-1 transition-all ${
          chatMode === 'agent'
            ? 'border-aegis-primary/30 focus-within:border-aegis-primary focus-within:ring-aegis-primary/20'
            : 'border-aegis-border focus-within:border-aegis-primary focus-within:ring-aegis-primary'
        }`}>
          <textarea
            ref={textareaRef}
            value={inputVal}
            onChange={(e) => {
              setInputVal(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
            }}
            onKeyDown={handleKeyDown}
            placeholder={status === 'connected' ? (chatMode === 'agent' ? 'Ask Agent to perform a task...' : 'Message Aegis...') : 'Connecting to backend...'}
            disabled={status !== 'connected'}
            rows={1}
            className="w-full bg-transparent text-xs text-aegis-text-primary placeholder:text-aegis-text-muted resize-none px-4 pt-3.5 pb-12 focus:outline-none leading-relaxed"
            style={{ minHeight: '52px' }}
          />

          {/* Input Bar Bottom Toolbar */}
          <div className="absolute bottom-2.5 left-3 right-3 flex items-center justify-between pointer-events-none">
            {/* "+" attach menu: Upload Document, then Tools/Skills lists with per-chat toggles */}
            <div className="relative pointer-events-auto">
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileUpload}
                className="hidden"
                accept=".txt,.md,.csv,.pdf,.ppt,.pptx,.docx,.xlsx,.png,.jpg,.jpeg,.mp3,.wav,.m4a,.ogg,.flac,.aac,.wma,.mp4,.mov,.mkv,.webm,.avi"
              />
              <button
                onClick={() => setAttachMenuOpen(v => !v)}
                disabled={isUploading}
                className={`p-1.5 rounded-lg transition-colors ${isUploading ? 'text-aegis-primary-light animate-pulse' : attachMenuOpen ? 'text-aegis-primary bg-aegis-overlay' : 'text-aegis-text-muted hover:text-aegis-text-secondary'}`}
                title="Add to this chat"
              >
                {isUploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              </button>

              {attachMenuOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setAttachMenuOpen(false)} />
                  <div className="absolute bottom-full left-0 mb-2 w-72 max-h-[26rem] overflow-y-auto bg-aegis-raised border border-aegis-border rounded-xl shadow-lg py-1.5 z-20">
                    <button
                      onClick={() => { setAttachMenuOpen(false); fileInputRef.current?.click(); }}
                      className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[13px] text-aegis-text-primary hover:bg-aegis-overlay transition-colors"
                    >
                      <Paperclip className="w-4 h-4 text-aegis-text-muted flex-shrink-0" />
                      <span className="whitespace-nowrap">Upload Document / Audio / Video</span>
                    </button>

                    {/* Tools — installed once via the Marketplace, switched
                        on/off here per chat (the toggle controls whether the
                        agent can invoke it). Clicking a row runs its manual
                        one-off action — e.g. Web Scraping opens the URL bar.
                        Agent-Mode-only: Chat Mode does no tool calling at
                        all, so this section (and the scrape bar it can open)
                        would otherwise be a dead end there. */}
                    {chatMode === 'agent' && (
                      <>
                        <div className="mt-1 pt-1.5 border-t border-aegis-border px-3.5 pb-1 text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wide">
                          Tools
                        </div>
                        {capTools.length === 0 ? (
                          <button
                            onClick={() => { setAttachMenuOpen(false); onOpenMarketplace?.(); }}
                            className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[13px] text-aegis-text-muted hover:bg-aegis-overlay hover:text-aegis-text-primary transition-colors"
                          >
                            <Store className="w-4 h-4 flex-shrink-0" />
                            <span className="whitespace-nowrap">Add tools from Marketplace</span>
                          </button>
                        ) : (
                          capTools.map(tool => (
                            <div key={tool.id} className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[13px] text-aegis-text-primary hover:bg-aegis-overlay transition-colors">
                              <button
                                onClick={() => handleToolClick(tool.id)}
                                className="flex items-center gap-2.5 flex-1 min-w-0 text-left"
                              >
                                <Globe className="w-4 h-4 text-aegis-text-muted flex-shrink-0" />
                                <span className="flex-1 truncate">{tool.name}</span>
                              </button>
                              <ToggleSwitch checked={tool.active} onChange={(v) => toggleCapability('tool', tool.id, v)} />
                            </div>
                          ))
                        )}
                      </>
                    )}

                    {/* Skills — pure guidance the chat agent (Chat Mode)
                        draws on automatically when relevant, only while on. */}
                    <div className="mt-1 pt-1.5 border-t border-aegis-border px-3.5 pb-1 text-[11px] font-semibold text-aegis-text-muted uppercase tracking-wide">
                      Skills
                    </div>
                    {capSkills.map(skill => (
                      <div key={skill.id} className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[13px] text-aegis-text-primary" title={skill.description}>
                        <Sparkles className="w-4 h-4 text-aegis-text-muted flex-shrink-0" />
                        <span className="flex-1 truncate">{skill.name}</span>
                        <ToggleSwitch checked={skill.active} onChange={(v) => toggleCapability('skill', skill.id, v)} />
                      </div>
                    ))}
                    <input
                      type="file"
                      ref={skillFileInputRef}
                      onChange={handleSkillFileChange}
                      className="hidden"
                      accept=".md"
                    />
                    <button
                      onClick={() => skillFileInputRef.current?.click()}
                      disabled={isUploadingSkill}
                      className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[13px] text-aegis-text-muted hover:bg-aegis-overlay hover:text-aegis-text-primary transition-colors disabled:opacity-50"
                    >
                      {isUploadingSkill ? <Loader2 className="w-4 h-4 flex-shrink-0 animate-spin" /> : <Paperclip className="w-4 h-4 flex-shrink-0" />}
                      <span className="whitespace-nowrap">Upload Custom Skill (.md)</span>
                    </button>
                    {capSkills.length === 0 && (
                      <button
                        onClick={() => { setAttachMenuOpen(false); onOpenMarketplace?.(); }}
                        className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[13px] text-aegis-text-muted hover:bg-aegis-overlay hover:text-aegis-text-primary transition-colors"
                      >
                        <Store className="w-4 h-4 flex-shrink-0" />
                        <span className="whitespace-nowrap">Add skills from Marketplace</span>
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>

            {/* Right Buttons: Active model status + RAM gauge + Send Button */}
            <div className="flex items-center gap-2 pointer-events-auto">
              {/* Mic — click records; click again (or the transcribing spinner)
                  stops and sends the clip to /api/voice/transcribe, which fills
                  the composer with the resulting text. Disabled (not hidden)
                  when 'unavailable' so a broken build is visibly diagnosable
                  rather than silently missing a button. */}
              <button
                onClick={handleMicClick}
                disabled={voiceStatus !== 'ready' || isTranscribing}
                title={
                  isTranscribing ? 'Transcribing...'
                    : isRecording ? 'Stop recording'
                    : voiceStatus === 'ready' ? 'Voice input'
                    : voiceStatus === 'checking' ? 'Checking voice input...'
                    : 'Voice input unavailable in this build'
                }
                className={`w-8 h-8 rounded-full flex items-center justify-center transition-all shadow-sm ${
                  isRecording
                    ? 'bg-aegis-error text-white animate-pulse'
                    : 'text-aegis-text-muted hover:text-aegis-text-secondary hover:bg-aegis-overlay'
                } disabled:opacity-40`}
              >
                {isTranscribing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : isRecording ? (
                  <Square className="w-3.5 h-3.5" />
                ) : (
                  <Mic className="w-4 h-4" />
                )}
              </button>

              {/* Active model chip — Claude-style: click opens a small card above
                  it showing the current model, its context window, and a "More
                  models" row through to the full LLM Panel, instead of jumping
                  straight there on click. */}
              {(() => {
                const modelLoaded = !!hardwareStatus?.active_model && hardwareStatus.active_model !== 'None';
                // Registry display names can carry a trailing "(filename.gguf)" —
                // fine as the full tooltip, too noisy for the short chip/header name.
                const rawDisplayName = modelLoaded
                  ? (hardwareStatus?.active_model_display && hardwareStatus.active_model_display !== 'None'
                      ? hardwareStatus.active_model_display
                      : hardwareStatus!.active_model.split('/').pop())
                  : 'No model loaded';
                const displayName = rawDisplayName?.replace(/\s*\([^)]*\)\s*$/, '').trim() || rawDisplayName;
                const contextLabel = hardwareStatus?.max_context
                  ? hardwareStatus.max_context >= 1000
                    ? `${Math.round(hardwareStatus.max_context / 1000)}K tokens`
                    : `${hardwareStatus.max_context} tokens`
                  : null;

                // RAM pressure shows up as color on the model chip itself now
                // (no separate gauge) — same thresholds the old battery used.
                const ramPercent = hardwareStatus?.ram_percent;
                const ramTier = !modelLoaded || ramPercent == null ? null
                  : ramPercent > 90 ? 'error' : ramPercent > 75 ? 'warning' : 'success';
                const dotColorClass = ramTier === 'error' ? 'bg-aegis-error'
                  : ramTier === 'warning' ? 'bg-aegis-warning'
                  : ramTier === 'success' ? 'bg-aegis-success'
                  : 'bg-aegis-text-muted';
                const ramTextColorClass = ramTier === 'error' ? 'text-aegis-error'
                  : ramTier === 'warning' ? 'text-aegis-warning'
                  : 'text-aegis-text-secondary';

                return (
                  <div className="relative" ref={modelMenuRef}>
                    {modelMenuOpen && (
                      <div className="absolute bottom-full left-0 mb-2 w-72 rounded-2xl border border-aegis-border bg-aegis-raised shadow-xl overflow-hidden z-20 animate-fade-in-up">
                        <div className="flex items-start justify-between gap-3 p-4">
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-aegis-text-primary truncate">{displayName}</p>
                            <p className={`text-xs mt-0.5 ${ramTextColorClass}`}>
                              {modelLoaded
                                ? `Running locally${ramPercent != null ? ` · RAM ${ramPercent.toFixed(0)}%` : ' · Ready'}`
                                : 'Open LLMs to load a model'}
                            </p>
                          </div>
                          {modelLoaded && <Check className="w-4 h-4 text-aegis-primary flex-shrink-0 mt-0.5" />}
                        </div>

                        {modelLoaded && contextLabel && (
                          <>
                            <div className="border-t border-aegis-border" />
                            <div className="flex items-center justify-between px-4 py-3 text-sm">
                              <span className="text-aegis-text-primary">Context window</span>
                              <span className="text-aegis-text-secondary">{contextLabel}</span>
                            </div>
                          </>
                        )}

                        <div className="border-t border-aegis-border" />
                        <button
                          onClick={() => { setModelMenuOpen(false); onOpenLLMPanel?.(); }}
                          className="w-full flex items-center justify-between px-4 py-3 text-sm text-aegis-text-primary hover:bg-aegis-overlay transition-colors"
                        >
                          <span>More models</span>
                          <ChevronRight className="w-4 h-4 text-aegis-text-muted" />
                        </button>
                      </div>
                    )}

                    <button
                      onClick={() => setModelMenuOpen(v => !v)}
                      title={`${rawDisplayName || ''}${ramPercent != null ? ` · RAM ${ramPercent.toFixed(0)}%` : ''}`}
                      className={`flex items-center gap-1.5 pl-2 pr-1.5 py-1 rounded-full border transition-colors max-w-[200px] text-xs font-medium ${
                        modelMenuOpen
                          ? 'border-aegis-text-muted bg-aegis-overlay text-aegis-text-primary'
                          : 'border-aegis-border hover:border-aegis-text-muted hover:bg-aegis-overlay text-aegis-text-secondary'
                      }`}
                    >
                      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 transition-colors duration-700 ${dotColorClass}`} />
                      <span className="truncate">{displayName}</span>
                      <ChevronDown className={`w-3 h-3 text-aegis-text-muted flex-shrink-0 transition-transform ${modelMenuOpen ? 'rotate-180' : ''}`} />
                    </button>
                  </div>
                );
              })()}

              {isStreaming ? (
                <button
                  onClick={onCancelGeneration}
                  className="w-8 h-8 rounded-full bg-aegis-error hover:bg-aegis-error/80 text-white flex items-center justify-center transition-all shadow-sm"
                  title="Stop Generating"
                >
                  <X className="w-4 h-4" />
                </button>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={(!inputVal.trim() && pendingAttachments.length === 0) || status !== 'connected'}
                  className="w-8 h-8 rounded-full bg-aegis-primary hover:bg-aegis-primary-dark text-white flex items-center justify-center transition-all shadow-sm disabled:opacity-30"
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Footer Disclaimer */}
        <p className="text-center text-[10px] text-aegis-text-muted mt-2">
          Press <span className="font-semibold text-aegis-text-secondary">Enter</span> to send • Aegis can generate errors. Verify important information.
        </p>
      </div>
    </div>
  );
}
