// A minimal, structural shape — WorkflowsView.tsx's own ToolDef (and
// anything else with an inputSchema.properties map) satisfies this without
// needing to import a shared type, so this stays a plain function any
// caller can use with its own tool-def shape.
interface ToolSchemaLike {
  inputSchema?: { properties?: Record<string, { type?: string } | undefined> };
}

// A tool whose schema requires an object/array parameter (an IAM policy
// document, a Lambda payload, ...) can't be filled in through the flat
// single-line "Fixed values" inputs without the user hand-writing valid
// JSON — and even then, static mode used to send it as a literal string,
// not a parsed value. Defaulting these to AI step means the user just
// describes what they want in plain English instead of ever hitting that.
// See WorkflowsView.tsx's addToolNode/tool-select handler for where this
// decides the node's initial isAi default.
export function toolNeedsStructuredInput(tool: ToolSchemaLike | undefined | null): boolean {
  const properties = tool?.inputSchema?.properties || {};
  return Object.values(properties).some((meta) => meta?.type === 'object' || meta?.type === 'array');
}
