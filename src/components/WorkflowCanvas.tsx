import React, { useMemo } from 'react';
import { CheckCircle2, Circle, PlayCircle, AlertCircle } from 'lucide-react';

interface PlanStep {
  step_id: string;
  tool: string;
  reason?: string;
  payload_preview?: string;
  depends_on?: string[];
  foreach?: string | null;
}

interface WorkflowCanvasProps {
  plan: PlanStep[];
  activeNodeId?: string | null;
  completedNodeIds?: Set<string>;
  failedNodeIds?: Set<string>;
}

export const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({ 
  plan, 
  activeNodeId, 
  completedNodeIds = new Set(),
  failedNodeIds = new Set() 
}) => {
  // 1. DAG Level Assignment (Deterministic Mapping Layer)
  const { levels, fallback } = useMemo(() => {
    const levelsMap = new Map<string, number>();
    const maxDepth = plan.length + 1;
    let fallback = false;

    // Initialize all with no dependencies to level 0
    plan.forEach(step => {
      if (!step.depends_on || step.depends_on.length === 0) {
        levelsMap.set(step.step_id, 0);
      }
    });

    // Iteratively resolve dependencies
    let changed = true;
    let iterations = 0;
    while (changed && iterations < maxDepth) {
      changed = false;
      iterations++;

      plan.forEach(step => {
        if (levelsMap.has(step.step_id)) return; // Already resolved

        const deps = step.depends_on || [];
        // Check if all dependencies are resolved
        const allResolved = deps.every(depId => levelsMap.has(depId));
        if (allResolved) {
          const maxDepLevel = Math.max(...deps.map(depId => levelsMap.get(depId) as number));
          levelsMap.set(step.step_id, maxDepLevel + 1);
          changed = true;
        }
      });
    }

    // If some nodes are unassigned, there's a cycle or missing dependency -> Fallback
    if (levelsMap.size < plan.length) {
      fallback = true;
    }

    // Group by levels
    const levels: PlanStep[][] = [];
    if (!fallback) {
      for (const [id, level] of levelsMap.entries()) {
        if (!levels[level]) levels[level] = [];
        const step = plan.find(s => s.step_id === id);
        if (step) levels[level].push(step);
      }
    }

    return { levels, fallback };
  }, [plan]);

  if (fallback) {
    return (
      <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-4 my-2">
        <div className="flex items-center gap-2 text-yellow-800 text-xs font-semibold mb-2">
          <AlertCircle className="w-4 h-4" />
          Showing simplified view — plan structure could not be mapped safely.
        </div>
        <div className="space-y-3">
          {plan.map((step, idx) => (
            <div key={step.step_id || idx} className="bg-white p-3 rounded-lg border border-yellow-100 text-xs shadow-sm">
              <strong className="text-gray-900">{idx + 1}. {step.tool}</strong>
              <p className="text-gray-600 mt-1">{step.reason}</p>
              {step.payload_preview && (
                <div className="mt-2 text-gray-500 font-mono text-[10px] bg-gray-50 p-2 rounded">
                  {step.payload_preview}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-slate-50 border border-slate-200 rounded-xl p-6 overflow-x-auto my-4 shadow-inner relative">
      <div className="flex gap-12 items-start min-w-max">
        {levels.map((column, levelIdx) => (
          <div key={levelIdx} className="flex flex-col gap-6 relative z-10">
            {column.map((step) => {
              const isActive = activeNodeId === step.step_id;
              const isCompleted = completedNodeIds.has(step.step_id);
              const isFailed = failedNodeIds.has(step.step_id);
              
              let statusColor = "border-slate-200 bg-white shadow-sm";
              let icon = <Circle className="w-4 h-4 text-slate-300" />;
              
              if (isActive) {
                statusColor = "border-blue-500 bg-blue-50 shadow-md ring-2 ring-blue-100";
                icon = <PlayCircle className="w-4 h-4 text-blue-500 animate-pulse" />;
              } else if (isFailed) {
                statusColor = "border-red-400 bg-red-50";
                icon = <AlertCircle className="w-4 h-4 text-red-500" />;
              } else if (isCompleted) {
                statusColor = "border-teal-400 bg-teal-50 opacity-80";
                icon = <CheckCircle2 className="w-4 h-4 text-teal-600" />;
              }

              return (
                <div 
                  key={step.step_id} 
                  className={`w-64 p-4 rounded-xl border ${statusColor} transition-all duration-300`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[10px] font-mono text-slate-500 truncate pr-2" title={step.tool}>
                      {step.tool}
                    </span>
                    {icon}
                  </div>
                  <div className="text-xs font-medium text-slate-800 line-clamp-2" title={step.reason}>
                    {step.reason || "Execute tool step"}
                  </div>
                  {step.foreach && (
                    <div className="mt-3 text-[10px] font-semibold bg-indigo-100 text-indigo-700 px-2 py-1 rounded inline-flex items-center gap-1">
                      ↺ Foreach: {step.foreach}
                    </div>
                  )}
                  {step.payload_preview && (
                    <div className="mt-3 text-[10px] font-mono text-slate-500 line-clamp-3 bg-slate-100/50 p-2 rounded border border-slate-100">
                      {step.payload_preview}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      
      {/* 
        Optional: Background SVG to draw wires between nodes.
        Since we have columns, drawing bezier curves between step elements is possible if we tracked refs,
        but for a purely structural lightweight canvas, placing them in left-to-right columns implicitly shows the DAG.
      */}
    </div>
  );
};
