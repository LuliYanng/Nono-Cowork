import { useState, useCallback, useRef, useEffect } from "react";

// Electron window control API exposed via preload
declare global {
  interface Window {
    electronAPI?: {
      minimize: () => void;
      maximize: () => void;
      close: () => void;
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
  PromptInputSubmit,
} from "@/components/ai-elements/prompt-input";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import {
  Tool,
  ToolHeader,
  ToolContent,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { Sidebar, type SessionItem, type SidebarView } from "@/components/sidebar";
import { type Notification } from "@/components/notification-card";
import { WorkspacePage } from "@/components/workspace-page";
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
import { PanelLeft, ChevronDown, Square } from "lucide-react";
import { Button } from "@/components/ui/button";

// Map litellm provider names → ModelSelectorLogo provider names
const PROVIDER_LOGO_MAP: Record<string, string> = {
  gemini: "google",
  anthropic: "anthropic",
  openai: "openai",
  deepseek: "deepseek",
  dashscope: "alibaba",
  mistral: "mistral",
  groq: "groq",
  xai: "xai",
};

// Capitalize provider name for display
function displayProvider(provider: string): string {
  const names: Record<string, string> = {
    gemini: "Google",
    anthropic: "Anthropic",
    openai: "OpenAI",
    deepseek: "DeepSeek",
    dashscope: "DashScope",
    mistral: "Mistral",
    groq: "Groq",
    xai: "xAI",
  };
  return names[provider] || provider.charAt(0).toUpperCase() + provider.slice(1);
}

// Available models from backend
type AvailableModels = string[];

// ── Types ──

type MessagePart =
  | { type: "text"; content: string }
  | { type: "tool_call"; toolName?: string; args?: Record<string, unknown>; round: number }
  | { type: "tool_result"; toolName?: string; result?: string; round: number };

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  parts?: MessagePart[];
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

  for (const msg of backendMessages) {
    if (msg.role === "user") {
      msgs.push({
        id: `hist-${++counter}`,
        role: "user",
        content: msg.content || "",
      });
    } else if (msg.role === "assistant") {
      const parts: MessagePart[] = [];

      // 1. Text content → text part
      if (msg.content) {
        parts.push({ type: "text", content: msg.content });
      }

      // 2. Tool calls → tool_call parts + matched tool_result parts
      if (msg.tool_calls && Array.isArray(msg.tool_calls)) {
        for (const tc of msg.tool_calls) {
          const fn = tc.function || {};
          const toolName = fn.name || "unknown";
          let args: Record<string, unknown> = {};
          try {
            args = JSON.parse(fn.arguments || "{}");
          } catch {
            // arguments might not be valid JSON
          }

          parts.push({
            type: "tool_call",
            toolName,
            args,
            round: 0,
          });

          // Match tool result by tool_call_id
          const toolResult = toolResultMap.get(tc.id);
          if (toolResult) {
            let result = toolResult.content || "";
            // Truncate large results for display (consistent with SSE streaming)
            if (result.length > 500) {
              result = result.slice(0, 500) + `… (${result.length} chars)`;
            }
            parts.push({
              type: "tool_result",
              toolName,
              result,
              round: 0,
            });
          }
        }
      }

      msgs.push({
        id: `hist-${++counter}`,
        role: "assistant",
        content: msg.content || "",
        reasoning: msg.reasoning_content || undefined,
        parts: parts.length > 0 ? parts : undefined,
      });
    }
    // role === "tool" consumed via toolResultMap — skip
  }

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
  is_running?: boolean;
}

// ── Config ──

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";
const API_TOKEN = import.meta.env.VITE_API_TOKEN || "";

// Helper: build headers with optional Bearer token
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  if (API_TOKEN) {
    headers["Authorization"] = `Bearer ${API_TOKEN}`;
  }
  return headers;
}

// ── Parts Renderer ──
// Renders MessagePart[] in order: text blocks + tool calls interleaved

function PartsRenderer({
  parts,
  isActive,
  isStreaming,
  defaultCollapsed = false,
}: {
  parts: MessagePart[];
  isActive: boolean;
  isStreaming?: boolean;
  defaultCollapsed?: boolean;
}) {
  const items: React.ReactNode[] = [];
  let i = 0;

  while (i < parts.length) {
    const part = parts[i];

    if (part.type === "text") {
      items.push(
        <MessageResponse key={`text-${i}`}>
          {part.content}
        </MessageResponse>
      );
      i++;
      continue;
    }

    if (part.type === "tool_call") {
      const nextPart = i + 1 < parts.length ? parts[i + 1] : null;
      const hasResult =
        nextPart?.type === "tool_result" &&
        nextPart.toolName === part.toolName;

      let toolState: "input-available" | "output-available" | "input-streaming";
      if (hasResult) {
        toolState = "output-available";
      } else if (isActive || (isStreaming && part.toolName === "delegate")) {
        // delegate stays "Running" as long as the stream is active (even if
        // thinkingMsgId was temporarily cleared by a status event)
        toolState = "input-available";
      } else {
        toolState = "input-streaming";
      }

      // Historical messages: collapsed by default; live streaming: auto-expand
      const shouldOpen = defaultCollapsed ? false : toolState === "output-available";

      // Stop button for long-running delegate tool — show whenever delegate
      // is still running (stream active + no result), independent of thinkingMsgId
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

      items.push(
        <Tool key={`t-${i}`} defaultOpen={shouldOpen}>
          <ToolHeader
            title={part.toolName || "tool"}
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
      );

      i = hasResult ? i + 2 : i + 1;
      continue;
    }

    // Skip standalone tool_result
    i++;
  }

  return <>{items}</>;
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
  const [availableModels, setAvailableModels] = useState<AvailableModels>([]);
  const [modelSelectorOpen, setModelSelectorOpen] = useState(false);
  // Track whether a stop request has been sent (for immediate UI feedback)
  const [isStopping, setIsStopping] = useState(false);
  // View mode: chat or workspace
  const [activeView, setActiveView] = useState<SidebarView>("chat");
  // Notifications (Workspace)
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);

  // Health check on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/health`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((data) => {
        setSessionStatus((prev) => ({ ...prev, model: data.model }));
      })
      .catch(() => {});
  }, []);

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
      setSessionList(data.sessions || []);
    } catch {
      // ignore
    }
  }, []);

  // Switch to a different conversation
  const handleSwitchSession = useCallback(async (sessionId: string) => {
    if (isStreaming) return;

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
      refreshStatus();
      fetchSessions();
    } catch {
      // ignore
    } finally {
      setLoadingSession(false);
    }
  }, [isStreaming, refreshStatus, fetchSessions]);

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

  // Load session list, models, and notifications on mount
  useEffect(() => {
    fetchSessions();
    fetchModels();
    fetchNotifications();
  }, [fetchSessions, fetchModels, fetchNotifications]);

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

    // Fetch-based SSE with auth header
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch(url, {
          headers: authHeaders(),
          signal: controller.signal,
        });
        const reader = res.body?.getReader();
        if (!reader) return;
        const decoder = new TextDecoder();
        let buffer = "";
        let eventType = "";

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
        // Connection closed or aborted
      }
    })();
    return () => controller.abort();
  }, [fetchNotifications]);

  // Send message — called by PromptInput onSubmit with { text, files }
  const handleSubmit = useCallback(async () => {
    const text = inputRef.current.trim();
    if (!text || isStreaming) return;

    // Add user message
    const userMsg: ChatMessage = { id: nextId(), role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setIsStreaming(true);
    setStatusText("Thinking...");

    // Prepare assistant message placeholder
    const assistantId = nextId();
    const currentParts: MessagePart[] = [];
    let assistantContent = "";
    let currentReasoning = "";

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
            reasoning: currentReasoning,
            parts: [...currentParts],
            ...patch,
          },
        ];
      });
    };

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ message: text }),
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
                currentReasoning += (data.content || "");
                setThinkingMsgId(assistantId);
                updateMsg({ reasoning: currentReasoning });
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
                });
                setThinkingMsgId(assistantId);
                updateMsg({ parts: [...currentParts] });
              } else if (eventType === "reply") {
                // Fallback: only use reply if no text_chunk was received
                // (reply may only contain the last round's text)
                if (!assistantContent) {
                  assistantContent = data.text;
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
      // Show error as assistant message
      setMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          role: "assistant",
          content: `❌ Connection error: ${err instanceof Error ? err.message : "Unknown error"}`,
        },
      ]);
    } finally {
      setIsStreaming(false);
      setIsStopping(false);
      setAnimatingMsgId(null);
      setThinkingMsgId(null);
      setStatusText("");
      refreshStatus();
      fetchSessions();
    }
  }, [isStreaming, nextId, refreshStatus, fetchSessions]);


  // PromptInput onSubmit handler — receives { text, files } from the component
  const handlePromptSubmit = useCallback(async () => {
    await handleSubmit();
  }, [handleSubmit]);

  return (
    <TooltipProvider>
      <div className="flex h-screen bg-background text-foreground overflow-hidden">
        {/* Sidebar */}
        <Sidebar
          isOpen={sidebarOpen}
          onToggle={() => setSidebarOpen((p) => !p)}
          sessions={sessionList}
          onSelectSession={(id) => {
            handleSwitchSession(id);
            setActiveView("chat");
          }}
          activeView={activeView}
          onViewChange={setActiveView}
          unreadCount={unreadCount}
          onNewChat={async () => {
            try {
              await fetch(`${API_BASE}/api/sessions`, {
                method: "POST",
                headers: authHeaders(),
              });
              setMessages([]);
              setIsStopping(false);
              refreshStatus();
              fetchSessions();
            } catch { /* ignore */ }
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

          {/* View: Workspace or Chat */}
          {activeView === "workspace" ? (
            <WorkspacePage
              notifications={notifications}
              unreadCount={unreadCount}
              onNotificationClick={handleNotificationClick}
              onOpenSession={(notif) => {
                handleNotificationClick(notif);
                setActiveView("chat");
              }}
              onMarkAllRead={handleMarkAllRead}
            />
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
                  >
                    <PromptInputTextarea
                      placeholder="Type a message..."
                      value={input}
                      onChange={(e) => setInput(e.currentTarget.value)}
                    />
                    <PromptInputFooter>
                      <div className="flex items-center gap-1">
                        {/* Model Selector */}
                        <ModelSelector open={modelSelectorOpen} onOpenChange={(open) => {
                          setModelSelectorOpen(open);
                          if (open) fetchModels();
                        }}>
                          <ModelSelectorTrigger>
                            <Button
                              variant="ghost"
                              className="h-6 px-1.5 text-[11px] text-muted-foreground/60 hover:text-foreground cursor-pointer gap-1"
                            >
                              {(() => {
                                const current = sessionStatus.model || '';
                                const provider = current.split('/')[0];
                                const logoProvider = PROVIDER_LOGO_MAP[provider];
                                return (
                                  <>
                                    {logoProvider && <ModelSelectorLogo provider={logoProvider} className="size-3" />}
                                    <span>{current.split('/').pop() || 'Select model'}</span>
                                    <ChevronDown className="size-3 opacity-50" />
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
                                availableModels.reduce<Record<string, string[]>>((groups, m) => {
                                  const [provider] = m.split('/');
                                  const key = provider || 'other';
                                  if (!groups[key]) groups[key] = [];
                                  groups[key].push(m);
                                  return groups;
                                }, {})
                              ).map(([provider, models]) => (
                                <ModelSelectorGroup key={provider} heading={displayProvider(provider)}>
                                  {models.map((m) => {
                                    const modelName = m.split('/').slice(1).join('/');
                                    const logoProvider = PROVIDER_LOGO_MAP[provider];
                                    const isCurrent = m === sessionStatus.model;
                                    return (
                                      <ModelSelectorItem
                                        key={m}
                                        value={m}
                                        onSelect={() => handleModelSwitch(m)}
                                        className={isCurrent ? 'bg-accent' : ''}
                                      >
                                        {logoProvider && <ModelSelectorLogo provider={logoProvider} className="size-4 mr-2" />}
                                        <ModelSelectorName>{modelName}</ModelSelectorName>
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
              <Conversation className="flex-1">
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
                    {messages.map((msg) => (
                      <Message key={msg.id} from={msg.role}>
                        <MessageContent>
                          {msg.role === "assistant" ? (
                            <>
                              {msg.reasoning && (
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
                            <p className="whitespace-pre-wrap">{msg.content}</p>
                          )}
                        </MessageContent>
                      </Message>
                    ))}
                    {isStreaming && !animatingMsgId && !thinkingMsgId && statusText && (
                      <div className="text-sm text-muted-foreground animate-pulse px-1">
                        {statusText}
                      </div>
                    )}
                </ConversationContent>
                <ConversationScrollButton />
              </Conversation>

              {/* Input area with gradient fade above */}
              <div className="relative shrink-0 px-4 pb-4">
                <div className="pointer-events-none absolute inset-x-0 -top-8 h-8 bg-gradient-to-t from-background to-transparent" />
                <div className="w-[85%] max-w-5xl mx-auto">
                  <PromptInput
                    onSubmit={handlePromptSubmit}
                  >
                    <PromptInputTextarea
                      placeholder="Type a message..."
                      value={input}
                      onChange={(e) => setInput(e.currentTarget.value)}
                    />
                    <PromptInputFooter>
                      <div className="flex items-center gap-1">
                        {/* Model Selector */}
                        <ModelSelector open={modelSelectorOpen} onOpenChange={(open) => {
                          setModelSelectorOpen(open);
                          if (open) fetchModels();
                        }}>
                          <ModelSelectorTrigger>
                            <Button
                              variant="ghost"
                              className="h-6 px-1.5 text-[11px] text-muted-foreground/60 hover:text-foreground cursor-pointer gap-1"
                            >
                              {(() => {
                                const current = sessionStatus.model || '';
                                const provider = current.split('/')[0];
                                const logoProvider = PROVIDER_LOGO_MAP[provider];
                                return (
                                  <>
                                    {logoProvider && <ModelSelectorLogo provider={logoProvider} className="size-3" />}
                                    <span>{current.split('/').pop() || 'Select model'}</span>
                                    <ChevronDown className="size-3 opacity-50" />
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
                                availableModels.reduce<Record<string, string[]>>((groups, m) => {
                                  const [provider] = m.split('/');
                                  const key = provider || 'other';
                                  if (!groups[key]) groups[key] = [];
                                  groups[key].push(m);
                                  return groups;
                                }, {})
                              ).map(([provider, models]) => (
                                <ModelSelectorGroup key={provider} heading={displayProvider(provider)}>
                                  {models.map((m) => {
                                    const modelName = m.split('/').slice(1).join('/');
                                    const logoProvider = PROVIDER_LOGO_MAP[provider];
                                    const isCurrent = m === sessionStatus.model;
                                    return (
                                      <ModelSelectorItem
                                        key={m}
                                        value={m}
                                        onSelect={() => handleModelSwitch(m)}
                                        className={isCurrent ? 'bg-accent' : ''}
                                      >
                                        {logoProvider && <ModelSelectorLogo provider={logoProvider} className="size-4 mr-2" />}
                                        <ModelSelectorName>{modelName}</ModelSelectorName>
                                        {isCurrent && <span className="text-xs text-muted-foreground">current</span>}
                                      </ModelSelectorItem>
                                    );
                                  })}
                                </ModelSelectorGroup>
                              ))}
                            </ModelSelectorList>
                          </ModelSelectorContent>
                        </ModelSelector>

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
                              inputTokenDetails: { cacheReadTokens: sessionStatus.total_cached_tokens ?? 0, noCacheTokens: undefined, cacheWriteTokens: undefined },
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
            </>
          )}
        </div>
      </div>
    </TooltipProvider>
  );
}

export default App;
