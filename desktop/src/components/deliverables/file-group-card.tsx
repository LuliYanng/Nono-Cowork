/**
 * FileGroupCard — Batch file changes deliverable component.
 *
 * Renders a collapsible list of files when the agent processes
 * multiple files at once (e.g., "organized 5 files into folders").
 * Each file row has its own open/folder buttons.
 */

import { useState } from "react";
import { ChevronDown, FolderSync, ExternalLink, FolderOpen } from "lucide-react";
import { toast } from "sonner";
import type { DeliverableComponentProps } from "./registry";
import type { FileGroupMetadata } from "./types";
import { syncPaths, getFileIcon, getFileName, getFileExtension } from "./utils";

// ── Component ──

export function FileGroupCard({
  deliverable,
  isUnread,
  mode = "full",
}: DeliverableComponentProps) {
  const [expanded, setExpanded] = useState(false);
  const meta = (deliverable.metadata || {}) as unknown as FileGroupMetadata;
  const files = meta.files || [];

  const hasElectron = !!window.electronAPI?.openFile;

  const actionCounts = {
    created: files.filter((f) => f.action === "created").length,
    modified: files.filter((f) => f.action === "modified").length,
    deleted: files.filter((f) => f.action === "deleted").length,
  };

  // ── Compact mode ──
  if (mode === "compact") {
    return (
      <div className="rounded-lg border border-border/30 bg-muted/15 mt-2 overflow-hidden">
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-muted/20 transition-colors"
        >
          <FolderSync size={13} className="text-muted-foreground/40 shrink-0" strokeWidth={1.8} />
          <span className="text-[13px] font-medium text-foreground/70 truncate">
            {deliverable.label}
          </span>
          <span className="text-[11px] text-muted-foreground/30 shrink-0">
            {files.length} files
          </span>
          <div className="flex-1" />
          <ChevronDown
            size={13}
            className={`text-muted-foreground/30 transition-transform duration-200 ${
              expanded ? "rotate-180" : ""
            }`}
          />
        </button>
        {expanded && (
          <div className="px-3 pb-2 flex flex-col gap-0.5 border-t border-border/20">
            {files.map((f, i) => (
              <FileRow key={i} file={f} compact hasElectron={hasElectron} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── Full mode ──
  return (
    <div
      className={`rounded-xl border overflow-hidden transition-all duration-200 ${
        isUnread
          ? "bg-background border-border/60 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.05)]"
          : "bg-background/50 border-border/30"
      }`}
    >
      {/* Header — clickable to expand */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2.5 px-4 py-3 text-left hover:bg-muted/10 transition-colors"
      >
        <FolderSync
          size={15}
          strokeWidth={1.6}
          className={`shrink-0 ${isUnread ? "text-foreground/50" : "text-muted-foreground/40"}`}
        />
        <span
          className={`text-[13px] font-medium truncate ${
            isUnread ? "text-foreground/85" : "text-foreground/55"
          }`}
        >
          {deliverable.label}
        </span>

        {/* Action count badges */}
        <div className="flex items-center gap-1.5 shrink-0">
          {actionCounts.created > 0 && (
            <span className="text-[10px] font-medium text-emerald-500 bg-emerald-500/10 px-1.5 py-0.5 rounded-full">
              +{actionCounts.created}
            </span>
          )}
          {actionCounts.modified > 0 && (
            <span className="text-[10px] font-medium text-blue-500 bg-blue-500/10 px-1.5 py-0.5 rounded-full">
              ~{actionCounts.modified}
            </span>
          )}
          {actionCounts.deleted > 0 && (
            <span className="text-[10px] font-medium text-red-400 bg-red-400/10 px-1.5 py-0.5 rounded-full">
              -{actionCounts.deleted}
            </span>
          )}
        </div>

        <div className="flex-1" />
        <span className="text-[11px] text-muted-foreground/25 shrink-0">
          {files.length} files
        </span>
        <ChevronDown
          size={14}
          className={`text-muted-foreground/30 transition-transform duration-200 ${
            expanded ? "rotate-180" : ""
          }`}
        />
      </button>

      {/* Expandable file list */}
      {expanded && (
        <div className="px-4 pb-3 flex flex-col gap-0.5 border-t border-border/20">
          {files.map((f, i) => (
            <FileRow key={i} file={f} hasElectron={hasElectron} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── File Row Sub-component ──

function FileRow({
  file,
  compact,
  hasElectron,
}: {
  file: { path: string; action: string; size?: string };
  compact?: boolean;
  hasElectron: boolean;
}) {
  const fileName = getFileName(file.path);
  const extension = getFileExtension(file.path);
  const Icon = getFileIcon(extension);

  const actionColor = {
    created: "text-emerald-500",
    modified: "text-blue-500",
    deleted: "text-red-400",
  }[file.action] || "text-muted-foreground/40";

  const actionSymbol = {
    created: "+",
    modified: "~",
    deleted: "-",
  }[file.action] || "•";

  const handleOpen = async () => {
    if (!window.electronAPI?.openFile) return;
    const localPath = syncPaths.resolve(file.path);
    const result = await window.electronAPI.openFile(localPath);
    if (!result.success) toast.error(`Cannot open: ${result.error}`);
  };

  const handleFolder = async () => {
    if (!window.electronAPI?.showInExplorer) return;
    const localPath = syncPaths.resolve(file.path);
    const result = await window.electronAPI.showInExplorer(localPath);
    if (!result.success) toast.error(`Cannot open folder: ${result.error}`);
  };

  return (
    <div className="group flex items-center gap-2 py-1.5 px-1 rounded-md hover:bg-muted/15 transition-colors">
      <span className={`text-[11px] font-mono font-bold w-3 text-center shrink-0 ${actionColor}`}>
        {actionSymbol}
      </span>
      <Icon size={compact ? 12 : 13} className="shrink-0 text-muted-foreground/35" strokeWidth={1.6} />
      <span className="text-[12px] text-foreground/60 truncate min-w-0">{fileName}</span>
      {file.size && (
        <span className="text-[10px] text-muted-foreground/25 shrink-0">{file.size}</span>
      )}
      {file.action !== "deleted" && hasElectron && (
        <>
          <div className="flex-1" />
          <button
            onClick={handleOpen}
            className="opacity-0 group-hover:opacity-100 p-1 rounded text-muted-foreground/30 hover:text-foreground/60 transition-all"
            title="Open file"
          >
            <ExternalLink size={11} />
          </button>
          <button
            onClick={handleFolder}
            className="opacity-0 group-hover:opacity-100 p-1 rounded text-muted-foreground/30 hover:text-foreground/60 transition-all"
            title="Show in folder"
          >
            <FolderOpen size={11} />
          </button>
        </>
      )}
    </div>
  );
}
