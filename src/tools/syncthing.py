"""Syncthing REST API lightweight client + Agent tool functions"""

import os
import json
import time
import requests
from tools.registry import tool


class SyncthingClient:
    """Lightweight Syncthing REST API client, wrapping only the functionality needed by Agent."""

    def __init__(self, url=None, api_key=None):
        self.url = (url or os.getenv("SYNCTHING_URL", "http://localhost:8384")).rstrip("/")
        self.api_key = api_key or os.getenv("SYNCTHING_API_KEY", "")
        self.headers = {"X-API-Key": self.api_key} if self.api_key else {}
        self._folder_cache = None  # Cached folder list

    def resolve_folder_id(self, file_path=None):
        """Auto-resolve folder ID.

        Resolution order:
        1. Match by file_path (find folder whose path contains the file)
        2. Match by WORKSPACE_DIR env var
        3. Fall back to the first folder

        Works with single or multiple synced folders.
        """
        if self._folder_cache is None:
            self._folder_cache = self.get_folders()

        if not self._folder_cache:
            raise ValueError("No synced folders configured in Syncthing")

        # Try to match by file path
        if file_path:
            abs_path = os.path.abspath(file_path)
            for f in self._folder_cache:
                if abs_path.startswith(os.path.abspath(f["path"])):
                    return f["id"]

        # Try to match by workspace env var
        workspace = os.getenv("WORKSPACE_DIR", "").strip()
        if workspace:
            workspace = os.path.abspath(os.path.expanduser(workspace))
            for f in self._folder_cache:
                if os.path.abspath(f["path"]) == workspace:
                    return f["id"]

        # Fall back to first folder
        return self._folder_cache[0]["id"]

    def _get(self, path, **params):
        r = requests.get(f"{self.url}{path}", headers=self.headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path, **params):
        r = requests.post(f"{self.url}{path}", headers=self.headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json() if r.content else {}

    def _post_json(self, path, data, **params):
        """POST with a JSON body."""
        r = requests.post(
            f"{self.url}{path}", headers={**self.headers, "Content-Type": "application/json"},
            params=params, data=json.dumps(data), timeout=10,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    def _patch(self, path, data):
        """PATCH with a JSON body (for config updates)."""
        r = requests.patch(
            f"{self.url}{path}", headers={**self.headers, "Content-Type": "application/json"},
            data=json.dumps(data), timeout=10,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    def get_system_status(self):
        return self._get("/rest/system/status")

    def get_connections(self):
        return self._get("/rest/system/connections")

    def get_folders(self):
        return self._get("/rest/config/folders")

    def get_folder_status(self, folder_id):
        return self._get("/rest/db/status", folder=folder_id)

    def scan(self, folder_id, sub_path=None):
        params = {"folder": folder_id}
        if sub_path:
            params["sub"] = sub_path
        return self._post("/rest/db/scan", **params)

    def is_idle(self, folder_id):
        status = self.get_folder_status(folder_id)
        return status.get("state") == "idle"

    def wait_for_sync(self, folder_id, timeout=30):
        start = time.time()
        while time.time() - start < timeout:
            if self.is_idle(folder_id):
                return True
            time.sleep(1)
        return False

    # ——— Versioning ———

    def get_versions(self, folder_id):
        """List archived file versions that can be restored."""
        return self._get("/rest/folder/versions", folder=folder_id)

    def restore_versions(self, folder_id, file_version_map: dict):
        """Restore files to archived versions.

        Args:
            folder_id: Folder ID
            file_version_map: {"path/to/file": "2024-01-01T12:00:00+08:00", ...}
        """
        return self._post_json("/rest/folder/versions", file_version_map, folder=folder_id)

    # ——— Pause / Resume ———

    def pause_folder(self, folder_id):
        """Pause syncing for a folder."""
        return self._patch(f"/rest/config/folders/{folder_id}", {"paused": True})

    def resume_folder(self, folder_id):
        """Resume syncing for a folder."""
        return self._patch(f"/rest/config/folders/{folder_id}", {"paused": False})

    # ——— Ignore list management ———

    def get_ignores(self, folder_id):
        """Get the current .stignore patterns for a folder."""
        return self._get("/rest/db/ignores", folder=folder_id)

    def set_ignores(self, folder_id, ignore_lines: list[str]):
        """Set .stignore patterns for a folder via API."""
        r = requests.post(
            f"{self.url}/rest/db/ignores",
            headers={**self.headers, "Content-Type": "application/json"},
            params={"folder": folder_id},
            data=json.dumps({"ignore": ignore_lines}),
            timeout=10,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    def add_ignore_pattern(self, folder_id, pattern: str):
        """Add a pattern to .stignore if not already present."""
        current = self.get_ignores(folder_id)
        lines = current.get("ignore", []) or []
        if pattern not in lines:
            lines.append(pattern)
            self.set_ignores(folder_id, lines)
            return True
        return False

    # ——— Error checking ———

    def get_folder_errors(self, folder_id):
        """Get sync errors for a folder."""
        return self._get("/rest/folder/errors", folder=folder_id)

    # ——— Auto-setup ———

    def ensure_versioning(self, max_age_days: int = 180):
        """Auto-enable Staggered File Versioning on all folders that don't have it.

        This is idempotent — if versioning is already configured, it skips that folder.
        Called automatically on first client init so the user never needs to configure it manually.
        """
        try:
            folders = self.get_folders()
            for f in folders:
                fid = f["id"]
                current = f.get("versioning", {}).get("type", "")
                if current:
                    continue  # Already has versioning, don't touch it

                self._patch(f"/rest/config/folders/{fid}", {
                    "versioning": {
                        "type": "staggered",
                        "params": {
                            "maxAge": str(max_age_days * 86400),  # Convert days to seconds
                            "cleanInterval": "3600",
                        },
                    }
                })
                print(f"  📦 Auto-enabled file versioning for folder '{f.get('label', fid)}'")
        except Exception:
            pass  # Non-critical — don't break agent startup if Syncthing is unreachable


# ————— Singleton client —————
_client = None
_initialized = False

def _get_client():
    global _client, _initialized
    if _client is None:
        _client = SyncthingClient()
    if not _initialized:
        _initialized = True
        _client.ensure_versioning()
    return _client


# ————— Agent tool functions —————

@tool(
    name="sync_status",
    tags=["read"],
    description="Check Syncthing synchronization status. Displays all synced folder paths, their sync status, and whether the user's device is online. Use as a diagnostic tool when sync issues arise.",
    parameters={
        "type": "object",
        "properties": {},
    },
)
def sync_status() -> str:
    """Check Syncthing sync status.

    Displays synced folder list, paths, sync state, and whether the user's device is online.
    """
    try:
        st = _get_client()

        # Connection status
        conns = st.get_connections().get("connections", {})
        online_devices = []
        for dev_id, info in conns.items():
            if info.get("connected"):
                name = info.get("clientVersion", "unknown")
                online_devices.append(f"  🟢 {dev_id[:12]}... ({name})")
            else:
                online_devices.append(f"  🔴 {dev_id[:12]}... (offline)")

        # Folder list
        folders = st.get_folders()
        folder_lines = []
        for f in folders:
            fid = f["id"]
            try:
                status = st.get_folder_status(fid)
                state = status.get("state", "unknown")
                local_files = status.get("localFiles", 0)
                global_files = status.get("globalFiles", 0)
                paused = f.get("paused", False)
                versioning = f.get("versioning", {}).get("type", "none")
                pause_tag = " ⏸️ PAUSED" if paused else ""
                folder_lines.append(
                    f"  📁 {f.get('label', fid)} (ID: {fid}){pause_tag}\n"
                    f"     Path: {f['path']}\n"
                    f"     State: {state} | Files: {local_files}/{global_files}\n"
                    f"     Versioning: {versioning}"
                )
            except Exception:
                folder_lines.append(f"  📁 {f.get('label', fid)} (ID: {fid}) - failed to get status")

        result = "📡 Syncthing Sync Status\n\n"
        result += "Device connections:\n" + ("\n".join(online_devices) if online_devices else "  (no remote devices)") + "\n\n"
        result += "Synced folders:\n" + "\n".join(folder_lines)
        return result

    except requests.ConnectionError:
        return "❌ Cannot connect to Syncthing (http://localhost:8384). Is the Syncthing service running?"
    except Exception as e:
        return f"❌ Failed to get sync status: {e}"


@tool(
    name="sync_wait",
    tags=["read"],
    description="Wait for file synchronization to complete. Call this after modifying files in the workspace to ensure changes have been synced to the user's local machine. Folder ID is auto-detected — no need to pass it.",
    parameters={
        "type": "object",
        "properties": {
            "timeout": {
                "type": "integer",
                "description": "Maximum seconds to wait. Default is 30.",
                "default": 30,
            },
        },
    },
)
def sync_wait(timeout: int = 30) -> str:
    """Wait for synced folder to finish syncing.

    Folder ID is auto-detected from workspace config.
    """
    try:
        st = _get_client()
        folder_id = st.resolve_folder_id()

        # Trigger a scan first to speed up change detection
        try:
            st.scan(folder_id)
        except Exception:
            pass

        if st.wait_for_sync(folder_id, timeout):
            status = st.get_folder_status(folder_id)
            return (
                f"✅ Sync complete\n"
                f"  Local files: {status.get('localFiles', '?')}\n"
                f"  Global files: {status.get('globalFiles', '?')}"
            )
        else:
            return f"⏳ Still syncing after {timeout}s. May have large changes, check again later."

    except requests.ConnectionError:
        return "❌ Cannot connect to Syncthing. Is the service running?"
    except Exception as e:
        return f"❌ Failed to wait for sync: {e}"


@tool(
    name="sync_versions",
    tags=["read"],
    description="List recoverable file versions in the synced folder. Syncthing keeps old versions when files are modified or deleted. Use this to find files that can be restored.",
    parameters={
        "type": "object",
        "properties": {},
    },
)
def sync_versions() -> str:
    """List archived file versions that can be restored."""
    try:
        st = _get_client()
        folder_id = st.resolve_folder_id()
        versions = st.get_versions(folder_id)

        if not versions:
            return (
                "📂 No archived versions found.\n\n"
                "This could mean:\n"
                "  - File versioning is not enabled\n"
                "  - No files have been modified/deleted by remote devices yet"
            )

        result = "📂 Archived versions:\n\n"
        for filepath, version_list in versions.items():
            result += f"  📄 {filepath}\n"
            for v in version_list:
                vtime = v.get("versionTime", "?")
                mod_time = v.get("modTime", "?")
                size = v.get("size", 0)
                size_str = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
                result += f"     ⏱️ {vtime} (modified: {mod_time}, size: {size_str})\n"

        result += (
            "\nTo restore a file, use sync_restore("
            "file_path=\"<path>\", version_time=\"<timestamp>\")"
        )
        return result

    except requests.ConnectionError:
        return "❌ Cannot connect to Syncthing. Is the service running?"
    except Exception as e:
        return f"❌ Failed to list versions: {e}"


@tool(
    name="sync_restore",
    tags=["write"],
    description="Restore a file to a previous version. Use sync_versions() first to see available versions and their timestamps.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative path of the file to restore (as shown in sync_versions output).",
            },
            "version_time": {
                "type": "string",
                "description": "Timestamp of the version to restore (e.g. '2024-01-15T10:30:00+08:00').",
            },
        },
        "required": ["file_path", "version_time"],
    },
)
def sync_restore(file_path: str, version_time: str) -> str:
    """Restore a file to a previous archived version."""
    try:
        st = _get_client()
        folder_id = st.resolve_folder_id()
        result = st.restore_versions(folder_id, {file_path: version_time})

        # The API returns errors as {"path": "error message"}, empty = success
        if not result:
            return f"✅ Restored '{file_path}' to version from {version_time}"

        errors = [f"  {path}: {err}" for path, err in result.items() if err]
        if errors:
            return "❌ Restore failed:\n" + "\n".join(errors)

        return f"✅ Restored '{file_path}' to version from {version_time}"

    except requests.ConnectionError:
        return "❌ Cannot connect to Syncthing. Is the service running?"
    except Exception as e:
        return f"❌ Failed to restore: {e}"


@tool(
    name="sync_pause",
    tags=["write"],
    description="Pause syncing. Use BEFORE batch file operations (renaming many files, large refactors) to prevent the user from seeing half-finished changes. Always call sync_resume() after.",
    parameters={
        "type": "object",
        "properties": {},
    },
)
def sync_pause() -> str:
    """Pause syncing before batch operations."""
    try:
        st = _get_client()
        folder_id = st.resolve_folder_id()
        st.pause_folder(folder_id)
        return (
            "⏸️ Sync paused.\n"
            "⚠️ Remember to call sync_resume() when done!"
        )
    except requests.ConnectionError:
        return "❌ Cannot connect to Syncthing. Is the service running?"
    except Exception as e:
        return f"❌ Failed to pause sync: {e}"


@tool(
    name="sync_resume",
    tags=["write"],
    description="Resume syncing after batch file operations are complete so changes can sync to the user's machine.",
    parameters={
        "type": "object",
        "properties": {},
    },
)
def sync_resume() -> str:
    """Resume syncing for a paused folder."""
    try:
        st = _get_client()
        folder_id = st.resolve_folder_id()

        # Trigger scan to pick up all changes, then resume
        try:
            st.scan(folder_id)
        except Exception:
            pass

        st.resume_folder(folder_id)
        return "▶️ Sync resumed. Changes will now sync to the user."
    except requests.ConnectionError:
        return "❌ Cannot connect to Syncthing. Is the service running?"
    except Exception as e:
        return f"❌ Failed to resume sync: {e}"


@tool(
    name="sync_ignore_add",
    tags=["write"],
    description="Add a pattern to .stignore so it won't sync to the user's machine. Use this BEFORE creating temporary files or directories in the sync folder that the user doesn't need (e.g., venvs, build outputs, temp data). Pattern uses Syncthing ignore syntax (e.g., '(?d)**/my_temp_dir').",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The ignore pattern to add (Syncthing .stignore syntax). Prefix with (?d) to also delete remote copies.",
            },
        },
        "required": ["pattern"],
    },
)
def sync_ignore_add(pattern: str) -> str:
    """Add a pattern to .stignore dynamically."""
    try:
        st = _get_client()
        folder_id = st.resolve_folder_id()
        added = st.add_ignore_pattern(folder_id, pattern)
        if added:
            return f"✅ Added ignore pattern: {pattern}"
        else:
            return f"ℹ️ Pattern already exists: {pattern}"
    except requests.ConnectionError:
        return "❌ Cannot connect to Syncthing. Is the service running?"
    except Exception as e:
        return f"❌ Failed to add ignore pattern: {e}"
