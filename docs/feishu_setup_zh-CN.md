[English](feishu_setup.md) | 简体中文

# 飞书机器人配置指南

## 第一步：在飞书开放平台创建应用（约15分钟）

### 1.1 创建应用
1. 访问 [飞书开放平台](https://open.feishu.cn/) 并登录
2. 点击右上角「创建企业自建应用」
3. 填写：
   - 应用名称：`VPS Agent`（随意）
   - 应用描述：`远程服务器 AI 助手`

### 1.2 获取凭证
1. 进入应用 → 左侧「凭证与基础信息」
2. 复制 **App ID**（格式 `cli_xxxxxxxxxx`）和 **App Secret**
3. 填入 VPS 上的 `.env`：
   ```
   FEISHU_APP_ID=cli_xxxxxxxxxx
   FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxx
   ```

### 1.3 启用机器人能力
1. 左侧「应用能力」→「添加应用能力」
2. 找到 **「机器人」** 并启用

### 1.4 配置权限
1. 左侧「权限管理」
2. 搜索并开启以下权限：

| 权限 | 说明 |
|------|------|
| `im:message` | 基础消息能力 |
| `im:message:send_as_bot` | 以机器人身份发消息 |
| `im:message.p2p_msg:readonly` | 获取单聊消息 |
| `im:message.group_at_msg:readonly` | 获取群聊 @消息 |
| `im:message:readonly` | 读取消息详情 |

### 1.5 配置事件订阅（关键步骤）
1. 左侧「事件与回调」
2. **订阅方式** → 选择 **「使用长连接接收事件」** ⚠️ 重要
3. 点击「添加事件」→ 搜索 `im.message.receive_v1` → 添加
4. 保存

### 1.6 发布应用
1. 左侧「版本管理与发布」
2. 点击「创建版本」→ 填写版本号（如 `1.0.0`）和更新说明
3. 提交审核
4. **等待审核通过**（企业内部应用通常几分钟到几小时）

> ⚠️ **必须发布应用**才能使用 WebSocket 长连接。未发布的应用会报「应用未建立长连接」错误。

### 1.7 开始使用
- **私聊**：在飞书中搜索你的机器人名称，直接发消息
- **群聊**：把机器人拉进群，@机器人 + 指令

---

## 第二步：在 VPS 上启动

```bash
# 确保 .env 中已配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET

# 方式一：直接运行
cd /path/to/nono-cowork
.venv/bin/python -m src.channels.feishu

# 方式二：使用 uv 入口
uv run feishu-bot
```

启动成功后会看到：
```
==================================================
🚀 Feishu Bot started (WebSocket long connection)
   App ID: cli_xxxxxx...
   Allowed users: not set
==================================================
Waiting for Feishu messages...
```

---

## 第三步：可选安全配置

### 限制使用用户
在 `.env` 中添加用户白名单：

```bash
# 用户的 open_id 可以在 VPS 日志中看到（用户发消息时会打印）
FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
```

### 后台运行（推荐）

**方式一：systemd 服务（推荐）**

使用统一的多渠道服务文件：

```bash
# 编辑 nono-cowork.service，将 YOUR_USERNAME 替换为你的实际用户名
# 在 .env 中设置 CHANNELS=feishu（或添加到已有渠道列表中）
sudo cp nono-cowork.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nono-cowork
```

**方式二：screen / tmux**

```bash
# 使用 screen
screen -S feishu-bot
cd /path/to/nono-cowork
uv run feishu-bot
# Ctrl+A, D 脱离

# 或使用 tmux
tmux new -s feishu-bot
cd /path/to/nono-cowork
uv run feishu-bot
# Ctrl+B, D 脱离
```

---

## 特殊命令

| 命令 | 作用 |
|------|------|
| `/reset` 或 `reset` | 重置会话上下文 |
| `/help` 或 `help` | 显示帮助信息 |

---

## 常见问题

### Q: 提示「应用未建立长连接」
A: 应用必须先发布（步骤 1.6）。WebSocket 长连接只有审核通过后才能使用。

### Q: 收不到消息
A: 检查以下几项：
1. 是否添加了 `im.message.receive_v1` 事件订阅？
2. 订阅方式是否选择了「使用长连接接收事件」？
3. 权限是否全部开启？
4. 应用是否已发布并审核通过？

### Q: 如何获取用户的 open_id
A: 启动飞书 Bot 后，让目标用户给机器人发一条消息，在 VPS 日志中查看 `from ou_xxx`。
