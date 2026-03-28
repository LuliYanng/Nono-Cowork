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
import { Sidebar, type SessionItem } from "@/components/sidebar";
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
import { PanelLeft } from "lucide-react";

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
  defaultCollapsed = false,
}: {
  parts: MessagePart[];
  isActive: boolean;
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
      } else if (isActive) {
        toolState = "input-available";
      } else {
        toolState = "input-streaming";
      }

      // Historical messages: collapsed by default; live streaming: auto-expand
      const shouldOpen = defaultCollapsed ? false : toolState === "output-available";

      items.push(
        <Tool key={`t-${i}`} defaultOpen={shouldOpen}>
          <ToolHeader
            title={part.toolName || "tool"}
            type={`tool-${part.toolName || "unknown"}` as `tool-${string}`}
            state={toolState}
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
  const [connected, setConnected] = useState<boolean | null>(null);
  const idCounter = useRef(0);
  const inputRef = useRef(input);
  inputRef.current = input;
  // Track which assistant message is currently being streamed (for animation)
  const [animatingMsgId, setAnimatingMsgId] = useState<string | null>(null);
  // Track which assistant message is actively receiving thought events
  const [thinkingMsgId, setThinkingMsgId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sessionList, setSessionList] = useState<SessionItem[]>([]);

  // Health check on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/health`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((data) => {
        setConnected(true);
        setSessionStatus((prev) => ({ ...prev, model: data.model }));
      })
      .catch(() => setConnected(false));
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
        e.preventDefault();
        setSidebarOpen((prev) => !prev);
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

  // Load session list on mount
  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

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
      setAnimatingMsgId(null);
      setThinkingMsgId(null);
      setStatusText("");
      refreshStatus();
      fetchSessions();
    }
  }, [isStreaming, nextId, refreshStatus, fetchSessions]);


  // Connection indicator color
  const connColor =
    connected === true
      ? "text-green-500"
      : connected === false
        ? "text-red-500"
        : "text-yellow-500";
  const connLabel =
    connected === true
      ? "Connected"
      : connected === false
        ? "Disconnected"
        : "Connecting...";



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
          onSelectSession={handleSwitchSession}
          onNewChat={async () => {
            try {
              await fetch(`${API_BASE}/api/sessions`, {
                method: "POST",
                headers: authHeaders(),
              });
              setMessages([]);
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
              <span className={`text-xs ${connColor}`}>● {connLabel}</span>
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

          {/* Chat area */}
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
                {messages.length === 0 && !isStreaming && !loadingSession && (
                  <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-2">
                    <p className="text-lg">👋 Ready to chat</p>
                    <p className="text-sm">
                      Send a message to start a conversation
                    </p>
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
                  {sessionStatus.active && (sessionStatus.prompt_tokens ?? 0) > 0 ? (
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
                  ) : (
                    sessionStatus.model && (
                      <span className="text-[11px] text-muted-foreground/50">
                        {sessionStatus.model.split('/').pop()}
                      </span>
                    )
                  )}
                  <PromptInputSubmit
                    className="cursor-pointer"
                    disabled={!isStreaming && !input.trim()}
                    status={isStreaming ? "streaming" : "ready"}
                    onStop={() => {
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
      </div>
    </TooltipProvider>
  );
}

export default App;
