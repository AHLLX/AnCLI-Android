# AnCLI (Android CLI)

AnCLI 是一个为已获取 Root 权限的 Android 设备打造的**双模式**命令行工具环境管理器。它通过两种互补的执行后端管理 CLI 工具：

- **PRoot/Ubuntu 模式** — 通过 PRoot 在隔离的 Ubuntu glibc 容器内运行 Python 和 Go 工具，完全独立于任何第三方应用。
- **Termux 宿主模式** — 通过 Android 宿主上的 Termux 运行时原生运行 Node.js 工具，绕过 PRoot 在 Android 15 上因 `ptrace` 线程拦截缺陷导致 `npm` 无法正常工作的限制。

## 功能特性

- **双模式执行引擎**：按工具自动选择后端。Python/Go 工具在 Ubuntu PRoot 容器内运行；Node.js 工具通过 Termux 宿主运行时原生运行。
- **模块化系统级集成**：支持作为标准的 Magisk/KernelSU/APatch 模块安装。启动时自动挂载工具到 `/system/bin`，并即时注入快捷方式到 KSU/AP 动态路径，安装后无需重启即可全局调用。
- **OTA 自动更新**：支持 root 管理器的 `updateJson` 更新规范，可在管理器 App 内直接检测并一键升级模块。
- **开机自动维护**：内置引导服务，每次开机自动修复容器 DNS 配置与关键文件权限。
- **交互式变量注入**：安装工具时自动提示输入环境变量（API 密钥、自定义中转端等），并安全地注入到运行包装器中。
- **动态云端注册表**：工具的安装、更新和卸载逻辑由托管在 GitHub 上的 JSON 注册表动态解析。
- **多重安全加固**：命令白名单校验、Shell 链式操作符过滤、环境变量转义和路径遍历防护。

## 支持的工具生态
*(由云端注册表动态下发)*

| 工具 | 运行时 | 执行后端 |
| :--- | :--- | :--- |
| **Aider** | Python | PRoot/Ubuntu |
| **MiMo Code** | Python | PRoot/Ubuntu |
| **Antigravity CLI (agy)** | Go（静态链接） | PRoot/Ubuntu |
| **Claude Code** | Node.js (Bun) | Termux 宿主 |
| **OpenCode** | Node.js | Termux 宿主 |

> **注意**：Node.js 工具需要设备上已安装 [Termux](https://termux.dev)。若检测到 Termux 未安装，AnCLI 会自动提示并引导用户完成设置。

## 安装方法

### 方式 A：通过 Root 管理器刷入（推荐）

1. 从 [Releases](https://github.com/AHLLX/AnCLI-Android/releases) 页面下载 `ancli-module.zip`。
2. 打开 **Magisk/KernelSU/APatch Manager** 应用。
3. 进入 **模块** → **从存储安装**，选择下载的 ZIP 文件。
4. 初始化完成后，在任意 root 终端输入 `ancli` 即可启动。

### 方式 B：命令行快速安装

在 Termux 或任意终端中获取 root 权限 (`su`) 后执行：

```bash
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/install.sh | sh
```

## 使用说明

### 交互菜单
```bash
ancli
```

### 命令行模式
```bash
ancli install <app_id>         # 安装工具
ancli uninstall <app_id>       # 卸载工具
ancli update <app_id>          # 更新已安装的工具
ancli config <app_id>          # 重新配置环境变量
ancli list                     # 列出已安装的工具
ancli --help                   # 显示帮助信息
ancli --version                # 显示版本号
```

### 运行已安装的工具
```bash
aider
claude
opencode
```

## 路径说明

| 组件 | 物理路径（宿主机视角） | 说明 |
| :--- | :--- | :--- |
| **Ubuntu 根文件系统** | `/data/local/tmp/ancli/rootfs/` | PRoot 容器文件目录 |
| **AnCLI 包管理器** | `/data/local/tmp/ancli/bin/ancli-core.py` | 包管理器 Python 主程序 |
| **安装状态数据库** | `/data/local/tmp/ancli/installed.json` | 已安装工具及其配置元数据 |
| **模块目录** | `/data/adb/modules/ancli/` | Magisk/KSU 挂载模块目录 |
| **即时命令路径** | `/data/adb/ksu/bin/` 或 `/data/adb/ap/bin/` | 免重启 wrapper 路径 |

## 自定义镜像源

```bash
export ANCLI_MIRROR="mirrors.ustc.edu.cn"
```

## 卸载方法

在 Magisk/KernelSU/APatch 管理器中删除 AnCLI 模块即可。内置的卸载脚本会自动清理所有 rootfs 文件、二进制程序和 wrapper 脚本。

## 技术架构

关于双模式执行架构、双重注入原理及云端配置协议，请参阅[技术架构详细文档](ARCHITECTURE.md)。关于 Android 15 兼容性技术分析，请参阅[兼容性白皮书](COMPATIBILITY.md)。
