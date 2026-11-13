# AnCLI 当前接手索引

本文是仓库级快速索引，描述当前 `main` 分支的实现边界。旧版 v1.0.x/v1.1.x 的排障复盘已经不再作为运行说明；历史细节请查 [`CHANGELOG.md`](CHANGELOG.md)。

## 当前基线

- 当前仓库版本：`v1.2.609`，模块 `versionCode=21`；KernelSU 官方目录维护见 [`docs/KERNELSU-MAINTENANCE.md`](docs/KERNELSU-MAINTENANCE.md)。
- 运行后端：Android 宿主 wrapper → 单层 PRoot → Ubuntu 24.04 arm64 glibc rootfs。
- 核心：[`src/ancli-core.py`](src/ancli-core.py)，只使用 Python 标准库。
- registry：[`src/registry.json`](src/registry.json)，安装、卸载、版本探测和官方 latest 来源都由数据定义。
- 模块安装入口：[`src/module/customize.sh`](src/module/customize.sh)。
- 宿主环境桥接：[`src/module/ancli_env.sh`](src/module/ancli_env.sh)。
- WebUI：[`src/module/webroot/index.html`](src/module/webroot/index.html)。

WebUI 设置窗口可视化编辑 `lang`、主题、`default_cwd`、`proxy_mode` 和 `proxy_url`，并显示
当前 PRoot 后端、Android 数据通道、生效目录和代理模式。主题保存在 WebUI 本地，其他四项写入
容器内 `/root/.ancli-config.json`。

## 路径和生命周期

| 内容 | 宿主 Android 视角路径 |
| :--- | :--- |
| Ubuntu rootfs | `/data/local/tmp/ancli/rootfs/` |
| AnCLI core | `/data/local/tmp/ancli/bin/ancli-core.py` |
| 安装状态 | `/data/local/tmp/ancli/installed.json` |
| 密钥 | `/data/local/tmp/ancli/secrets/<tool>.env`（0600） |
| 官方版本缓存 | `/data/local/tmp/ancli/.update_cache.json` |
| 默认配置 | rootfs 内 `/root/.ancli-config.json` |
| 共享 skills | rootfs 内 `/root/.agents/skills` |
| 默认运行时上下文模板 | `src/runtime-context/AGENTS.md`；首次探测后用 `ancli agent-init --finalize` 写回设备实测内容 |

刷入时 `customize.sh` 准备 rootfs、APT 基础工具、core、registry、环境脚本和 Agent 记忆模板。安装工具时，core 在主 PRoot 中直接执行 registry 命令，生成 wrapper，记录安装状态并探测工具自身版本。模块管理器卸载默认只清理宿主动态组件；只有 `/data/local/tmp/ancli_force_purge` 存在时才删除整个 AnCLI 数据目录。

## Wrapper 硬约束

- 不写真实 Android `/system/bin`；wrapper 写入模块 overlay、KSU/APatch 即时路径和 `/data/local/tmp/ancli/bin` 调试路径。
- 写 wrapper 前先删除旧 inode；KSU/APatch 路径从临时文件通过 `cp -f` 覆盖。
- 不嵌套 PRoot；完整绑定 `/dev`、`/proc`、`/sys`、`/sdcard`、`/storage`、`/mnt`、`/data`、`/apex`、`/linkerconfig`、`/system` 和 `/data/adb`。
- wrapper 不能 `exec` 或后台运行 PRoot：必须让 PRoot 保持前台以接收 TUI 按键，同时由 watchdog 监控启动终端并在父进程消失时结束 PRoot；还要保存并恢复 TTY，避免 TUI 退出后破坏调用者终端。
- 宿主侧维护 Android 代理、DNS、`/mnt/ancli-data` 和浏览器 URL 队列；容器默认不依赖 Android bionic 命令。若 `/system/bin` 已暴露，`getprop`、`pm`、`dumpsys`、`cmd` 可作为只读能力按需探测，`am` 仍由宿主桥接。

## 默认工作目录和 Agent 记忆

工具从 `/` 启动时使用 `/root`，也可以用以下命令指定绝对路径：

```sh
ancli config --set default_cwd=/sdcard/projects
ancli config --set default_cwd=
```

`ancli agent-init` 会在容器全局目录 `/root/.agents/AGENTS.md` 生成运行时上下文，内容包括当前 AnCLI 路径、代理/DNS/浏览器约定、工具状态、配置变量名、共享 skills 位置和安全边界；绝不写入变量值。`ancli repair`、`ancli skills` 和成功安装工具时会幂等补齐。项目目录中的用户 `AGENTS.md` 默认保留。

全局可配置键只有 `default_cwd`、`lang`、`proxy_mode` 和 `proxy_url`；用 `ancli config --json` 查看当前值与键名。工具级环境变量由 `registry.json` 声明，使用 `ancli config <app_id> --set KEY=VALUE` 设置。

```sh
ancli agent-init
ancli agent-init --json
ancli agent-init --refresh
```

## 更新检测和 JSON 边界

`ancli check` 分开处理两件事：

1. 用 registry 的 `version_cmd` 在容器内询问工具真实的已安装版本；
2. 用 registry 的 `latest` 规范查询官方版本并写入 `.update_cache.json`。

`list --json` 只读本地 registry 和缓存；`status --json`、`list --json`、`check --json`、`config --json` 和 `skills --json` 的 stdout 必须保持可解析 JSON，诊断输出走 stderr。

## 维护入口

- 只读状态：`ancli status --json`、`ancli list --json`。
- 路径和能力诊断：`ancli doctor --json`。
- 环境修复：`ancli repair`。
- 共享 skills：`ancli skills`，skill 内容放在 `/root/.agents/skills/<name>/SKILL.md`。
- 官方版本：`ancli check`。
- WebUI 本地预览：`node .webui-preview/build.js`，截图必须带 `--virtual-time-budget=4000`。
- 真机 hot-patch：使用 `scripts/device_deploy_helper.py`，从容器内写入 AnCLI 目录；不要用宿主 `su -c` 一行命令替代。

## 证据边界

没有连接真实设备时，只报告源码、离线单测、shell 语法检查和静态构建结果。不要把本地预览、短时命令检查或历史报告写成刷机、重启、长期稳定性、现场吞吐或移动端验收证据。

详细架构见 [`ARCHITECTURE.md`](ARCHITECTURE.md)，兼容性边界见 [`COMPATIBILITY.md`](COMPATIBILITY.md)，长期约束见 [`AGENTS.md`](AGENTS.md)。
