# AnCLI

AnCLI 是一个为已 Root 安卓设备打造的统一、免系统修改（Systemless）的命令行环境管理器与插件式安装器。当前所有受支持的工具统一运行在一个 Ubuntu glibc PRoot 容器中，同时提供受控的 Android 路径映射、代理处理、浏览器跳转和 Root 管理器 wrapper。

当前运行后端只有 `proot-ubuntu`。Android 的 `/system` 继续保持只读，工具通过模块 systemless wrapper 与 KernelSU/APatch 动态路径暴露；容器内 Agent 的全局运行时记忆固定在 `/root/.agents/AGENTS.md`。

## 功能特性

- **独立二进制工具无需宿主 Node.js**：Claude Code、OpenCode 和 MiMo Code 直接安装官方预编译 Linux-arm64 二进制；DeepSeek Harness（`dsh`）是明确的 npm 例外，会在容器内引导自己的 Node LTS。
- **模块化系统级集成**：支持作为 Magisk、KernelSU 或 APatch 模块安装。模块 overlay 在启动后通过 `/system/bin` 暴露 wrapper，同时即时注入 KSU/AP 动态路径，安装后无需重启手机即可全局调用。
- **OTA 自动更新**：支持 root 管理器的 `updateJson` 更新规范，可直接在管理器 App 内检测并一键升级模块。
- **开机自动维护**：内置引导服务，每次开机时自动修复容器 DNS 配置与关键文件权限，保证环境稳定性。
- **交互式变量注入**：在安装工具时自动提示输入所需的环境变量（如 API 密钥、自定义中转端），并安全地注入到运行包装器中。
- **动态云端注册表**：工具的安装、更新和卸载逻辑由托管在 GitHub 上的 JSON 注册表动态解析，支持通过提交 Pull Request 扩展生态。
- **命令行转义与代理直通**：通过 Python 内置网络库直连，绕过 ADB 命令行转义字符（如 `|`, `--`）被剥离的 bug；需要代理的工具使用 Android 系统代理，DeepSeek Harness（`dsh`）使用直连模式，避免旧代理缓存导致连接失败。
- **PRoot 系统调用稳定性修补**：自动规避 Android 内核对 `io_uring` 和 `epoll` 翻译残缺导致的 Event Loop 阻塞死锁问题，确保基于 Node.js / Bun 的现代终端应用（如 MiMo 和 Claude Code）能够完美接收键盘的 Raw TTY 交互事件，不卡顿、不乱码。
- **物理键盘输入法支持**：初始化自动安装 `fcitx5` 及 `fcitx5-chinese-addons`（中文拼音/五笔等），并在环境配置中自动向容器导出输入法环境变量（`GTK_IM_MODULE=fcitx`, `QT_IM_MODULE=fcitx`, `XMODIFIERS=@im=fcitx`），以解决部分用户在安卓设备上外接物理键盘（蓝牙/有线键盘）时，在终端 TUI 应用（如 Aider, MiMo 等）中无法直接输入中文或其他非英文字符的问题。
- **跨沙箱浏览器自动跳转（网页授权登录）**：容器把需要打开的 URL 写入宿主队列，由 wrapper 调用 Android 浏览器；无法自动跳转时会把完整 URL 回显到终端，便于手动打开。
- **多语言界面 (zh/en)**：CLI 和 WebUI 共用持久化的语言配置。
- **WebUI 可视化设置**：设置窗口可编辑语言、主题、默认工作目录、代理模式和手动代理地址，并显示当前后端、Android 数据通道、生效目录和代理模式。
- **简洁的 CLI 输出**：交互菜单和状态行只保留必要信息，命令细节通过 `ancli --help`、JSON 输出和日志查看。
- **多重安全加固**：实现命令白名单校验、Shell 链式操作符过滤、环境变量转义和路径遍历防护。
- **稳定的 dsh WebUI 启动**：AnCLI WebUI 等待 dsh 生成带 token 的地址后打开浏览器；3080 已有服务时复用该服务。
- **统一品牌图标**：模块图标和 WebUI 顶部使用黑底终端与安卓机器人图标。
- **终端生命周期绑定**：Agent wrapper 会绑定启动它的终端父进程；终端正常切到后台时继续运行，终端被划掉或强制停止时自动结束 PRoot 和 Agent。完全初始化前也会先清理已知 Agent 进程。

## 项目指导文件

- [AGENTS.md](AGENTS.md) 是本地开发、模块维护、设备验证和发布流程的项目交接入口。修改核心脚本、registry、wrapper 或发布资产前先阅读它。
- `src/runtime-context/AGENTS.md` 是手机容器内 Agent 使用的模板，安装后渲染到 `/root/.agents/AGENTS.md`，与项目开发指导文件分开维护。
- Antigravity CLI 的全局规则入口是 `/root/.gemini/AGENTS.md`，AnCLI 会把它链接到同一份容器运行时记忆。

## 支持的工具生态
*(由云端注册表动态下发)*

| 工具 | 运行时 | 安装与运行后端 |
| :--- | :--- | :--- |
| **Aider** | Python | pip 模块包安装（PRoot 容器） |
| **MiMo Code** | Node.js/JS | 预编译原生二进制文件（PRoot 容器） |
| **Antigravity CLI (agy)** | Go (动态链接) | 独立发布二进制文件（PRoot 容器） |
| **Claude Code** | Node.js/JS | 预编译原生二进制文件（PRoot 容器，无需 NPM） |
| **OpenCode** | Node.js/JS | 预编译原生二进制文件（PRoot 容器，无需 NPM） |
| **Grok** | Rust (静态链接) | 独立发布二进制文件（PRoot 容器） |
| **DeepSeek Harness (dsh)** | Node.js/JS | npm 包 `@deepseek-ai/dsh` + Node LTS 引导（要求 Node ≥ 22.19） |

### 在安卓上使用 DeepSeek Harness

`dsh` 是唯一从 **npm** 安装的工具（它的启动器要解析 80 多个插件 bundle 包），因此注册表条目会先引导一个现代 Node，再从官方源安装 CLI：

```bash
ancli install dsh        # 下载 Node LTS → npm i -g @deepseek-ai/dsh
dsh --version            # 运行时版本（也是 ancli check 探测的值）
dsh web                  # Web UI 监听 http://127.0.0.1:3080，并通过 AnCLI 的 xdg-open 桥接直接拉起手机浏览器
dsh web --no-open        # 或者只打印 URL，不自动开浏览器
dsh headless "总结这个仓库"   # 一次性任务，打印结果后退出
```

- **API 密钥**：`ancli config dsh` 填 `DEEPSEEK_API_KEY`（可选 `DEEPSEEK_BASE_URL`），或在 Web UI 的 Settings → Models 里填写。两者都会落到 `/root/.dsh`（即 `$DSH_HOME`），模块升级不会丢。
- **代理**：DSH 在 AnCLI registry 中使用直连模式，不继承 Android 系统代理缓存；其他需要代理的工具继续使用 wrapper 的系统代理探测。
- **Profile**：`web`、`headless`、`sdk`、`sdk-minimal`、`acp` 首次运行会自动初始化。**不存在 `tui` profile**：`dsh tui` 会直接报 *"profile \"tui\" does not exist; create it with `dsh plugin --profile tui add <package>`"*，上游 `packages/boot/app-boot` 的内置模板里没有终端 app bundle，npm 上也没有发布任何 TUI bundle（CLI `--help` 里的 `dsh tui` 示例是过期的）。交互界面就是 Web UI；脚本化一次性任务用 `dsh headless "…"`。（`dsh plugin … add <package>` 需要 `pnpm`，容器里没有预装。）
- **浏览器会自动打开**：`dsh web` 打印带鉴权的 URL 后交给手机浏览器（AnCLI 在宿主侧代开：容器里跑不了 Android 的 `am`，失败时会把 URL 回显到终端）。这个 URL 带**每次运行的 token**——直接打开收藏夹里的 `127.0.0.1:3080` 会显示 *"dsh web authentication required"*，请用打印出来的完整 URL（它会写入签名 cookie 再跳到不带 token 的地址）。`--no-open` 可只在终端保留 URL；如果上一次 `dsh web` 还在跑，端口被占用，新的会直接退出——先 `Ctrl+C` 停掉旧的。
- **看到 "authentication required" 怎么排查**：那句话只表示"这次请求没带上有效 cookie"。① 确认端口上跑的是**当前**这个实例（旧的 `dsh web` 没停掉时，浏览器打开新实例的 token 也会被旧实例拒绝）——先 `Ctrl+C` 停掉所有旧会话再重开；② 用**完整打印的 URL 新开一个标签页**（不要刷新那个已经跳成 `/` 的地址，也不要开无痕模式）；③ 浏览器若禁用了该站点 cookie，请放行 `127.0.0.1` 或换个浏览器——服务端流程本身可用（可用 `scripts/probe_dsh_auth.py` 在容器里验证）。

## 安装方法

### 方式 A：通过 Root 管理器刷入 (推荐)

1. 从 [Releases](https://github.com/AHLLX/AnCLI-Android/releases) 页面下载 `ancli-v<版本号>.zip` 模块包（文件名与 Release 标签一致，例如 `ancli-v1.2.603.zip`）。
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
ancli check                    # 联网检查官方最新版本与可更新项
ancli doctor --json            # 输出只读路径、挂载和工具诊断
ancli agent-init               # 生成容器全局 Agent 记忆和环境指导
ancli agent-init --refresh     # 已安装工具变化后刷新 AnCLI 生成的记忆文件
ancli repair                   # 检测并修复环境问题
ancli --help                   # 显示帮助信息
ancli --version                # 显示版本号
```

### 可配置项

全局配置保存在容器内的 `/root/.ancli-config.json`。当前只接受以下四个键：

| 配置键 | 可用值 | 作用 |
| :--- | :--- | :--- |
| `default_cwd` | 绝对路径或空值 | 从 `/` 启动工具时使用的目录；留空恢复 `/root`。 |
| `lang` | `zh`、`en` | AnCLI 命令输出和 WebUI 的语言偏好。 |
| `proxy_mode` | `system`、`manual`、`direct` | 使用 Android 系统探测、手动代理或直连。 |
| `proxy_url` | `http://host:port` 或 `https://host:port` | 手动代理地址，仅在 `manual` 模式使用。 |

```bash
ancli config --json
ancli config --help
ancli config --set lang=zh
ancli config --set default_cwd=/sdcard/projects
ancli config --set proxy_mode=manual --set proxy_url=http://192.168.1.10:7890
ancli config --set proxy_mode=direct
```

工具自己的环境变量由 `src/registry.json` 声明，可用 `ancli list --json` 查看；
使用 `ancli config <app_id> --set KEY=VALUE` 写入，密钥只保存在容器的 secrets 文件中，
不会嵌入 wrapper。当前 Claude Code 支持 `ANTHROPIC_API_KEY`、
`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`；DeepSeek Harness
支持 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`；所有工具都可选配置
`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`。

### 运行已安装的工具
工具安装完成后，可直接在终端中调用，无需添加 `ancli` 前缀：
```bash
aider
claude
opencode
mimo
agy
```

### 更新检测（识别官方最新版本）

AnCLI 不再"猜"版本号：对每个已安装的工具，它会

1. **问工具自己**（registry 里的 `version_cmd`，例如 `claude --version`）拿到本地真实版本；
2. **问官方发布接口**（registry 里的 `latest`）拿到官方最新版本。

| 工具 | 官方版本来源 |
| :--- | :--- |
| Aider | PyPI（`aider-chat`） |
| MiMo Code | GitHub Releases（`XiaomiMiMo/MiMo-Code`） |
| Antigravity CLI | 官方发布清单（`antigravity-cli-auto-updater…/manifests/linux_arm64.json`） |
| Claude Code | GitHub Releases（`anthropics/claude-code`） |
| OpenCode | GitHub Releases（`anomalyco/opencode`） |
| Grok CLI | 官方通道指针（`x.ai/cli/stable`，失败时回落 GCS 镜像） |

结果缓存 6 小时（`/data/local/tmp/ancli/.update_cache.json`），所以 WebUI 打开很快、离线也能看；
WebUI 顶部有 **检查更新 (check)** 按钮可随时联网刷新，缓存过期（>6 小时）时打开页面也会自动检查；
单次查询失败时窗口为 15 分钟，并保留上一次已知版本。

```bash
ancli check            # 联网刷新"本地版本 + 官方最新版本"
ancli check --json     # 同上，机器可读（给脚本/自动化用；WebUI 走 ancli check + list --json）
ancli list             # 检查后显示 "可更新 → vX (来源)"
```

**注意**：WebUI 页面（`webroot/index.html`）只能随模块刷入更新——`ancli repair` 无法替换它（`/data/adb/modules` 受 SELinux 保护）。若 `check` 报 `[Errno 13] Permission denied`，说明当前 shell 域不能写 `/data/local/tmp/ancli`，请在 KernelSU 管理器里打开 WebUI 点「检查更新」（走管理器的 exec 通道），或换一个 root 终端再试。

`ancli update` 失败（断网、代理不可用、官方 404）现在会以非 0 退出码结束，
WebUI 会显示更新失败原因，并保留已记录的版本号。

## 网络代理与 VPN 说明

在 Root (UID 0) 环境下，安卓的 VPN 软件（如 Clash、v2rayNG 等运行在 TUN 模式时）通常会放行 Root 流量以避免产生死循环。这会导致在终端里运行 `agy` 或 `aider` 等工具时，流量会直接穿透 VPN 直连网络。

**AnCLI 提供了完美的解决方案：**
1. **自动代理探测**：每次执行快捷命令（如敲下 `agy`）时，启动脚本会自动通过 `dumpsys connectivity` 请求 Android 系统的网络状态。如果你的 VPN 设置了全局或系统 HTTP 代理，框架会自动拾取代理的 IP 和端口，并注入到运行容器中，实现**零配置透明代理**。
2. **强制硬编码代理（纯 VPN 模式）**：如果你的翻墙软件不支持注入系统代理，可以在终端输入 `ancli config <工具名>`（例如 `ancli config agy`），在交互提示中直接填入你的本地代理地址（如 `http://127.0.0.1:7890`）到 `HTTP_PROXY` 和 `ALL_PROXY` 项中，即可将其永久绑定到该工具。

## 统一 Agent Skills

每个 Agent 工具都从自己的路径读 skill，但它们都认一套 "agents" 约定，所以 AnCLI 把它们指到同一个目录：

```bash
ancli skills          # 建 ~/.agents/skills，并把各工具的 skills 目录链过去
ancli skills --json   # 同上，机器可读
ancli repair          # 也会执行这套设置（WebUI 的「修复环境」按钮同理）
```

把 skill 放到 `/root/.agents/skills/<名字>/SKILL.md`（frontmatter 需要 `name` 和 `description`，名字用小写 kebab-case），下面所有工具都能看到。**已有内容的工具自有 skills 目录不会被覆盖**，命令会把它们列为 `kept`。

| 工具 | 全局（容器 `$HOME`） | 项目级 | 备注 |
| :--- | :--- | :--- | :--- |
| **Claude Code** | `~/.claude/skills/<名字>/SKILL.md` | `<仓库>/.claude/skills/` | 已链到统一目录 |
| **OpenCode** | `~/.config/opencode/skills/`、`~/.claude/skills/`、`~/.agents/skills/` | `<仓库>/.opencode/skills/`、`.claude/skills/`、`.agents/skills/` | 只认文件夹形式，不认平铺 `.md` |
| **MiMo Code** | `~/.mimocode/skills/`、`~/.claude/skills/`、`~/.agents/skills/` | 仓库内同名路径 | |
| **Antigravity CLI** | `~/.agents/skills/`（另读 `~/.agents/skills.json` 索引）、`~/.gemini/config/skills/` | `<仓库>/.agents/skills/` | 原生读统一根 |
| **Grok CLI** | `~/.grok/skills/`、`~/.claude/skills/`、`~/.agents/skills/`、内置 `~/.grok/bundled/skills/` | `<仓库>/.grok/skills/`、`.claude/skills/`、`.agents/skills/` | |
| **DeepSeek Harness** | `~/.dsh/skills/`、`~/.agents/skills/` | `<仓库>/.dsh/skills/`、`.agents/skills/` | 也接受平铺 `<名字>.md` |
| **Aider** | —（没有 skill 机制） | `CONVENTIONS.md`（或 `aider --read 文件`） | 用 conventions 文件替代 |

skill 存在容器的 `/root` 下，模块升级不会丢；想跟着项目走就放仓库里的 `<仓库>/.agents/skills/`——除 Aider 外所有工具都认这个路径。

## 工作区与"手机上的真实文件"

容器并不是个隔离沙箱：宿主机的目录是**绑定挂载**进容器的，所以容器里的路径就是手机上的真实路径，改完立刻生效（文件管理器/编辑器马上能看到），不需要同步。

| 容器内路径 | 实际是什么 |
| :--- | :--- |
| `/sdcard`、`/storage/emulated/0` | **手机内部存储**（文件管理器里看到的那些） |
| `/data` | 手机 `/data`（各 App 私有目录等，root 可见） |
| `/mnt`、`/storage` | 手机对应的挂载点 |
| `/root` | 容器私有的 HOME：各工具的配置、密钥、skills 都在这，**不在**手机文件管理器里 |

所以 `dsh web` 的"选择工作区目录"里：

- 默认停在 **容器 HOME `/root`**，那里只有 `.claude`、`.dsh` 这类点文件，所以第一眼像是空的——勾选"显示隐藏文件"才看得到，那些是**工具配置**，不是你的手机文件。
- 想操作手机文件，就在路径框里填 `/sdcard` 或某个项目目录（如 `/sdcard/projects/demo`）。
- 更省事的办法：**先 cd 到项目目录再启动**——`cd /sdcard/projects/demo && dsh web`，工作区会默认落在那里，AI 的当前目录直接就是你的工程。
- 注意：Android 11+ 对 `/sdcard/Android/data` 等目录有访问限制；带空格的路径记得加引号。

### 默认工作目录（从 `/` 启动时）

Termius 新会话、或 `su` 之后直接敲 `mimo`，宿主 cwd 是 `/`。全屏 TUI 启动时会遍历 cwd，等于扫描整个容器 + 绑进来的 `/data`、`/sdcard`（几十万文件），会卡很久甚至起不来。AnCLI 把这种启动转到一个小目录——默认 `/root`，也可以自己指定：

```bash
ancli config                                     # 查看当前设置
ancli config --set default_cwd=/sdcard/projects  # 从 / 启动时落到这里
ancli config --set default_cwd=                  # 留空 = 恢复内置默认 /root
```

WebUI 里点「设置」也能改（同一份配置，存在容器 `/root/.ancli-config.json`）。设置窗口还可以编辑
`lang`、`proxy_mode` 和 `proxy_url`；主题偏好保存在当前 WebUI。状态卡片会显示当前 Ubuntu/PRoot
后端、`/mnt/ancli-data` 数据通道、生效目录和代理模式。

修改 `default_cwd` 不会移动运行时上下文。AnCLI 统一维护容器全局的
`/root/.agents/AGENTS.md`，需要重建时运行 `ancli agent-init`。

只影响“从 `/` 启动”这一种情况：先 `cd` 到项目目录再启动时，用的永远是你的 cwd。目录必须是容器里存在的绝对路径，否则 `ancli config` 会直接拒绝并提示。

### 默认 Agent 记忆与环境指导

AnCLI 会在容器全局目录 `/root/.agents/AGENTS.md` 维护一份简短的运行时 `AGENTS.md`。运行
`ancli agent-init` 可以显式生成；`ancli repair`、`ancli skills` 和成功安装工具时也会刷新
AnCLI 自己生成的文件。默认工作目录只影响工具启动位置，运行时上下文始终位于
`/root/.agents/AGENTS.md`。

文件包含当前 AnCLI 版本、容器与 Android 数据路径、代理/DNS 和浏览器接力约定、已安装工具、
已配置的环境变量名（绝不写入变量值）、共享 skills 路径以及 Agent 操作边界。它不是密钥存储，
不要把 API key、cookie 或 token 写进去。已有的项目 `AGENTS.md` 会保留；模板位于
`src/runtime-context/AGENTS.md`，设备运行时文件可用 `ancli agent-init --refresh` 刷新。

```bash
ancli agent-init
ancli agent-init --json
ancli agent-init --refresh
```

## 跨 App 数据（为什么有时看不到 `/data/data`）

Android 给每个 App 的进程一个独立的 mount namespace，并把它**不该看到的** `/data/data` 用 tmpfs 遮住（只留自己的几个目录，`mode 751`）。所以从 Termius 这类 App 里 `su` 出来的 root shell，会继承这个"缩水"的视图——这不是 proot 的限制，从 ADB 或系统级终端 `su` 进去就能看到完整的 752 个包。

AnCLI 的处理方式是：**每次启动工具时，在宿主侧把真实的 `/data` 非递归 bind 到 `/mnt/ancli-data`**。非递归 bind 不会继承子挂载，正好绕过那层 tmpfs 遮蔽；容器里 `/mnt` 本来就是绑进来的，所以路径直接可见：

```bash
ls /mnt/ancli-data/data                 # 所有 App 的私有目录（真实视图）
ls /mnt/ancli-data/data/com.tencent.mm  # 某个 App 的私有数据
```

- 需要 root；`/mnt` 不可写时自动跳过，不影响工具启动。
- 幂等：已经在挂载表里就不会重复挂（`grep /proc/mounts` 判断）。
- `ancli repair` 会顺带检查并挂载；`ancli status` / `status --json` 的 `android_data` 字段显示是否就绪。
- 挂载只在当前 mount namespace 内有效：App 内的会话随 App 结束；从 ADB 全局 `su` 挂的那次会留到重启（重启自然清空）。

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
- **彻底清除（完全卸载）**：如果你想彻底删除容器、已安装工具、密钥和配置，请在 `ancli` 主菜单中按 `u` 键，然后选择 `[3] Completely uninstall AnCLI` 获取受支持的清理流程。在 root Android shell 中先创建一次性清除标记，再从 Magisk、KernelSU 或 APatch 管理器卸载模块：
  ```bash
  touch /data/local/tmp/ancli_force_purge
  ```
  卸载脚本会消费并删除这个标记，清理 AnCLI 数据目录和动态 wrapper。升级时不要保留这个标记。

## 技术架构

关于双重注入免重启原理、PRoot 挂载细节及云端配置协议，请参阅 [技术架构详细文档](ARCHITECTURE.md)。关于 Android 15/16 下 Node.js 和 Bun 的兼容性技术边界，请参阅 [兼容性 white paper](COMPATIBILITY.md)。
