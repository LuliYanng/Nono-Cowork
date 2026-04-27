import { useState, useEffect } from "react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import type { Automation, AutomationType } from "./routines-page";
import { Loader2, Settings2, Sparkles } from "lucide-react";

interface RoutineEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  automation?: Automation;
  onSave: (data: any) => Promise<boolean>;
}

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8080";
const API_TOKEN = import.meta.env.VITE_API_TOKEN || "";

function authHeaders() {
  const headers: Record<string, string> = {};
  if (API_TOKEN) headers["Authorization"] = `Bearer ${API_TOKEN}`;
  return headers;
}

export function RoutineEditorDialog({ open, onOpenChange, automation, onSave }: RoutineEditorDialogProps) {
  const isEdit = !!automation;
  
  const [type, setType] = useState<AutomationType>("cron");
  const [name, setName] = useState("");
  const [schedule, setSchedule] = useState("");      // cron expression
  const [pathPattern, setPathPattern] = useState(""); // file_drop pattern
  const [fileActions, setFileActions] = useState("added,modified");
  const [triggerSlug, setTriggerSlug] = useState("");  // trigger slug
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [toolAccess, setToolAccess] = useState("full");

  const [loading, setLoading] = useState(false);
  const [errorProp, setErrorProp] = useState("");
  const [availableModels, setAvailableModels] = useState<string[]>([]);

  useEffect(() => {
    if (open) {
      setErrorProp("");
      // Fetch models
      fetch(`${API_BASE}/api/models`, { headers: authHeaders() })
        .then(r => r.json())
        .then(d => {
          if (d.available) setAvailableModels(d.available);
        })
        .catch(() => {});

      if (automation) {
        setType(automation.type);
        setModel(automation.model || "");
        const rawToolAccess = (automation.config?.tool_access as string) || "full";
        setToolAccess(["full", "read_only", "none"].includes(rawToolAccess) ? rawToolAccess : "full");

        if (automation.type === "cron") {
          setName(automation.name || "");
          setSchedule(automation.schedule || "");
          setPrompt((automation.config?.task_prompt as string) || automation.description || "");
        } else if (automation.type === "trigger") {
          setTriggerSlug(automation.schedule || "");
          setName(automation.schedule || "");
          setPrompt((automation.config?.agent_prompt as string) || automation.description || "");
        } else if (automation.type === "file_drop") {
          setName(automation.name || "");
          setPathPattern(automation.schedule || (automation.config?.path_pattern as string) || "");
          setFileActions(((automation.config?.actions as string[]) || ["added", "modified"]).join(","));
          setPrompt((automation.config?.agent_prompt as string) || automation.description || "");
        }
      } else {
        setType("cron");
        setName("");
        setSchedule("");
        setPathPattern("");
        setFileActions("added,modified");
        setTriggerSlug("");
        setPrompt("");
        setModel("");
        setToolAccess("full");
      }
    }
  }, [open, automation]);

  const handleSubmit = async () => {
    setErrorProp("");

    // Validation
    if (type === "cron") {
      if (!name.trim()) return setErrorProp("Name is required");
      if (!schedule.trim()) return setErrorProp("Cron expression is required");
      if (!prompt.trim()) return setErrorProp("Prompt is required");
    } else if (type === "trigger") {
      if (!triggerSlug.trim()) return setErrorProp("Trigger slug is required");
      if (!prompt.trim()) return setErrorProp("Prompt is required");
    } else if (type === "file_drop") {
      if (!name.trim()) return setErrorProp("Name is required");
      if (!pathPattern.trim()) return setErrorProp("Path pattern is required");
      if (!prompt.trim()) return setErrorProp("Prompt is required");
    }

    setLoading(true);
    
    try {
      // Build unified payload for /api/automations
      let payload: Record<string, unknown> = {
        type,
        model: model === "default" ? "" : model,
        tool_access: toolAccess,
      };

      if (type === "cron") {
        payload = { ...payload, task_name: name, cron: schedule, task_prompt: prompt };
      } else if (type === "trigger") {
        payload = { ...payload, trigger_slug: triggerSlug, agent_prompt: prompt };
      } else if (type === "file_drop") {
        const actions = fileActions.split(",").map(s => s.trim()).filter(Boolean);
        payload = { ...payload, name, path_pattern: pathPattern, agent_prompt: prompt, file_actions: actions };
      }

      const success = await onSave(payload);
      
      if (success) {
        onOpenChange(false);
      } else {
        setErrorProp("Failed to save routine.");
      }
    } catch (e: any) {
      setErrorProp(e.message || "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton className="sm:max-w-3xl max-h-[90vh] flex flex-col gap-0 p-0 overflow-hidden" aria-describedby={undefined}>
        <div className="bg-muted/50 border-b px-6 py-4 flex items-center justify-between shrink-0">
          <DialogTitle className="text-lg font-medium flex items-center gap-2">
            <Sparkles className="size-5 text-blue-500" />
            {isEdit ? "Edit Routine Settings" : "Create New Routine"}
          </DialogTitle>
        </div>

        <div className="p-6 overflow-y-auto flex-1 flex flex-col md:flex-row gap-8">
          {/* Left Column: Settings */}
          <div className="flex flex-col gap-5 md:w-1/3 shrink-0">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground/80 pb-1 border-b">
              <Settings2 className="size-4" />
              General Configuration
            </div>

            {!isEdit && (
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Type</label>
                <Select value={type} onValueChange={(v) => { setType(v as AutomationType); setErrorProp(""); }}>
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cron">Scheduled Task (Cron)</SelectItem>
                    <SelectItem value="trigger">Event Trigger</SelectItem>
                    <SelectItem value="file_drop">File Drop</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            {type === "cron" && (
              <>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Task Name</label>
                  <Input
                    className="h-9"
                    placeholder="e.g. Daily Metrics Report"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    disabled={loading}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Schedule (Cron)</label>
                  <Input
                    className="h-9 font-mono text-sm"
                    placeholder="e.g. 0 9 * * *"
                    value={schedule}
                    onChange={(e) => setSchedule(e.target.value)}
                    disabled={loading}
                  />
                  <p className="text-[11px] text-muted-foreground leading-snug">Uses standard cron format (min hr day mo wk)</p>
                </div>
              </>
            )}

            {type === "trigger" && (
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Trigger Slug</label>
                <Input
                  className="h-9 font-mono text-sm"
                  placeholder="e.g. GMAIL_NEW_GMAIL_MESSAGE"
                  value={triggerSlug}
                  onChange={(e) => setTriggerSlug(e.target.value)}
                  disabled={isEdit || loading}
                />
                <p className="text-[11px] text-muted-foreground leading-snug">Exact event identifier from Composio.</p>
              </div>
            )}

            {type === "file_drop" && (
              <>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Rule Name</label>
                  <Input
                    className="h-9"
                    placeholder="e.g. Auto Translate"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    disabled={loading}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Path Pattern</label>
                  <Input
                    className="h-9 font-mono text-sm"
                    placeholder="e.g. 翻译/* or 报销/*.pdf"
                    value={pathPattern}
                    onChange={(e) => setPathPattern(e.target.value)}
                    disabled={loading}
                  />
                  <p className="text-[11px] text-muted-foreground leading-snug">
                    Glob pattern relative to sync folder root. Use * for any file, ** for recursive.
                  </p>
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Trigger On</label>
                  <Select value={fileActions} onValueChange={(v) => setFileActions(v || "added,modified")}>
                    <SelectTrigger className="h-9">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="added,modified">New & Modified Files</SelectItem>
                      <SelectItem value="added">New Files Only</SelectItem>
                      <SelectItem value="modified">Modified Files Only</SelectItem>
                      <SelectItem value="added,modified,deleted">All Changes</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </>
            )}

            <div className="flex flex-col gap-1.5 mt-2">
              <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Agent Model</label>
              <Select value={model || "default"} onValueChange={(v) => setModel(v || "default")}>
                <SelectTrigger className="h-9">
                  <SelectValue placeholder="Global Default" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">Global Default</SelectItem>
                  {/* Ensure the current model always appears as an option even
                      before the async /api/models fetch completes. Without this,
                      @base-ui Select crashes (white screen) when value doesn't
                      match any SelectItem. */}
                  {model && model !== "default" && !availableModels.includes(model) && (
                    <SelectItem value={model}>{model.split('/').pop()}</SelectItem>
                  )}
                  {availableModels.map(m => (
                    <SelectItem key={m} value={m}>{m.split('/').pop()}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Tool Access</label>
              <Select value={toolAccess} onValueChange={(v) => setToolAccess(v || "full")}>
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="full">Full Access (All Tools)</SelectItem>
                  <SelectItem value="read_only">Read-Only Tools</SelectItem>
                  <SelectItem value="none">No Tools (Chat Only)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Right Column: Prompt Engine */}
          <div className="flex flex-col gap-3 flex-1 h-full min-h-[350px]">
            <div className="flex flex-col gap-0.5">
              <label className="text-sm font-semibold text-foreground">Agent Prompt Instructions</label>
              <p className="text-[12px] text-muted-foreground">
                {type === "file_drop"
                  ? "Define what the AI agent should do when a matching file appears. The agent receives the file path in abs_path."
                  : "Define exactly what the AI agent should execute autonomously when this routine triggers. You can use Markdown."}
              </p>
            </div>
            <Textarea
              className="resize-none flex-1 min-h-[250px] p-4 font-mono text-sm leading-relaxed focus-visible:ring-blue-500/30"
              placeholder={
                type === "file_drop"
                  ? "e.g. Read the file at abs_path.\nTranslate its contents to Chinese.\nSave the translation alongside the original file with _zh suffix."
                  : "e.g. 1. Read the latest emails.\n2. Generate a daily summary.\n3. Save it to the workspace docs folder."
              }
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={loading}
            />
          </div>
        </div>

        {/* Footer Area */}
        <div className="px-6 py-4 bg-muted/30 border-t flex items-center justify-between shrink-0">
          <div className="text-[13px] text-red-500 font-medium">
            {errorProp && <span>{errorProp}</span>}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={loading}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={loading} className="min-w-[100px]">
              {loading && <Loader2 className="mr-2 size-4 animate-spin" />}
              Save Routine
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
