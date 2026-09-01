import { useState, useRef, useEffect } from 'react';
import { Sparkles, ChevronDown, ChevronRight, Zap } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChatMessage } from '@/hooks/useSocket';

interface AgentThinkingProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingContent?: string;
}

export default function AgentThinking({ messages, isStreaming, streamingContent }: AgentThinkingProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll inside the expanded view when new content arrives
  useEffect(() => {
    if (isExpanded && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streamingContent, isExpanded]);

  if (messages.length === 0 && !streamingContent) return null;

  return (
    <div className="max-w-4xl w-full flex gap-3 mx-auto justify-start mb-4">
      {/* Icon */}
      <div className="w-8 h-8 rounded-xl bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600 flex-shrink-0 mt-0.5 shadow-sm">
        <Sparkles className="w-4 h-4" />
      </div>

      <div className="w-full flex flex-col space-y-2 max-w-2xl">
        {/* Toggle Button */}
        <button 
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center justify-between bg-white border border-indigo-100/50 rounded-xl p-3 shadow-sm hover:bg-gray-50 transition-colors text-left"
        >
          <div className="flex items-center gap-2">
            {isStreaming ? (
              <div className="flex items-center gap-1.5 h-4 opacity-60">
                <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            ) : (
              <CheckCircle className="w-4 h-4 text-green-500" />
            )}
            <span className="text-xs font-semibold text-gray-700">
              {isStreaming ? 'Agent is working...' : 'Agent finished tasks'}
            </span>
            <span className="text-[10px] font-medium text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-md">
              {messages.length} steps
            </span>
          </div>
          {isExpanded ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
        </button>

        {/* Collapsible Content */}
        {isExpanded && (
          <div 
            ref={scrollRef}
            className="bg-gray-50 border border-gray-200/60 rounded-xl p-4 max-h-[400px] overflow-y-auto space-y-4 shadow-inner"
          >
            {messages.map((msg, i) => (
              <div key={msg.id} className="relative pl-4 border-l-2 border-indigo-100">
                <div className="absolute -left-[5px] top-1.5 w-2 h-2 rounded-full bg-indigo-300" />
                
                {msg.content.includes('__PAGINATION_CAP__') ? (
                  <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs shadow-sm">
                    <div className="flex items-start gap-2 mb-2">
                      <span className="text-sm mt-0.5">⚠️</span>
                      <p className="font-bold text-amber-900 mt-0.5">Pagination Limit Reached</p>
                    </div>
                    <p className="text-[11px] text-amber-800 leading-relaxed italic">
                      The tool returned too much data and was truncated. The agent will continue with the fetched data.
                    </p>
                  </div>
                ) : msg.msgType === 'tool_call' ? (
                  <div className="bg-white border border-gray-200 rounded-lg p-3 text-xs shadow-sm">
                    <div className="prose prose-sm prose-slate max-w-none prose-p:leading-relaxed break-words">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : (
                  <div className="text-[11px] text-gray-600 leading-relaxed italic">
                    {msg.content}
                  </div>
                )}
              </div>
            ))}

            {/* Current Streaming Thought */}
            {isStreaming && streamingContent && (
              <div className="relative pl-4 border-l-2 border-indigo-200 animate-pulse">
                <div className="absolute -left-[5px] top-1.5 w-2 h-2 rounded-full bg-indigo-400" />
                <div className="text-[11px] text-gray-600 leading-relaxed italic">
                  {streamingContent}
                  <span className="inline-block w-1.5 h-3 ml-1 bg-gray-400 animate-pulse align-middle rounded-sm" />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function CheckCircle(props: any) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
      <polyline points="22 4 12 14.01 9 11.01"></polyline>
    </svg>
  );
}
