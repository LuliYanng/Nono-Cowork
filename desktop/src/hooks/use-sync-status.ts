import { useState, useEffect, useRef, useCallback } from "react";

// ── Types ──

export interface SyncDevice {
  id: string;
  name: string;
  connected: boolean;
  address: string;
}

export interface SyncFolder {
  id: string;
  label: string;
  state: "idle" | "syncing" | "scanning" | "error" | "unknown";
  completion: number;
}

export interface SyncStatus {
  online: boolean;
  device_id: string;
  connections: {
    total: number;
    connected: number;
    devices: SyncDevice[];
  };
  folders: SyncFolder[];
}

export type SyncState = "connected" | "syncing" | "disconnected" | "loading";

const POLL_INTERVAL = 15_000; // 15 seconds

/**
 * Hook that polls /api/sync/status and returns a simplified sync state
 * for the sidebar indicator.
 */
export function useSyncStatus(apiBase: string, getHeaders: () => Record<string, string>) {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [state, setState] = useState<SyncState>("loading");

  // Use refs so the fetch callback never changes identity
  const apiBaseRef = useRef(apiBase);
  apiBaseRef.current = apiBase;
  const headersRef = useRef(getHeaders);
  headersRef.current = getHeaders;

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${apiBaseRef.current}/api/sync/status`, {
        headers: headersRef.current(),
      });
      if (!res.ok) {
        setState("disconnected");
        return;
      }
      const data: SyncStatus = await res.json();
      setStatus(data);

      // Derive simplified state
      if (!data.online) {
        setState("disconnected");
      } else if (data.folders.some((f) => f.state === "syncing" || f.state === "scanning")) {
        setState("syncing");
      } else if (data.connections.connected > 0) {
        setState("connected");
      } else {
        // Syncthing is online but no devices connected
        setState("disconnected");
      }
    } catch {
      setState("disconnected");
    }
  }, []); // stable — no deps, uses refs

  useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [fetchStatus]);

  return { status, state, refetch: fetchStatus };
}
