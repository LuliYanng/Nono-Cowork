[English](syncthing_setup.md) | 简体中文

# Syncthing 文件同步配置指南

通过 Syncthing 让你的本地电脑与 VPS 之间自动同步文件。Agent 在 VPS 上修改文件后，你本地会自动收到更新。

## 工作原理

```
你的电脑                    VPS
┌──────────────┐        ┌──────────────┐
│  ~/Sync      │◄──────►│  ~/Sync      │
│  （你的文件）  │        │ （Agent 在    │
│              │        │  这里工作）    │
└──────────────┘        └──────────────┘
       ▲  Syncthing 自动双向同步  ▲
```

## 1. 在 VPS 上安装 Syncthing

### Debian / Ubuntu

```bash
# 添加官方仓库
sudo mkdir -p /etc/apt/keyrings
curl -L -o /etc/apt/keyrings/syncthing-archive-keyring.gpg \
  https://syncthing.net/release-key.gpg
echo "deb [signed-by=/etc/apt/keyrings/syncthing-archive-keyring.gpg] \
  https://apt.syncthing.net/ syncthing stable" | \
  sudo tee /etc/apt/sources.list.d/syncthing.list

sudo apt update
sudo apt install syncthing
```

### 启动并设为开机自启

```bash
# 设置为用户服务
systemctl --user enable syncthing
systemctl --user start syncthing

# 查看状态
systemctl --user status syncthing
```

> ⚠️ **使用 root 用户？** `systemctl --user` 对 root 用户默认不可用。请改用系统级服务：
> ```bash
> sudo systemctl enable syncthing@root
> sudo systemctl start syncthing@root
> ```

### 开放防火墙端口

```bash
sudo ufw allow 22000/tcp   # Syncthing 文件同步
sudo ufw allow 21027/udp   # Syncthing 发现协议
```

### 远程访问 Web UI（可选）

默认情况下 Syncthing Web UI 只监听 `127.0.0.1:8384`。要从本地浏览器访问：

```bash
# 方式一（推荐）：SSH 端口转发
ssh -L 8384:localhost:8384 你的用户名@你的VPS-IP
# 然后在本地浏览器打开 http://localhost:8384

# 方式二：修改监听地址（不推荐，安全性较低）
# 编辑 ~/.local/state/syncthing/config.xml
# 将 <address>127.0.0.1:8384</address> 改为 <address>0.0.0.0:8384</address>
# ⚠️ 记得设置 Web UI 密码
```

## 2. 在你的本地电脑上安装 Syncthing

- **Windows（使用桌面端）**：Nono CoWork 桌面端可内嵌 Syncthing——不需要额外安装。详见[桌面端配置](desktop_setup_zh-CN.md)。
- **Windows（独立安装）**：下载 [SyncTrayzor](https://github.com/canton7/SyncTrayzor/releases)（带系统托盘图标的 Syncthing）
- **macOS**：`brew install syncthing` 或从 [syncthing.net](https://syncthing.net/) 下载
- **Linux**：与 VPS 安装方式相同

启动后，在浏览器打开 http://localhost:8384 进入 Web UI。

## 3. 配对设备

> 💡 **使用桌面端？** 可以跳过这一步。桌面端会在「Settings → Save & Reconnect」时自动与 VPS 交换 Device ID。详见[桌面端配置](desktop_setup_zh-CN.md#文件同步自动配对)。

### 手动配对（不使用桌面端）：

1. **获取 VPS 设备 ID**：在 VPS 的 Web UI → 右上角「操作」→「显示 ID」

2. **在本地添加 VPS 设备**：本地 Web UI →「添加远程设备」→ 粘贴 VPS 设备 ID

3. **在 VPS 确认**：VPS 的 Web UI 会弹出确认提示 → 点击「添加」

## 4. 创建共享文件夹

1. 在 VPS 上创建文件夹：
   ```bash
   mkdir -p ~/Sync
   ```

2. 在 VPS 的 Web UI →「添加文件夹」：
   - 文件夹标签：`Sync`（随意）
   - 文件夹路径：`/home/你的用户名/Sync`
   - 在「共享」标签页中勾选你的本地设备

3. 本地 Web UI 会出现共享请求 → 接受并选择本地目标路径

## 5. 配置 Agent 的 Syncthing API Key

Agent 需要 Syncthing REST API 来查询同步状态。

```bash
# 获取 API Key（在 VPS 上执行）
grep apikey ~/.local/state/syncthing/config.xml
# 输出类似：<apikey>xxxxxxxxxxxxxxxxxxxxx</apikey>

# 填入 .env
SYNCTHING_API_KEY=xxxxxxxxxxxxxxxxxxxxx
```

## 6. 推荐的 .stignore 配置

在同步文件夹中创建 `.stignore` 文件，排除不需要同步的内容。

> **提示：** Agent 启动时会通过 `_ensure_stignore()` 自动检查关键规则是否存在，但建议一开始就配置完整的 `.stignore`。

`(?d)` 前缀表示新忽略的文件也会从远端删除。`**` 通配符匹配任意层级子目录。

```
// Python 虚拟环境（通配符覆盖 .venv, .venv2, .blog_venv 等变体）
(?d)**/*venv*
(?d)**/env

// Python 缓存和构建产物
(?d)**/__pycache__
(?d)**/*.pyc
(?d)**/*.pyo
(?d)**/*.egg-info

// Node.js
(?d)**/node_modules

// IDE 和系统文件
(?d)**/.idea
(?d)**/.vscode
(?d)**/.DS_Store
(?d)**/Thumbs.db

// Git 仓库
(?d)**/.git

// Agent 内部产物
(?d).agent_snapshots
(?d).stversions

// 大型二进制文件（防止意外同步）
(?d)**/*.zip
(?d)**/*.tar.gz
(?d)**/*.mp4
```

## 7. 验证同步

1. 在本地同步文件夹中创建测试文件：
   ```bash
   echo "hello from local" > ~/Sync/test.txt
   ```

2. 几秒后，在 VPS 上检查：
   ```bash
   cat ~/Sync/test.txt
   ```

3. 在 Agent 中发送指令如「检查同步状态」→ Agent 会调用 `sync_status()` 确认

同步工作正常后，你就可以把工作文件放在同步文件夹里，然后通过飞书/Telegram 指挥 Agent 处理了！
