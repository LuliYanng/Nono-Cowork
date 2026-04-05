import { useState, useEffect } from "react";
import { Clock, Zap, FolderOpen, Play, Trash2, CalendarClock, Loader2, Plus, Edit2, AlertTriangle } from "lucide-react";
import { RoutineEditorDialog } from "./routine-editor-dialog";
import { toast } from "sonner";

// ── Types ──

export type AutomationType = "cron" | "trigger" | "file_drop";

export interface Automation {
  id: string;
  type: AutomationType;
  name: string;
  description: string;
  schedule: string;
  enabled: boolean;
  model: string;
  channel_name: string;
  user_id: string;
  created_at: string;
  last_run_at: string | null;
  last_result: string;
  next_run_at: string | null;
  config: Record<string, unknown>;
}

interface AutomationsResponse {
  automations: Automation[];
  total: number;
  counts: { cron: number; trigger: number; file_drop: number };
}

// ── Config ──

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";
const API_TOKEN = import.meta.env.VITE_API_TOKEN || "";

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  if (API_TOKEN) {
    headers["Authorization"] = `Bearer ${API_TOKEN}`;
  }
  return headers;
}

// ── Simple Switch Component ──
function Switch({ checked, onChange, disabled }: { checked: boolean; onChange: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onChange}
      disabled={disabled}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
        disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"
      } ${checked ? "bg-blue-500/80" : "bg-muted-foreground/20"}`}
      aria-checked={checked}
      role="switch"
    >
      <span
        className={`pointer-events-none block h-4 w-4 rounded-full bg-white shadow-lg ring-0 transition-transform ${
          checked ? "translate-x-4" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

// ── Type display config ──
const TYPE_CONFIG = {
  cron: { icon: Clock, label: "Scheduled Tasks", color: "blue" },
  trigger: { icon: Zap, label: "Triggers", color: "amber" },
  file_drop: { icon: FolderOpen, label: "File Drop", color: "emerald" },
} as const;

// ── Component ──

export function RoutinesPage() {
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [counts, setCounts] = useState({ cron: 0, trigger: 0, file_drop: 0 });
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | AutomationType>("all");
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [toggleErrors, setToggleErrors] = useState<Record<string, string>>({});
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingAutomation, setEditingAutomation] = useState<Automation | undefined>(undefined);

  useEffect(() => {
    fetchAutomations();
  }, []);

  const fetchAutomations = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE}/api/automations`, {
        headers: authHeaders({ "Content-Type": "application/json" }),
      });
      if (res.ok) {
        const data: AutomationsResponse = await res.json();
        setAutomations(data.automations);
        setCounts(data.counts || { cron: 0, trigger: 0, file_drop: 0 });
      }
    } catch (e) {
      console.error("Failed to fetch automations", e);
    } finally {
      setLoading(false);
    }
  };

  /** Map trigger slug prefix to a human-readable app name */
  const appNameFromSlug = (slug: string): string => {
    const prefix = slug.split("_")[0];
    const map: Record<string, string> = {
      GMAIL: "Gmail",
      SLACK: "Slack",
      GITHUB: "GitHub",
      GOOGLE: "Google",
      NOTION: "Notion",
      LINEAR: "Linear",
      OUTLOOK: "Outlook",
    };
    return map[prefix] || prefix;
  };

  // ── Unified API calls ──

  const handleToggle = async (a: Automation) => {
    setToggleErrors((p) => { const next = { ...p }; delete next[a.id]; return next; });
    setActionLoading((p) => ({ ...p, [a.id]: true }));
    try {
      const res = await fetch(`${API_BASE}/api/automations/${a.id}/toggle`, {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
      });

      if (res.ok) {
        const data = await res.json();
        setAutomations((prev) =>
          prev.map((item) => {
            if (item.id === a.id) {
              return { ...item, enabled: data.enabled, id: data.id || item.id, next_run_at: data.next_run_at || item.next_run_at };
            }
            return item;
          })
        );
        return;
      }

      // ── Handle structured errors ──
      const data = await res.json().catch(() => null);

      if (res.status === 400 && data?.error_code === "connection_missing") {
        const appName = appNameFromSlug(data.trigger_slug || a.schedule);
        const msg = `${appName} connection expired. Please reconnect to re-enable this trigger.`;
        toast.error(msg, { duration: 6000 });
        setToggleErrors((p) => ({ ...p, [a.id]: msg }));
      } else {
        toast.error(data?.error || "Failed to toggle automation");
      }
    } catch (e) {
      console.error("Failed to toggle", e);
      toast.error("Network error — could not reach the server");
    } finally {
      setActionLoading((p) => ({ ...p, [a.id]: false }));
    }
  };

  const handleDelete = async (a: Automation) => {
    if (!confirm(`Are you sure you want to delete "${a.name}"?`)) return;

    setActionLoading((p) => ({ ...p, [a.id]: true }));
    try {
      const res = await fetch(`${API_BASE}/api/automations/${a.id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (res.ok) {
        setAutomations((prev) => prev.filter((item) => item.id !== a.id));
        setCounts((prev) => ({ ...prev, [a.type]: Math.max(0, (prev[a.type] || 0) - 1) }));
      }
    } catch (e) {
      console.error("Failed to delete", e);
      setActionLoading((p) => ({ ...p, [a.id]: false }));
    }
  };

  const handleRun = async (a: Automation) => {
    if (a.type !== "cron") return;

    setActionLoading((p) => ({ ...p, [`${a.id}-run`]: true }));
    try {
      const res = await fetch(`${API_BASE}/api/automations/${a.id}/run`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (res.ok) {
        toast.success("Task triggered! Output will appear in Workspace.");
      }
    } catch (e) {
      console.error("Failed to trigger task", e);
    } finally {
      setActionLoading((p) => ({ ...p, [`${a.id}-run`]: false }));
    }
  };

  const handleSaveRoutine = async (data: any) => {
    try {
      const isEdit = !!editingAutomation;
      const endpoint = isEdit
        ? `/api/automations/${editingAutomation!.id}`
        : "/api/automations";
      const method = isEdit ? "PUT" : "POST";

      const res = await fetch(`${API_BASE}${endpoint}`, {
        method,
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(data),
      });

      if (res.ok) {
        await fetchAutomations();
        return true;
      }
      const err = await res.json().catch(() => null);
      toast.error(err?.error || "Failed to save routine");
      return false;
    } catch (e) {
      console.error("Failed to save routine", e);
      return false;
    }
  };

  const filteredAutomations = automations.filter(
    (a) => filter === "all" || a.type === filter
  );

  const filterTabs: { key: "all" | AutomationType; label: string }[] = [
    { key: "all", label: "All" },
    { key: "cron", label: "Scheduled" },
    { key: "trigger", label: "Triggers" },
    { key: "file_drop", label: "File Drop" },
  ];

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-background">
      {/* Header */}
      <div className="shrink-0 px-8 pt-6 pb-4">
        <div className="max-w-3xl mx-auto flex items-end justify-between">
          <div>
            <h1 className="text-xl font-semibold text-foreground/85 tracking-tight flex items-center gap-2">
              <CalendarClock size={20} className="text-muted-foreground" />
              Routines
            </h1>
            <p className="text-[13px] text-muted-foreground/50 mt-1">
              Scheduled tasks, event triggers, and file-drop automations
            </p>
          </div>
          <button
            className="flex items-center gap-1.5 px-3 py-1.5 bg-foreground/5 hover:bg-foreground/10 text-foreground/80 rounded-lg text-xs font-medium transition-colors cursor-pointer"
            onClick={() => { setEditingAutomation(undefined); setEditorOpen(true); }}
          >
            <Plus size={14} />
            <span>New Routine</span>
          </button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="px-8 shrink-0 border-b border-border/40">
        <div className="max-w-3xl mx-auto flex items-center gap-6">
          {filterTabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setFilter(tab.key)}
              className={`pb-3 text-[13px] font-medium transition-colors relative ${
                filter === tab.key ? "text-foreground" : "text-muted-foreground/60 hover:text-foreground/80"
              }`}
            >
              {tab.label}
              <span className="ml-1.5 text-[10px] text-muted-foreground bg-muted hover:bg-muted/80 px-1.5 py-0.5 rounded-full">
                {tab.key === "all" ? automations.length : counts[tab.key] || 0}
              </span>
              {filter === tab.key && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-foreground rounded-t-full" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="max-w-3xl mx-auto flex flex-col gap-4">
          {loading ? (
            <div className="flex flex-col gap-4 animate-pulse">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex flex-col rounded-xl border bg-card p-4 shadow-sm">
                  <div className="flex justify-between items-start">
                    <div className="flex items-start gap-4">
                      <div className="size-10 rounded-lg bg-muted/60 shrink-0" />
                      <div className="flex flex-col gap-2 pt-0.5">
                        <div className="h-4 w-36 rounded bg-muted/50" />
                        <div className="h-3 w-56 rounded bg-muted/30" />
                        <div className="flex gap-3 mt-2">
                          <div className="h-5 w-24 rounded-md bg-muted/40" />
                          <div className="h-5 w-32 rounded-md bg-muted/30" />
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 h-10 pr-3">
                      <div className="h-3 w-6 rounded bg-muted/30" />
                      <div className="h-5 w-9 rounded-full bg-muted/40" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : filteredAutomations.length === 0 ? (
            <div className="flex flex-col items-center justify-center p-12 text-center border border-dashed rounded-xl bg-muted/20">
              <div className="size-12 bg-muted/50 rounded-full flex items-center justify-center mb-4">
                <CalendarClock size={24} className="text-muted-foreground/60" />
              </div>
              <p className="text-sm font-medium text-foreground/80">No routines found</p>
              <p className="text-xs text-muted-foreground mt-1 max-w-[280px]">
                Create a scheduled task, event trigger, or file-drop rule to automate your workflows.
              </p>
            </div>
          ) : (
            filteredAutomations.map((a) => {
              const typeConf = TYPE_CONFIG[a.type] || TYPE_CONFIG.cron;
              const Icon = typeConf.icon;
              const isEnabled = a.enabled;

              return (
                <div
                  key={a.id}
                  className="group relative flex flex-col rounded-xl border bg-card p-4 shadow-sm transition-all hover:shadow-md"
                >
                  <div className="flex justify-between items-start">
                    <div className="flex items-start gap-4">
                      {/* Icon */}
                      <div
                        className={`size-10 rounded-lg flex items-center justify-center shrink-0 ${
                          isEnabled
                            ? a.type === "cron"
                              ? "bg-blue-500/10 text-blue-500"
                              : a.type === "trigger"
                              ? "bg-amber-500/10 text-amber-500"
                              : "bg-emerald-500/10 text-emerald-500"
                            : "bg-muted text-muted-foreground/50"
                        }`}
                      >
                        <Icon size={20} strokeWidth={2} />
                      </div>

                      {/* Info */}
                      <div>
                        <div className="flex items-center gap-2">
                          <h3
                            className={`text-[15px] font-semibold tracking-tight ${
                              isEnabled ? "text-foreground" : "text-muted-foreground/70"
                            }`}
                          >
                            {a.name}
                          </h3>
                          {a.type === "file_drop" && (
                            <span className="text-[10px] font-medium uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600">
                              FILE DROP
                            </span>
                          )}
                        </div>
                        <p className="text-[13px] text-muted-foreground mt-1 line-clamp-1 leading-relaxed">
                          {a.description}
                        </p>

                        <div className="flex items-center gap-4 mt-3 text-xs text-muted-foreground/80 font-medium">
                          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-muted/50 border">
                            <span className="text-muted-foreground font-mono">{a.schedule}</span>
                          </div>
                          {a.type === "cron" && (
                            <>
                              {a.next_run_at ? (
                                <span>Next: {new Date(a.next_run_at).toLocaleString(undefined, {
                                    month: 'short', day: 'numeric', hour: '2-digit', minute:'2-digit'
                                })}</span>
                              ) : (
                                <span>Task Disabled</span>
                              )}
                            </>
                          )}
                          {a.type === "file_drop" && (
                            <span className="text-emerald-600/70">
                              on {(a.config?.actions as string[])?.join(", ") || "added, modified"}
                            </span>
                          )}
                          {toggleErrors[a.id] && (
                            <div className="flex items-center gap-1 text-amber-500">
                              <AlertTriangle size={12} />
                              <span className="truncate max-w-[260px]">Connection expired</span>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Right side Toggle */}
                    <div className="flex items-center h-10 shrink-0">
                      <div className="flex items-center gap-2 mr-3 px-3">
                         <span className={`text-[11px] font-medium uppercase tracking-wider ${isEnabled ? "text-foreground/70" : "text-muted-foreground/50"}`}>
                           {isEnabled ? "ON" : "OFF"}
                         </span>
                         <Switch
                           checked={isEnabled}
                           onChange={() => handleToggle(a)}
                           disabled={actionLoading[a.id]}
                         />
                      </div>
                    </div>
                  </div>

                  {/* Actions hover overlay */}
                  <div className="absolute right-4 bottom-4 flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    {a.type === "cron" && (
                      <button
                        onClick={() => handleRun(a)}
                        disabled={actionLoading[`${a.id}-run`]}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-foreground/70 hover:text-foreground hover:bg-muted rounded-md transition-colors"
                      >
                        {actionLoading[`${a.id}-run`] ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Play size={14} />
                        )}
                        Run Now
                      </button>
                    )}
                    <button
                      onClick={() => { setEditingAutomation(a); setEditorOpen(true); }}
                      className="px-2 py-1.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded-md transition-colors"
                      aria-label="Edit"
                    >
                      <Edit2 size={15} />
                    </button>
                    <button
                      onClick={() => handleDelete(a)}
                      disabled={actionLoading[a.id]}
                      className="px-2 py-1.5 text-muted-foreground hover:text-red-500 hover:bg-red-500/10 rounded-md transition-colors"
                      aria-label="Delete"
                    >
                      {actionLoading[a.id] ? (
                        <Loader2 size={15} className="animate-spin" />
                      ) : (
                        <Trash2 size={15} />
                      )}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
      <RoutineEditorDialog
        open={editorOpen}
        onOpenChange={setEditorOpen}
        automation={editingAutomation}
        onSave={handleSaveRoutine}
      />
    </div>
  );
}
