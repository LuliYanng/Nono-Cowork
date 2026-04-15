"use client";

import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { DynamicToolUIPart, ToolUIPart } from "ai";
import {
  CheckCircleIcon,
  ChevronRightIcon,
  CircleIcon,
  ClockIcon,
  XCircleIcon,
  TerminalIcon,
  SearchIcon,
  FileTextIcon,
  PencilIcon,
  RefreshCwIcon,
  Loader2Icon,
} from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { isValidElement, useCallback } from "react";

import { CodeBlock } from "./code-block";
import { useScrollAnchor } from "./use-scroll-anchor";

export type ToolProps = ComponentProps<typeof Collapsible>;

export const Tool = ({ className, onOpenChange, ...props }: ToolProps) => {
  const anchorOnOpenChange = useScrollAnchor();

  const handleOpenChange = useCallback(
    (open: boolean) => {
      anchorOnOpenChange(open);
      onOpenChange?.(open);
    },
    [anchorOnOpenChange, onOpenChange]
  );

  return (
    <Collapsible
      className={cn("group not-prose w-full", className)}
      onOpenChange={handleOpenChange}
      {...props}
    />
  );
};

export type ToolPart = ToolUIPart | DynamicToolUIPart;

export type ToolHeaderProps = {
  title?: string;
  className?: string;
  actions?: ReactNode;
} & (
  | { type: ToolUIPart["type"]; state: ToolUIPart["state"]; toolName?: never }
  | {
      type: DynamicToolUIPart["type"];
      state: DynamicToolUIPart["state"];
      toolName: string;
    }
);

const getToolIcon = (name: string, state: ToolPart["state"]) => {
  if (state === "input-streaming" || state === "input-available") {
    return <Loader2Icon className="size-[14px] text-muted-foreground animate-spin" />;
  }
  const n = name.toLowerCase();
  if (n.includes('command') || n.includes('bash')) return <TerminalIcon className="size-[14px] text-muted-foreground" />;
  if (n.includes('search') || n.includes('web')) return <SearchIcon className="size-[14px] text-muted-foreground" />;
  if (n.includes('edit')) return <PencilIcon className="size-[14px] text-muted-foreground" />;
  if (n.includes('file') || n.includes('read')) return <FileTextIcon className="size-[14px] text-muted-foreground" />;
  if (n.includes('sync')) return <RefreshCwIcon className="size-[14px] text-muted-foreground" />;
  return <TerminalIcon className="size-[14px] text-muted-foreground" />;
};

export const ToolHeader = ({
  className,
  title,
  type,
  state,
  toolName,
  actions,
  ...props
}: ToolHeaderProps) => {
  const derivedName =
    type === "dynamic-tool" ? toolName : type.split("-").slice(1).join("-");

  const isError = state === "output-error";

  return (
    <CollapsibleTrigger
      className={cn(
        "group/tool flex w-full items-center gap-2 py-1.5 px-2 -mx-2 rounded-md hover:bg-muted/40 focus:outline-none transition-colors cursor-pointer",
        className
      )}
      {...props}
    >
      <div className="flex items-center justify-center w-[24px]">
        {getToolIcon(derivedName, state)}
      </div>
      <span className={cn(
        "text-[13px] transition-colors text-left flex-1 truncate font-medium",
        isError ? "text-destructive" : "text-muted-foreground/80 group-hover/tool:text-foreground group-data-[open]/tool:text-foreground"
      )}>
        {derivedName}: "{title ?? derivedName}"
      </span>
      {actions && (
        <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          {actions}
        </div>
      )}
    </CollapsibleTrigger>
  );
};

export type ToolContentProps = ComponentProps<typeof CollapsibleContent>;

export const ToolContent = ({ className, ...props }: ToolContentProps) => (
  <CollapsibleContent
    className={cn(
      "overflow-hidden outline-none pl-[32px] pb-2",
      className
    )}
  >
    <div className="mt-1 flex flex-col gap-2 rounded-lg border border-border/80 p-2 shadow-sm" {...props} />
  </CollapsibleContent>
);

export type ToolInputProps = ComponentProps<"div"> & {
  input: ToolPart["input"];
};

export const ToolInput = ({ className, input, ...props }: ToolInputProps) => (
  <div className={cn("space-y-1 overflow-hidden bg-muted/40 rounded-md p-3", className)} {...props}>
    <h4 className="font-medium text-muted-foreground text-[11px] capitalize tracking-wide">
      Input
    </h4>
    <div className="rounded-md">
      <CodeBlock code={typeof input === 'string' ? input : JSON.stringify(input, null, 2)} language="json" />
    </div>
  </div>
);

export type ToolOutputProps = ComponentProps<"div"> & {
  output: ToolPart["output"];
  errorText: ToolPart["errorText"];
};

export const ToolOutput = ({
  className,
  output,
  errorText,
  ...props
}: ToolOutputProps) => {
  if (!(output || errorText)) {
    return null;
  }

  let Output = <div>{output as ReactNode}</div>;

  if (typeof output === "object" && !isValidElement(output)) {
    Output = (
      <CodeBlock code={JSON.stringify(output, null, 2)} language="json" />
    );
  } else if (typeof output === "string") {
    Output = <CodeBlock code={output} language="json" />;
  }

  return (
    <div className={cn(
      "space-y-1 bg-muted/40 rounded-md p-3", 
      errorText ? "bg-destructive/10 text-destructive" : "", 
      className
    )} {...props}>
      <h4 className={cn(
        "font-medium text-muted-foreground text-[11px] capitalize tracking-wide",
        errorText && "text-destructive/80"
      )}>
        {errorText ? "Error" : "Result"}
      </h4>
      <div className="overflow-x-auto rounded-md text-xs [&_table]:w-full">
        {errorText && <div>{errorText}</div>}
        {Output}
      </div>
    </div>
  );
};

