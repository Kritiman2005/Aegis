'use client';

import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import { 
  Send, 
  Paperclip, 
  Sparkles, 
  Zap, 
  CheckCircle2, 
  Info, 
  CornerDownLeft, 
  User as UserIcon,
  Save,
  Check,
  X,
  Bookmark,
  Loader2
} from 'lucide-react';
import { type ChatMessage, type ConnectionStatus } from '@/hooks/useSocket';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAppSelector } from '@/hooks/useStore';
import { selectSessionId } from '@/store/sessionSlice';

interface ChatViewProps {
  messages: ChatMessage[];
  status: ConnectionStatus;
  isStreaming: boolean;
  onSendMessage: (msg: string, msgType?: string, mode?: string, userPrompt?: string) => boolean;
  onClearMessages: () => void;
  activeConnectorName?: string;
  activeNodeId?: string | null;
  completedNodeIds?: Set<string>;
  failedNodeIds?: Set<string>;
}

export default function ChatView({
  messages,
  status,
  isStreaming,
  onSendMessage,
  onClearMessages,
  activeConnectorName = 'GitHub',
  activeNodeId,
  completedNodeIds,
  failedNodeIds,
}: ChatViewProps) {
  const sessionId = useAppSelector(selectSessionId);
  const [inputVal, setInputVal] = useState('');
  const [savingMsgId, setSavingMsgId] = useState<string | null>(null);
  const [savedMsgId, setSavedMsgId] = useState<string | null>(null);
  const [chatMode, setChatMode] = useState<'chat' | 'agent'>('chat');
  
  // Schedule state
  const [schedulingPlanId, setSchedulingPlanId] = useState<string | null>(null);
  const [scheduleCron, setScheduleCron] = useState<string>('every_1_hour');
  
  // Memory Extraction UI State
  const [activeBookmarkIndex, setActiveBookmarkIndex] = useState<number | null>(null);
  const [extractionPrompt, setExtractionPrompt] = useState('');
  const [selectedChips, setSelectedChips] = useState<Set<number>>(new Set());
  
  // Plan Editing State
  const [editingPlanId, setEditingPlanId] = useState<string | null>(null);
  const [planEditContent, setPlanEditContent] = useState('');
  
  // Document Upload State
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !sessionId) return;
    
    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('conversation_id', sessionId);
    
    try {
      const res = await fetch('http://127.0.0.1:8000/api/documents/upload', {
        method: 'POST',
        body: formData,
      });
      if (res.ok) {
        // Create a fake system message locally to show the upload success
        onSendMessage(`[System] Successfully uploaded document: **${file.name}**. It is now available for RAG in Chat Mode.`, 'toast', chatMode);
      } else {
        onSendMessage(`[System] Failed to upload document.`, 'toast', chatMode);
      }
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

  const handleSaveWholeMessage = (index: number, msgId: string) => {
    setSavingMsgId(msgId);
    setActiveBookmarkIndex(null);
    const content = messages[index].content;
    onSendMessage(content, 'save_whole_message', chatMode);
    
    setTimeout(() => {
      setSavingMsgId(null);
      setSavedMsgId(msgId);
      setTimeout(() => setSavedMsgId(null), 2000);
    }, 1000);
  };

  const handleExtractSpecificFacts = (index: number, msgId: string) => {
    if (!extractionPrompt.trim()) return;
    setSavingMsgId(msgId);
    setActiveBookmarkIndex(null);
    
    // Gather context
    const startIndex = Math.max(0, index - 2);
    const contextWindow = messages
      .slice(startIndex, index + 1)
      .map(m => `[${m.role}] ${m.content}`)
      .join('\n\n');
      
    onSendMessage(contextWindow, 'extract_specific_facts', chatMode, extractionPrompt);
    setExtractionPrompt('');
    
    setTimeout(() => {
      setSavingMsgId(null);
      setSavedMsgId(msgId);
      setTimeout(() => setSavedMsgId(null), 2000);
    }, 1000);
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isStreaming]);

  const handleSend = () => {
    if (!inputVal.trim() || isStreaming || status !== 'connected') return;
    const sent = onSendMessage(inputVal, 'message', chatMode);
    if (sent) {
      setInputVal('');
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
    <div className="flex-1 flex flex-col h-full bg-[#F8F9FA] overflow-hidden">
      {/* ── Chat Messages Thread (Matching Image 2) ─────────────────────────── */}
      <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-6">
        {messages.length === 0 ? (
          /* Welcome Banner when starting new chat */
          <div className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto my-auto py-12">
            <div className="w-12 h-12 rounded-2xl bg-black flex items-center justify-center text-white text-xl mb-4 shadow-md">
              <Zap className="w-6 h-6 fill-white text-black" />
            </div>
            <h3 className="text-lg font-bold text-gray-900">How can Aegis assist you today?</h3>
            <p className="text-xs text-gray-500 mt-1.5 leading-relaxed">
              Connected to local AI engine & active MCP tools. Ask me to search documents, draft emails, query databases, or post updates.
            </p>
            <div className="grid grid-cols-2 gap-2 mt-6 w-full text-left">
              {[
                'Draft a follow-up email on Gmail',
                'Check recent pull requests on GitHub',
                'Find candidate resume in Google Drive',
                'Post status update to Slack channel'
              ].map((sample) => (
                <button
                  key={sample}
                  onClick={() => onSendMessage(sample, 'message', chatMode)}
                  className="p-3 rounded-xl bg-white border border-gray-200 text-xs text-gray-700 hover:border-gray-400 hover:shadow-sm transition-all"
                >
                  {sample}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg, index) => {
            const isUser = msg.role === 'user';
            const isSystem = msg.role === 'system';
            
            if (isSystem) {
              return (
                <div key={msg.id} className="flex justify-center my-4">
                  <div className="px-4 py-1.5 bg-gray-100 text-gray-500 text-xs font-medium rounded-full shadow-sm border border-gray-200">
                    {msg.content}
                  </div>
                </div>
              );
            }

            return (
              <div
                key={msg.id}
                className={`flex gap-3 max-w-4xl mx-auto ${isUser ? 'justify-end' : 'justify-start'}`}
              >
                {/* Assistant Icon (Matching Image 2) */}
                {!isUser && (
                  <div className="w-8 h-8 rounded-xl bg-black flex items-center justify-center text-white flex-shrink-0 mt-0.5 shadow-sm">
                    <Zap className="w-4 h-4 fill-white text-black" />
                  </div>
                )}

                {/* Message Bubble Container */}
                <div className={`space-y-2 max-w-2xl ${isUser ? 'items-end' : 'items-start'}`}>
                  {/* Sender Header */}
                  <div className={`flex items-center gap-2 text-[11px] text-gray-400 ${isUser ? 'justify-end' : 'justify-start'}`}>
                    <span className="font-semibold text-gray-700">{isUser ? 'You' : 'Aegis'}</span>
                    <span>•</span>
                    <span>
                      {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                    {!msg.isStreaming && (
                      <div className="relative">
                        <button
                          onClick={() => setActiveBookmarkIndex(activeBookmarkIndex === index ? null : index)}
                          disabled={savingMsgId === msg.id}
                          className={`ml-2 p-1 rounded transition-colors ${
                            savedMsgId === msg.id ? 'text-teal-500' : 'hover:text-gray-900 hover:bg-gray-100'
                          }`}
                          title="Save context to memory"
                        >
                          {savingMsgId === msg.id ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : savedMsgId === msg.id ? (
                            <Check className="w-3.5 h-3.5" />
                          ) : (
                            <Bookmark className="w-3.5 h-3.5" />
                          )}
                        </button>
                        
                        {activeBookmarkIndex === index && (
                          <div className="absolute top-full mt-1 right-0 w-64 bg-white rounded-xl shadow-lg border border-gray-100 p-2 z-10 flex flex-col gap-1 text-left">
                            <button
                              onClick={() => handleSaveWholeMessage(index, msg.id)}
                              className="text-left px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 rounded-lg w-full"
                            >
                              Save Entire Message
                            </button>
                            <div className="px-3 py-2 text-xs border-t border-gray-50 mt-1">
                              <span className="font-semibold text-gray-500 mb-1 block">Extract Specific Fact</span>
                              <div className="flex gap-1">
                                <input
                                  type="text"
                                  value={extractionPrompt}
                                  onChange={(e) => setExtractionPrompt(e.target.value)}
                                  placeholder="e.g. Priya's email"
                                  className="flex-1 bg-gray-50 border border-gray-200 rounded px-2 py-1 outline-none focus:border-indigo-400"
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleExtractSpecificFacts(index, msg.id);
                                  }}
                                />
                                <button
                                  onClick={() => handleExtractSpecificFacts(index, msg.id)}
                                  className="bg-indigo-600 text-white rounded px-2 hover:bg-indigo-700 transition-colors"
                                >
                                  Save
                                </button>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Message Body */}
                  {isUser ? (
                    /* User Speech Bubble (Black background matching Image 2) */
                    <div className="bg-black text-white rounded-2xl rounded-tr-sm p-4 text-xs leading-relaxed shadow-sm">
                      {msg.content}
                    </div>
                  ) : (
                    /* Assistant Message Card (White bordered card matching Image 2) */
                    <div className="bg-white border border-gray-200 rounded-2xl p-5 text-xs text-gray-800 leading-relaxed shadow-sm space-y-4">
                      {/* Formatted Markdown Content (hide JSON blocks of plan) */}
                      <div className="font-sans w-full overflow-hidden">
                        {msg.isStreaming && !msg.content ? (
                          <div className="flex items-center gap-1.5 h-6 opacity-70">
                            <div className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                            <div className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                            <div className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-bounce" style={{ animationDelay: '300ms' }} />
                          </div>
                        ) : (
                          <div className="prose prose-sm prose-slate max-w-none break-words marker:text-gray-400 prose-p:leading-relaxed">
                            <ReactMarkdown 
                              remarkPlugins={[remarkGfm]}
                              components={{
                                code({node, inline, className, children, ...props}) {
                                  // Hide JSON plan blocks from text rendering, as they are handled by Canvas
                                  if (msg.content.includes('Proposed Execution Plan') && className === 'language-json') {
                                    return null;
                                  }
                                  return <code className={className} {...props}>{children}</code>;
                                }
                              }}
                            >
                              {msg.content}
                            </ReactMarkdown>
                          </div>
                        )}
                      </div>

                      {/* Interactive Execution Plan Card (if plan response detected) */}
                      {msg.content.includes('Proposed Execution Plan') && (() => {
                        let parsedPlan = null;
                        try {
                          const jsonMatch = msg.content.match(/```json\n([\s\S]*?)\n```/);
                          if (jsonMatch && jsonMatch[1]) {
                            parsedPlan = JSON.parse(jsonMatch[1]);
                          }
                        } catch (e) {
                          console.error("Failed to parse plan json", e);
                        }
                        
                        return (
                          <div className="p-4 rounded-xl bg-gray-50 border border-gray-200 space-y-3">
                            <div className="flex items-center justify-between border-b border-gray-200 pb-2">
                              <span className="text-xs font-bold text-gray-900 flex items-center gap-1.5">
                                <CheckCircle2 className="w-4 h-4 text-teal-600" />
                                Execution Confirmation Required
                              </span>
                              <span className="text-[10px] text-gray-400">Human-In-The-Loop</span>
                            </div>
                            <div className="flex flex-col gap-2 pt-1">
                            {editingPlanId === msg.id ? (
                              <div className="space-y-2">
                                <textarea
                                  value={planEditContent}
                                  onChange={(e) => setPlanEditContent(e.target.value)}
                                  className="w-full text-xs font-mono bg-white border border-gray-200 rounded-lg p-3 h-48 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                                />
                                <div className="flex gap-2">
                                  <button
                                    onClick={() => {
                                      onSendMessage(`Please use exactly this updated plan:\n\n${planEditContent}`, 'message', chatMode);
                                      setEditingPlanId(null);
                                    }}
                                    className="px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 transition-all shadow-sm"
                                  >
                                    Submit Edit
                                  </button>
                                  <button
                                    onClick={() => setEditingPlanId(null)}
                                    className="px-4 py-2 rounded-xl bg-gray-200 text-gray-700 text-xs font-medium hover:bg-gray-300 transition-all"
                                  >
                                    Cancel Edit
                                  </button>
                                </div>
                              </div>
                            ) : schedulingPlanId === msg.id ? (
                              <div className="space-y-3 bg-white p-3 rounded-lg border border-gray-200">
                                <label className="block text-xs font-semibold text-gray-700">Select Schedule Interval</label>
                                <select 
                                  value={scheduleCron}
                                  onChange={(e) => setScheduleCron(e.target.value)}
                                  className="w-full bg-gray-50 border border-gray-200 rounded-lg p-2 text-xs outline-none focus:ring-2 focus:ring-indigo-100"
                                >
                                  <option value="every_1_min">Every 1 Minute (Test)</option>
                                  <option value="every_1_hour">Every 1 Hour</option>
                                  <option value="every_1_day">Every 1 Day</option>
                                </select>
                                <div className="flex gap-2 pt-2">
                                  <button
                                    onClick={() => {
                                      onSendMessage(scheduleCron, 'schedule_plan', chatMode, scheduleCron);
                                      setSchedulingPlanId(null);
                                    }}
                                    className="px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 transition-all shadow-sm"
                                  >
                                    Confirm Schedule
                                  </button>
                                  <button
                                    onClick={() => setSchedulingPlanId(null)}
                                    className="px-4 py-2 rounded-xl bg-gray-200 text-gray-700 text-xs font-medium hover:bg-gray-300 transition-all"
                                  >
                                    Cancel
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <div className="flex flex-wrap gap-2 mt-2">
                                <button
                                  onClick={() => onSendMessage('yes', 'message', chatMode)}
                                  className="px-4 py-2 rounded-xl bg-black text-white text-xs font-semibold hover:bg-neutral-800 transition-all shadow-sm flex items-center gap-1"
                                >
                                  Proceed & Execute
                                </button>
                                <button
                                  onClick={() => setSchedulingPlanId(msg.id)}
                                  className="px-4 py-2 rounded-xl bg-indigo-100 text-indigo-700 text-xs font-semibold hover:bg-indigo-200 transition-all shadow-sm flex items-center gap-1"
                                >
                                  Schedule Plan
                                </button>
                                <button
                                  onClick={() => {
                                    setEditingPlanId(msg.id);
                                    setPlanEditContent(msg.content);
                                  }}
                                  className="px-4 py-2 rounded-xl bg-gray-200 text-gray-700 text-xs font-medium hover:bg-gray-300 transition-all"
                                >
                                  Edit Payload
                                </button>
                                <button
                                  onClick={() => onSendMessage('cancel', 'message', chatMode)}
                                  className="px-4 py-2 rounded-xl border border-gray-200 text-red-600 bg-white text-xs font-medium hover:bg-red-50 transition-all"
                                >
                                  Cancel Plan
                                </button>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                      })()}
                      {/* Interactive Memory Extraction Card */}
                      {msg.content.includes('Proposed Memory Extraction:') && (
                        <div className="p-4 rounded-xl bg-indigo-50 border border-indigo-100 space-y-3">
                          <div className="flex items-center justify-between border-b border-indigo-200 pb-2">
                            <span className="text-xs font-bold text-indigo-900 flex items-center gap-1.5">
                              <Save className="w-4 h-4 text-indigo-600" />
                              Memory Extraction Proposed
                            </span>
                            <span className="text-[10px] text-indigo-400">Review Required</span>
                          </div>

                          <div className="flex flex-col gap-2 pt-1">
                            {editingPlanId === msg.id ? (
                              <div className="space-y-2">
                                <textarea
                                  value={planEditContent}
                                  onChange={(e) => setPlanEditContent(e.target.value)}
                                  className="w-full text-xs font-mono bg-white border border-indigo-200 rounded-lg p-3 h-48 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                                />
                                <div className="flex gap-2">
                                  <button
                                    onClick={() => {
                                      onSendMessage(`Please use exactly this updated memory:\n\n${planEditContent}`, 'message', chatMode);
                                      setEditingPlanId(null);
                                    }}
                                    className="px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 transition-all shadow-sm"
                                  >
                                    Submit Memory Edit
                                  </button>
                                  <button
                                    onClick={() => setEditingPlanId(null)}
                                    className="px-4 py-2 rounded-xl bg-indigo-200 text-indigo-800 text-xs font-medium hover:bg-indigo-300 transition-all"
                                  >
                                    Cancel Edit
                                  </button>
                                </div>
                              </div>
                            ) : (() => {
                              let parsedChips = null;
                              try {
                                const rawJsonStr = msg.content.split('└ *Preview:*\\n> ')[1]?.replace(/\\n> /g, '\\n') || '';
                                if (!rawJsonStr) {
                                  // fallback if formatting differs
                                  const jsonMatch = msg.content.match(/```json\n([\s\S]*?)\n```/);
                                  if (jsonMatch) parsedChips = JSON.parse(jsonMatch[1]);
                                  else {
                                    // Try to parse the blockquote preview
                                    const previewParts = msg.content.split('Preview:*');
                                    if(previewParts.length > 1) {
                                      const text = previewParts[1].replace(/> /g, '').trim();
                                      parsedChips = JSON.parse(text);
                                    }
                                  }
                                } else {
                                  parsedChips = JSON.parse(rawJsonStr);
                                }
                              } catch(e) {}

                              return (
                                <div>
                                  {parsedChips && Array.isArray(parsedChips) && (
                                    <div className="flex flex-wrap gap-2 mb-4">
                                      {parsedChips.map((chip, idx) => (
                                        <button
                                          key={idx}
                                          onClick={() => {
                                            const next = new Set(selectedChips);
                                            if (next.has(idx)) next.delete(idx);
                                            else next.add(idx);
                                            setSelectedChips(next);
                                          }}
                                          className={`px-3 py-2 rounded-lg text-xs font-medium border text-left flex flex-col gap-1 transition-all ${
                                            selectedChips.has(idx) 
                                              ? 'bg-indigo-600 border-indigo-600 text-white shadow-md' 
                                              : 'bg-white border-indigo-200 text-indigo-900 hover:bg-indigo-50'
                                          }`}
                                        >
                                          <strong>{chip.label}</strong>
                                          <span className={`text-[10px] ${selectedChips.has(idx) ? 'text-indigo-200' : 'text-indigo-500'}`}>
                                            Type: {chip.type}
                                          </span>
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                  
                                  <div className="flex flex-wrap gap-2">
                                    <button
                                      onClick={() => {
                                        if (parsedChips && Array.isArray(parsedChips) && selectedChips.size > 0) {
                                          const selected = Array.from(selectedChips).map(idx => (idx + 1).toString()).join(' ');
                                          onSendMessage(selected, 'message', chatMode);
                                        } else {
                                          onSendMessage('yes', 'message', chatMode);
                                        }
                                        setSelectedChips(new Set());
                                      }}
                                      className="px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 transition-all shadow-sm"
                                    >
                                      {selectedChips.size > 0 ? `Save Selected (${selectedChips.size})` : 'Save All'}
                                    </button>
                                    <button
                                      onClick={() => {
                                        setEditingPlanId(msg.id);
                                        let rawJsonStr = '';
                                        const previewParts = msg.content.split('Preview:*');
                                        if(previewParts.length > 1) {
                                          rawJsonStr = previewParts[1].replace(/> /g, '').trim();
                                        }
                                        setPlanEditContent(rawJsonStr);
                                      }}
                                      className="px-4 py-2 rounded-xl bg-indigo-100 text-indigo-700 text-xs font-medium hover:bg-indigo-200 transition-all"
                                    >
                                      Edit Details
                                    </button>
                                    <button
                                      onClick={() => {
                                        onSendMessage('no', 'message', chatMode);
                                        setSelectedChips(new Set());
                                      }}
                                      className="px-4 py-2 rounded-xl border border-indigo-200 text-red-600 bg-white text-xs font-medium hover:bg-red-50 transition-all"
                                    >
                                      Cancel Save
                                    </button>
                                  </div>
                                </div>
                              );
                            })()}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* User Icon */}
                {isUser && (
                  <div className="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center text-white flex-shrink-0 mt-0.5 shadow-sm">
                    <UserIcon className="w-4 h-4" />
                  </div>
                )}
              </div>
            );
          })
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* ── Bottom Input Bar (Matching Image 2) ─────────────────────────────── */}
      <div className="p-6 pt-2 bg-[#F8F9FA] max-w-4xl w-full mx-auto space-y-3">
        {/* Mode Toggle */}
        <div className="flex justify-center">
          <div className="bg-gray-200/50 p-1 rounded-lg flex items-center gap-1">
            <button
              onClick={() => {
                if (chatMode === 'agent') onSendMessage('__system_mode_switch__', 'system');
                setChatMode('chat');
              }}
              className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all ${
                chatMode === 'chat' 
                  ? 'bg-white text-gray-900 shadow-sm' 
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              Chat
            </button>
            <button
              onClick={() => {
                if (chatMode === 'chat') onSendMessage('__system_mode_switch__', 'system');
                setChatMode('agent');
              }}
              className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all flex items-center gap-1.5 ${
                chatMode === 'agent' 
                  ? 'bg-white text-indigo-600 shadow-sm' 
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              Agent
            </button>
          </div>
        </div>

        <div className={`relative bg-white border rounded-2xl shadow-sm focus-within:ring-1 transition-all ${
          chatMode === 'agent' 
            ? 'border-indigo-200 focus-within:border-indigo-500 focus-within:ring-indigo-500/20' 
            : 'border-gray-200 focus-within:border-black focus-within:ring-black'
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
            className="w-full bg-transparent text-xs text-gray-900 placeholder:text-gray-400 resize-none px-4 pt-3.5 pb-12 focus:outline-none leading-relaxed"
            style={{ minHeight: '52px' }}
          />

          {/* Input Bar Bottom Toolbar */}
          <div className="absolute bottom-2.5 left-3 right-3 flex items-center justify-between pointer-events-none">
            {/* Attachment Button */}
            <div className="pointer-events-auto">
              <input 
                type="file" 
                ref={fileInputRef} 
                onChange={handleFileUpload} 
                className="hidden" 
                accept=".txt,.md,.pdf,.ppt,.pptx,.png,.jpg,.jpeg"
              />
              <button 
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
                className={`p-1.5 transition-colors ${isUploading ? 'text-indigo-400 animate-pulse' : 'text-gray-400 hover:text-gray-700'}`}
                title="Upload Document for RAG"
              >
                {isUploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Paperclip className="w-4 h-4" />}
              </button>
            </div>

            {/* Right Buttons: Sparkles + Dark Send Button */}
            <div className="flex items-center gap-2 pointer-events-auto">
              <button className="p-1.5 text-purple-600 hover:text-purple-800 transition-colors">
                <Sparkles className="w-4 h-4" />
              </button>

              <button
                onClick={handleSend}
                disabled={!inputVal.trim() || isStreaming || status !== 'connected'}
                className={`w-8 h-8 rounded-xl text-white flex items-center justify-center transition-all shadow-sm disabled:opacity-30 ${
                  chatMode === 'agent' 
                    ? 'bg-indigo-600 hover:bg-indigo-700 disabled:hover:bg-indigo-600' 
                    : 'bg-black hover:bg-neutral-800 disabled:hover:bg-black'
                }`}
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>

        {/* Footer Disclaimer */}
        <p className="text-center text-[10px] text-gray-400 mt-2">
          Press <span className="font-semibold text-gray-600">Enter</span> to send • Aegis can generate errors. Verify important information.
        </p>
      </div>
    </div>
  );
}
