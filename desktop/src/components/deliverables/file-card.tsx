/**
 * FileCard — Single file deliverable component.
 *
 * Renders a file with action/status badge, path display, and interactive
 * buttons (Open File, Show in Folder). Supports two modes:
 *   - "full": used in notification cards — complete card with header + path + action bar
 *   - "compact": used in chat tool results — inline bar with filename + buttons
 */

import { FolderOpen, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { DeliverableComponentProps } from "./registry";
import type { FileMetadata } from "./types";
import { syncPaths, getFileIcon, getFileName, getFileExtension } from "./utils";

// ── Standalone compact mode props (for chat tool results) ──

interface FileCardStandaloneProps {
  path: string;
  action?: "created" | "modified" | "deleted";
  size?: string;
  mode?: "compact";
}

type FileCardProps = DeliverableComponentProps | FileCardStandaloneProps;

function isStandalone(props: FileCardProps): props is FileCardStandaloneProps {
  return "path" in props && !("deliverable" in props);
}

// ── Component ──

export function FileCard(props: FileCardProps) {
  // Normalize data from either props shape
  let path: string;
  let absPath: string | undefined;
  let action: "created" | "modified" | "deleted";
  let size: string | undefined;
  let mode: "full" | "compact";
  let isUnread = true;

  if (isStandalone(props)) {
    path = props.path;
    absPath = undefined;
    action = props.action || "created";
    size = props.size;
    mode = props.mode || "compact";
  } else {
    const meta = (props.deliverable.metadata || {}) as unknown as FileMetadata;
    path = meta.path || "";
    absPath = meta.abs_path;
    action = meta.action || "created";
    size = meta.size;
    mode = props.mode || "full";
    isUnread = props.isUnread;
  }

  const fileName = getFileName(path);
  const extension = getFileExtension(path);
  const FileIcon = getFileIcon(extension);

  const actionConfig = {
    created: { color: "text-emerald-600", label: "Created", bgColor: "bg-emerald-500/10" },
    modified: { color: "text-blue-500", label: "Modified", bgColor: "bg-blue-500/10" },
    deleted: { color: "text-red-400", label: "Deleted", bgColor: "bg-red-400/10" },
  }[action];

  const handleOpenFile = async () => {
    if (!window.electronAPI?.openFile) {
      toast.error("Only available in desktop app");
      return;
    }
    const localPath = syncPaths.resolve(absPath || path);
    const result = await window.electronAPI.openFile(localPath);
    if (!result.success) {
      toast.error(`Cannot open file: ${result.error}`);
    }
  };

  const handleShowInExplorer = async () => {
    if (!window.electronAPI?.showInExplorer) {
      toast.error("Only available in desktop app");
      return;
    }
    const localPath = syncPaths.resolve(absPath || path);
    const result = await window.electronAPI.showInExplorer(localPath);
    if (!result.success) {
      toast.error(`Cannot open folder: ${result.error}`);
    }
  };

  const hasElectron = !!window.electronAPI?.openFile;

  // ── Compact mode: inline bar for chat ──
  if (mode === "compact") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border/30 bg-muted/15 px-3 py-2 mt-2">
        <FileIcon size={14} className="shrink-0 text-muted-foreground/50" strokeWidth={1.8} />
        <span className="text-[13px] font-medium text-foreground/70 truncate min-w-0">
          {fileName}
        </span>
        <span className={`text-[10px] font-medium ${actionConfig.color} shrink-0`}>
          {actionConfig.label}
        </span>
        {size && (
          <span className="text-[11px] text-muted-foreground/30 shrink-0">
            {size}
          </span>
        )}
        {action !== "deleted" && hasElectron && (
          <>
            <div className="flex-1" />
            <button
              onClick={handleOpenFile}
              className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium text-muted-foreground/50 hover:text-foreground/70 hover:bg-muted/40 transition-colors"
            >
              <ExternalLink size={11} />
              Open
            </button>
            <button
              onClick={handleShowInExplorer}
              className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium text-muted-foreground/50 hover:text-foreground/70 hover:bg-muted/40 transition-colors"
            >
              <FolderOpen size={11} />
              Folder
            </button>
          </>
        )}
      </div>
    );
  }

  // ── Full mode: single row for notifications ──
  return (
    <TooltipProvider delay={300}>
      <div className="flex items-center gap-2 rounded-lg border border-border/30 bg-muted/8 px-3 py-2">
        <FileIcon
          size={14}
          strokeWidth={1.7}
          className={`shrink-0 ${isUnread ? "text-foreground/45" : "text-muted-foreground/35"}`}
        />
        <Tooltip>
          <TooltipTrigger
            className={`text-[13px] font-medium truncate min-w-0 cursor-default ${
              isUnread ? "text-foreground/75" : "text-foreground/50"
            }`}
          >
            {fileName}
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-[400px] font-mono text-[11px] break-all">
            {path}
          </TooltipContent>
        </Tooltip>
        <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full shrink-0 ${actionConfig.color} ${actionConfig.bgColor}`}>
          {actionConfig.label}
        </span>
        {size && (
          <span className="text-[11px] text-muted-foreground/25 shrink-0">{size}</span>
        )}
        {action !== "deleted" && hasElectron && (
          <>
            <div className="flex-1" />
            <Tooltip>
              <TooltipTrigger
                onClick={handleOpenFile}
                className="p-1.5 rounded-md text-muted-foreground/30 hover:text-foreground/60 hover:bg-muted/30 transition-colors"
              >
                <ExternalLink size={13} />
              </TooltipTrigger>
              <TooltipContent side="bottom" className="text-xs">Open File</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger
                onClick={handleShowInExplorer}
                className="p-1.5 rounded-md text-muted-foreground/30 hover:text-foreground/60 hover:bg-muted/30 transition-colors"
              >
                <FolderOpen size={13} />
              </TooltipTrigger>
              <TooltipContent side="bottom" className="text-xs">Show in Folder</TooltipContent>
            </Tooltip>
          </>
        )}
      </div>
    </TooltipProvider>
  );
}
