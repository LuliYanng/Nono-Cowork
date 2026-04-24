[English](desktop_setup.md) | 简体中文

# 桌面端配置指南

Nono CoWork 桌面端连接到你的 VPS 后端，提供完整界面：与 Agent 聊天、管理自动化规则、审核通知、监控文件同步。

## 前提条件

- 一个正在运行的 Nono CoWork 后端（[快速开始](../README_zh-CN.md#快速开始)）
- 你的 VPS 地址和 API Token（来自 `.env`）

## 安装

### 方式一：下载安装包（推荐）

> 🚧 安装包即将上线。目前请使用下方的"从源码构建"方式。

<!-- 上线后从 [GitHub Releases](https://github.com/KilYep/nono-cowork/releases) 下载。 -->

### 方式二：从源码构建

需要 [Node.js](https://nodejs.org/) ≥ 18。

```bash
cd desktop
npm install
npm run package
```

安装程序在 `desktop/release/` 目录中，运行即可安装。

> 开发调试请使用 `npm run electron:dev` —— 详见 [desktop/README.md](../desktop/README.md)。

## 连接到 VPS

首次启动后：

1. 点击侧边栏的 **Settings**
2. 输入 **Server Address**（例如 `http://your-vps-ip:8080`）
3. 输入 **Access Token** —— VPS `.env` 文件中的 `DESKTOP_API_TOKEN` 值
4. 点击 **Test Connection** → **Save & Reconnect**

> 💡 配置保存在本地，只需设置一次。

## 文件同步

桌面端集成了 [Syncthing](syncthing_setup_zh-CN.md)，可在 VPS 和本地之间自动同步文件。

### 自动配对

如果 VPS 和本地都在运行 Syncthing，桌面端会在连接时自动交换 Device ID——**无需手动配对**。

### Windows 内嵌 Syncthing

在 Windows 上，桌面端内置了 Syncthing 运行时，不需要额外安装。

开发或自行构建时，先准备二进制文件：

```bash
cd desktop
npm run syncthing:prepare:win
```

### 同步状态

侧边栏底部的指示器显示：

| 图标 | 状态 |
|:---|:---|
| 🟢 已同步 | 已连接且文件最新 |
| 🔵 同步中... | 文件传输进行中 |
| ⬜ 未连接 | 本地 Syncthing 未运行或 VPS 不可达 |

## 功能一览

| 功能 | 说明 |
|:---|:---|
| **聊天** | 流式响应的对话界面，支持模型切换和代码块 |
| **工作区** | 通知中心——审核 Agent 交付物（邮件草稿、报告），一键批准或忽略 |
| **自动化规则** | 管理定时调度、事件触发、文件监听 |
| **设置** | 服务器连接、同步状态、模型选择 |
