# AnCLI (Android CLI) 🚀

> 像可靠的大叔 (Uncle) 一样，帮你把各种桌面级 CLI/TUI 环境全搞定！

[English](README.md) | [技术架构文档](ARCHITECTURE.md)

AnCLI 是一个为 Android Root 环境打造的 **"纯血 Linux 命令行工具通用底座与应用商店"**。它以标准的 **Magisk/KernelSU/APatch 模块**形式安装，通过 `proot` 启动原生 Ubuntu Base 根文件系统，完美绕过 Android Bionic C 库的限制，让你直接无缝运行基于 Node.js、Python 和 Go 的桌面级终端工具。

## 🎯 核心亮点

- **标准模块安装 (Magisk/KernelSU/APatch)**：通过 Manager 应用一键刷入 ZIP 即可。模块框架自动将 `ancli` 挂载到 `/system/bin`，同时注入 KSU/AP 动态路径 —— 安装工具后**无需重启**即可使用。
- **OTA 自动更新**：模块支持 `updateJson`，Manager 应用会自动检测新版本，一键升级。
- **开机自修复**：内置 `service.sh` 每次开机自动修复 DNS 配置和文件权限，无需手动维护。
- **Python 驱动的终端应用商店**：纯 Python 标准库编写的包管理器，提供彩色交互菜单、3 次重试的云端注册表获取、安全的环境变量注入。
- **云端插件注册表**：通过云端 JSON 文件定义工具安装逻辑。添加新工具只需提交 PR 修改注册表。
- **无前缀直达**：装完直接用。敲 `aider` 就是 Aider，敲 `claude` 就是 Claude Code。
- **安全加固**：命令白名单校验、Shell 操作符拦截、路径遍历防护、`shlex.quote()` 转义注入的环境变量。
- **"大满贯"底座**：底层拉取官方 `ubuntu-base` 镜像，通过 `apt-get` 补齐 `Node.js + Python3 + Git`，彻底杜绝依赖碎片化。

## 📦 支持的工具生态
*(以下列表由云端注册表动态下发)*
- [x] **Aider** (强大的终端 AI 结对编程神器)
- [x] **Claude Code** (Anthropic 官方出品的纯终端智能 Agent)
- [x] **Antigravity CLI (agy)** (Google 官方出品的高性能终端智能体)
- [x] **OpenCode** (开源的跨语言 AI 编程 Agent)
- [x] **MiMo Code** (专为 Android/Proot 环境定制的本地终端 Agent)
- [ ] *欢迎提交 PR 补充！*

## 🚀 安装方式

### 方式 A — Manager 刷入 (推荐)

1. 从 [Releases](https://github.com/AHLLX/AnCLI-Android/releases) 下载 `ancli-module.zip`
2. 打开 **Magisk/KernelSU/APatch Manager** 应用
3. 进入 **模块 → 从存储安装** → 选择 ZIP 文件
4. 等待初始化完成（自动下载 PRoot + Ubuntu 根文件系统 + APT 依赖）
5. 完成！在任意 root shell 中输入 `ancli` 即可

### 方式 B — 命令行快速安装

```bash
# 在 Termux 或任意终端中获取 root 权限 (su) 后执行：
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/install.sh | sh
```

此脚本会自动检测你的 root 管理器，下载模块 ZIP 并引导你完成安装。

## 💻 使用方法

### 交互模式
```bash
ancli
```
呼出应用商店菜单，输入数字序号即可安装、更新、卸载或重新配置任意工具。

### 命令行模式
```bash
ancli install aider          # 安装工具
ancli uninstall claude-code  # 卸载工具
ancli update aider           # 更新已安装的工具
ancli config aider           # 重新配置 API Key / 环境变量
ancli list                   # 列出所有已安装的工具
ancli --help                 # 显示帮助信息
ancli --version              # 显示版本号
```

### 安装工具后直接使用
```bash
# 无前缀直达！敲工具名就能用
aider
claude
opencode
```

## 🗑️ 卸载

直接在 Magisk/KernelSU/APatch Manager 中**删除 AnCLI 模块**即可。内置的 `uninstall.sh` 会自动清理所有 rootfs 文件、wrapper 脚本和动态 bin 链接。

## 📂 核心路径与安装位置指南

| 组件 | 物理路径 | 说明 |
| :--- | :--- | :--- |
| **Ubuntu 底座 (Rootfs)** | `/data/local/tmp/ancli/rootfs/` | Proot 容器（完整 Ubuntu 文件系统） |
| **AnCLI 控制大脑** | `/data/local/tmp/ancli/bin/ancli-core.py` | Python 包管理器主程序 |
| **已安装应用数据库** | `/data/local/tmp/ancli/installed.json` | 跟踪已安装应用及其元数据 |
| **模块目录** | `/data/adb/modules/ancli/` | 标准模块路径（自动挂载 `system/bin/ancli`） |
| **即时快捷命令** | `/data/adb/ksu/bin/` 或 `/data/adb/ap/bin/` | 免重启的全局命令存放处 |
| **NPM 软件** | `.../rootfs/usr/local/lib/node_modules/` | Proot 内的 Node.js 全局包 |
| **Pip 软件** | `.../rootfs/usr/local/lib/python3.12/dist-packages/` | Proot 内的 Python 全局包 |

## 🌐 自定义镜像源

海外用户可以覆盖默认的 USTC 镜像：

```bash
# 安装前设置，或在 shell 中导出
export ANCLI_MIRROR="archive.ubuntu.com"
```

## 📚 详细文档
关于底层 Proot 实现原理、双重注入免重启机制、模块生命周期以及云端注册表规范，请参阅 [技术架构详细文档](ARCHITECTURE.md)。
