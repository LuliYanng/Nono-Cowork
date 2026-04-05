import { useState, useEffect } from "react";
import {
  X,
  Server,
  Key,
  Loader2,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
} from "lucide-react";
import type { SyncState } from "@/hooks/use-sync-status";

// ── Types ──

interface SettingsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  apiBase: string;
  apiToken: string;
  syncState: SyncState;
}

type TestStatus = "idle" | "testing" | "success" | "error";

// ── Electron API type guard ──

const electron = (window as any).electronAPI as {
  getAppConfig: () => Promise<Record<string, string>>;
  saveAppConfig: (config: Record<string, string>) => Promise<{ success: boolean }>;
  reloadWindow: () => Promise<{ success: boolean }>;
} | undefined;

// ── Component ──

export function SettingsDialog({
  isOpen,
  onClose,
  apiBase,
  apiToken,
  syncState,
}: SettingsDialogProps) {
  const [serverUrl, setServerUrl] = useState(apiBase);
  const [token, setToken] = useState(apiToken);
  const [testStatus, setTestStatus] = useState<TestStatus>("idle");
  const [testMessage, setTestMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);

  // Load saved config on mount
  useEffect(() => {
    if (isOpen && electron) {
      electron.getAppConfig().then((config) => {
        if (config?.apiBase) setServerUrl(config.apiBase);
        if (config?.apiToken) setToken(config.apiToken);
      });
    }
  }, [isOpen]);

  // Track changes
  useEffect(() => {
    setHasChanges(serverUrl !== apiBase || token !== apiToken);
  }, [serverUrl, token, apiBase, apiToken]);

  if (!isOpen) return null;

  // ── Test connection ──
  const testConnection = async () => {
    setTestStatus("testing");
    setTestMessage("");

    const base = serverUrl.replace(/\/+$/, "");
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;

    try {
      const res = await fetch(`${base}/api/health`, { headers, signal: AbortSignal.timeout(8000) });
      if (!res.ok) {
        setTestStatus("error");
        setTestMessage(res.status === 401 ? "Invalid token — authentication failed" : `Server error: ${res.status}`);
        return;
      }
      const data = await res.json();
      setTestStatus("success");
      setTestMessage(`Connected — ${data.model || "server online"}`);
    } catch (err: any) {
      setTestStatus("error");
      setTestMessage(
        err?.name === "TimeoutError"
          ? "Connection timed out — check the server address"
          : "Cannot reach server — check the URL"
      );
    }
  };

  // ── Save and apply ──
  const saveAndApply = async () => {
    setSaving(true);
    const config = {
      apiBase: serverUrl.replace(/\/+$/, ""),
      apiToken: token,
    };

    if (electron) {
      await electron.saveAppConfig(config);
      // Reload to apply new config
      await electron.reloadWindow();
    } else {
      // Fallback for browser dev: just reload the page
      window.location.reload();
    }
    setSaving(false);
  };

  // ── Sync status text ──
  const syncLabel: Record<SyncState, { text: string; color: string }> = {
    connected: { text: "File sync active", color: "text-emerald-500" },
    syncing: { text: "Syncing files...", color: "text-blue-400" },
    disconnected: { text: "File sync disconnected", color: "text-muted-foreground/50" },
    loading: { text: "Checking sync...", color: "text-muted-foreground/50" },
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Dialog */}
      <div className="relative w-full max-w-md mx-4 bg-background border border-border/50 rounded-xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border/30">
          <h2 className="text-[15px] font-semibold text-foreground">Settings</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground/60 hover:text-foreground transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-5">
          {/* Server URL */}
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-[13px] font-medium text-foreground/80">
              <Server size={14} className="text-muted-foreground/60" />
              Server Address
            </label>
            <input
              type="url"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              placeholder="http://your-vps-ip:8080"
              className="w-full px-3 py-2.5 bg-muted/40 border border-border/40 rounded-lg text-[13px] text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500/50 transition-all"
            />
          </div>

          {/* API Token */}
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-[13px] font-medium text-foreground/80">
              <Key size={14} className="text-muted-foreground/60" />
              Access Token
            </label>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Your API token"
              className="w-full px-3 py-2.5 bg-muted/40 border border-border/40 rounded-lg text-[13px] text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500/50 transition-all"
            />
          </div>

          {/* Test connection button + result */}
          <div className="space-y-2">
            <button
              onClick={testConnection}
              disabled={!serverUrl || testStatus === "testing"}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-medium bg-muted/60 hover:bg-muted text-foreground/70 hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed transition-all"
            >
              {testStatus === "testing" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              Test Connection
            </button>

            {testStatus === "success" && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-500/10 text-emerald-600 text-[12px]">
                <CheckCircle2 size={14} />
                {testMessage}
              </div>
            )}
            {testStatus === "error" && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 text-red-500 text-[12px]">
                <AlertCircle size={14} />
                {testMessage}
              </div>
            )}
          </div>

          {/* Sync status (read-only info) */}
          <div className="pt-2 border-t border-border/20">
            <div className={`flex items-center gap-2 text-[12px] ${syncLabel[syncState].color}`}>
              <RefreshCw size={12} />
              {syncLabel[syncState].text}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-border/30 bg-muted/20">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-[13px] text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={saveAndApply}
            disabled={!hasChanges || saving || !serverUrl}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-medium bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {saving && <Loader2 size={14} className="animate-spin" />}
            Save & Reconnect
          </button>
        </div>
      </div>
    </div>
  );
}
