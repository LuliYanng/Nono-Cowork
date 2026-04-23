"use client";


import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { DynamicToolUIPart, ToolUIPart } from "ai";
import {
  TerminalIcon,
  SearchIcon,
  FileTextIcon,
  PencilIcon,
  RefreshCwIcon,
  PlugIcon,
} from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { isValidElement, useCallback, useState } from "react";

import { CodeBlock } from "./code-block";
import { Shimmer } from "./shimmer";
import { useScrollAnchor } from "./use-scroll-anchor";

// ────────────────────────────────────────────────────────────────────────────
// Brand-logo rendering for Composio third-party tools
//
// Composio's `COMPOSIO_MULTI_EXECUTE_TOOL` calls contain a `tools[]` array
// where each entry has a `tool_slug` like `GMAIL_FETCH_EMAILS` — the prefix
// before the first underscore identifies the toolkit. We extract unique
// toolkits and render their brand logos (stacked if more than one).
//
// Logos come from Google's public favicon service — free, no API key,
// and returns each site's actual favicon (which for major SaaS is the
// brand's real multi-color logo). For toolkits not in our domain map,
// we fall back to `{toolkit}.com` as a best guess; if that 404s the
// <img onError> handler shows a letter-avatar fallback.
// ────────────────────────────────────────────────────────────────────────────
// Human-readable display names for backend tool names.
// Keys are the raw snake_case names from the backend; values are the
// user-facing labels rendered in the chat UI. Unknown tools fall back
// to auto-formatting (snake_case → Title Case).
// ────────────────────────────────────────────────────────────────────────────

const TOOL_DISPLAY_NAMES: Record<string, [string, string]> = {
  //                                        [active,              done]
  // File operations
  read_file:                                ["Reading",           "Read"],
  edit_file:                                ["Editing",           "Edited"],
  write_file:                               ["Writing",           "Wrote"],
  list_snapshots:                           ["Checking snapshots","Checked snapshots"],
  // Commands
  run_command:                              ["Running",           "Ran"],
  check_command_status:                     ["Checking status",   "Checked status"],
  // Web
  web_search:                               ["Searching",         "Searched"],
  read_webpage:                             ["Reading page",      "Read page"],
  // Memory
  memory_write:                             ["Memorizing",        "Memorized"],
  // Delegation
  delegate:                                 ["Delegating",        "Delegated"],
  delegate_status:                          ["Checking delegate", "Checked delegate"],
  // Channel
  send_file:                                ["Sending",           "Sent"],
  // Routines
  list_routines:                            ["Listing routines",  "Listed routines"],
  create_routine:                           ["Creating routine",  "Created routine"],
  update_routine:                           ["Updating routine",  "Updated routine"],
  manage_routine:                           ["Managing routine",  "Managed routine"],
  // Sync (Syncthing)
  sync_status:                              ["Checking sync",     "Checked sync"],
  sync_wait:                                ["Waiting for sync",  "Waited for sync"],
  sync_versions:                            ["Checking versions", "Checked versions"],
  sync_restore:                             ["Restoring",         "Restored"],
  sync_pause:                               ["Pausing sync",      "Paused sync"],
  sync_resume:                              ["Resuming sync",     "Resumed sync"],
  sync_ignore_add:                          ["Ignoring",          "Ignored"],
  // Composio
  composio_list_triggers:                   ["Listing triggers",  "Listed triggers"],
  composio_wait_for_connection:             ["Connecting",        "Connected"],
  // Delivery (hidden from UI, but included for completeness)
  report_result:                            ["Reporting",         "Reported"],
};

/** Convert a raw tool name to a human-readable display label.
 *  Uses present participle (-ing) when active, past tense when done.
 *  Prefers the curated mapping; falls back to auto-formatting
 *  snake_case → Title Case (e.g. "my_cool_tool" → "My cool tool"). */
export function getToolDisplayName(rawName: string, isDone = false): string {
  const entry = TOOL_DISPLAY_NAMES[rawName];
  if (entry) return isDone ? entry[1] : entry[0];
  // Auto-format: replace underscores with spaces, capitalize first letter
  const formatted = rawName.replace(/_/g, " ");
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

//

// Map Composio toolkit prefix → canonical domain for favicon lookup.
// Keep domain specific enough that the favicon is the brand logo
// (e.g. mail.google.com for the Gmail "M" rather than google.com's "G").
const TOOLKIT_DOMAINS: Record<string, string> = {
  // Gmail: use apex `gmail.com` (not `mail.google.com`) — Google's favicon
  // service returns the Google G-logo for google.com subdomains rather than
  // the product's own icon. If you see wrong logos for other Google products
  // (Sheets/Docs/Drive), use a LOGO_URL_OVERRIDES entry below instead.
  gmail: "gmail.com",
  googlesheets: "sheets.google.com",
  googledocs: "docs.google.com",
  googledrive: "drive.google.com",
  googlecalendar: "calendar.google.com",
  googlebigquery: "cloud.google.com",
  googlemaps: "maps.google.com",
  youtube: "youtube.com",
  notion: "notion.so",
  slack: "slack.com",
  discord: "discord.com",
  telegram: "telegram.org",
  whatsapp: "whatsapp.com",
  github: "github.com",
  gitlab: "gitlab.com",
  bitbucket: "bitbucket.org",
  jira: "atlassian.com",
  confluence: "atlassian.com",
  trello: "trello.com",
  linear: "linear.app",
  asana: "asana.com",
  clickup: "clickup.com",
  monday: "monday.com",
  airtable: "airtable.com",
  salesforce: "salesforce.com",
  hubspot: "hubspot.com",
  intercom: "intercom.com",
  zendesk: "zendesk.com",
  freshdesk: "freshdesk.com",
  pipedrive: "pipedrive.com",
  stripe: "stripe.com",
  shopify: "shopify.com",
  paypal: "paypal.com",
  figma: "figma.com",
  dropbox: "dropbox.com",
  onedrive: "onedrive.live.com",
  box: "box.com",
  zoom: "zoom.us",
  twitter: "twitter.com",
  x: "x.com",
  linkedin: "linkedin.com",
  reddit: "reddit.com",
  facebook: "facebook.com",
  instagram: "instagram.com",
  spotify: "spotify.com",
  openai: "openai.com",
  anthropic: "anthropic.com",
  mailchimp: "mailchimp.com",
  typeform: "typeform.com",
  calendly: "calendly.com",
  notion_database: "notion.so",
};

/** Hard-coded logo URL overrides for toolkits where Google's favicon
 *  service returns the wrong icon (e.g. subdomains that fall back to the
 *  parent brand). Add entries here as you encounter issues in production. */
const LOGO_URL_OVERRIDES: Record<string, string> = {
  gmail: "https://www.gstatic.com/images/branding/product/1x/gmail_2020q4_48dp.png",
};

/** Return a logo URL for the toolkit. Prefers manual override, then the
 *  canonical domain's favicon, falling back to `{toolkit}.com`. */
function getBrandLogoUrl(toolkit: string): string {
  if (LOGO_URL_OVERRIDES[toolkit]) return LOGO_URL_OVERRIDES[toolkit];
  const domain = TOOLKIT_DOMAINS[toolkit] || `${toolkit}.com`;
  return `https://www.google.com/s2/favicons?domain=${domain}&sz=64`;
}

/** A single brand logo with graceful fallback to the toolkit's first letter. */
export function BrandLogo({
  toolkit,
  size = 16,
  className,
}: {
  toolkit: string;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);

  if (failed || !toolkit) {
    return (
      <span
        className={cn(
          "inline-flex items-center justify-center rounded-sm bg-muted text-[9px] font-bold uppercase text-muted-foreground",
          className
        )}
        style={{ width: size, height: size }}
        aria-label={toolkit}
      >
        {toolkit[0] || "?"}
      </span>
    );
  }

  return (
    <img
      src={getBrandLogoUrl(toolkit)}
      alt={toolkit}
      width={size}
      height={size}
      className={cn("object-contain", className)}
      style={{ width: size, height: size }}
      onError={() => setFailed(true)}
    />
  );
}

/** Render stacked brand logos (overlapping avatars) for multiple toolkits.
 *  `size` controls the outer avatar diameter; logos inside are size - 4 so
 *  the ring/border is visible without cropping the brand mark. */
export function StackedBrandLogos({
  toolkits,
  size = 20,
  className,
}: {
  toolkits: string[];
  size?: number;
  className?: string;
}) {
  if (toolkits.length === 0) return null;
  const iconSize = Math.max(size - 6, 10);
  return (
    <div className={cn("flex items-center", className)}>
      {toolkits.map((tk, idx) => (
        <div
          key={tk}
          className={cn(
            "relative flex items-center justify-center rounded-full bg-background ring-2 ring-background",
            idx > 0 && "-ml-1.5"
          )}
          style={{
            width: size,
            height: size,
            zIndex: toolkits.length - idx,
          }}
        >
          <BrandLogo toolkit={tk} size={iconSize} />
        </div>
      ))}
    </div>
  );
}

/** Extract unique toolkit slugs from a COMPOSIO_MULTI_EXECUTE_TOOL input. */
export function extractComposioToolkits(input: unknown): string[] {
  if (!input || typeof input !== "object") return [];
  const tools = (input as { tools?: unknown }).tools;
  if (!Array.isArray(tools)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of tools) {
    const slug = (t as { tool_slug?: unknown })?.tool_slug;
    if (typeof slug !== "string") continue;
    const toolkit = slug.split("_")[0]?.toLowerCase();
    if (!toolkit || seen.has(toolkit)) continue;
    seen.add(toolkit);
    out.push(toolkit);
  }
  return out;
}

/** True if the tool name is a Composio meta-tool. */
export function isComposioTool(name: string): boolean {
  return name.startsWith("COMPOSIO_");
}

export type ToolProps = ComponentProps<typeof Collapsible>;

export const Tool = ({ className, onOpenChange, ...props }: ToolProps) => {
  const anchorOnOpenChange = useScrollAnchor();

  const handleOpenChange = useCallback(
    (...args: Parameters<NonNullable<typeof onOpenChange>>) => {
      anchorOnOpenChange(args[0]);
      onOpenChange?.(...args);
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
  /** Raw tool input — when provided for a Composio call, used to derive
   *  brand logos and a human-readable title from the `thought` field. */
  input?: unknown;
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

const getToolIcon = (name: string) => {
  const n = name.toLowerCase();
  if (n.includes('command') || n.includes('bash')) return <TerminalIcon className="size-[16px] text-muted-foreground" />;
  if (n.includes('search') || n.includes('web')) return <SearchIcon className="size-[16px] text-muted-foreground" />;
  if (n.includes('edit')) return <PencilIcon className="size-[16px] text-muted-foreground" />;
  if (n.includes('file') || n.includes('read')) return <FileTextIcon className="size-[16px] text-muted-foreground" />;
  if (n.includes('sync')) return <RefreshCwIcon className="size-[16px] text-muted-foreground" />;
  if (n.includes('composio')) return <PlugIcon className="size-[16px] text-muted-foreground" />;
  return <TerminalIcon className="size-[16px] text-muted-foreground" />;
};

export const ToolHeader = ({
  className,
  title,
  input,
  type,
  state,
  toolName,
  actions,
  ...props
}: ToolHeaderProps) => {
  const derivedName =
    type === "dynamic-tool" ? toolName : type.split("-").slice(1).join("-");

  const isError = state === "output-error";
  const isLoading =
    state === "input-streaming" || state === "input-available";

  // Composio-specific display: extract toolkits from sub-calls, prefer
  // `input.thought` as the card title (it's designed to be user-facing).
  const isComposio = isComposioTool(derivedName);
  const composioToolkits = isComposio ? extractComposioToolkits(input) : [];
  const hasBrandLogos = composioToolkits.length > 0;

  let composioThought: string | undefined;
  if (isComposio && input && typeof input === "object") {
    const t = (input as { thought?: unknown }).thought;
    if (typeof t === "string" && t.trim()) composioThought = t.trim();
  }

  // Final title: for Composio, prefer thought → passed title → tool_slug
  let resolvedTitle = title;
  if (isComposio) {
    resolvedTitle =
      composioThought ||
      title ||
      derivedName.replace(/^COMPOSIO_/, "").replace(/_/g, " ").toLowerCase();
  }

  return (
    <CollapsibleTrigger
      className={cn(
        "group/tool flex w-full items-center gap-2 py-1.5 px-2 -mx-2 rounded-md hover:bg-muted/40 focus:outline-none transition-colors cursor-pointer",
        className
      )}
      {...props}
    >
      <div className="flex items-center justify-center min-w-[28px]">
        {hasBrandLogos && !isLoading ? (
          <StackedBrandLogos toolkits={composioToolkits} size={22} />
        ) : (
          getToolIcon(derivedName)
        )}
      </div>
      <span
        className={cn(
          "text-[13px] transition-colors text-left flex-1 truncate font-medium",
          isError
            ? "text-destructive"
            : "text-muted-foreground/80 group-hover/tool:text-foreground group-data-[open]/tool:text-foreground"
        )}
      >
        {isComposio ? (
          // Composio: show the friendly title alone (no "TOOL_NAME: ..." prefix)
          <>
            {hasBrandLogos && composioToolkits.length === 1 && (
              <span className="capitalize mr-1.5 text-foreground/70">
                {composioToolkits[0]}
              </span>
            )}
            {resolvedTitle}
          </>
        ) : isLoading ? (
          <Shimmer duration={1}>
            {`${getToolDisplayName(derivedName, false)} ${resolvedTitle && resolvedTitle !== derivedName ? resolvedTitle : ""}`.trim()}
          </Shimmer>
        ) : (
          <>
            <span className="text-foreground/70 mr-1.5">
              {getToolDisplayName(derivedName, true)}
            </span>
            {resolvedTitle && resolvedTitle !== derivedName ? resolvedTitle : null}
          </>
        )}
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

