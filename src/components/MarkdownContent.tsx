import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Shared Claude-style prose styling for every assistant/tool-call bubble in
// the app (chat responses, streaming bubble, agent tool-call cards) — one
// place to keep code blocks readable and headers/lists consistent instead of
// each call site re-deriving its own prose-* class soup.
const PROSE_CLASSES =
  'prose prose-sm prose-slate max-w-none break-words ' +
  'marker:text-aegis-text-muted prose-p:leading-relaxed ' +
  'prose-headings:text-aegis-text-primary prose-h3:text-sm prose-h3:font-bold prose-h3:mb-2 prose-h3:mt-0 ' +
  'prose-strong:text-aegis-text-primary prose-li:text-aegis-text-secondary prose-li:leading-snug ' +
  'prose-a:text-aegis-primary prose-a:no-underline hover:prose-a:underline ' +
  'prose-code:bg-aegis-overlay prose-code:text-aegis-text-primary prose-code:font-medium ' +
  'prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-[11px] ' +
  'prose-code:before:content-none prose-code:after:content-none ' +
  'prose-pre:bg-[#0d1117] prose-pre:rounded-xl prose-pre:p-3 prose-pre:my-2 ' +
  'prose-pre:text-[11px] prose-pre:leading-relaxed prose-pre:shadow-inner ' +
  'prose-blockquote:border-aegis-primary/30 prose-blockquote:text-aegis-text-secondary prose-blockquote:not-italic prose-blockquote:font-normal ' +
  'prose-table:text-[11px]';

interface MarkdownContentProps {
  content: string;
  className?: string;
}

export default function MarkdownContent({ content, className = '' }: MarkdownContentProps) {
  return (
    <div className={`${PROSE_CLASSES} ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
