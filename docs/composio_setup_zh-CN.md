[English](composio_setup.md) | 简体中文

# Composio 应用集成配置指南

[Composio](https://composio.dev) 将 Agent 连接到 1,000+ 外部应用（Gmail、GitHub、Slack、Notion 等），通过托管的 OAuth 认证。它提供两项关键能力：

- **按需工具调用** — Agent 可以搜索、读取和写入已连接应用的数据（如发送邮件、创建 GitHub Issue）。
- **事件触发器** — Agent 可以订阅实时事件（如"收到 @partner.com 的新邮件"）并自动处理。

> 💡 **Composio 是可选的。** Agent 不依赖它也能正常运行——文件同步、网络搜索、Shell 命令等内置工具均可独立使用。当你需要应用集成时再接入即可。

## 1. 获取 API Key（约 2 分钟）

1. 访问 [app.composio.dev](https://app.composio.dev/) 注册 / 登录
2. 进入 **Settings** → **API Keys**
3. 创建新的 API Key 并复制
4. 添加到 VPS 上的 `.env`：
   ```
   COMPOSIO_API_KEY=your_api_key_here
   ```

## 2. 配置 Webhook（事件触发器需要）

如果你想使用**事件触发器**（如"收到新邮件时自动处理"），需要暴露一个 webhook 端点，让 Composio 能将事件发送到你的 VPS。

> 💡 如果你只需要按需工具（手动让 Agent 查邮件、创建 Issue 等），可以跳过这一步。触发器监听器使用 WebSocket 连接，但初始配置可能需要 webhook 可访问。

添加到 `.env`：
```
# 你的 VPS 公网 IP 或域名
SERVER_HOST=your-vps-ip-or-domain

# Webhook 端口（默认 9090）
WEBHOOK_PORT=9090
```

确保 webhook 端口对外可访问：
```bash
# UFW
sudo ufw allow 9090/tcp

# 或 iptables
sudo iptables -A INPUT -p tcp --dport 9090 -j ACCEPT
```

## 3. 连接应用（OAuth 授权）

应用连接在**运行时通过 Agent** 完成——你不需要手动配置 OAuth 凭据。

当你让 Agent 做需要某个应用的事情（比如"查看我的 Gmail"），它会：

1. 检测到该应用尚未连接
2. 生成一个 **OAuth 授权链接**
3. 通过桌面端、Telegram、飞书或终端发送给你
4. **等待** 你在浏览器中完成授权
5. 确认连接成功后继续执行任务

示例对话：
```
你：    帮我看看 Gmail 里有没有 @partner.com 的新邮件
Agent： Gmail 尚未连接。请点击链接授权：
        🔗 https://app.composio.dev/auth/gmail/...
        （等待授权中...）
        ✅ Gmail 已连接！正在查看邮件...
```

> 应用一旦连接就会保持有效。除非 token 过期，否则不需要重新授权。

## 4. 验证

在 `.env` 中设置好 `COMPOSIO_API_KEY` 后，重启 Agent 并检查日志：

```bash
# 如果通过 systemd 运行
sudo journalctl -u nono-cowork -f | grep -i composio

# 预期输出：
# Composio initialized: X meta tools loaded for user 'default'
# Composio trigger listener started
```

也可以在终端测试：
```bash
uv run agent
# 然后输入："search composio tools for gmail"
```

## 5. 事件触发器（进阶）

应用连接后，你可以创建事件驱动的自动化：

```
你：    当我收到 @partner.com 的新邮件时，下载所有附件
        并保存到我的同步文件夹。

Agent： 好的，我来设置一个 Gmail 触发器。正在创建触发器
        GMAIL_NEW_GMAIL_MESSAGE...
        ✅ 触发器已创建！我会自动处理匹配的邮件
        并将附件保存到你的同步文件夹。
```

Agent 会：
1. 订阅相关的 Composio 触发器
2. 通过 WebSocket 实时接收事件
3. 使用一次性 Agent 会话处理每个事件
4. 以通知卡片形式交付结果

### 管理触发器

让 Agent 帮你管理触发器：
- `"列出我的活跃触发器"` — 查看所有运行中的触发器
- `"删除 Gmail 触发器"` — 移除特定触发器

## 配置参考

| 变量 | 必填 | 默认值 | 说明 |
|:---|:---|:---|:---|
| `COMPOSIO_API_KEY` | 是 | — | 你的 Composio API Key |
| `COMPOSIO_USER_ID` | 否 | `default` | Composio 会话的用户 ID |
| `COMPOSIO_AUTH_WAIT_TIMEOUT` | 否 | `300` | 等待 OAuth 授权完成的秒数 |
| `SERVER_HOST` | 触发器需要 | — | 你的 VPS 公网 IP 或域名 |
| `WEBHOOK_PORT` | 触发器需要 | `9090` | Webhook HTTP 端口 |

## 故障排查

### Q: 日志显示 "Composio not initialized"
A: 检查 `.env` 中 `COMPOSIO_API_KEY` 是否正确设置，然后重启 Agent。

### Q: OAuth 链接无法使用
A: 确保你在 [app.composio.dev](https://app.composio.dev/) 登录的是正确的 Composio 账号。链接与你的 `COMPOSIO_USER_ID` 关联。

### Q: 触发器不触发
A: 逐项检查：
1. `COMPOSIO_API_KEY` 是否已设置？
2. 应用是否已连接？（问 Agent："检查我的 Gmail 连接状态"）
3. 查看日志中的 WebSocket 连接状态：`journalctl -u nono-cowork | grep "trigger"`
4. 对于依赖 webhook 的触发器：`SERVER_HOST` 是否已设置？端口 `WEBHOOK_PORT` 是否可访问？
