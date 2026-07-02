# AnCLI

AnCLI 是一个为已 Root 安卓设备打造的统一、免系统修改（Systemless）的命令行环境管理器与插件式安装器。它基于轻量容器化技术，让用户能够在安卓端原生且无缝地运行各种标准的 GNU/Linux glibc 命令行工具（如 Python AI 编程助手、Go 二进制体、基于 Node.js 的终端智能体及各种主流开发套件）。

## 功能特性

- **无需 Node.js 与 NPM 环境**：基于 Node.js 的工具（如 Claude Code、OpenCode 和 MiMo Code）直接以官方发布的预编译 Linux-arm64 原生二进制文件安装，避免了复杂的 npm 依赖和 JS 编译。
- **模块化系统级集成**：支持作为 Magisk、KernelSU 或 APatch 模块安装。系统启动时自动挂载工具到 `/system/bin`，并即时注入快捷方式到 KSU/AP 动态路径，安装后无需重启手机即可全局调用。
- **OTA 自动更新**：支持 root 管理器的 `updateJson` 更新规范，可直接在管理器 App 内检测并一键升级模块。
- **开机自动维护**：内置引导服务，每次开机时自动修复容器 DNS 配置与关键文件权限，保证环境稳定性。
- **交互式变量注入**：在安装工具时自动提示输入所需的环境变量（如 API 密钥、自定义中转端），并安全地注入到运行包装器中。
- **动态云端注册表**：工具的安装、更新和卸载逻辑由托管在 GitHub 上的 JSON 注册表动态解析，支持通过提交 Pull Request 扩展生态。
- **命令行转义与代理直通**：通过 Python 内置网络库直连，绕过 ADB 命令行转义字符（如 `|`, `--`）被剥离的 bug；同时支持将宿主机的 HTTP 代理环境变量直接下发，一键连通下载通道。
- **PRoot 系统调用稳定性修补**：自动规避 Android 内核对 `io_uring` 和 `epoll` 翻译残缺导致的 Event Loop 阻塞死锁问题，确保基于 Node.js / Bun 的现代终端应用（如 MiMo 和 Claude Code）能够完美接收键盘的 Raw TTY 交互事件，不卡顿、不乱码。
- **多重安全加固**：实现命令白名单校验、Shell 链式操作符过滤、环境变量转义和路径遍历防护。

## 支持的工具生态
*(由云端注册表动态下发)*

| 工具 | 运行时 | 安装与运行后端 |
| :--- | :--- | :--- |
| **Aider** | Python | pip 模块包安装（PRoot 容器） |
| **MiMo Code** | Node.js/JS | 预编译原生二进制文件（PRoot 容器） |
| **Antigravity CLI (agy)** | Go (静态链接) | 独立发布二进制文件（PRoot 容器） |
| **Claude Code** | Node.js/JS | 预编译原生二进制文件（PRoot 容器，无需 NPM） |
| **OpenCode** | Node.js/JS | 预编译原生二进制文件（PRoot 容器，无需 NPM） |

## 安装方法

### 方式 A：通过 Root 管理器刷入 (推荐)

1. 从 [Releases](https://github.com/AHLLX/AnCLI-Android/releases) 页面下载 `ancli-module.zip` 模块包。
2. 打开你的 Magisk、KernelSU 或 APatch 管理器应用。
3. 进入 **模块** -> **从存储安装**，选择下载的 ZIP 文件。
4. 安装并完成初始化后，在任意 root 终端输入 `ancli` 即可启动。

### 方式 B：命令行快速安装

在 Termux 或任意终端中获取 root 权限 (`su`) 后执行：

```bash
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/install.sh | sh
```

此脚本会自动检测当前的 root管理器，下载对应的模块 ZIP 并引导完成刷入。

## 使用说明

### 交互菜单
启动包管理器主界面：
```bash
ancli
```

### 命令行模式
```bash
ancli install <app_id>         # 安装工具
ancli uninstall <app_id>       # 卸载工具
ancli update <app_id>          # 更新已安装的工具
ancli config <app_id>          # 重新配置环境变量 (API Key 等)
ancli list                     # 列出已安装的工具列表
ancli repair                   # 检测并修复环境问题
ancli --help                   # 显示帮助信息
ancli --version                # 显示版本号
```

### 运行已安装的工具
工具安装完成后，可直接在终端中调用，无需添加 `ancli` 前缀：
```bash
aider
claude
opencode
mimo
agy
```

## 路径说明

| 组件 | 物理路径 (宿主机视角) | 说明 |
| :--- | :--- | :--- |
| **Ubuntu 根文件系统** | `/data/local/tmp/ancli/rootfs/` | 完整的 Ubuntu 容器文件目录 |
| **AnCLI 包管理器** | `/data/local/tmp/ancli/bin/ancli-core.py` | 包管理器 Python 主程序 |
| **安装状态数据库** | `/data/local/tmp/ancli/installed.json` | 记录已安装工具及其配置元数据 |
| **模块目录** | `/data/adb/modules/ancli/` | Magisk/KSU 挂载模块目录 |
| **即时命令路径** | `/data/adb/ksu/bin/` 或 `/data/adb/ap/bin/` | 免重启直接调用的 wrapper 路径 |

## 自定义镜像源

如果需要在初始化过程中使用国内特定镜像源（默认使用清华 TUNA 镜像），可在安装前在 shell 中导出 `ANCLI_MIRROR` 变量：

```bash
export ANCLI_MIRROR="mirrors.ustc.edu.cn"
```

## 卸载方法

- **普通卸载（安全且默认）**：在 Magisk/KernelSU/APatch 管理器中删除模块时，系统只会清理模块的挂载入口，而**安全保留**你的完整 Ubuntu 容器系统、已安装的 Python 依赖包和 API 密钥配置。这样你在以后升级重装时能够瞬间恢复环境。
- **彻底清除（完全卸载）**：如果你想彻底从手机上抹除框架、容器和所有产生的大文件，请在 `ancli` 主菜单中按 `u` 键，然后选择 `[3] Completely uninstall AnCLI` 获取清理指令；或者直接在 root 终端执行以下命令进行物理删除：
  ```bash
  rm -rf /data/local/tmp/ancli
  ```
  执行完后，再前往管理器中卸载 AnCLI 模块即可。

## 技术架构

关于双重注入免重启原理、PRoot 挂载细节及云端配置协议，请参阅 [技术架构详细文档](ARCHITECTURE.md)。关于 Android 15 下 Node.js 和 Bun 的兼容性技术边界，请参阅 [兼容性 white paper](COMPATIBILITY.md)。
