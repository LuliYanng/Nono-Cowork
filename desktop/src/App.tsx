import { useState, useCallback, useRef, useEffect, Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { Toaster, toast } from "sonner";

// ── Global Error Boundary ── catches render errors including those in Portals ──
class AppErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[App] Unhandled render error:", error, info.componentStack);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100vh', gap:16, padding:32, textAlign:'center', fontFamily:'system-ui' }}>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#ef4444" strokeWidth="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          <p style={{ color:'#ef4444', fontWeight:600, fontSize:14, margin:0 }}>Render error — check DevTools console (Ctrl+Shift+I)</p>
          <pre style={{ fontSize:12, color:'#888', background:'#f3f4f6', padding:12, borderRadius:6, maxWidth:600, overflow:'auto', textAlign:'left', whiteSpace:'pre-wrap', margin:0 }}>{this.state.error.message}{"\n"}{this.state.error.stack?.split("\n").slice(0,10).join("\n")}</pre>
          <button style={{ fontSize:12, padding:'6px 14px', borderRadius:6, border:'1px solid #d1d5db', background:'#f9fafb', cursor:'pointer' }} onClick={() => this.setState({ error: null })}>Dismiss</button>
        </div>
      );
    }
    return this.props.children;
  }
}

// Electron window control API exposed via preload
declare global {
  interface Window {
    electronAPI?: {
      minimize: () => void;
      maximize: () => void;
      close: () => void;
      // File system operations (deliverable components)
      openFile: (path: string) => Promise<{ success: boolean; error?: string }>;
      openFolder: (path: string) => Promise<{ success: boolean; error?: string }>;
      showInExplorer: (path: string) => Promise<{ success: boolean; error?: string }>;
      // Local Syncthing query (zero-config path mapping)
      syncthingLocalFolders: () => Promise<{
        success: boolean;
        folders: Array<{ id: string; label: string; path: string }>;
        error?: string;
      }>;
      syncthingLocalDevice: () => Promise<{
        success: boolean;
        deviceId: string;
        deviceName?: string;
        error?: string;
      }>;
      syncthingEnsureRemoteDevice: (args: {
        deviceId: string;
        deviceName?: string;
      }) => Promise<{ success: boolean; added: boolean; error?: string }>;
      syncthingRuntimeInfo: () => Promise<{
        success: boolean;
        managed: boolean;
        baseUrl: string;
        configPath: string;
        processAlive: boolean;
      }>;
      // Sync folder management
      dialogSelectFolder: () => Promise<{ success: boolean; path?: string; canceled?: boolean }>;
      syncthingAddFolder: (args: {
        localPath: string;
        vpsDeviceId: string;
      }) => Promise<{
        success: boolean;
        folderId: string;
        folderLabel: string;
        localPath: string;
        alreadyExists: boolean;
        /** True when a default .stignore was written into the folder root. */
        wroteDefaultIgnore?: boolean;
        error?: string;
      }>;
      syncthingListSyncFolders: (args: {
        vpsDeviceId: string;
      }) => Promise<{ success: boolean; folders: Array<{ id: string; label: string; path: string }>; error?: string }>;
      syncthingRemoveFolder: (args: {
        folderId: string;
      }) => Promise<{ success: boolean; error?: string }>;
      syncthingEnsureIgnores: () => Promise<{
        success: boolean;
        checked?: number;
        written?: number;
        written_paths?: string[];
        error?: string;
      }>;
      // Onboarding helpers for default workspace
      getHomeDir: () => Promise<{ success: boolean; path?: string; error?: string }>;
      ensureDir: (dirPath: string) => Promise<{ success: boolean; path?: string; error?: string }>;
      getAppConfig: () => Promise<Record<string, string>>;
      saveAppConfig: (config: Record<string, string>) => Promise<{ success: boolean }>;
      reloadWindow: () => Promise<{ success: boolean }>;
      getPlatform: () => string;
    };
  }
}
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message";
import {
  PromptInput,
  PromptInputTextarea,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputSubmit,
  PromptInputActionMenu,
  PromptInputActionMenuTrigger,
  PromptInputActionMenuContent,
  PromptInputActionAddAttachments,
  usePromptInputAttachments,
} from "@/components/ai-elements/prompt-input";
import { SyncFolderWidget } from "@/components/sync-folder-widget";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Shimmer } from "@/components/ai-elements/shimmer";
import {
  Tool,
  ToolHeader,
  ToolContent,
  ToolInput,
  ToolOutput,
  StackedBrandLogos,
  extractComposioToolkits,
} from "@/components/ai-elements/tool";
import { SearchToolCall } from "@/components/ai-elements/search-tool";
import { useScrollAnchor } from "@/components/ai-elements/use-scroll-anchor";
import { Sidebar, type SessionItem, type SidebarView, type WorkspaceItem } from "@/components/sidebar";
import { NewWorkspaceDialog } from "@/components/new-workspace-dialog";
import { OnboardingDialog } from "@/components/onboarding-dialog";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { RenameDialog } from "@/components/rename-dialog";
import { type Notification, type Deliverable } from "@/components/notification-card";
import { WorkspacePage } from "@/components/workspace-page";
import { RoutinesPage } from "@/components/routines-page";
import { syncPaths, FileCard, getDeliverableComponent } from "@/components/deliverables";
import { useSyncStatus } from "@/hooks/use-sync-status";
import { SettingsDialog } from "@/components/settings-dialog";
import {
  Context,
  ContextTrigger,
  ContextContent,
  ContextContentHeader,
  ContextContentBody,
  ContextInputUsage,
  ContextOutputUsage,
  ContextCacheUsage,
} from "@/components/ai-elements/context";
import {
  ModelSelector,
  ModelSelectorTrigger,
  ModelSelectorContent,
  ModelSelectorInput,
  ModelSelectorList,
  ModelSelectorEmpty,
  ModelSelectorGroup,
  ModelSelectorItem,
  ModelSelectorLogo,
  ModelSelectorName,
} from "@/components/ai-elements/model-selector";
import { PanelLeft, ChevronDown, Square, AlertCircle, RotateCcw, ImageIcon, Paperclip, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

// ── Model metadata (from backend MODEL_REGISTRY) ──

interface ModelInfo {
  id: string;       // LiteLLM routing ID (e.g. "openrouter/anthropic/claude-sonnet-4.6")
  name: string;     // Human-readable name (e.g. "Claude Sonnet 4.6")
  provider: string; // Logo / grouping key (e.g. "anthropic")
}

type AvailableModels = ModelInfo[];

// Capitalize provider slug for group headings
function displayProvider(provider: string): string {
  const names: Record<string, string> = {
    google: "Google",
    anthropic: "Anthropic",
    openai: "OpenAI",
    deepseek: "DeepSeek",
    minimax: "MiniMax",
    dashscope: "DashScope",
    mistral: "Mistral",
    groq: "Groq",
    xai: "xAI",
  };
  return names[provider] || provider.charAt(0).toUpperCase() + provider.slice(1);
}

// Resolve display info for a model ID.
// If the model is in availableModels, uses its metadata; otherwise falls back to
// best-effort string parsing (handles both "provider/model" and "router/provider/model").
function resolveModelInfo(modelId: string, models: ModelInfo[]): { name: string; provider: string } {
  const found = models.find(m => m.id === modelId);
  if (found) return { name: found.name, provider: found.provider };
  const parts = modelId.split('/');
  return {
    name: parts[parts.length - 1] || modelId,
    provider: parts.length >= 3 ? parts[1] : parts[0] || '',
  };
}

// ── Types ──

type MessagePart =
  | { type: "text"; content: string }
  | { type: "reasoning"; content: string }
  | { type: "tool_call"; toolName?: string; args?: Record<string, unknown>; round: number; description?: string }
  | { type: "tool_result"; toolName?: string; result?: string; round: number };

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  parts?: MessagePart[];
  // User-attached images (data URLs for display)
  images?: { data: string; filename: string }[];
  // Marks assistant messages that carry a backend/connection error so the
  // UI can render them distinctly (red banner + retry) instead of looking
  // like a normal reply.
  isError?: boolean;
}

// ── History Converter ──
// Converts backend OpenAI-format history into frontend ChatMessage[] with parts[]

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function convertHistoryToMessages(backendMessages: any[]): { messages: ChatMessage[]; counter: number } {
  const msgs: ChatMessage[] = [];
  let counter = 0;

  // Pre-index tool results by tool_call_id for O(1) lookup
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const toolResultMap = new Map<string, any>();
  for (const msg of backendMessages) {
    if (msg.role === "tool" && msg.tool_call_id) {
      toolResultMap.set(msg.tool_call_id, msg);
    }
  }

  // Accumulator: merge consecutive assistant messages into a single ChatMessage.
  // This prevents models that don't output content between tool calls from
  // creating many separate message bubbles with one tool each.
  let accParts: MessagePart[] = [];
  let accContent = "";
  let accReasoning = "";
  let accRound = 0;

  const flushAssistant = () => {
    if (accParts.length === 0 && !accContent && !accReasoning) return;
    msgs.push({
      id: `hist-${++counter}`,
      role: "assistant",
      content: accContent,
      reasoning: accReasoning || undefined,
      parts: accParts.length > 0 ? [...accParts] : undefined,
    });
    accParts = [];
    accContent = "";
    accReasoning = "";
    accRound = 0;
  };

  for (const msg of backendMessages) {
    if (msg.role === "user") {
      flushAssistant();
      // Handle multimodal content (array with text + image_url parts)
      let textContent = "";
      const histImages: { data: string; filename: string }[] = [];
      if (Array.isArray(msg.content)) {
        for (const part of msg.content) {
          if (part.type === "text") {
            textContent = part.text || "";
          } else if (part.type === "image_url") {
            const url = part.image_url?.url || "";
            // Persisted sessions have placeholder like "[image:image/png]"
            if (url && !url.startsWith("[")) {
              histImages.push({ data: url, filename: "image" });
            } else if (url.startsWith("[image:")) {
              // Show placeholder for stripped images
              histImages.push({ data: "", filename: url });
            }
          }
        }
      } else {
        textContent = msg.content || "";
      }
      msgs.push({
        id: `hist-${++counter}`,
        role: "user",
        content: textContent,
        ...(histImages.length > 0 ? { images: histImages } : {}),
      });
    } else if (msg.role === "assistant") {
      // 1. Reasoning content → reasoning part
      if (msg.reasoning_content) {
        accParts.push({ type: "reasoning", content: msg.reasoning_content });
        accReasoning = accReasoning
          ? accReasoning + "\n\n" + msg.reasoning_content
          : msg.reasoning_content;
      }

      // 2. Text content → text part
      if (msg.content) {
        accParts.push({ type: "text", content: msg.content });
        accContent = accContent
          ? accContent + "\n\n" + msg.content
          : msg.content;
      }

      // 3. Tool calls → tool_call parts + matched tool_result parts
      if (msg.tool_calls && Array.isArray(msg.tool_calls)) {
        for (const tc of msg.tool_calls) {
          const fn = tc.function || {};
          const toolName = fn.name || "unknown";
          let args: Record<string, unknown> = {};
          let description: string | undefined;
          try {
            args = JSON.parse(fn.arguments || "{}");
            if (args.description && typeof args.description === "string") {
              description = args.description;
              delete args.description;
            }
          } catch {
            // arguments might not be valid JSON
          }

          accParts.push({
            type: "tool_call",
            toolName,
            args,
            description,
            round: accRound,
          });

          // Match tool result by tool_call_id
          const toolResult = toolResultMap.get(tc.id);
          if (toolResult) {
            let result = toolResult.content || "";
            const isSearch = toolName.includes("search") || toolName.includes("web");
            // Truncate large results for display (consistent with SSE streaming)
            if (!isSearch && result.length > 500) {
              result = result.slice(0, 500) + `… (${result.length} chars)`;
            }
            accParts.push({
              type: "tool_result",
              toolName,
              result,
              round: accRound,
            });
          }
        }
        accRound++;
      }
    }
    // role === "tool" consumed via toolResultMap — skip
  }

  flushAssistant();

  return { messages: msgs, counter };
}

interface SessionStatus {
  active: boolean;
  model?: string;
  context_pct?: number;
  prompt_tokens?: number;
  context_limit?: number;
  total_tokens?: number;
  total_prompt_tokens?: number;
  total_completion_tokens?: number;
  total_cached_tokens?: number;
  total_cache_write_tokens?: number;
  is_running?: boolean;
}

// ── Config ──
// Defaults from .env, can be overridden by saved Electron config.
// Use 127.0.0.1 (not localhost) to avoid Windows + Node 17+ IPv4/IPv6
// resolution mismatch when the backend binds 0.0.0.0 (IPv4-only on Windows).
let API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8080";
let API_TOKEN = import.meta.env.VITE_API_TOKEN || "";

// Load saved config from Electron (called once on mount)
async function loadSavedConfig() {
  const electron = window.electronAPI;
  if (!electron?.getAppConfig) return;
  try {
    const config = await electron.getAppConfig();
    if (config?.apiBase) API_BASE = config.apiBase;
    if (config?.apiToken) API_TOKEN = config.apiToken;
  } catch { /* ignore in browser dev */ }
}

// Helper: build headers with optional Bearer token
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  if (API_TOKEN) {
    headers["Authorization"] = `Bearer ${API_TOKEN}`;
  }
  return headers;
}

// ── Parts Renderer ──
// Renders MessagePart[] in order: text blocks + tool calls interleaved.
// At the end, aggregates file operations and report_result deliverables
// into interactive deliverable cards (FileCard, EmailDraftCard, etc.)

const FILE_TOOL_NAMES = ["write_file", "edit_file", "create_file"];


function summarizeToolNames(toolNames: string[]): string | null {
  if (toolNames.length === 0) return null;
  const counts: Record<string, number> = { command: 0, file: 0, search: 0, other: 0 };
  for (const name of toolNames) {
    const n = name.toLowerCase();
    if (n.includes("command") || n.includes("bash")) counts.command++;
    else if (n.includes("file") || n.includes("read") || n.includes("edit")) counts.file++;
    else if (n.includes("search") || n.includes("web") || n.includes("url")) counts.search++;
    else counts.other++;
  }
  const segments = [];
  if (counts.command > 0) segments.push(`ran ${counts.command} command${counts.command > 1 ? 's' : ''}`);
  if (counts.file > 0) segments.push(`accessed ${counts.file} file${counts.file > 1 ? 's' : ''}`);
  if (counts.search > 0) segments.push(`made ${counts.search} search${counts.search > 1 ? 'es' : ''}`);
  if (counts.other > 0) segments.push(`used ${counts.other} tool${counts.other > 1 ? 's' : ''}`);
  return segments.join(', ');
}

function AgentStepGroup({ title, toolkits, children, defaultOpen, isStreaming }: { title: string, toolkits?: string[], children: React.ReactNode, defaultOpen: boolean, isStreaming?: boolean }) {
  const [isOpen, setIsOpen] = useState(defaultOpen || !!isStreaming);
  const hasEverStreamedRef = useRef(!!isStreaming);
  const [hasAutoClosed, setHasAutoClosed] = useState(false);
  const anchorOnOpenChange = useScrollAnchor();

  // Auto-open when streaming starts
  useEffect(() => {
    if (isStreaming) {
      hasEverStreamedRef.current = true;
      setIsOpen(true);
    }
  }, [isStreaming]);

  // Auto-close when streaming ends (container was flushed by content)
  useEffect(() => {
    if (hasEverStreamedRef.current && !isStreaming && isOpen && !hasAutoClosed) {
      const timer = setTimeout(() => {
        setIsOpen(false);
        setHasAutoClosed(true);
      }, 800);
      return () => clearTimeout(timer);
    }
  }, [isStreaming, isOpen, hasAutoClosed]);

  const handleOpenChange = useCallback((newOpen: boolean) => {
    anchorOnOpenChange(newOpen);
    setIsOpen(newOpen);
  }, [anchorOnOpenChange]);

  return (
    <Collapsible open={isOpen} onOpenChange={handleOpenChange} className="group/step w-full">
      <CollapsibleTrigger className="flex w-full items-center gap-2 py-1.5 text-[13px] text-muted-foreground/80 hover:text-foreground transition-colors outline-none cursor-pointer">
        {toolkits && toolkits.length > 0 && (
          <StackedBrandLogos toolkits={toolkits} size={24} />
        )}
        <span className="text-left capitalize">
          {isStreaming ? <Shimmer duration={1}>{title}</Shimmer> : title}
        </span>
        <ChevronDown className="size-3.5 text-muted-foreground/50 transition-transform group-data-[open]/step:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="flex flex-col gap-1 pl-2">
          {children}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

// ── Collapsible User Message ──
// Collapses long user messages (>8 lines) with a gradient fade + "Show more" toggle
function CollapsibleUserMessage({ content }: { content: string }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isOverflowing, setIsOverflowing] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  // 8 lines × 20px line-height (text-sm = 14px font, 20px line-height)
  const MAX_HEIGHT = 160;

  useEffect(() => {
    if (contentRef.current) {
      setIsOverflowing(contentRef.current.scrollHeight > MAX_HEIGHT);
    }
  }, [content]);

  return (
    <div className="relative">
      <div
        ref={contentRef}
        className="whitespace-pre-wrap transition-[max-height] duration-300 ease-in-out"
        style={{
          maxHeight: isExpanded || !isOverflowing ? undefined : `${MAX_HEIGHT}px`,
          overflow: isExpanded || !isOverflowing ? undefined : "hidden",
        }}
      >
        {content}
      </div>
      {/* Gradient fade overlay at the bottom of collapsed content */}
      {isOverflowing && !isExpanded && (
        <div
          className="absolute bottom-0 left-0 right-0 h-12 pointer-events-none rounded-b-lg"
          style={{
            background: "linear-gradient(to top, var(--secondary) 0%, transparent 100%)",
          }}
        />
      )}
      {/* Show more / Show less toggle */}
      {isOverflowing && (
        <button
          onClick={() => setIsExpanded((v) => !v)}
          className="relative z-10 text-xs text-muted-foreground/70 hover:text-foreground transition-colors mt-1.5 cursor-pointer select-none"
        >
          {isExpanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

// ── Attachment Preview (inline in the prompt input header) ──

function AttachmentPreview() {
  const attachments = usePromptInputAttachments();
  if (attachments.files.length === 0) return null;

  return (
    <PromptInputHeader>
      <div className="flex flex-wrap gap-2 p-2">
        {attachments.files.map((file) => (
          <div
            key={file.id}
            className="group relative rounded-lg border border-border/50 overflow-hidden"
          >
            {file.mediaType?.startsWith("image/") ? (
              <img
                src={file.url}
                alt={file.filename || "Attachment"}
                className="h-16 w-16 object-cover"
              />
            ) : (
              <div className="flex h-16 w-16 items-center justify-center bg-muted">
                <Paperclip className="size-5 text-muted-foreground" />
              </div>
            )}
            <button
              onClick={() => attachments.remove(file.id)}
              className="absolute -right-1 -top-1 hidden group-hover:flex size-5 items-center justify-center rounded-full bg-destructive text-destructive-foreground shadow-sm cursor-pointer"
              type="button"
            >
              <X className="size-3" />
            </button>
          </div>
        ))}
      </div>
    </PromptInputHeader>
  );
}

function PartsRenderer({
  parts,
  isActive,
  isStreaming,
  defaultCollapsed: _defaultCollapsed = false,
}: {
  parts: MessagePart[];
  isActive: boolean;
  isStreaming?: boolean;
  defaultCollapsed?: boolean;
}) {
  // First pass: build a flat list of typed items (reasoning, text, tool nodes)
  type ItemNode = { kind: "reasoning" | "text" | "tool" | "search-tool"; node: React.ReactNode; round?: number; toolName?: string; args?: Record<string, unknown> };
  const flatItems: ItemNode[] = [];
  let i = 0;

  while (i < parts.length) {
    const part = parts[i];

    if (part.type === "reasoning") {
      const isLastPart = i === parts.length - 1;
      const isReasoningStreaming = isLastPart && isActive;
      flatItems.push({
        kind: "reasoning",
        node: (
          <Reasoning
            key={`reasoning-${i}`}
            isStreaming={isReasoningStreaming}
            className="w-full"
            defaultOpen={isReasoningStreaming ? undefined : false}
          >
            <ReasoningTrigger />
            <ReasoningContent>{part.content}</ReasoningContent>
          </Reasoning>
        ),
      });
      i++;
      continue;
    }

    if (part.type === "text") {
      // Skip empty content — only non-empty text acts as a flush signal
      if (part.content) {
        flatItems.push({
          kind: "text",
          node: (
            <MessageResponse key={`text-${i}`}>
              {part.content}
            </MessageResponse>
          ),
        });
      }
      i++;
      continue;
    }

    if (part.type === "tool_call") {
      const nextPart = i + 1 < parts.length ? parts[i + 1] : null;
      const hasResult =
        nextPart?.type === "tool_result" &&
        nextPart.toolName === part.toolName;

      // Skip report_result tool panel
      const toolName = part.toolName || "";
      if (toolName === "report_result" && hasResult) {
        i = i + 2;
        continue;
      }

      // Determine tool state
      let toolState: "input-available" | "output-available" | "input-streaming";
      if (hasResult) {
        toolState = "output-available";
      } else if (isActive || (isStreaming && part.toolName === "delegate")) {
        toolState = "input-available";
      } else {
        toolState = "input-streaming";
      }

      const shouldOpen = false; // Always closed by default, users can click to inspect args/output

      const delegateStopAction =
        part.toolName === "delegate" && !hasResult && isStreaming ? (
          <button
            className="flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium text-red-500 hover:bg-red-500/10 hover:text-red-600 transition-colors cursor-pointer"
            onClick={() => {
              fetch(`${API_BASE}/api/command/stop`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ scope: "delegate" }),
              }).catch(() => {});
            }}
          >
            <Square className="size-3 fill-current" />
            Stop
          </button>
        ) : undefined;

      let displayTitle = part.description || part.toolName || "tool";
      if (!part.description && part.args) {
        // Composio meta-tools carry a user-facing `thought` field — prefer it.
        if (toolName.startsWith("COMPOSIO_") && typeof part.args.thought === "string" && part.args.thought.trim()) {
          displayTitle = part.args.thought.trim();
        }
        else if (typeof part.args.query === "string") displayTitle = part.args.query;
        else if (typeof part.args.command === "string") displayTitle = part.args.command;
        else if (typeof part.args.path === "string") displayTitle = part.args.path;
        else if (typeof part.args.url === "string") displayTitle = part.args.url;
        else if (typeof part.args.file_path === "string") displayTitle = part.args.file_path;
        else if (typeof part.args.target_folder === "string") displayTitle = part.args.target_folder;
      }
      if (displayTitle.length > 60) {
        displayTitle = displayTitle.substring(0, 60) + '...';
      }

      const isSearchTool = toolName.includes("search") || toolName.includes("web") || toolName.includes("url");

      if (isSearchTool) {
        flatItems.push({
          kind: "search-tool",
          node: (
            <SearchToolCall
              key={`t-${i}`}
              query={displayTitle}
              resultString={hasResult && nextPart && nextPart.type === "tool_result" ? nextPart.result : undefined}
            />
          ),
          round: part.round,
          toolName: part.toolName,
          args: part.args,
        });
      } else {
        flatItems.push({
          kind: "tool",
          node: (
            <Tool key={`t-${i}`} defaultOpen={shouldOpen}>
              <ToolHeader
                title={displayTitle}
                input={part.args}
                type={`tool-${part.toolName || "unknown"}` as `tool-${string}`}
                state={toolState}
                actions={delegateStopAction}
              />
              <ToolContent>
                {part.args && Object.keys(part.args).length > 0 && (
                  <ToolInput input={part.args} />
                )}
                {hasResult && nextPart && nextPart.type === "tool_result" && (
                  <ToolOutput output={nextPart.result} errorText={undefined} />
                )}
              </ToolContent>
            </Tool>
          ),
          round: part.round,
          toolName: part.toolName,
          args: part.args,
        });
      }

      i = hasResult ? i + 2 : i + 1;
      continue;
    }

    // Skip standalone tool_result
    i++;
  }

  // Second pass: Container state machine.
  // Uses non-empty content as a flush signal to create alternating
  // "Tool Container → Content → Tool Container" structure.
  //
  // States:
  //   currentContainer === null  →  FLAT mode (reasoning/content rendered directly)
  //   currentContainer !== null  →  COLLECTING mode (reasoning collected into container)
  //
  // Transitions:
  //   reasoning  → container exists ? collect : render flat
  //   text       → flush container (if any), render flat
  //   tool       → container exists ? collect : create new container, collect

  type ContainerItem = { kind: "tool" | "search-tool" | "reasoning"; node: React.ReactNode; toolName?: string; args?: Record<string, unknown> };
  const output: React.ReactNode[] = [];
  let currentContainer: ContainerItem[] | null = null;
  let groupIdx = 0;

  const flushContainer = (isEndFlush = false) => {
    if (!currentContainer || currentContainer.length === 0) {
      currentContainer = null;
      return;
    }

    const toolNames = currentContainer
      .filter(ci => ci.kind === "tool" || ci.kind === "search-tool")
      .map(ci => ci.toolName)
      .filter(Boolean) as string[];

    // Collect unique Composio toolkits from this group so AgentStepGroup
    // can render their brand logos at the start of the title.
    const toolkitSet = new Set<string>();
    for (const ci of currentContainer) {
      if (!ci.toolName) continue;
      if (ci.toolName === "COMPOSIO_MULTI_EXECUTE_TOOL" && ci.args) {
        for (const tk of extractComposioToolkits(ci.args)) toolkitSet.add(tk);
      }
    }
    const toolkits = Array.from(toolkitSet);

    const summary = summarizeToolNames(toolNames) || "Used tools";
    // Active container (end-flush during streaming) stays open; completed ones collapse
    const containerStreaming = isEndFlush && !!(isActive || isStreaming);

    output.push(
      <AgentStepGroup key={`group-${groupIdx}`} title={summary} toolkits={toolkits} defaultOpen={containerStreaming} isStreaming={containerStreaming}>
        {currentContainer.map(ci => ci.node)}
      </AgentStepGroup>
    );
    groupIdx++;
    currentContainer = null;
  };

  for (let j = 0; j < flatItems.length; j++) {
    const item = flatItems[j];

    if (item.kind === "reasoning") {
      if (currentContainer !== null) {
        // COLLECTING mode → reasoning goes into container
        currentContainer.push({ kind: "reasoning", node: item.node });
      } else {
        // FLAT mode → render reasoning directly
        output.push(item.node);
      }
    } else if (item.kind === "text") {
      // Content (non-empty) → flush container if exists, then render flat
      flushContainer();
      output.push(item.node);
    } else if (item.kind === "tool" || item.kind === "search-tool") {
      // Tool → add to container (create lazily if needed)
      if (currentContainer === null) {
        currentContainer = [];
      }
      currentContainer.push({ kind: item.kind, node: item.node, toolName: item.toolName, args: item.args });
    }
  }

  // Loop end → flush remaining container (active during streaming)
  flushContainer(true);

  return <div className="flex flex-col gap-1 w-full">{output}</div>;
}

// ── App ──

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [sessionStatus, setSessionStatus] = useState<SessionStatus>({
    active: false,
  });
  const idCounter = useRef(0);
  const inputRef = useRef(input);
  inputRef.current = input;
  // Track which assistant message is currently being streamed (for animation)
  const [animatingMsgId, setAnimatingMsgId] = useState<string | null>(null);
  // Track which assistant message is actively receiving thought events
  const [thinkingMsgId, setThinkingMsgId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sessionList, setSessionList] = useState<SessionItem[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  // Workspaces (Phase D — workspace-as-project)
  const [workspaceList, setWorkspaceList] = useState<WorkspaceItem[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null);
  const [newWorkspaceOpen, setNewWorkspaceOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  // Don't re-open the onboarding dialog after the user has dismissed it
  // during this app lifetime; they can still create a workspace via the
  // sidebar's `+ New Workspace` button.
  const [onboardingDismissed, setOnboardingDismissed] = useState(false);
  const [workspacesLoaded, setWorkspacesLoaded] = useState(false);
  // Strictly "is there a workspace marked is_default=true?" — separate
  // from `workspaceList.length > 0` so we can still prompt onboarding
  // when the user has workspaces but none of them is the safety-net.
  const [hasDefaultWorkspace, setHasDefaultWorkspace] = useState(false);

  // Confirmation / rename dialog state. Each holds a discriminated
  // payload describing the pending action + a `busy` flag so the
  // dialog can show a disabled state while the request is in flight.
  type PendingConfirm =
    | {
        kind: "delete-workspace";
        wsId: string;
        label: string;
        sessionCount: number;
        busy: boolean;
      }
    | {
        kind: "delete-session";
        sessionId: string;
        preview: string;
        busy: boolean;
      };
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);

  type PendingRename = {
    kind: "workspace";
    wsId: string;
    current: string;
    busy: boolean;
  };
  const [pendingRename, setPendingRename] = useState<PendingRename | null>(null);
  // Resolve the active workspace record (fallback to default if unset) so the
  // sync widget and other workspace-scoped UI can bind to a concrete folder.
  const activeWorkspace =
    workspaceList.find((w) => w.id === activeWorkspaceId) ||
    workspaceList.find((w) => w.is_default) ||
    null;
  const [availableModels, setAvailableModels] = useState<AvailableModels>([]);
  const [modelSelectorOpen, setModelSelectorOpen] = useState(false);
  // Track whether a stop request has been sent (for immediate UI feedback)
  const [isStopping, setIsStopping] = useState(false);
  // View mode: chat or workspace
  const [activeView, setActiveView] = useState<SidebarView>("chat");
  // Notifications (Workspace)
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);

  // Sync status (sidebar indicator)
  const { status: syncStatus, state: syncState } = useSyncStatus(API_BASE, () => authHeaders());

  // Settings dialog
  const [settingsOpen, setSettingsOpen] = useState(false);
  const autoPairAttemptedRef = useRef(false);
  const autoPairRunningRef = useRef(false);

  const autoPairSyncthing = useCallback(async () => {
    if (autoPairAttemptedRef.current || autoPairRunningRef.current) return;
    autoPairRunningRef.current = true;
    autoPairAttemptedRef.current = true;

    try {
      const electron = window.electronAPI;
      if (!electron?.syncthingLocalDevice || !electron?.syncthingEnsureRemoteDevice) return;

      // Step 1: Read local Syncthing device info from Electron (localhost API)
      const local = await electron.syncthingLocalDevice();
      if (!local.success || !local.deviceId) return;

      // Step 2: Register local device on VPS via authenticated desktop API
      const res = await fetch(`${API_BASE}/api/sync/pair`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          device_id: local.deviceId,
          device_name: local.deviceName || "Desktop Client",
        }),
      });

      if (!res.ok) return;
      const pair = await res.json();
      const vpsDeviceId = (pair?.vps_device_id || "").trim();
      if (!vpsDeviceId) return;

      // Step 3: Ensure the returned VPS device is trusted on local Syncthing
      let host = "Nono CoWork VPS";
      try {
        host = new URL(API_BASE).hostname || host;
      } catch {
        // Keep default label when API_BASE is not a full URL
      }
      await electron.syncthingEnsureRemoteDevice({
        deviceId: vpsDeviceId,
        deviceName: `Nono CoWork (${host})`,
      });

    } catch {
      // Silent fallback: auto-pair is best-effort and should not block the app
    } finally {
      autoPairRunningRef.current = false;
    }
  }, []);

  // Load saved config on mount (before health check)
  useEffect(() => {
    loadSavedConfig().then(() => {
      // After config is loaded, do health check
      fetch(`${API_BASE}/api/health`, { headers: authHeaders() })
        .then((r) => r.json())
        .then((data) => {
          setSessionStatus((prev) => ({ ...prev, model: data.model }));
        })
        .catch(() => {});

      // Initialize sync path resolver
      syncPaths.init(API_BASE, authHeaders());

      // Best-effort silent auto-pair (first use / server migration friendly)
      autoPairSyncthing();
    });
  }, [autoPairSyncthing]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
        e.preventDefault();
        setSidebarOpen((prev) => !prev);
      }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'M') {
        e.preventDefault();
        setModelSelectorOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Generate unique ID
  const nextId = useCallback(() => {
    idCounter.current += 1;
    return `msg-${idCounter.current}`;
  }, []);

  // Fetch session status
  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/status`, { headers: authHeaders() });
      const data = await res.json();
      setSessionStatus(data);
    } catch {
      // ignore
    }
  }, []);

  const statusRefreshTimers = useRef<number[]>([]);

  useEffect(() => {
    return () => {
      statusRefreshTimers.current.forEach((id) => window.clearTimeout(id));
      statusRefreshTimers.current = [];
    };
  }, []);

  const scheduleStatusRefreshBurst = useCallback(() => {
    statusRefreshTimers.current.forEach((id) => window.clearTimeout(id));
    statusRefreshTimers.current = [];

    [2000, 5000, 9000, 14000, 20000].forEach((delayMs) => {
      const timeoutId = window.setTimeout(() => {
        void refreshStatus();
      }, delayMs);
      statusRefreshTimers.current.push(timeoutId);
    });
  }, [refreshStatus]);

  // Fetch available models list
  const fetchModels = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/models`, { headers: authHeaders() });
      const data = await res.json();
      setAvailableModels(data.available || []);
    } catch {
      // ignore
    }
  }, []);

  // Switch model
  const handleModelSwitch = useCallback(async (model: string) => {
    try {
      await fetch(`${API_BASE}/api/models/current`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ model }),
      });
      // Immediately update the single source of truth
      setSessionStatus(prev => ({ ...prev, model }));
      setModelSelectorOpen(false);
    } catch {
      // ignore
    }
  }, []);

  // Fetch session list for sidebar
  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/sessions`, { headers: authHeaders() });
      const data = await res.json();
      const list = data.sessions || [];
      setSessionList(list);
    } catch {
      // ignore
    }
  }, []);

  // Fetch workspaces list for sidebar
  const fetchWorkspaces = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/workspaces`, { headers: authHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      setWorkspaceList(data.workspaces || []);
      setHasDefaultWorkspace(!!data.default_workspace_id);
      if (data.active_workspace_id) {
        setActiveWorkspaceId(data.active_workspace_id);
      } else if (data.default_workspace_id) {
        setActiveWorkspaceId(data.default_workspace_id);
      }
    } catch {
      // ignore
    } finally {
      setWorkspacesLoaded(true);
    }
  }, []);

  // Switch to a different conversation
  const handleSwitchSession = useCallback(async (sessionId: string) => {
    if (isStreaming) return;

    // Immediately update highlight — no waiting for server
    setCurrentSessionId(sessionId);

    // Show skeleton immediately for instant visual feedback
    setLoadingSession(true);
    setMessages([]);
    setIsStopping(false);

    try {
      // Single request: switch endpoint now returns session data inline
      const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/switch`, {
        method: "PUT",
        headers: authHeaders(),
      });
      if (!res.ok) {
        setLoadingSession(false);
        return;
      }

      const data = await res.json();

      // Convert backend messages to frontend ChatMessage format
      const { messages: msgs, counter } = convertHistoryToMessages(data.messages || []);

      idCounter.current = counter;
      setMessages(msgs);
      // Switching sessions may change the active workspace
      if (data.workspace_id) setActiveWorkspaceId(data.workspace_id);
      refreshStatus();
      fetchSessions();
      fetchWorkspaces();
    } catch {
      // ignore
    } finally {
      setLoadingSession(false);
    }
  }, [isStreaming, refreshStatus, fetchSessions, fetchWorkspaces]);

  // ── Notifications ──

  const fetchNotifications = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/notifications`, { headers: authHeaders() });
      const data = await res.json();
      setNotifications(data.notifications || []);
      setUnreadCount(data.unread ?? data.unread_count ?? 0);
    } catch {
      // ignore
    }
  }, []);

  const handleNotificationClick = useCallback(async (notification: Notification) => {
    if (isStreaming) return;

    // Mark as read (fire-and-forget)
    if (notification.status === "unread") {
      fetch(`${API_BASE}/api/notifications/${notification.id}/read`, {
        method: "PUT",
        headers: authHeaders(),
      }).catch(() => {});
    }

    // Load the autonomous session
    setLoadingSession(true);
    setMessages([]);
    setIsStopping(false);

    try {
      // Try notification-specific session endpoint first
      const res = await fetch(`${API_BASE}/api/notifications/${notification.id}/session`, {
        headers: authHeaders(),
      });

      if (res.ok) {
        const data = await res.json();
        const { messages: msgs, counter } = convertHistoryToMessages(data.messages || data.history || []);
        idCounter.current = counter;
        setMessages(msgs);
        refreshStatus();
      } else if (notification.session_id) {
        // Fallback: try regular session switch
        await handleSwitchSession(notification.session_id);
      }
    } catch {
      // Fallback: try regular session switch
      if (notification.session_id) {
        try { await handleSwitchSession(notification.session_id); } catch { /* ignore */ }
      }
    } finally {
      setLoadingSession(false);
    }

    // Refresh notifications to update read status
    fetchNotifications();
  }, [isStreaming, handleSwitchSession, fetchNotifications, refreshStatus]);

  const handleMarkAllRead = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/notifications/read-all`, {
        method: "PUT",
        headers: authHeaders(),
      });
      fetchNotifications();
    } catch {
      // ignore
    }
  }, [fetchNotifications]);

  const handleArchive = useCallback(async (notification: Notification) => {
    // Save snapshot for rollback
    const prevNotifications = notifications;
    const prevUnread = unreadCount;

    // Optimistic update — move card to "done" immediately
    setNotifications((prev) =>
      prev.map((n) =>
        n.id === notification.id ? { ...n, status: "dismissed" as const } : n
      )
    );
    if (notification.status === "unread") {
      setUnreadCount((prev) => Math.max(0, prev - 1));
    }

    try {
      const res = await fetch(`${API_BASE}/api/notifications/${notification.id}/action`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ action_type: "archive" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Sync with server truth (in background)
      fetchNotifications();
    } catch {
      // Revert to exact previous state
      setNotifications(prevNotifications);
      setUnreadCount(prevUnread);
      toast.error("Failed to skip, please try again");
    }
  }, [fetchNotifications, notifications, unreadCount]);

  // Note: handleNotificationAction does NOT use optimistic update.
  // Actions like send_email / save_draft have real-world side effects,
  // so we let the child component (EmailDraftAction) manage its own
  // loading → success/failure state. Only after confirmed success
  // do we refresh to sync the server status.
  const handleNotificationAction = useCallback(async (
    notificationId: string,
    actionType: string,
    deliverableIndex: number,
  ): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/api/notifications/${notificationId}/action`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ action_type: actionType, deliverable_index: deliverableIndex }),
      });
      if (res.ok) {
        fetchNotifications();
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }, [fetchNotifications]);

  // First-launch / no-default onboarding. Opens when:
  //   - workspaces have loaded
  //   - no workspace is marked as the safety-net default
  //   - sync is connected (we need the VPS device id to create folders)
  //   - the user hasn't already dismissed it this app lifetime
  // Critically, the absence of a default triggers this even when other
  // workspaces already exist — that's the state a user lands in after
  // the v1→v2 migration demotes their auto-promoted workspace.
  useEffect(() => {
    if (!workspacesLoaded) return;
    if (onboardingDismissed) return;
    if (hasDefaultWorkspace) return;
    if (syncState !== "connected") return;
    if (!syncStatus?.device_id) return;
    setOnboardingOpen(true);
  }, [workspacesLoaded, onboardingDismissed, hasDefaultWorkspace, syncState, syncStatus?.device_id]);

  // Load session list, models, workspaces, and notifications on mount
  useEffect(() => {
    fetchSessions();
    fetchModels();
    fetchWorkspaces();
    fetchNotifications();
  }, [fetchSessions, fetchModels, fetchWorkspaces, fetchNotifications]);

  // One-shot retrofit: drop a default .stignore into any already-synced folder
  // that doesn't have one. Fixes historical folders that were syncing node_modules
  // / .venv / etc before the ignore-on-create logic existed.
  useEffect(() => {
    const api = window.electronAPI;
    if (!api?.syncthingEnsureIgnores) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.syncthingEnsureIgnores();
        if (cancelled) return;
        if (res.success && (res.written ?? 0) > 0) {
          toast.info(
            `Added default ignore rules to ${res.written} sync folder${res.written === 1 ? "" : "s"}`,
            {
              description:
                "node_modules, .venv, caches and secrets will stop syncing. You may want to delete already-uploaded copies on the VPS.",
            },
          );
        }
      } catch {
        // silent — non-critical retrofit
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // SSE: real-time notification stream
  useEffect(() => {
    const url = `${API_BASE}/api/notifications/stream`;
    const headers = API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : undefined;

    // EventSource doesn't support custom headers natively,
    // so we use fetch-based SSE if token is needed, otherwise native EventSource
    if (!headers) {
      const es = new EventSource(url);
      es.addEventListener("new_notification", (e) => {
        try {
          const notif = JSON.parse(e.data) as Notification;
          setNotifications((prev) => [notif, ...prev]);
          setUnreadCount((prev) => prev + 1);
        } catch { /* ignore */ }
      });
      es.addEventListener("notification_update", () => {
        fetchNotifications();
      });
      return () => es.close();
    }

    // Fetch-based SSE with auth header + auto-reconnect
    const controller = new AbortController();
    (async () => {
      while (!controller.signal.aborted) {
        try {
          const res = await fetch(url, {
            headers: authHeaders(),
            signal: controller.signal,
          });
          const reader = res.body?.getReader();
          if (!reader) break;
          const decoder = new TextDecoder();
          let buffer = "";
          let eventType = "";

          // On successful connect, refresh notifications to catch any
          // events that arrived while disconnected
          fetchNotifications();

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
              if (line.startsWith("event:")) {
                eventType = line.slice(6).trim();
              } else if (line.startsWith("data:")) {
                const dataStr = line.slice(5).trim();
                if (!dataStr) continue;
                try {
                  if (eventType === "new_notification") {
                    const notif = JSON.parse(dataStr) as Notification;
                    setNotifications((prev) => [notif, ...prev]);
                    setUnreadCount((prev) => prev + 1);
                  } else if (eventType === "notification_update") {
                    fetchNotifications();
                  }
                } catch { /* ignore */ }
              }
            }
          }
        } catch {
          if (controller.signal.aborted) break;
        }
        // Reconnect after 3s (mirrors native EventSource retry behavior)
        if (!controller.signal.aborted) {
          await new Promise((r) => setTimeout(r, 3000));
        }
      }
    })();
    return () => controller.abort();
  }, [fetchNotifications]);

  // Send message — called by PromptInput onSubmit with { text, files }
  const handleSubmit = useCallback(async (
    overrideText?: string,
    attachedImages?: { data: string; filename: string }[],
  ) => {
    const text = (overrideText ?? inputRef.current).trim();
    const hasImages = attachedImages && attachedImages.length > 0;
    if ((!text && !hasImages) || isStreaming) return;

    // Add user message (with image thumbnails for display)
    const userMsg: ChatMessage = {
      id: nextId(),
      role: "user",
      content: text || (hasImages ? "(see attached images)" : ""),
      ...(hasImages ? { images: attachedImages } : {}),
    };
    setMessages((prev) => [...prev, userMsg]);
    if (overrideText === undefined) {
      setInput("");
    }
    setIsStreaming(true);
    setStatusText("Thinking...");

    // Prepare assistant message placeholder
    const assistantId = nextId();
    const currentParts: MessagePart[] = [];
    let assistantContent = "";

    const updateMsg = (patch: Partial<ChatMessage>) => {
      setMessages((prev) => {
        const existing = prev.find((m) => m.id === assistantId);
        if (existing) {
          return prev.map((m) =>
            m.id === assistantId ? { ...m, ...patch } : m
          );
        }
        return [
          ...prev,
          {
            id: assistantId,
            role: "assistant" as const,
            content: assistantContent,
            parts: [...currentParts],
            ...patch,
          },
        ];
      });
    };

    try {
      // Build request body — include images when present
      const chatBody: Record<string, unknown> = { message: text || "(see attached images)" };
      if (hasImages) {
        chatBody.images = attachedImages!.map((img) => ({
          data: img.data,
          filename: img.filename,
        }));
      }

      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(chatBody),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        for (const line of lines) {
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;

            try {
              const data = JSON.parse(dataStr);

              if (eventType === "status") {
                setStatusText(data.text);
                // Clear thinking state so status text can be displayed
                // (thinkingMsgId stays set after thought events, blocking status visibility)
                setThinkingMsgId(null);
              } else if (eventType === "reasoning_chunk") {
                const lastPart = currentParts[currentParts.length - 1];
                if (lastPart && lastPart.type === "reasoning") {
                  lastPart.content += (data.content || "");
                } else {
                  currentParts.push({ type: "reasoning", content: data.content || "" });
                }
                setThinkingMsgId(assistantId);
                updateMsg({ parts: [...currentParts] });
              } else if (eventType === "text_chunk") {
                // Append to last text part, or create new one
                const lastPart = currentParts[currentParts.length - 1];
                if (lastPart && lastPart.type === "text") {
                  lastPart.content += (data.content || "");
                } else {
                  currentParts.push({ type: "text", content: data.content || "" });
                }
                assistantContent += (data.content || "");
                setAnimatingMsgId(assistantId);
                updateMsg({ content: assistantContent, parts: [...currentParts] });
              } else if (eventType === "thought") {
                // Tool events: push in order
                currentParts.push({
                  type: data.type,
                  round: data.round,
                  toolName: data.tool_name,
                  args: data.args,
                  result: data.result,
                  description: data.description,
                });
                setThinkingMsgId(assistantId);
                updateMsg({ parts: [...currentParts] });
              } else if (eventType === "reply") {
                // Backend signals agent-layer failures with a "❌" prefix
                // (e.g. "❌ Execution error: LLM stream went silent…").
                // Render those as an error banner rather than a plain reply.
                const replyText: string = data.text ?? "";
                const looksLikeError = replyText.startsWith("❌");
                if (looksLikeError) {
                  assistantContent = replyText;
                  updateMsg({ content: assistantContent, isError: true });
                } else if (!assistantContent) {
                  // Fallback: only use reply if no text_chunk was received
                  // (reply may only contain the last round's text)
                  assistantContent = replyText;
                  setAnimatingMsgId(assistantId);
                  updateMsg({ content: assistantContent });
                }
              } else if (eventType === "done") {
                break;
              }
            } catch {
              // Not valid JSON, skip (could be heartbeat comment)
            }
          }
        }
      }
    } catch (err) {
      // Show error as assistant message (rendered as red banner + retry)
      setMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          role: "assistant",
          content: `❌ Connection error: ${err instanceof Error ? err.message : "Unknown error"}`,
          isError: true,
        },
      ]);
    } finally {
      setIsStreaming(false);
      setIsStopping(false);
      setAnimatingMsgId(null);
      setThinkingMsgId(null);
      setStatusText("");
      refreshStatus();
      scheduleStatusRefreshBurst();
      fetchSessions();
    }
  }, [isStreaming, nextId, refreshStatus, scheduleStatusRefreshBurst, fetchSessions]);


  // PromptInput onSubmit handler — receives { text, files } from the component
  const handlePromptSubmit = useCallback(async (
    message: { text: string; files: import("ai").FileUIPart[] },
  ) => {
    // Extract image files as data URLs for the API
    const imageFiles = message.files.filter((f) =>
      f.mediaType?.startsWith("image/"),
    );
    const images: { data: string; filename: string }[] = imageFiles
      .filter((f) => f.url && !f.url.startsWith("blob:"))  // Already converted to data URL by PromptInput
      .map((f) => ({ data: f.url, filename: f.filename || "image" }));

    // Always clear the controlled input state on submit
    setInput("");

    await handleSubmit(
      message.text || undefined,
      images.length > 0 ? images : undefined,
    );
  }, [handleSubmit]);

  // Retry: find the user message immediately preceding the clicked error
  // message and resubmit its text. Backend history already contains the
  // original user turn, so resending produces a duplicate user turn — the
  // LLM handles this fine, and the duplicate is visible in the UI which is
  // honest behavior.
  const handleRetryError = useCallback((errorMsgId: string) => {
    if (isStreaming) return;
    const idx = messages.findIndex((m) => m.id === errorMsgId);
    if (idx <= 0) return;
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i].role === "user" && messages[i].content) {
        void handleSubmit(messages[i].content);
        return;
      }
    }
  }, [isStreaming, messages, handleSubmit]);

  return (
    <>
    <TooltipProvider>
      <div className="flex h-screen bg-background text-foreground overflow-hidden">
        {/* Sidebar */}
        <Sidebar
          isOpen={sidebarOpen}
          onToggle={() => setSidebarOpen((p) => !p)}
          sessions={sessionList}
          currentSessionId={currentSessionId}
          onSelectSession={(id) => {
            handleSwitchSession(id);
            setActiveView("chat");
          }}
          workspaces={workspaceList}
          activeWorkspaceId={activeWorkspaceId}
          onNewWorkspace={() => setNewWorkspaceOpen(true)}
          onNewChatInWorkspace={(wsId) => {
            // New chat scoped to a specific workspace
            setMessages([]);
            setIsStopping(false);
            setCurrentSessionId(null);
            setActiveWorkspaceId(wsId);
            setActiveView("chat");
            fetch(`${API_BASE}/api/sessions`, {
              method: "POST",
              headers: authHeaders({ "Content-Type": "application/json" }),
              body: JSON.stringify({ workspace_id: wsId }),
            })
              .then((r) => r.json())
              .then((data) => {
                if (data.session_id) setCurrentSessionId(data.session_id);
                if (data.workspace_id) setActiveWorkspaceId(data.workspace_id);
                refreshStatus();
                fetchSessions();
                fetchWorkspaces();
              })
              .catch(() => {});
          }}
          onDeleteWorkspace={(wsId) => {
            // Dispatch to the confirm dialog; actual API call happens in
            // the dialog's onConfirm handler below so the user sees a
            // proper "are you sure?" prompt + post-action toast.
            const ws = workspaceList.find((w) => w.id === wsId);
            if (!ws) return;
            setPendingConfirm({
              kind: "delete-workspace",
              wsId,
              label: ws.label,
              sessionCount: ws.session_count || 0,
              busy: false,
            });
          }}
          onRenameWorkspace={(wsId) => {
            const current = workspaceList.find((w) => w.id === wsId);
            if (!current) return;
            setPendingRename({
              kind: "workspace",
              wsId,
              current: current.label,
              busy: false,
            });
          }}
          activeView={activeView}
          onViewChange={setActiveView}
          unreadCount={unreadCount}
          syncState={syncState}
          onSettingsOpen={() => setSettingsOpen(true)}
          onNewChat={() => {
            // If already on an empty session (no user messages), just switch to chat view
            const hasUserMessages = messages.some((m) => m.role === "user");
            if (!hasUserMessages && activeView === "chat") return;
            if (!hasUserMessages && activeView !== "chat") return;

            // Optimistic: clear UI immediately (zero delay)
            setMessages([]);
            setIsStopping(false);
            setCurrentSessionId(null);

            // Fire server request in background — don't block UI
            fetch(`${API_BASE}/api/sessions`, {
              method: "POST",
              headers: authHeaders({ "Content-Type": "application/json" }),
              body: JSON.stringify(
                activeWorkspaceId ? { workspace_id: activeWorkspaceId } : {},
              ),
            })
              .then((r) => r.json())
              .then((data) => {
                if (data.session_id) setCurrentSessionId(data.session_id);
                if (data.workspace_id) setActiveWorkspaceId(data.workspace_id);
                refreshStatus();
                fetchSessions();
                fetchWorkspaces();
              })
              .catch(() => {});
          }}
          onDeleteSession={(id) => {
            // Resolve a short preview of the chat so the confirm
            // dialog can say "Delete chat \"Fix login bug\"?"
            const s = sessionList.find((x) => x.id === id);
            const preview = (s?.preview || "this chat").toString().trim();
            setPendingConfirm({
              kind: "delete-session",
              sessionId: id,
              preview,
              busy: false,
            });
          }}
        />


        {/* Main content */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* Draggable Title Bar */}
          <header
            className="flex items-center justify-between px-4 h-11 select-none shrink-0"
            style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
          >
            <div
              className="flex items-center gap-2"
              style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
            >
              {!sidebarOpen && (
                <button
                  onClick={() => setSidebarOpen(true)}
                  className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                  aria-label="Open sidebar"
                >
                  <PanelLeft size={16} />
                </button>
              )}
              {!sidebarOpen && (
                <span className="text-[13px] font-medium text-muted-foreground">Nono CoWork</span>
              )}
            </div>
            <div
              className="flex items-center gap-2"
              style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
            >
              {/* Window controls */}
              <div className="flex items-center ml-2 gap-0.5">
                <button
                  onClick={() => window.electronAPI?.minimize()}
                  className="w-8 h-7 flex items-center justify-center rounded hover:bg-muted text-muted-foreground transition-colors"
                  aria-label="Minimize"
                >
                  <svg width="12" height="12" viewBox="0 0 12 12"><rect y="5" width="12" height="1.5" rx="0.75" fill="currentColor"/></svg>
                </button>
                <button
                  onClick={() => window.electronAPI?.maximize()}
                  className="w-8 h-7 flex items-center justify-center rounded hover:bg-muted text-muted-foreground transition-colors"
                  aria-label="Maximize"
                >
                  <svg width="12" height="12" viewBox="0 0 12 12"><rect x="1" y="1" width="10" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg>
                </button>
                <button
                  onClick={() => window.electronAPI?.close()}
                  className="w-8 h-7 flex items-center justify-center rounded hover:bg-red-500/80 hover:text-white text-muted-foreground transition-colors"
                  aria-label="Close"
                >
                  <svg width="12" height="12" viewBox="0 0 12 12"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
                </button>
              </div>
            </div>
          </header>

          {/* View: Workspace, Chat or Routines */}
          {activeView === "workspace" ? (
            <WorkspacePage
              notifications={notifications}
              unreadCount={unreadCount}
              onNotificationClick={handleNotificationClick}
              onOpenSession={(notif) => {
                handleNotificationClick(notif);
                setActiveView("chat");
              }}
              onArchive={handleArchive}
              onExecuteAction={handleNotificationAction}
              onMarkAllRead={handleMarkAllRead}
            />
          ) : activeView === "routines" ? (
            <RoutinesPage />
          ) : messages.length === 0 && !isStreaming && !loadingSession ? (
            /* ── Welcome screen: centered ── */
            <div className="flex-1 flex flex-col items-center justify-center px-4">
              <div className="w-[85%] max-w-3xl flex flex-col items-center gap-8">
                <h1
                  className="text-[3rem] text-foreground/70 tracking-wide"
                  style={{ fontFamily: "'Lora', serif" }}
                >
                  Nono CoWork
                </h1>
                <div className="w-full">
                  <PromptInput
                    onSubmit={handlePromptSubmit}
                    accept="image/*"
                    multiple
                  >
                    <AttachmentPreview />
                    <PromptInputTextarea
                      placeholder="Type a message..."
                      value={input}
                      onChange={(e) => setInput(e.currentTarget.value)}
                    />
                    <PromptInputFooter>
                      <div className="flex items-center gap-1">
                        {/* Attachment menu */}
                        <PromptInputActionMenu>
                          <PromptInputActionMenuTrigger
                            tooltip="Attach image"
                          />
                          <PromptInputActionMenuContent>
                            <PromptInputActionAddAttachments label="Add images" />
                          </PromptInputActionMenuContent>
                        </PromptInputActionMenu>
                        {/* Sync Folder */}
                        <SyncFolderWidget
                          apiBase={API_BASE}
                          getHeaders={() => authHeaders()}
                          syncState={syncState}
                          activeWorkspace={activeWorkspace}
                        />
                        {/* Model Selector */}
                        <ModelSelector open={modelSelectorOpen} onOpenChange={(open) => {
                          setModelSelectorOpen(open);
                          if (open) fetchModels();
                        }}>
                          <ModelSelectorTrigger>
                            <Button
                              variant="ghost"
                              className="h-8 px-2.5 text-sm text-muted-foreground/80 hover:text-foreground cursor-pointer gap-1.5"
                            >
                              {(() => {
                                const current = sessionStatus.model || '';
                                const info = resolveModelInfo(current, availableModels);
                                return (
                                  <>
                                    {info.provider && <ModelSelectorLogo provider={info.provider} className="size-4" />}
                                    <span>{info.name || 'Select model'}</span>
                                    <ChevronDown className="size-3.5 opacity-50" />
                                  </>
                                );
                              })()}
                            </Button>
                          </ModelSelectorTrigger>
                          <ModelSelectorContent title="Select a Model">
                            <ModelSelectorInput placeholder="Search models..." />
                            <ModelSelectorList>
                              <ModelSelectorEmpty>No models found.</ModelSelectorEmpty>
                              {Object.entries(
                                availableModels.reduce<Record<string, ModelInfo[]>>((groups, m) => {
                                  const key = m.provider || 'other';
                                  if (!groups[key]) groups[key] = [];
                                  groups[key].push(m);
                                  return groups;
                                }, {})
                              ).map(([provider, models]) => (
                                <ModelSelectorGroup key={provider} heading={displayProvider(provider)}>
                                  {models.map((m) => {
                                    const isCurrent = m.id === sessionStatus.model;
                                    return (
                                      <ModelSelectorItem
                                        key={m.id}
                                        value={m.id}
                                        onSelect={() => handleModelSwitch(m.id)}
                                        className={isCurrent ? 'bg-accent' : ''}
                                      >
                                        {m.provider && <ModelSelectorLogo provider={m.provider} className="size-4 mr-2" />}
                                        <ModelSelectorName>{m.name}</ModelSelectorName>
                                        {isCurrent && <span className="text-xs text-muted-foreground">current</span>}
                                      </ModelSelectorItem>
                                    );
                                  })}
                                </ModelSelectorGroup>
                              ))}
                            </ModelSelectorList>
                          </ModelSelectorContent>
                        </ModelSelector>
                      </div>
                      <PromptInputSubmit
                        className="cursor-pointer"
                        disabled={!isStreaming && !input.trim()}
                        status={isStopping ? "submitted" : isStreaming ? "streaming" : "ready"}
                        onStop={() => {
                          if (isStopping) return;
                          setIsStopping(true);
                          fetch(`${API_BASE}/api/command/stop`, {
                            method: "POST",
                            headers: authHeaders({ "Content-Type": "application/json" }),
                            body: JSON.stringify({}),
                          }).catch(() => {});
                        }}
                      />
                    </PromptInputFooter>
                  </PromptInput>
                </div>
              </div>
            </div>
          ) : (
            /* ── Conversation mode: messages + input at bottom ── */
            <>
              <div className="relative flex-1 min-h-0">
                {/* Top gradient fade below title bar */}
                <div className="pointer-events-none absolute inset-x-0 top-0 h-8 z-10 flex justify-center">
                  <div className="w-[85%] max-w-5xl h-full bg-gradient-to-b from-background to-transparent" />
                </div>
                <Conversation className="h-full">
                <ConversationContent className="w-[85%] max-w-5xl mx-auto pb-6">
                    {/* Skeleton loading for session switch */}
                    {loadingSession && (
                      <div className="flex flex-col gap-5 py-4 animate-pulse">
                        {/* User message skeleton */}
                        <div className="flex justify-end">
                          <div className="rounded-lg bg-secondary/60 h-10 w-[45%]"></div>
                        </div>
                        {/* Assistant message skeleton */}
                        <div className="flex flex-col gap-2.5 w-[75%]">
                          <div className="rounded bg-muted/50 h-3.5 w-full"></div>
                          <div className="rounded bg-muted/50 h-3.5 w-[92%]"></div>
                          <div className="rounded bg-muted/50 h-3.5 w-[60%]"></div>
                        </div>
                        {/* User message skeleton */}
                        <div className="flex justify-end">
                          <div className="rounded-lg bg-secondary/60 h-8 w-[35%]"></div>
                        </div>
                        {/* Assistant message skeleton */}
                        <div className="flex flex-col gap-2.5 w-[80%]">
                          <div className="rounded bg-muted/50 h-3.5 w-full"></div>
                          <div className="rounded bg-muted/50 h-3.5 w-[85%]"></div>
                          <div className="rounded bg-muted/40 h-3.5 w-[70%]"></div>
                          <div className="rounded bg-muted/30 h-3.5 w-[45%]"></div>
                        </div>
                      </div>
                    )}
                    {(() => {
                      // ── Pre-compute turn boundaries ──
                      // A "turn" = a user message + all following assistant messages (until next user msg)
                      // We render deliverables at the end of each completed turn.
                      const turnEnds: Set<number> = new Set();
                      for (let k = 0; k < messages.length; k++) {
                        if (messages[k].role === "user" && k > 0) {
                          // The previous message was the end of a turn
                          turnEnds.add(k - 1);
                        }
                      }
                      // Last message is also a turn end (if not streaming)
                      if (messages.length > 0 && !isStreaming) {
                        turnEnds.add(messages.length - 1);
                      }

                      // Collect deliverables for a range of messages [start..end]
                      function collectTurnDeliverables(start: number, end: number) {
                        const fileOps: Array<{ path: string; action: "created" | "modified" }> = [];
                        const reportDelivs: Array<Record<string, unknown>> = [];

                        for (let m = start; m <= end; m++) {
                          const msg = messages[m];
                          if (msg.role !== "assistant" || !msg.parts) continue;
                          for (let j = 0; j < msg.parts.length; j++) {
                            const p = msg.parts[j];
                            if (p.type !== "tool_call") continue;
                            const tn = p.toolName || "";
                            const next = j + 1 < msg.parts.length ? msg.parts[j + 1] : null;
                            const done = next?.type === "tool_result" && next.toolName === tn;
                            if (!done) continue;

                            if (FILE_TOOL_NAMES.includes(tn)) {
                              const fp = (p.args?.path || p.args?.file_path || p.args?.target_file) as string | undefined;
                              if (fp && syncPaths.isSyncPath(fp)) {
                                fileOps.push({ path: fp, action: tn === "edit_file" ? "modified" : "created" });
                              }
                            }
                            if (tn === "report_result") {
                              const delivs = p.args?.deliverables;
                              if (Array.isArray(delivs)) reportDelivs.push(...delivs);
                            }
                          }
                        }

                        if (fileOps.length === 0 && reportDelivs.length === 0) return null;
                        return { fileOps, reportDelivs };
                      }

                      // ── Render messages with per-turn deliverables ──
                      const output: React.ReactNode[] = [];
                      let turnStart = 0;

                      messages.forEach((msg, idx) => {
                        // Render the message
                        output.push(
                          <Message key={msg.id} from={msg.role}>
                            <MessageContent>
                              {msg.role === "assistant" && msg.isError ? (
                                <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm">
                                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                                  <div className="flex min-w-0 flex-1 flex-col gap-2">
                                    <p className="whitespace-pre-wrap break-words text-foreground">
                                      {msg.content.replace(/^❌\s*/, "")}
                                    </p>
                                    <div>
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => handleRetryError(msg.id)}
                                        disabled={isStreaming}
                                        className="h-7 gap-1.5 text-xs"
                                      >
                                        <RotateCcw className="h-3 w-3" />
                                        Retry
                                      </Button>
                                    </div>
                                  </div>
                                </div>
                              ) : msg.role === "assistant" ? (
                                <>
                                  {msg.reasoning && (!msg.parts || !msg.parts.some(p => p.type === "reasoning")) && (
                                    <Reasoning
                                      isStreaming={msg.id === thinkingMsgId && !msg.content}
                                      className="w-full"
                                    >
                                      <ReasoningTrigger />
                                      <ReasoningContent>{msg.reasoning}</ReasoningContent>
                                    </Reasoning>
                                  )}
                                  {msg.parts && msg.parts.length > 0 && (
                                    <PartsRenderer
                                      parts={msg.parts}
                                      isActive={msg.id === thinkingMsgId}
                                      isStreaming={isStreaming}
                                      defaultCollapsed={msg.id.startsWith("hist-")}
                                    />
                                  )}
                                </>
                              ) : (
                                <>
                                  {/* User-attached image thumbnails */}
                                  {msg.images && msg.images.length > 0 && (
                                    <div className="flex flex-wrap gap-2 mb-2">
                                      {msg.images.map((img, imgIdx) => (
                                        img.data ? (
                                          <img
                                            key={imgIdx}
                                            src={img.data}
                                            alt={img.filename || "Attached image"}
                                            className="max-w-[200px] max-h-[200px] rounded-md object-cover border border-border/50"
                                          />
                                        ) : (
                                          <div
                                            key={imgIdx}
                                            className="flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground"
                                          >
                                            <ImageIcon className="size-3.5" />
                                            <span>{img.filename.replace(/^\[image:/, "").replace(/\]$/, "")}</span>
                                          </div>
                                        )
                                      ))}
                                    </div>
                                  )}
                                  <CollapsibleUserMessage content={msg.content} />
                                </>
                              )}
                            </MessageContent>
                          </Message>
                        );

                        // If this is a turn end, render deliverables for this turn
                        if (turnEnds.has(idx)) {
                          const collected = collectTurnDeliverables(turnStart, idx);
                          if (collected) {
                            output.push(
                              <div key={`deliverables-${idx}`} className="flex flex-wrap gap-2 px-1">
                                {collected.fileOps.map((op, fi) => (
                                  <FileCard key={`fop-${idx}-${fi}`} path={op.path} action={op.action} mode="compact" />
                                ))}
                                {collected.reportDelivs.map((d, di) => {
                                  const Component = getDeliverableComponent(d.type as string);
                                  if (Component) {
                                    return (
                                      <Component
                                        key={`rd-${idx}-${di}`}
                                        deliverable={d as unknown as Deliverable}
                                        isUnread={true}
                                        mode="full"
                                      />
                                    );
                                  }
                                  return null;
                                })}
                              </div>
                            );
                          }
                          turnStart = idx + 1;
                        }
                      });

                      return output;
                    })()}
                    {isStreaming && !animatingMsgId && !thinkingMsgId && statusText && (
                      <div className="text-sm text-muted-foreground animate-pulse px-1">
                        {statusText}
                      </div>
                    )}
                </ConversationContent>
                <ConversationScrollButton />
              </Conversation>
              </div>

              {/* Input area with gradient fade above */}
              <div className="relative shrink-0 px-4 pb-4">
                <div className="pointer-events-none absolute inset-x-0 -top-8 h-8 flex justify-center">
                  <div className="w-[85%] max-w-5xl h-full bg-gradient-to-t from-background to-transparent" />
                </div>
                <div className="w-[85%] max-w-5xl mx-auto">
                  <PromptInput
                    onSubmit={handlePromptSubmit}
                    accept="image/*"
                    multiple
                  >
                    <AttachmentPreview />
                    <PromptInputTextarea
                      placeholder="Type a message..."
                      value={input}
                      onChange={(e) => setInput(e.currentTarget.value)}
                    />
                    <PromptInputFooter>
                      <div className="flex items-center gap-1">
                        {/* Attachment menu */}
                        <PromptInputActionMenu>
                          <PromptInputActionMenuTrigger
                            tooltip="Attach image"
                          />
                          <PromptInputActionMenuContent>
                            <PromptInputActionAddAttachments label="Add images" />
                          </PromptInputActionMenuContent>
                        </PromptInputActionMenu>
                        {/* Sync Folder */}
                        <SyncFolderWidget
                          apiBase={API_BASE}
                          getHeaders={() => authHeaders()}
                          syncState={syncState}
                          activeWorkspace={activeWorkspace}
                        />

                        {/* Model Selector */}
                        <ModelSelector open={modelSelectorOpen} onOpenChange={(open) => {
                          setModelSelectorOpen(open);
                          if (open) fetchModels();
                        }}>
                          <ModelSelectorTrigger>
                            <Button
                              variant="ghost"
                              className="h-8 px-2.5 text-sm text-muted-foreground/80 hover:text-foreground cursor-pointer gap-1.5"
                            >
                              {(() => {
                                const current = sessionStatus.model || '';
                                const info = resolveModelInfo(current, availableModels);
                                return (
                                  <>
                                    {info.provider && <ModelSelectorLogo provider={info.provider} className="size-4" />}
                                    <span>{info.name || 'Select model'}</span>
                                    <ChevronDown className="size-3.5 opacity-50" />
                                  </>
                                );
                              })()}
                            </Button>
                          </ModelSelectorTrigger>
                          <ModelSelectorContent title="Select a Model">
                            <ModelSelectorInput placeholder="Search models..." />
                            <ModelSelectorList>
                              <ModelSelectorEmpty>No models found.</ModelSelectorEmpty>
                              {Object.entries(
                                availableModels.reduce<Record<string, ModelInfo[]>>((groups, m) => {
                                  const key = m.provider || 'other';
                                  if (!groups[key]) groups[key] = [];
                                  groups[key].push(m);
                                  return groups;
                                }, {})
                              ).map(([provider, models]) => (
                                <ModelSelectorGroup key={provider} heading={displayProvider(provider)}>
                                  {models.map((m) => {
                                    const isCurrent = m.id === sessionStatus.model;
                                    return (
                                      <ModelSelectorItem
                                        key={m.id}
                                        value={m.id}
                                        onSelect={() => handleModelSwitch(m.id)}
                                        className={isCurrent ? 'bg-accent' : ''}
                                      >
                                        {m.provider && <ModelSelectorLogo provider={m.provider} className="size-4 mr-2" />}
                                        <ModelSelectorName>{m.name}</ModelSelectorName>
                                        {isCurrent && <span className="text-xs text-muted-foreground">current</span>}
                                      </ModelSelectorItem>
                                    );
                                  })}
                                </ModelSelectorGroup>
                              ))}
                            </ModelSelectorList>
                          </ModelSelectorContent>
                        </ModelSelector>
                      </div>
                      <div className="flex items-center gap-1.5">
                        {/* Context Usage */}
                        {sessionStatus.active && (sessionStatus.prompt_tokens ?? 0) > 0 && (
                          <Context
                            usedTokens={sessionStatus.prompt_tokens ?? 0}
                            maxTokens={sessionStatus.context_limit ?? 200000}
                            modelId={sessionStatus.model?.replace('/', ':') ?? ''}
                            usage={{
                              inputTokens: sessionStatus.total_prompt_tokens ?? 0,
                              outputTokens: sessionStatus.total_completion_tokens ?? 0,
                              totalTokens: sessionStatus.total_tokens ?? 0,
                              cachedInputTokens: sessionStatus.total_cached_tokens ?? 0,
                              inputTokenDetails: { cacheReadTokens: sessionStatus.total_cached_tokens ?? 0, noCacheTokens: undefined, cacheWriteTokens: sessionStatus.total_cache_write_tokens ?? 0 },
                              outputTokenDetails: { textTokens: undefined, reasoningTokens: undefined },
                            }}
                          >
                            <ContextTrigger className="h-6 px-1.5 text-[11px] text-muted-foreground/60" />
                            <ContextContent side="top" align="start">
                              <ContextContentHeader />
                              <ContextContentBody className="space-y-1">
                                <ContextInputUsage />
                                <ContextOutputUsage />
                                <ContextCacheUsage />
                              </ContextContentBody>
                            </ContextContent>
                          </Context>
                        )}
                        <PromptInputSubmit
                          className="cursor-pointer"
                          disabled={!isStreaming && !input.trim()}
                          status={isStopping ? "submitted" : isStreaming ? "streaming" : "ready"}
                          onStop={() => {
                            if (isStopping) return;
                            setIsStopping(true);
                            fetch(`${API_BASE}/api/command/stop`, {
                              method: "POST",
                              headers: authHeaders({ "Content-Type": "application/json" }),
                              body: JSON.stringify({}),
                            }).catch(() => {});
                          }}
                        />
                      </div>
                    </PromptInputFooter>
                  </PromptInput>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </TooltipProvider>
    <Toaster
      position="top-right"
      toastOptions={{
        className: "!bg-background !text-foreground !border-border/50 !shadow-lg",
        duration: 4000,
      }}
    />
    <SettingsDialog
      isOpen={settingsOpen}
      onClose={() => setSettingsOpen(false)}
      apiBase={API_BASE}
      apiToken={API_TOKEN}
      syncState={syncState}
    />
    <OnboardingDialog
      open={onboardingOpen}
      onClose={() => {
        setOnboardingOpen(false);
        setOnboardingDismissed(true);
      }}
      apiBase={API_BASE}
      getHeaders={() => authHeaders()}
      vpsDeviceId={syncStatus?.device_id || ""}
      existingWorkspaces={workspaceList.length > 0}
      onCreated={(wsId) => {
        setActiveWorkspaceId(wsId);
        setActiveView("chat");
        setMessages([]);
        setIsStopping(false);
        setCurrentSessionId(null);
        setOnboardingOpen(false);
        setOnboardingDismissed(true);
        fetchWorkspaces();
        // Start a fresh chat in this brand-new default workspace.
        fetch(`${API_BASE}/api/sessions`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ workspace_id: wsId }),
        })
          .then((r) => r.json())
          .then((data) => {
            if (data.session_id) setCurrentSessionId(data.session_id);
            refreshStatus();
            fetchSessions();
          })
          .catch(() => {});
      }}
    />
    <NewWorkspaceDialog
      open={newWorkspaceOpen}
      onClose={() => setNewWorkspaceOpen(false)}
      apiBase={API_BASE}
      getHeaders={() => authHeaders()}
      vpsDeviceId={syncStatus?.device_id || ""}
      onCreated={(wsId) => {
        // Refresh workspace list, switch into the new one, and start a fresh chat there.
        setActiveWorkspaceId(wsId);
        setActiveView("chat");
        setMessages([]);
        setIsStopping(false);
        setCurrentSessionId(null);
        fetchWorkspaces();
        fetch(`${API_BASE}/api/sessions`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ workspace_id: wsId }),
        })
          .then((r) => r.json())
          .then((data) => {
            if (data.session_id) setCurrentSessionId(data.session_id);
            refreshStatus();
            fetchSessions();
          })
          .catch(() => {});
      }}
    />
    <ConfirmDialog
      open={pendingConfirm !== null}
      busy={pendingConfirm?.busy}
      tone="danger"
      title={
        pendingConfirm?.kind === "delete-workspace"
          ? `Delete workspace "${pendingConfirm.label}"?`
          : pendingConfirm?.kind === "delete-session"
          ? "Delete this chat?"
          : ""
      }
      description={
        pendingConfirm?.kind === "delete-workspace" ? (
          <>
            <p style={{ margin: 0 }}>This will permanently:</p>
            <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
              <li>Stop syncing this folder</li>
              <li>Remove the folder from both this device and the VPS</li>
              <li>Delete all synced files from the VPS</li>
            </ul>
            {pendingConfirm.sessionCount > 0 ? (
              <p style={{ margin: "6px 0 0" }}>
                <strong>{pendingConfirm.sessionCount}</strong>
                {pendingConfirm.sessionCount === 1 ? " chat" : " chats"} in
                this workspace will fall back to your default workspace —
                the chats themselves won't be deleted.
              </p>
            ) : null}
            <p style={{ margin: "6px 0 0", color: "#8A8886" }}>
              Your local files will NOT be touched. This cannot be undone.
            </p>
          </>
        ) : pendingConfirm?.kind === "delete-session" ? (
          <p style={{ margin: 0 }}>
            {pendingConfirm.preview.length > 120
              ? pendingConfirm.preview.slice(0, 120) + "…"
              : pendingConfirm.preview}
          </p>
        ) : null
      }
      confirmLabel={pendingConfirm?.kind === "delete-workspace" ? "Delete workspace" : "Delete chat"}
      onCancel={() => {
        if (pendingConfirm?.busy) return;
        setPendingConfirm(null);
      }}
      onConfirm={async () => {
        if (!pendingConfirm || pendingConfirm.busy) return;

        if (pendingConfirm.kind === "delete-workspace") {
          const { wsId, label } = pendingConfirm;
          setPendingConfirm({ ...pendingConfirm, busy: true });
          try {
            // Step A: backend nukes VPS side (Syncthing config + files)
            // and deletes the workspace record. Returns the folder_id so
            // we can finish up on the local Syncthing side.
            const res = await fetch(`${API_BASE}/api/workspaces/${wsId}`, {
              method: "DELETE",
              headers: authHeaders(),
            });
            if (!res.ok) {
              const err = await res.json().catch(() => ({ error: "delete failed" }));
              toast.error(err.error || `Could not delete workspace (HTTP ${res.status})`);
              setPendingConfirm({ ...pendingConfirm, busy: false });
              return;
            }
            const payload: {
              folder_id?: string | null;
              vps_folder_removed?: boolean;
              vps_files_removed?: boolean;
              vps_folder_path?: string | null;
            } = await res.json().catch(() => ({}));

            // Step B: remove the folder from local Syncthing config so
            // this device stops broadcasting a folder the VPS no longer
            // knows about. Local files are untouched (Syncthing never
            // deletes files it was just told to forget).
            if (payload.folder_id && window.electronAPI?.syncthingRemoveFolder) {
              try {
                await window.electronAPI.syncthingRemoveFolder({
                  folderId: payload.folder_id,
                });
              } catch {
                // Non-fatal: the workspace is already gone; a stale local
                // folder config will just show as "unshared". Log via toast.
                toast.warning(
                  "Workspace deleted, but local Syncthing folder could not be removed. " +
                  "You can remove it manually from Settings.",
                );
              }
            }

            if (activeWorkspaceId === wsId) setActiveWorkspaceId(null);
            fetchWorkspaces();
            fetchSessions();

            // Honest success message: tell the user exactly what got nuked.
            const vpsOk = payload.vps_folder_removed && payload.vps_files_removed;
            toast.success(`Deleted workspace "${label}"`, {
              description: vpsOk
                ? "VPS folder and files removed. Your local files are untouched."
                : payload.vps_folder_removed
                ? "VPS folder removed, but cloud files could not be deleted. Check VPS manually."
                : "Workspace record removed. VPS was unreachable — clean up manually when it's back.",
            });
            setPendingConfirm(null);
          } catch {
            toast.error("Network error — could not delete workspace");
            setPendingConfirm({ ...pendingConfirm, busy: false });
          }
          return;
        }

        if (pendingConfirm.kind === "delete-session") {
          const { sessionId } = pendingConfirm;
          setPendingConfirm({ ...pendingConfirm, busy: true });
          try {
            const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`, {
              method: "DELETE",
              headers: authHeaders(),
            });
            if (res.ok) {
              fetchSessions();
              fetchWorkspaces();
              toast.success("Chat deleted");
              setPendingConfirm(null);
            } else {
              toast.error(`Could not delete chat (HTTP ${res.status})`);
              setPendingConfirm({ ...pendingConfirm, busy: false });
            }
          } catch {
            toast.error("Network error — could not delete chat");
            setPendingConfirm({ ...pendingConfirm, busy: false });
          }
        }
      }}
    />
    <RenameDialog
      open={pendingRename !== null}
      busy={pendingRename?.busy}
      title="Rename workspace"
      label="Workspace name"
      initialValue={pendingRename?.current || ""}
      onCancel={() => {
        if (pendingRename?.busy) return;
        setPendingRename(null);
      }}
      onConfirm={async (next) => {
        if (!pendingRename || pendingRename.busy) return;
        const { wsId, current } = pendingRename;
        setPendingRename({ ...pendingRename, busy: true });
        try {
          const res = await fetch(`${API_BASE}/api/workspaces/${wsId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ label: next }),
          });
          if (res.ok) {
            fetchWorkspaces();
            toast.success(`Renamed "${current}" → "${next}"`);
            setPendingRename(null);
          } else {
            toast.error(`Could not rename workspace (HTTP ${res.status})`);
            setPendingRename({ ...pendingRename, busy: false });
          }
        } catch {
          toast.error("Network error — could not rename workspace");
          setPendingRename({ ...pendingRename, busy: false });
        }
      }}
    />
    </>
  );
}

function AppWithErrorBoundary() {
  return (
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  );
}

export default AppWithErrorBoundary;
