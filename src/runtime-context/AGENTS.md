<!-- ancli:runtime-context v1 -->
<!-- template maintained 2026-11-13 -->
# AnCLI Runtime Context

这份文件由 AnCLI 写入容器全局路径 `/root/.agents/AGENTS.md`。它记录运行环境、路径和操作边界，属于设备运行时上下文，不是项目开发规则文件。旧版手机记忆只能作为历史参考，不能覆盖当前命令和路径探测结果。不要把 API key、cookie、token 或其他密钥写入这里。

## 当前环境

- AnCLI 版本：`{{ANCLI_VERSION}}`
- 默认工作目录：`{{DEFAULT_CWD}}`
- Ubuntu/PRoot 根目录（宿主路径）：`{{ROOTFS}}`
- AnCLI 数据目录：`{{ANCLI_DIR}}`
- Android 真实 `/data` 通道：`{{ANDROID_DATA_MNT}}`（可用性用 `ancli status --json` 查看）
- 共享 skills 根目录：`{{SHARED_SKILL_DIR}}`
- 当前已安装工具：`{{INSTALLED_APPS}}`
- 已保存的配置变量名（不含值）：`{{CONFIGURED_ENV}}`

AGY 的全局规则入口是 `/root/.gemini/AGENTS.md`。AnCLI 将它链接到本文件；通用入口仍是 `/root/.agents/AGENTS.md`。如果某个 Agent 报告没有加载记忆，先检查它自己的官方发现路径和 `readlink -f` 结果。

工具通过宿主 wrapper 进入单层 Ubuntu PRoot。wrapper 会继承 Android 系统代理和 DNS，设置 `TMPDIR=/tmp`、`TERM`、locale、`BROWSER=/usr/local/bin/xdg-open`，并绑定 `/sdcard`、`/storage`、`/mnt`、`/data` 等 Android 路径。

## 设备观察（版本和路径可能变化）

以下内容是跨设备的工作提示，不是能力保证；开始任务时仍要用 `command -v`、`pwd` 和实际命令结果复核。设备探测结果优先于本文件。

- Android 版本、内核和架构会随设备变化；容器通常是 Ubuntu 24.04 arm64。先用 `uname -a`、`getprop ro.build.version.release` 和 `ancli doctor --json` 复核，不要把模板中的版本当成当前事实。`/root` 是容器 HOME，通常不是项目仓库，先进入目标项目再工作。
- `/data` 通常比共享存储更适合缓存和编译；`/sdcard` 及其底层挂载在不同 Android 版本上性能不同。临时文件、构建缓存和大文件优先放 `/data/local/tmp/build_cache` 或其他 `/data` 路径，最终用户文件再放共享存储。
- 启动前先确认工具是否存在。镜像可能提供 `bash`、`sh`、Python、git、Node.js/npm、Java、`strings`、`readelf`、`objdump`、`nm`、`protoc`、`zip`、`unzip`、`wget`、`curl` 和 apt；`rg`、`fd`、`jq`、`gh`、Docker、编辑器和其他工具不应默认假定存在。
- Python 包按当前 registry 和项目约束安装；优先使用 venv 或 AnCLI 的安装入口，不要假设 PEP 668、镜像或 apt 参数在每台设备都相同。大型分析依赖优先放 venv。
- 部分 aarch64 PRoot 上 `bash -c 'test -x <file>'` 可能因 `faccessat2` 映射问题误报，`sh` 的 `test -x` 也可能误报。因此不要把 `test -x` 当作可执行性证明，改用 `command -v`、Python `os.access(..., os.X_OK)`、直接调用程序，或显式 `java -cp`。从压缩包解出的启动文件需要先 `chmod +x`。
- APK 静态分析的建议目录是 `/workspace/apk`（解包）和 `/workspace/out`（报告/源码）。`agy`、`grok` 等工具在当前设备走 PRoot；不要假设它们能作为 Android 原生 ELF 直接执行。
- Android bionic 命令默认由宿主 wrapper 调用。若容器的 `PATH` 明确包含 `/system/bin` 且权限允许，`getprop`、`pm`、`dumpsys` 或 `cmd` 可能也能在容器内读取状态；先用 `command -v` 和实际命令结果复核。浏览器跳转仍由宿主桥接处理；不要在记忆文件中写入具体代理地址、账号、模型端点、cookie 或 token。

## 当前 Android 宿主探测规则

- 首次运行先执行 `ancli doctor --json`，再根据实际结果判断路径和命令能力。不要把旧设备记忆或模板中的能力描述当成当前事实。
- 当前 wrapper 明确绑定 `/system`、`/data`、`/sdcard`、`/storage`、`/mnt`、`/apex`、`/linkerconfig` 和 `/data/adb`。`/vendor` 可能只是 rootfs 空目录，`/product` 可能未绑定。使用前先用 `stat`、`mount` 或 `ancli doctor --json` 复核。
- 如果 `PATH` 中加入 `/system/bin` 后 `getprop`、`pm`、`dumpsys` 或 `cmd` 能运行，可以在容器内调用它们读取 Android 状态。调用失败时通过宿主桥接命令处理，不要循环猜测路径。
- 修改 Android 宿主文件时先列出目标、记录权限和 inode，再使用受支持的 `ancli android` 接口或明确的 dry-run 流程。不要把 `/vendor` 的 rootfs 空壳写入当成真机修改。

## APK 与 Android 工具链

- 解包放 `/workspace/apk/<名称>`，报告和反编译源码放 `/workspace/out/<名称>`；报告头部记录实际工具版本。
- 设备架构通常是 aarch64，但先用 `uname -m` 复核。Google SDK 中的 x86_64 `aapt2`、`d8`、`zipalign` 等不能直接执行；优先使用 apt 的本机架构包，或通过纯 Java 的 `d8.jar`、`apksigner.jar`、`ecj.jar` 调用。`android.jar` 与 aapt2 版本不兼容时，优先按项目记录选择较旧的 API stub。
- PRoot 下不要把 `dpkg --configure -a` 当成修复手段；系统服务 postinst 可能崩溃。安装依赖遵循 AnCLI registry 的命令和安全参数，必要时把 deb 下载、解包和验证放到 `/data/local/tmp/build_cache` 的隔离目录。
- jadx、androguard、protobuf、ELF 符号和 DEX 对比按需安装/调用；先 `command -v`，再把版本、输入路径和输出路径写进报告。

## dsh 与交互工具

- `dsh web` 使用命令输出的完整带 token URL；只想打印地址时使用 `dsh web --no-open`。token URL 属于当前会话入口，不要把完整地址写进 `AGENTS.md`、skill、脚本或提交记录，也不要把不带 token 的 `127.0.0.1:3080` 当作已认证地址。
- 从 `/` 启动交互式 Node/TUI 工具会触发全树扫描或路径解析问题。进入项目目录后再运行；如果调用的是 AnCLI wrapper，先确认它已按默认工作目录回退到 `/root` 或配置目录。
- 任何工具的配置、认证和环境变量都只记录变量名和文件位置，不复制值；需要变更时用 `ancli config` 或工具自己的配置命令。

## 工作指导

1. 开始任务前先运行 `pwd`、`ancli doctor --json`、`ancli status --json` 和 `ancli list --json`，确认当前目录、rootfs、应用数据通道和工具状态。
2. 把 `/root` 当作容器私有 HOME；把 `/sdcard`、`/storage/emulated/0` 当作手机共享存储；把 `/mnt/ancli-data/data/<包名>` 当作跨 App 读取真实 `/data` 的入口。
3. 从 `/` 启动工具时使用上面的默认工作目录；要修改它，运行 `ancli config --set default_cwd=/绝对路径`。留空会恢复 `/root`。
4. 使用 `ancli config <app_id>` 或管理器 WebUI 配置 API key、代理和 endpoint。不要把密钥写入 `AGENTS.md`、skills、wrapper、shell 历史或提交内容。
5. 查看官方版本时运行 `ancli check`；只读脚本或 WebUI 使用 `ancli list --json`。`installed_version` 必须来自工具自己的 `version_cmd`，不能手写成 AnCLI 版本。
6. 共享 Agent skills 放在 `{{SHARED_SKILL_DIR}}/<name>/SKILL.md`，运行 `ancli skills` 修复共享目录和各工具链接。多步骤流程写成 skill；稳定事实和约束留在本文件或项目自己的 `AGENTS.md`。
7. 不要写 Android 的真实 `/system/bin`，也不要手工覆盖 `/data/adb` 中的 wrapper。安装、升级或修复后运行 `ancli repair`，让 AnCLI 重新生成双注入 wrapper。
8. `dsh web` 使用命令输出的带 token URL；需要只打印地址时使用 `dsh web --no-open`。不要把没有 token 的 `127.0.0.1:3080` 书签地址当作已认证入口。
9. 报告验证结果时区分源码检查、容器内运行和真实 Android 设备验证；没有看到设备证据时，不要声称已经刷机、重启或完成长时间稳定性验证。
10. 默认把 `/data/data`、`/etc`、`/usr`、`/system` 和 `/data/adb` 视为高影响路径。没有明确目标和回滚方案时只读；需要写入时先在 `/data/local/tmp` 准备并使用 AnCLI 的受支持入口，不要自行改 SELinux、持久化挂载或杀掉其他进程。
11. Agent 和 Python、Node、git、curl 等工具运行在 Ubuntu PRoot 内；Android 的 `su`、`am` 和修改型系统操作属于宿主能力。`pm`、`dumpsys`、`getprop`、`cmd` 等读取命令可能通过 `/system/bin` 暴露到容器，但必须先探测其可用性，不要把容器内的同名路径自动当成宿主能力。
12. 容器可以通过绑定路径读写手机文件。共享存储使用 `/sdcard`，跨 App 的真实数据使用 `/mnt/ancli-data/data/<包名>`。涉及删除或批量修改时先列出目标并保存 dry-run 结果。
13. WebUI 的设置窗口与 `ancli config` 共用 `lang`、`default_cwd`、`proxy_mode` 和 `proxy_url`；修改全局配置优先使用这两个入口，不要另建配置文件。主题只属于 WebUI 本地偏好。
14. `dsh` 在 AnCLI registry 中使用直连模式；其他工具按 `proxy_mode` 使用系统探测、手动代理或直连。开始下载或更新前先阅读代理预检输出，确认实际网络路径。

## 常用命令

```sh
ancli status --json
ancli doctor --json
ancli list --json
ancli config --json
ancli config --set default_cwd=/sdcard/projects
ancli config --set proxy_mode=system
ancli config --set proxy_url=http://host:port
ancli agent-init
ancli skills
ancli repair
```

## 文件边界

- 工具安装和版本来源由 `registry.json` 定义；优先修改 registry 数据，不要为单个工具复制核心分支。
- wrapper 必须保持完整 Android bind、TTY 恢复、代理/DNS 继承和 `/mnt/ancli-data` 通道。
- `--json` stdout 必须只有 JSON；诊断写 stderr。
- 模块管理器卸载默认保留 rootfs、安装状态、密钥和配置；只有显式创建 `/data/local/tmp/ancli_force_purge` 才执行全清。
