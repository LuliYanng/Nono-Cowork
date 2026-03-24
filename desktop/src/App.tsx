import { useState, useCallback, useRef, useEffect } from "react";
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

// ── Types ──

interface ThoughtEvent {
  type: "tool_call" | "tool_result";
  round: number;
  toolName?: string;
  args?: Record<string, unknown>;
  result?: string;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  thoughts?: ThoughtEvent[];
}

interface SessionStatus {
  active: boolean;
  model?: string;
  context_pct?: number;
  prompt_tokens?: number;
  context_limit?: number;
  is_running?: boolean;
}

// ── Config ──

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";

// ── Tool Events Renderer ──
// Renders tool_call + tool_result as Tool components

function ToolEventsRenderer({
  thoughts,
  isActive,
}: {
  thoughts: ThoughtEvent[];
  isActive: boolean;
}) {
  const items: React.ReactNode[] = [];
  let i = 0;

  while (i < thoughts.length) {
    const evt = thoughts[i];

    if (evt.type === "tool_call") {
      const nextEvt = i + 1 < thoughts.length ? thoughts[i + 1] : null;
      const hasResult =
        nextEvt?.type === "tool_result" &&
        nextEvt.toolName === evt.toolName;

      let toolState: "input-available" | "output-available" | "input-streaming";
      if (hasResult) {
        toolState = "output-available";
      } else if (isActive) {
        toolState = "input-available";
      } else {
        toolState = "input-streaming";
      }

      items.push(
        <Tool key={`t-${i}`} defaultOpen={toolState === "output-available"}>
          <ToolHeader
            title={evt.toolName || "tool"}
            type={`tool-${evt.toolName || "unknown"}` as `tool-${string}`}
            state={toolState}
          />
          <ToolContent>
            {evt.args && Object.keys(evt.args).length > 0 && (
              <ToolInput input={evt.args} />
            )}
            {hasResult && nextEvt && (
              <ToolOutput output={nextEvt.result} errorText={undefined} />
            )}
          </ToolContent>
        </Tool>
      );

      i = hasResult ? i + 2 : i + 1;
      continue;
    }

    i++;
  }

  return <>{items}</>;
}

// ── App ──

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
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

  // Health check on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then((r) => r.json())
      .then((data) => {
        setConnected(true);
        setSessionStatus((prev) => ({ ...prev, model: data.model }));
      })
      .catch(() => setConnected(false));
  }, []);

  // Generate unique ID
  const nextId = useCallback(() => {
    idCounter.current += 1;
    return `msg-${idCounter.current}`;
  }, []);

  // Fetch session status
  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/status`);
      const data = await res.json();
      setSessionStatus(data);
    } catch {
      // ignore
    }
  }, []);

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
    const currentThoughts: ThoughtEvent[] = [];
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
            thoughts: [...currentThoughts],
            ...patch,
          },
        ];
      });
    };

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
                assistantContent += (data.content || "");
                setAnimatingMsgId(assistantId);
                updateMsg({ content: assistantContent });
              } else if (eventType === "thought") {
                currentThoughts.push({
                  type: data.type,
                  round: data.round,
                  toolName: data.tool_name,
                  args: data.args,
                  result: data.result,
                });
                setThinkingMsgId(assistantId);
                updateMsg({ thoughts: [...currentThoughts] });
              } else if (eventType === "reply") {
                // Final complete reply (fallback if text_chunk was not used)
                assistantContent = data.text;
                setAnimatingMsgId(assistantId);
                updateMsg({ content: assistantContent });
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
    }
  }, [isStreaming, nextId, refreshStatus]);

  // Slash commands
  const handleCommand = useCallback(
    async (cmd: string) => {
      try {
        const res = await fetch(`${API_BASE}/api/command/${cmd}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const data = await res.json();

        if (cmd === "reset") {
          setMessages([]);
        }

        if (data.result) {
          setStatusText(data.result);
          setTimeout(() => setStatusText(""), 5000);
        }

        refreshStatus();
      } catch {
        setStatusText(`❌ Failed to execute /${cmd}`);
      }
    },
    [refreshStatus]
  );

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

  // Context bar
  const ctxPct = sessionStatus.context_pct ?? 0;
  const ctxColor =
    ctxPct < 50
      ? "bg-green-500"
      : ctxPct < 80
        ? "bg-yellow-500"
        : "bg-red-500";

  // PromptInput onSubmit handler — receives { text, files } from the component
  const handlePromptSubmit = useCallback(async () => {
    await handleSubmit();
  }, [handleSubmit]);

  return (
    <TooltipProvider>
      <div className="flex flex-col h-screen bg-background text-foreground">
        {/* Header */}
        <header className="flex items-center justify-between px-4 py-2 border-b border-border">
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-semibold">Nono CoWork</h1>
            <span className={`text-xs ${connColor}`}>● {connLabel}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {sessionStatus.model || "..."}
            </span>
            <button
              onClick={() => handleCommand("reset")}
              className="text-xs px-2 py-1 rounded hover:bg-muted text-muted-foreground"
              disabled={isStreaming}
            >
              Reset
            </button>
            <button
              onClick={() => handleCommand("stop")}
              className="text-xs px-2 py-1 rounded hover:bg-muted text-muted-foreground"
              disabled={!isStreaming}
            >
              Stop
            </button>
          </div>
        </header>

        {/* Chat area */}
        <Conversation className="flex-1">
          <ConversationContent>
            {messages.length === 0 && !isStreaming && (
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
                      {msg.thoughts && msg.thoughts.length > 0 && (
                        <ToolEventsRenderer
                          thoughts={msg.thoughts}
                          isActive={msg.id === thinkingMsgId}
                        />
                      )}
                      {msg.content && (
                        <MessageResponse
                          animated
                          isAnimating={msg.id === animatingMsgId}
                        >
                          {msg.content}
                        </MessageResponse>
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

        {/* Input area */}
        <div className="border-t border-border p-3">
          <PromptInput
            onSubmit={handlePromptSubmit}
          >
            <PromptInputTextarea
              placeholder="Type a message..."
              value={input}
              onChange={(e) => setInput(e.currentTarget.value)}
            />
            <PromptInputFooter>
              <div />
              <PromptInputSubmit
                disabled={isStreaming || !input.trim()}
                status={isStreaming ? "streaming" : "ready"}
              />
            </PromptInputFooter>
          </PromptInput>
        </div>

        {/* Footer: context bar */}
        {sessionStatus.active && (
          <footer className="flex items-center gap-3 px-4 py-1.5 border-t border-border text-xs text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <div className="w-24 h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${ctxColor}`}
                  style={{ width: `${ctxPct}%` }}
                />
              </div>
              <span>{ctxPct.toFixed(0)}% context</span>
            </div>
          </footer>
        )}
      </div>
    </TooltipProvider>
  );
}

export default App;
