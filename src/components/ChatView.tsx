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
  X
} from 'lucide-react';
import { type ChatMessage, type ConnectionStatus } from '@/hooks/useSocket';

interface ChatViewProps {
  messages: ChatMessage[];
  status: ConnectionStatus;
  isStreaming: boolean;
  onSendMessage: (msg: string) => boolean;
  onClearMessages: () => void;
  activeConnectorName?: string;
}

export default function ChatView({
  messages,
  status,
  isStreaming,
  onSendMessage,
  onClearMessages,
  activeConnectorName = 'GitHub',
}: ChatViewProps) {
  const [inputVal, setInputVal] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isStreaming]);

  const handleSend = () => {
    if (!inputVal.trim() || isStreaming || status !== 'connected') return;
    const sent = onSendMessage(inputVal);
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
                  onClick={() => onSendMessage(sample)}
                  className="p-3 rounded-xl bg-white border border-gray-200 text-xs text-gray-700 hover:border-gray-400 hover:shadow-sm transition-all"
                >
                  {sample}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === 'user';

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
                      {/* Formatted Markdown Content */}
                      <div className="whitespace-pre-wrap font-sans">
                        {msg.content}
                      </div>

                      {/* Interactive Execution Plan Card (if plan response detected) */}
                      {msg.content.includes('Proposed Execution Plan') && (
                        <div className="p-4 rounded-xl bg-gray-50 border border-gray-200 space-y-3">
                          <div className="flex items-center justify-between border-b border-gray-200 pb-2">
                            <span className="text-xs font-bold text-gray-900 flex items-center gap-1.5">
                              <CheckCircle2 className="w-4 h-4 text-teal-600" />
                              Execution Confirmation Required
                            </span>
                            <span className="text-[10px] text-gray-400">Human-In-The-Loop</span>
                          </div>

                          <div className="flex items-center gap-2 pt-1">
                            <button
                              onClick={() => onSendMessage('yes')}
                              className="px-4 py-2 rounded-xl bg-black text-white text-xs font-semibold hover:bg-neutral-800 transition-all shadow-sm"
                            >
                              Proceed & Execute
                            </button>
                            <button
                              onClick={() => onSendMessage('no')}
                              className="px-4 py-2 rounded-xl bg-gray-200 text-gray-700 text-xs font-medium hover:bg-gray-300 transition-all"
                            >
                              Cancel / Edit
                            </button>
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
      <div className="p-6 pt-2 bg-[#F8F9FA] max-w-4xl w-full mx-auto">
        <div className="relative bg-white border border-gray-200 rounded-2xl shadow-sm focus-within:border-black focus-within:ring-1 focus-within:ring-black transition-all">
          <textarea
            ref={textareaRef}
            value={inputVal}
            onChange={(e) => {
              setInputVal(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
            }}
            onKeyDown={handleKeyDown}
            placeholder={status === 'connected' ? 'Message Aegis...' : 'Connecting to backend...'}
            disabled={status !== 'connected'}
            rows={1}
            className="w-full bg-transparent text-xs text-gray-900 placeholder:text-gray-400 resize-none px-4 pt-3.5 pb-12 focus:outline-none leading-relaxed"
            style={{ minHeight: '52px' }}
          />

          {/* Input Bar Bottom Toolbar */}
          <div className="absolute bottom-2.5 left-3 right-3 flex items-center justify-between pointer-events-none">
            {/* Attachment Button */}
            <button className="pointer-events-auto p-1.5 text-gray-400 hover:text-gray-700 transition-colors">
              <Paperclip className="w-4 h-4" />
            </button>

            {/* Right Buttons: Sparkles + Dark Send Button */}
            <div className="flex items-center gap-2 pointer-events-auto">
              <button className="p-1.5 text-purple-600 hover:text-purple-800 transition-colors">
                <Sparkles className="w-4 h-4" />
              </button>

              <button
                onClick={handleSend}
                disabled={!inputVal.trim() || isStreaming || status !== 'connected'}
                className="w-8 h-8 rounded-xl bg-black text-white flex items-center justify-center hover:bg-neutral-800 disabled:opacity-30 disabled:hover:bg-black transition-all shadow-sm"
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
