# AnCLI v1.0.1 — 技术重构与排障自愈复盘总结

本项目已于本轮迭代中完成了向官方正式发行版 `v1.0.0` 和具有强大排障自愈机制的 `v1.0.1`（VersionCode 2）的深度演进。以下是所有核心修改与技术细节的深度复盘：

---

## 1. 核心包管理器更新 (`ancli-core.py`)

### 🌐 容器网络与 DNS 隔离自愈（攻克 Go 语言 bind 闪退）
* **Clash/Tun 污染网卡欺骗自愈**：在包装器（Wrapper）启动时，自动在宿主机执行网卡虚拟化循环，将 Clash 潜在的 IP 段（`198.18.0.10` 到 `198.18.0.25`）物理绑定到本地环回设备 `lo` 上：
  ```bash
  for i in $(seq 10 25); do ip addr add 198.18.0.$i/32 dev lo 2>/dev/null || true; done
  ```
  这彻底欺骗并满足了 Go 语言运行时（如 `agy`）在初始化 Socket 监听 `localhost` 时对历史残留 IP 的遍历绑定需求，消除了 `bind: cannot assign requested address` 的致命闪退。
* **纯 Go DNS 解析器强制开启**：在 Wrapper 脚本中强行导出环境变量 `export GODEBUG=netdns=go`，迫使 Go 运行时无视 Android 宿主系统的 `dnsproxyd` 劫持，严格读取容器内 hosts 文件。
* **Hosts 环回重定向**：在 Wrapper 启动参数中，增加 `-b {ANCLI_DIR}/hosts:/etc/hosts` 挂载，隔离宿主机 hosts 污染。

### 📁 容器文件系统所有权自愈
* **免 Root UID 冲突解锁**：在环境自愈函数 `repair_env` 以及数据库写入 `save_installed` 中，加入自动将 `/root/.config`、`.claude`、`.gemini` 等配置目录的所有权 `chown` 重置为宿主普通用户 `shell:shell`，并将权限设为 `777` 的逻辑。彻底避免了多用户交叉运行导致凭证被 root 锁死出现 `Permission denied` 的崩溃。
* **健壮性容错（Crash-Proof）**：将包装器写入 Magisk systemless 目录时的 `os.makedirs` 调用移入 `try...except` 异常捕获块内，保证即便该目录受宿主机 SELinux 策略限制无法修改，也能优雅跳过并不崩溃，确保 KSU/APatch 包装器成功写入。

### 📂 动态 CWD 工作目录透传
* **解决 mimo 等 Node 智能体扫描挂起**：将 Wrapper 中的固定 `-w /root` 替换为动态工作目录透传 **`-b "$PWD" -w "$PWD"`**。使容器工作目录与宿主机当前 `cd` 所在的开发目录 1:1 直通映射，彻底消除了 mimo 启动时因强制扫描容器根目录数万系统文件导致的 CPU 吃满、无限卡死挂起。

---

## 2. 卸载防丢与生命周期重构 (`uninstall.sh` & 模块 `uninstall.sh`)

* **TTY 交互与静默模式智能判定**：在卸载脚本中合入 TTY 自动检测逻辑。
* **升级/重装静默数据保留**：当在 Magisk / KernelSU 管理器中点击“卸载”时（静默非交互执行），**默认只清理宿主侧的快捷 wrappers 和挂载节点，100% 安全保留 `/data/local/tmp/ancli/` 下的整个 Ubuntu 容器系统、所有已装 pip/python 库（如 `gitpython`）和配置数据库**。避免升级模块时数据丢失，重装后秒恢复。
* **强制清除覆盖**：若需彻底格式化，只需在卸载前于宿主终端执行 `touch /data/local/tmp/ancli_force_purge`，即可在静默卸载时全盘物理抹除。

---

## 3. 发行版版本号与云端 releases 净化

* **OTA 版本对齐**：统一将主程序版本升级为 `1.0.1`，Magisk 模块 versionCode 递增为 `2`，更新 `module.prop` 与 `update.json` 属性配置。
* **GitHub 历史脏数据净化**：
  * 通过 GitHub CLI 大批量清除了调试期间产生的 `v1.1.0` ~ `v1.3.2` 共 12 个冗余 Release 及 Git Tags 记录。
  * 将 `v1.0.0` 和 `v1.0.1` 发行版的标题后缀去除了 `for Android` 字样，项目整体面貌归于清爽、标准、极具工程专业感。
* **定位明确化**：修改了 `README`、`README.zh`、`CHANGELOG`、`ARCHITECTURE`、`COMPATIBILITY` 和 `AGENTS` 规范，将程序定位统一明确为“Root 安卓设备下的统一免修改（Systemless）命令行环境管理器与插件式安装器”。

---

## 4. 终极自愈运行指南（绕过宿主 noexec 与 adb 权限锁）

由于现代 Android 系统对 `/data/local/tmp` 目录强加了 `noexec` 禁止运行属性，且 `/data/adb` 目录在开机后处于只读挂载状态：
我们已将最新的自愈包装器安全写入了手机的 `/data/local/tmp/ancli/bin/` 下。**你只需直接用 `sh` 解释器读取该文本流**，即可 100% 绕过 `noexec` 拦截：

* **运行 agy**：`sh /data/local/tmp/ancli/bin/agy`
* **运行 mimo**：`sh /data/local/tmp/ancli/bin/mimo`
* **运行 claude**：`sh /data/local/tmp/ancli/bin/claude`（已彻底解决 Auditor SSH 客户端缓存 UID 冲突报错）
* **运行 opencode**：`sh /data/local/tmp/ancli/bin/opencode`
