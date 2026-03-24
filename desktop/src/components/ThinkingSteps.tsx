import { useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  WrenchIcon,
  MessageCircleIcon,
  CheckCircle2Icon,
  LoaderIcon,
} from "lucide-react";

// ── Types ──

export interface ThoughtStep {
  type: "narration" | "tool_call" | "tool_result";
  round: number;
  content?: string;
  toolName?: string;
  args?: Record<string, unknown>;
  result?: string;
}

interface ThinkingStepsProps {
  steps: ThoughtStep[];
  isActive: boolean; // still receiving thought events
}

// ── Sub-components ──

function ToolArgs({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args);
  if (entries.length === 0) return null;

  return (
    <div className="mt-1 ml-5 text-xs text-muted-foreground font-mono space-y-0.5">
      {entries.map(([key, value]) => {
        let display = String(value);
        if (display.length > 80) display = display.slice(0, 80) + "…";
        return (
          <div key={key}>
            <span className="text-muted-foreground/70">{key}:</span>{" "}
            <span>{display}</span>
          </div>
        );
      })}
    </div>
  );
}

function ToolResult({ result }: { result: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = result.length > 120;
  const display = !expanded && isLong ? result.slice(0, 120) + "…" : result;

  return (
    <div className="mt-1 ml-5">
      <button
        className="text-xs text-muted-foreground/70 hover:text-muted-foreground flex items-center gap-1 transition-colors"
        onClick={() => setExpanded((v) => !v)}
        type="button"
      >
        <span>↳</span>
        <span className="font-mono truncate max-w-[400px]">{display}</span>
        {isLong && (
          <span className="ml-1 text-[10px]">
            {expanded ? "(collapse)" : "(expand)"}
          </span>
        )}
      </button>
    </div>
  );
}

// ── Main Component ──

export function ThinkingSteps({ steps, isActive }: ThinkingStepsProps) {
  const [collapsed, setCollapsed] = useState(false);

  const toggle = useCallback(() => setCollapsed((v) => !v), []);

  if (steps.length === 0) return null;

  // Group steps into meaningful display items
  const toolCallCount = steps.filter((s) => s.type === "tool_call").length;
  const statusLabel = isActive
    ? "Thinking…"
    : `${toolCallCount} tool call${toolCallCount !== 1 ? "s" : ""} used`;

  return (
    <div className="mb-2">
      {/* Header / toggle */}
      <button
        className={cn(
          "flex items-center gap-1.5 text-xs font-medium transition-colors",
          "text-muted-foreground hover:text-foreground"
        )}
        onClick={toggle}
        type="button"
      >
        {isActive ? (
          <LoaderIcon className="size-3.5 animate-spin" />
        ) : (
          <CheckCircle2Icon className="size-3.5 text-green-500" />
        )}
        <span>{statusLabel}</span>
        {collapsed ? (
          <ChevronRightIcon className="size-3" />
        ) : (
          <ChevronDownIcon className="size-3" />
        )}
      </button>

      {/* Steps list */}
      {!collapsed && (
        <div className="mt-1.5 ml-1 border-l-2 border-border pl-3 space-y-1.5">
          {steps.map((step, i) => {
            if (step.type === "narration") {
              return (
                <div key={i} className="flex items-start gap-1.5 text-xs">
                  <MessageCircleIcon className="size-3 mt-0.5 text-blue-400 shrink-0" />
                  <span className="text-muted-foreground italic">
                    {step.content}
                  </span>
                </div>
              );
            }

            if (step.type === "tool_call") {
              return (
                <div key={i}>
                  <div className="flex items-center gap-1.5 text-xs">
                    <WrenchIcon className="size-3 text-orange-400 shrink-0" />
                    <span className="font-mono font-medium text-foreground/80">
                      {step.toolName}
                    </span>
                  </div>
                  {step.args && Object.keys(step.args).length > 0 && (
                    <ToolArgs args={step.args} />
                  )}
                </div>
              );
            }

            if (step.type === "tool_result") {
              return (
                <div key={i}>
                  <ToolResult result={step.result || "(empty)"} />
                </div>
              );
            }

            return null;
          })}
          {isActive && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground animate-pulse">
              <LoaderIcon className="size-3 animate-spin" />
              <span>working…</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
