# Tool Access Control System

> 统一的工具权限控制系统，用于限制 subagent（trigger / cron）可使用的工具范围。

## 核心概念

```
用户配置                    内部实现
┌──────────────────┐     ┌──────────────────────────────────────────┐
│ tool_access:     │     │  自定义工具: filter_tools_by_tags()      │
│   "read_only"    │ ──▶ │  Composio:   create_restricted_session() │
│   "read_write"   │     │                                          │
│   "safe"         │     │  两套系统各自过滤，对外统一为一个参数     │
│   "full"         │     └──────────────────────────────────────────┘
└──────────────────┘
```

**设计哲学**：
- 基础文件操作（read/write/edit_file）**永远可用**，不受限制
- `tool_access` 只控制**外部 API 操作**、**危险命令**和**系统管理**

---

## 工具分类

### 永远可用（无 tag）

这些是 agent 的基本能力，任何 `tool_access` 设置下都不会被过滤。

| 工具 | 说明 |
|---|---|
| `read_file` | 读取文件内容 |
| `write_file` | 创建/覆写文件 |
| `edit_file` | 搜索替换编辑文件 |
| `list_snapshots` | 查看编辑前的备份快照 |
| `check_command_status` | 查看后台命令的输出状态 |

### 有 tag（受 tool_access 控制）

| tag | 工具 | 说明 |
|---|---|---|
| **`execute`** | `run_command` | 执行 shell 命令，高风险 |
| **`read`** | `sync_status` | Syncthing 同步状态 |
| | `sync_wait` | 等待同步完成 |
| | `sync_versions` | 查看文件版本历史 |
| | `list_scheduled_tasks` | 查看定时任务列表 |
| | `delegate_status` | 查看 subagent 状态 |
| | `composio_list_triggers` | 查看可用 trigger 类型 |
| | `composio_list_active_triggers` | 查看已激活的 triggers |
| **`write`** | `send_file` | 通过 IM 发送文件给用户 |
| | `memory_write` | 写入持久记忆文件 |
| | `sync_restore` | 恢复文件到旧版本 |
| | `sync_pause` | 暂停同步 |
| | `sync_resume` | 恢复同步 |
| | `sync_ignore_add` | 添加同步忽略规则 |
| **`network`** | `web_search` | 搜索引擎搜索（同时有 read tag） |
| | `read_webpage` | 读取网页内容（同时有 read tag） |
| **`admin`** | `delegate` | 委派子任务给 subagent |
| | `create_scheduled_task` | 创建定时任务 |
| | `delete_scheduled_task` | 删除定时任务 |
| | `update_scheduled_task` | 修改定时任务 |
| | `composio_create_trigger` | 创建 trigger |
| | `composio_delete_trigger` | 删除 trigger |
| | `composio_wait_for_connection` | 等待 OAuth 连接 |

---

## 权限预设 (Presets)

### 预设定义

| preset | 自定义工具允许的 tags | Composio session 配置 | 适用场景 |
|---|---|---|---|
| `"full"` | **全部**（不过滤） | 不限制 | 完全信任的场景 |
| `"read_only"` | `{read}` | `tags=["readOnlyHint"]` | 只监控，不做任何操作 |
| `"read_write"` | `{read, write}` | `tags={disable: ["destructiveHint"]}` | 可以读+本地写，但不能远程破坏 |
| `"safe"` | `{read, write, network}` | `tags={disable: ["destructiveHint"]}` | 可联网可本地写，但不能远程破坏 |

### 各预设下的工具可用性

| 工具 | `full` | `read_only` | `read_write` | `safe` |
|---|:---:|:---:|:---:|:---:|
| **永远可用组** | | | | |
| `read_file` / `write_file` / `edit_file` | ✅ | ✅ | ✅ | ✅ |
| `list_snapshots` / `check_command_status` | ✅ | ✅ | ✅ | ✅ |
| **execute** | | | | |
| `run_command` | ✅ | ❌ | ❌ | ❌ |
| **read** | | | | |
| `sync_status` / `sync_wait` / `sync_versions` | ✅ | ✅ | ✅ | ✅ |
| `list_scheduled_tasks` / `delegate_status` | ✅ | ✅ | ✅ | ✅ |
| `composio_list_triggers/_active` | ✅ | ✅ | ✅ | ✅ |
| **write** | | | | |
| `send_file` / `memory_write` | ✅ | ❌ | ✅ | ✅ |
| `sync_pause` / `sync_resume` / `sync_restore` | ✅ | ❌ | ✅ | ✅ |
| **network** | | | | |
| `web_search` / `read_webpage` | ✅ | ✅* | ❌ | ✅ |
| **admin** | | | | |
| `delegate` / `create_task` / `create_trigger` | ✅ | ❌ | ❌ | ❌ |
| **Composio 外部 API** | | | | |
| `GMAIL_FETCH_EMAILS` (readOnlyHint) | ✅ | ✅ | ✅ | ✅ |
| `GMAIL_GET_ATTACHMENT` (readOnlyHint) | ✅ | ✅ | ✅ | ✅ |
| `GMAIL_CREATE_EMAIL_DRAFT` (destructiveHint) | ✅ | ❌ | ❌ | ❌ |
| `GMAIL_SEND_EMAIL` (destructiveHint) | ✅ | ❌ | ❌ | ❌ |

> \* `web_search` 和 `read_webpage` 同时有 `network` 和 `read` 两个 tag，只要命中其中一个就可用，所以 `read_only` 下也可用。

---

## Composio 工具的特殊处理

Composio 工具不使用我们的 tag 系统，而是通过 **session 级别过滤**控制。

### 映射关系

```python
# src/tools/composio_tools.py
_COMPOSIO_ACCESS_CONFIG = {
    "read_only":  {"tags": ["readOnlyHint"]},        # 只保留只读工具
    "read_write": {"tags": {"disable": ["destructiveHint"]}},  # 排除破坏性工具
    "safe":       {"tags": {"disable": ["destructiveHint"]}},  # 同上
    # "full" = 不传任何限制
}
```

### Composio tag 含义

| Composio tag | 含义 | 示例工具 |
|---|---|---|
| `readOnlyHint` | 只读取数据 | GMAIL_FETCH_EMAILS, GMAIL_GET_ATTACHMENT |
| `destructiveHint` | 修改/删除远程数据 | GMAIL_SEND_EMAIL, GMAIL_CREATE_DRAFT, GITHUB_DELETE_REPO |
| `idempotentHint` | 可安全重试 | — |
| `openWorldHint` | 开放世界操作 | — |

### 为什么两套系统分开

- **我们的 `write`** = 本地文件操作（write_file, sync_restore）→ 低风险
- **Composio 的 `destructiveHint`** = 远程 API 操作（发邮件, 删仓库）→ 高风险
- 语义不同，所以映射关系由 `_COMPOSIO_ACCESS_CONFIG` 手动定义

---

## 使用方式

### 在 Trigger Recipe 中设置

```json
{
    "trigger_slug": "GMAIL_NEW_GMAIL_MESSAGE",
    "agent_prompt": "...",
    "model": "gemini/gemini-3-flash-preview",
    "tool_access": "read_write"
}
```

### 在定时任务中设置

```json
{
    "task_name": "每日博客摘要",
    "cron": "0 9 * * *",
    "task_prompt": "...",
    "tool_access": "full"
}
```

### 通过 LLM 工具创建时设置

```
composio_create_trigger(
    trigger_slug="GMAIL_NEW_GMAIL_MESSAGE",
    agent_prompt="...",
    tool_access="read_write"
)

create_scheduled_task(
    task_name="每日报告",
    cron="0 9 * * *",
    task_prompt="...",
    tool_access="safe"
)
```

---

## 架构调用链

```
trigger 事件 / cron 触发
    │
    ▼
_handle_trigger_event() / _run_task()
    │ 读取 recipe/task 的 tool_access
    ▼
build_restricted_tools(tool_access="read_write")
    │
    ├─→ filter_tools_by_tags(custom_schemas, {"read","write"})
    │     → 过滤自定义工具
    │
    └─→ create_restricted_tools_schema("read_write")
          → 创建受限 Composio session (disable destructiveHint)
          → 返回受限的 meta-tool schemas
    │
    ▼ 合并为 tools_override
    │
provider.run_with_history(tools_override=tools_override)
    │
    ▼
agent_loop(tools_override=tools_override)
    │ active_tools = tools_override or tools_schema
    ▼
call_llm_stream(tools=active_tools)
    → LLM 看到的工具列表已经过滤
    → COMPOSIO_SEARCH_TOOLS 也只能发现受限的工具
```

---

## 关键源码位置

| 文件 | 关键内容 |
|---|---|
| `src/tools/registry.py` | `@tool(tags=...)` 装饰器, `filter_tools_by_tags()`, `TOOL_ACCESS_PRESETS` |
| `src/tools/__init__.py` | `build_restricted_tools()` 统一入口 |
| `src/tools/composio_tools.py` | `create_restricted_tools_schema()`, `_COMPOSIO_ACCESS_CONFIG` |
| `src/agent.py` | `agent_loop(tools_override=...)` |
| `src/subagent/self_agent.py` | `run_with_history(tools_override=...)` |
| `src/composio_triggers.py` | `_handle_trigger_event()` 读取 recipe.tool_access |
| `src/scheduler/executor.py` | `_run_task()` 读取 task.tool_access |
| `data/trigger_recipes.json` | trigger 的 tool_access 配置 |
| `data/scheduled_tasks.json` | cron 的 tool_access 配置 |
