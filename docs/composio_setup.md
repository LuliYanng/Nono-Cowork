English | [简体中文](composio_setup_zh-CN.md)

# Composio App Integrations Setup Guide

[Composio](https://composio.dev) connects the agent to 1,000+ external apps (Gmail, GitHub, Slack, Notion, etc.) through managed OAuth. It provides two key capabilities:

- **On-demand tools** — The agent can search, read, and write data across connected apps (e.g., send an email, create a GitHub issue).
- **Event triggers** — The agent can subscribe to real-time events (e.g., "new email from @partner.com") and process them automatically.

> 💡 **Composio is optional.** The agent works without it — you can use file sync, web search, shell commands, and all built-in tools without Composio. Add it when you want app integrations.

## 1. Get Your API Key (~2 minutes)

1. Go to [app.composio.dev](https://app.composio.dev/) and sign up / log in
2. Navigate to **Settings** → **API Keys**
3. Create a new API key and copy it
4. Add to `.env` on your VPS:
   ```
   COMPOSIO_API_KEY=your_api_key_here
   ```

## 2. Configure Webhook (Required for Event Triggers)

If you want to use **event triggers** (e.g., "when a new email arrives, process it"), you need to expose a webhook endpoint so Composio can send events to your VPS.

> 💡 If you only need on-demand tools (manually asking the agent to check email, create issues, etc.), you can skip this step. The trigger listener uses a WebSocket connection, but the initial setup may require webhook accessibility.

Add to `.env`:
```
# Your VPS public IP or domain
SERVER_HOST=your-vps-ip-or-domain

# Webhook port (default: 9090)
WEBHOOK_PORT=9090
```

Make sure the webhook port is accessible from the internet:
```bash
# UFW
sudo ufw allow 9090/tcp

# Or iptables
sudo iptables -A INPUT -p tcp --dport 9090 -j ACCEPT
```

## 3. Connect Apps (OAuth)

App connections happen **through the agent** at runtime — you don't need to configure OAuth credentials manually.

When you ask the agent to do something that requires an app (e.g., "check my Gmail"), it will:

1. Detect that the app isn't connected yet
2. Generate an **OAuth authorization link**
3. Send the link to you (via Desktop, Telegram, Feishu, or terminal)
4. **Wait** for you to complete the authorization in your browser
5. Confirm the connection is active and proceed with the task

Example conversation:
```
You:    Check my Gmail for new emails from @partner.com
Agent:  Gmail is not connected yet. Please authorize access:
        🔗 https://app.composio.dev/auth/gmail/...
        (Waiting for authorization...)
        ✅ Gmail is now connected! Checking for emails...
```

> Once an app is connected, it stays connected. You don't need to re-authorize unless the token expires.

## 4. Verify

After setting `COMPOSIO_API_KEY` in `.env`, restart the agent and check the logs:

```bash
# If running via systemd
sudo journalctl -u nono-cowork -f | grep -i composio

# Expected output:
# Composio initialized: X meta tools loaded for user 'default'
# Composio trigger listener started
```

You can also test in the terminal:
```bash
uv run agent
# Then type: "search composio tools for gmail"
```

## 5. Event Triggers (Advanced)

Once apps are connected, you can create event-driven automations:

```
You:    When I receive a new email from @partner.com, download any
        attachments and save them to my sync folder.

Agent:  I'll set up a Gmail trigger for that. Creating trigger
        GMAIL_NEW_GMAIL_MESSAGE...
        ✅ Trigger created! I'll automatically process matching
        emails and save attachments to your sync folder.
```

The agent will:
1. Subscribe to the relevant Composio trigger
2. Receive events via WebSocket in real-time
3. Process each event with a disposable agent session
4. Deliver results as notification cards

### Managing Triggers

Ask the agent to manage your triggers:
- `"List my active triggers"` — See all running triggers
- `"Delete the Gmail trigger"` — Remove a specific trigger

## Configuration Reference

| Variable | Required | Default | Description |
|:---|:---|:---|:---|
| `COMPOSIO_API_KEY` | Yes | — | Your Composio API key |
| `COMPOSIO_USER_ID` | No | `default` | User ID for Composio sessions |
| `COMPOSIO_AUTH_WAIT_TIMEOUT` | No | `300` | Seconds to wait for OAuth completion |
| `SERVER_HOST` | For triggers | — | Your VPS public IP or domain |
| `WEBHOOK_PORT` | For triggers | `9090` | Webhook HTTP port |

## Troubleshooting

### Q: "Composio not initialized" in logs
A: Check that `COMPOSIO_API_KEY` is set correctly in `.env` and restart the agent.

### Q: OAuth link doesn't work
A: Make sure you're logged into the correct Composio account at [app.composio.dev](https://app.composio.dev/). The link is tied to your `COMPOSIO_USER_ID`.

### Q: Triggers not firing
A: Check the following:
1. Is `COMPOSIO_API_KEY` set?
2. Is the app connected? (Ask the agent: "check my Gmail connection status")
3. Check logs for WebSocket connection: `journalctl -u nono-cowork | grep "trigger"`
4. For webhook-dependent triggers: is `SERVER_HOST` set and port `WEBHOOK_PORT` accessible?
