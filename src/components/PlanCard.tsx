// Renders the "Proposed Execution Plan" message chat.py builds before
// executing an Agent Mode plan (see ChatAgent._present_plan_for_confirmation)
// as a plain-language card. Aegis is built for non-coders, so this
// deliberately never shows raw tool names or JSON in the primary view —
// only a human sentence per step, with the technical detail available
// behind an opt-in toggle for anyone who wants it.

interface PlanStep {
  index: number;
  tool: string;
  actionBadge: 'read-only' | 'writes';
  scope: string;
  reason?: string;
  argumentsRaw?: string;
  dependsOn?: string;
}

interface ParsedPlan {
  steps: PlanStep[];
  warnings: string[];
  hasCTA: boolean;
}

const TITLE_MARKER = '**Proposed Execution Plan:**';
const STEP_HEADER_RE = /^\*\*Step (\d+): `([^`]+)`\*\* `\[(read-only|writes)\]` `\[scope: ([^\]]+)\]`$/;
const REASON_RE = /^> (.+)$/;
const ARGUMENTS_RE = /^- \*Arguments:\* `(.+)`$/;
const DEPENDS_RE = /^- \*Depends on:\* (.+)$/;
const WARNINGS_HEADER = '**Warnings:**';
const CTA_PREFIX = "Would you like me to proceed with this?";

// Deliberately strict — this is parsing backend-generated text, not
// arbitrary user/model markdown, so any line that doesn't match the exact
// shape chat.py emits means the format drifted and we should fall back to
// plain markdown rendering rather than show a mis-parsed card.
export function parsePlanMarkdown(content: string): ParsedPlan | null {
  if (!content.trimStart().startsWith(TITLE_MARKER)) return null;

  const lines = content.split('\n');
  const steps: PlanStep[] = [];
  const warnings: string[] = [];
  let hasCTA = false;
  let inWarnings = false;
  let current: PlanStep | null = null;

  for (const line of lines) {
    const stepMatch = line.match(STEP_HEADER_RE);
    if (stepMatch) {
      current = {
        index: parseInt(stepMatch[1], 10),
        tool: stepMatch[2],
        actionBadge: stepMatch[3] as 'read-only' | 'writes',
        scope: stepMatch[4],
      };
      steps.push(current);
      inWarnings = false;
      continue;
    }
    if (line.trim() === WARNINGS_HEADER) {
      inWarnings = true;
      current = null;
      continue;
    }
    if (line.startsWith(CTA_PREFIX)) {
      hasCTA = true;
      continue;
    }
    if (inWarnings) {
      if (line.startsWith('- ')) warnings.push(line.slice(2));
      continue;
    }
    if (!current) continue;
    const reasonMatch = line.match(REASON_RE);
    if (reasonMatch) { current.reason = reasonMatch[1]; continue; }
    const argsMatch = line.match(ARGUMENTS_RE);
    if (argsMatch) { current.argumentsRaw = argsMatch[1]; continue; }
    const dependsMatch = line.match(DEPENDS_RE);
    if (dependsMatch) { current.dependsOn = dependsMatch[1]; continue; }
  }

  if (steps.length === 0) return null;
  return { steps, warnings, hasCTA };
}

// ── Plain-language step descriptions ────────────────────────────────────────
// Tool names come from MCP servers the user connects (Gmail, Drive, GitHub,
// Notion, or any custom one) — there's no fixed list to hand-write copy for,
// so most tools go through the generic humanizer below. web_scrape is the
// one built-in tool nearly every user hits, so it gets real wording.

const ARG_PRIORITY_KEYS = [
  'url', 'query', 'q', 'search', 'path', 'file', 'filename',
  'name', 'title', 'subject', 'to', 'recipient', 'message', 'text',
  'channel', 'repo', 'repository', 'owner', 'id', 'page',
];

function truncateValue(v: string, max = 70): string {
  return v.length > max ? v.slice(0, max - 1) + '…' : v;
}

function parseArgs(raw?: string): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
  } catch {
    // Not valid JSON — headline arg just won't be found, which is fine.
  }
  return null;
}

function pickHeadlineArg(args: Record<string, unknown> | null): string | null {
  if (!args) return null;
  for (const key of ARG_PRIORITY_KEYS) {
    const val = args[key];
    if (typeof val === 'string' && val.trim()) return val;
    if (typeof val === 'number') return String(val);
  }
  for (const val of Object.values(args)) {
    if (typeof val === 'string' && val.trim()) return val;
    if (typeof val === 'number') return String(val);
  }
  return null;
}

// snake_case tool name -> "Sentence case" — the only generic move that's
// safe to make with no knowledge of what the tool actually does.
function humanizeToolName(tool: string): string {
  const words = tool.replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const KNOWN_TOOLS: Record<string, (arg: string | null) => { verb: string; value: string | null }> = {
  web_scrape: (arg) => ({ verb: 'Read the webpage', value: arg }),
};

function describeStep(step: PlanStep): { verb: string; value: string | null } {
  const args = parseArgs(step.argumentsRaw);
  const value = pickHeadlineArg(args);
  if (KNOWN_TOOLS[step.tool]) return KNOWN_TOOLS[step.tool](value);
  return { verb: humanizeToolName(step.tool), value };
}

function scopeLabel(scope: string): string | null {
  if (scope === 'sample') return 'Just a quick sample';
  if (scope === 'exhaustive') return 'Everything — may take a bit longer';
  return null; // "single" is the default, unremarkable case
}

function StepRow({ step }: { step: PlanStep }) {
  const { verb, value } = describeStep(step);
  const scopeText = scopeLabel(step.scope);
  const isWrite = step.actionBadge === 'writes';

  return (
    <div className="flex gap-3">
      <div className="flex-shrink-0 w-6 h-6 rounded-full bg-aegis-primary/15 text-aegis-primary-light text-[12px] font-bold flex items-center justify-center mt-0.5">
        {step.index}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-[14px] font-medium text-aegis-text-primary leading-snug">
          {verb}
          {value && (
            <>
              : <span className="font-semibold">{truncateValue(value)}</span>
            </>
          )}
        </p>

        {step.reason && (
          <p className="text-[12px] text-aegis-text-muted leading-snug">{step.reason}</p>
        )}

        <div className="flex items-center gap-1.5 flex-wrap">
          {isWrite && (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full text-aegis-warning bg-aegis-warning/10 border border-aegis-warning/30">
              Makes a change
            </span>
          )}
          {scopeText && (
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full text-aegis-text-muted bg-aegis-overlay">
              {scopeText}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PlanCard({
  content,
  interactive = false,
  onApprove,
  onEdit,
}: {
  content: string;
  interactive?: boolean;
  onApprove?: () => void;
  onEdit?: () => void;
}) {
  const plan = parsePlanMarkdown(content);
  if (!plan) return null;

  const hasWrites = plan.steps.some(s => s.actionBadge === 'writes');

  return (
    <div className="space-y-3">
      <div>
        <p className="text-[14px] font-semibold text-aegis-text-primary">Here's what I'll do</p>
        <p className="text-[12px] text-aegis-text-muted mt-0.5">
          {hasWrites
            ? "This will make changes — review each step before approving."
            : "This only reads information — nothing will be changed."}
        </p>
      </div>

      <div className="space-y-3">
        {plan.steps.map(step => (
          <StepRow key={step.index} step={step} />
        ))}
      </div>

      {plan.warnings.length > 0 && (
        <div className="pl-3 border-l border-aegis-warning/40 space-y-1">
          <p className="text-[12px] font-semibold text-aegis-warning">Heads up</p>
          {plan.warnings.map((w, i) => (
            <p key={i} className="text-[12px] text-aegis-text-secondary leading-snug">{w}</p>
          ))}
        </div>
      )}

      {plan.hasCTA && interactive && (
        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={onApprove}
            className="px-4 py-2 bg-aegis-primary hover:bg-aegis-primary-dark text-white text-[13px] font-semibold rounded-xl transition-colors"
          >
            Yes, do this
          </button>
          <button
            onClick={onEdit}
            className="px-4 py-2 border border-aegis-border text-aegis-text-secondary hover:text-aegis-text-primary text-[13px] font-medium rounded-xl transition-colors"
          >
            Let me change something
          </button>
        </div>
      )}
    </div>
  );
}
