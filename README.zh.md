# AnCLI (Android CLI)

AnCLI 是一个为已获取 Root 权限的 Android 设备打造的通用 Linux 命令行工具环境管理器与安装器。它通过 PRoot 在用户态启动底层的 Ubuntu Base 根文件系统，完美绕过了 Android Bionic C 库的限制，使用户能够无缝、免重启地在 Android 设备上运行基于 Node.js、Python 和 Go 的 Linux 命令行工具。

## 功能特性

- **模块化系统级集成**：支持作为标准的 Magisk/KernelSU/APatch 模块安装。系统启动时自动挂载工具到 `/system/bin`，并即时注入快捷方式到 KSU/AP 动态路径，实现安装工具后无需重启手机即可全局调用。
- **OTA 自动更新**：支持 root 管理器的 `updateJson` 更新规范，可直接在管理器 App 内检测并一键升级模块。
- **开机自动维护**：内置引导服务，每次开机时自动修复容器 DNS 配置与关键文件权限，保证环境稳定性。
- **交互式变量注入**：在安装工具时自动提示输入所需的环境变量（如 API 密钥、自定义中转端），并安全地注入到运行包装器中。
- **动态云端注册表**：工具的安装、更新和卸载逻辑由托管在 GitHub 上的 JSON 注册表动态解析，支持通过提交 Pull Request 扩展生态。
- **多重安全加固**：实现命令白名单校验、Shell 链式操作符过滤、环境变量转义和路径遍历防护。

## 支持的工具生态
*(由云端注册表动态下发)*
- **Aider** (终端 AI 结对编程工具)
- **Claude Code** (Anthropic 官方终端 Agent)
- **OpenCode** (开源终端 AI 编程工具)
- **MiMo Code** (专为 Android/Proot 环境定制的终端 Agent)
- **Antigravity CLI (agy)** (Google 官方高性能终端 Agent)

## 安装方法

### 方式 A：通过 Root 管理器刷入 (推荐)

1. 从 [Releases](https://github.com/AHLLX/AnCLI-Android/releases) 页面下载 `ancli-module.zip` 模块包。
2. 打开你的 **Magisk/KernelSU/APatch Manager** 应用。
3. 进入 **模块** -> **从存储安装**，选择下载的 ZIP 文件。
4. 安装并完成初始化（下载 PRoot + Ubuntu 根文件系统及基础依赖）后，在任意 root 终端输入 `ancli` 即可启动。

### 方式 B：命令行快速安装

在 Termux 或任意终端中获取 root 权限 (`su`) 后执行：

```bash
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI-Android/main/src/install.sh | sh
```

此脚本会自动检测当前的 root 管理器，下载对应的模块 ZIP 并引导完成刷入。

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
ancli --help                   # 显示帮助信息
ancli --version                # 显示版本号
```

### 运行已安装的工具
工具安装完成后，可直接在终端中调用，无需添加 `ancli` 前缀：
```bash
aider
claude
opencode
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

直接在 Magisk/KernelSU/APatch 管理器中**删除 AnCLI 模块**即可。内置的卸载脚本会自动清理所有的 rootfs 容器文件、二进制程序、wrapper 脚本和动态 bin 链接。

## 技术架构

关于双重注入免重启原理、PRoot 挂载细节及云端配置协议，请参阅 [技术架构详细文档](ARCHITECTURE.md)。
