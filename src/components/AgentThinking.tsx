import { useState, useRef, useEffect } from 'react';
import { ChatMessage } from '@/hooks/useSocket';
import MarkdownContent from './MarkdownContent';

interface AgentThinkingProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingContent?: string;
}

// Claude-style tool-call presentation: no avatar, no card, no spinner or
// bouncing dots — a single plain-text toggle line ("Working…" while active,
// shimmering the same way the "Thinking…" label does; "Used N tools" once
// done) that expands into the actual tool results, styled as flat text.
export default function AgentThinking({ messages, isStreaming, streamingContent }: AgentThinkingProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isExpanded && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streamingContent, isExpanded]);

  // Only suppress entirely when there's truly nothing to show — a turn that
  // isn't streaming and has no prior tool calls. While isStreaming is true,
  // always render the label (even with empty messages/streamingContent —
  // that's exactly the gap before the plan's first token arrives), so Agent
  // Mode never goes silent between hitting Enter and the first token, the
  // same guarantee Chat Mode's "Thinking…" label already makes.
  if (!isStreaming && messages.length === 0 && !streamingContent) return null;

  // This group can also hold 'thought' entries — raw in-progress narration
  // text ("Executing Task 1/1: Calling `web_scrape`...") that gets flushed
  // into its own message whenever streamed text arrives just before a
  // step_result. Only 'tool_call' entries are real finished tool calls, so
  // counting messages.length directly overstates it (a single-tool turn
  // reads "Used 2 tools" — the thought narration plus the one real result —
  // until reload, when the unpersisted thought entry is gone and it
  // corrects itself to "Used 1 tool". Count the right thing from the start.
  const toolCallCount = messages.filter(m => m.msgType === 'tool_call').length;
  const label = isStreaming
    ? 'Working…'
    : `Used ${toolCallCount} tool${toolCallCount === 1 ? '' : 's'}`;

  return (
    <div className="max-w-4xl mx-auto">
      <div className="max-w-2xl w-full space-y-2">
        <button
          onClick={() => setIsExpanded(v => !v)}
          className="text-[13px] text-left transition-colors"
        >
          <span className={isStreaming ? 'text-shimmer animate-shimmer' : 'text-aegis-text-muted hover:text-aegis-text-secondary'}>
            {label}
          </span>
        </button>

        {isExpanded && (
          <div
            ref={scrollRef}
            className="pl-3 border-l border-aegis-border space-y-3 max-h-[420px] overflow-y-auto animate-fade-in"
          >
            {messages.map((msg) => (
              <div key={msg.id} className="text-[13px] group">
                {msg.content.includes('__PAGINATION_CAP__') ? (
                  <p className="text-aegis-text-muted italic">
                    The tool returned too much data and was truncated. The agent will continue with the fetched data.
                  </p>
                ) : msg.msgType === 'tool_call' ? (
                  <MarkdownContent content={msg.content} />
                ) : (
                  <p className="text-aegis-text-muted italic">{msg.content}</p>
                )}
              </div>
            ))}

            {isStreaming && streamingContent && (
              <p className="text-[13px] text-aegis-text-muted italic animate-fade-in">{streamingContent}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
