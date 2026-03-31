"""
Channel operation tools — send files and media to user via IM.
"""

import os
from tools.registry import tool


@tool(
    name="send_file",
    tags=["write"],
    description="Send a file to the user via their IM channel (Feishu/Telegram). Use this when the user asks you to send, share, or deliver a file to them. The file will be sent as an attachment in the chat. Images are automatically displayed inline.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to send.",
            },
            "caption": {
                "type": "string",
                "description": "Optional caption or description to accompany the file.",
                "default": "",
            },
        },
        "required": ["path"],
    },
)
def send_file(path: str, caption: str = "") -> str:
    """Send a file to the user via their current IM channel."""
    from context import get_context
    from channels.registry import get_channel

    # Validate file exists
    if not os.path.exists(path):
        return f"❌ File not found: {path}"
    if os.path.isdir(path):
        return f"❌ Path is a directory, not a file: {path}"

    # Get current execution context
    ctx = get_context()
    if not ctx:
        return "❌ Cannot send file: no channel context (this tool only works during IM conversations)."

    channel_name = ctx["channel_name"]
    user_id = ctx["user_id"]

    # Get channel instance
    channel = get_channel(channel_name)
    if not channel:
        return f"❌ Channel '{channel_name}' not available."

    # Send
    file_name = os.path.basename(path)
    size = os.path.getsize(path)
    size_str = f"{size / 1024 / 1024:.1f}MB" if size >= 1024 * 1024 else f"{size / 1024:.1f}KB"

    success = channel.send_file(user_id, path, caption=caption)
    if success:
        return f"✅ File sent: {file_name} ({size_str})"
    else:
        return f"❌ Failed to send file. The file is available at: {path}"
