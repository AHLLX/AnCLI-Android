# APK 静态分析工具与环境需求文档

> 编写背景：在对《Joyose_2.4.92》进行逆向分析时，发现当前环境（Termux/精简 Linux，无 Java、无 Android SDK 工具、无反编译工具链、pip 受限）严重限制了分析深度。本文档整理"需要什么工具、为什么、怎么验收"，供后续搭建标准 APK 分析环境。

---

## 1. 目标

搭建一套开箱即用的 APK 静态分析环境，输入任意 APK/解包目录，能自动产出：

1. Manifest 明文（含权限、组件、意图过滤器）
2. 资源反编译（res/ + resources.arsc + 字符串表）
3. Java/Kotlin **可读源码**（而非只有类名/字符串）
4. Native 库（.so）符号与反汇编
5. 模型文件（TFLite/ONNX）结构化解析
6. 配置文件（JSON/protobuf/自定义格式）解码
7. 网络端点、硬编码密钥、敏感字符串清单
8. （可选）自动化安全扫描报告

---

## 2. 本次分析实际遇到的问题（现象 → 根因 → 缺什么）

| # | 现象 | 根因 | 缺少的工具 |
|---|---|---|---|
| 1 | 二进制 Manifest 打不开 | 没有 `aapt`/`aapt2`，只能手写 Python 解析 AXML（浪费大量时间） | aapt2 / apktool |
| 2 | DEX 只能列出类名，看不到方法体 | 没有 Java 运行时 + jadx | **Java 17 JRE + jadx** |
| 3 | 方法名提取出现乱码 | 手写 DEX class_data 解析器（且踩了 method_id 结构/偏移的坑） | baksmali / jadx |
| 4 | `strings` 命令不存在 | 精简系统无 binutils | binutils（strings/readelf/objdump） |
| 5 | `xxd`/`hexdump` 不存在 | 同上 | xxd / busybox |
| 6 | .so 导出函数只能靠正则扫字符串 | 无 readelf/nm | binutils / rizin |
| 7 | .so 内部逻辑完全不可读 | 无反编译器 | Ghidra / rizin / IDA |
| 8 | GPU profile（protobuf）字段解析失败 | 无 `protoc` + proto 定义 | protoc + python protobuf |
| 9 | TFLite 模型手动解析 flatbuffer，多次踩 vtable/uoffset 坑 | 无现成模型工具 | ai-edge-litert / netron / flatc |
| 10 | `pip install androguard` 被拒 | PEP 668 externally-managed-environment | 用 venv 或 `--break-system-packages` |
| 11 | 无法确认"哪些字符串属于哪个类" | 只有全局字符串池，无类级关联 | jadx 反编译（源码级关联） |
| 12 | 无法验证运行时行为（云端是否真下发、perflock 是否真执行） | 纯静态分析 | Frida 动态 hook（可选） |

**核心结论**：缺的不是"某一个工具"，而是**一整套标准逆向工具链 + 包管理权限**。

---

## 3. 工具需求清单

### P0 —— 必需（下次分析就要，直接影响基础产出）

| 工具 | 用途 | 安装方式建议 |
|---|---|---|
| **Java 17 JRE** | 运行一切 Java 系工具 | `pkg install openjdk-17`（Termux）或 apt |
| **jadx** | DEX → Java 可读源码（当前最大痛点） | `pip install jadx` 或 GitHub release |
| **apktool** | APK 完整解包（含 smali、资源、Manifest 明文） | GitHub release / apt |
| **aapt2**（或 build-tools） | 官方 Manifest/资源解析 | Android SDK build-tools |
| **binutils** | `strings`、`readelf`、`objdump`、`nm` | `apt install binutils` |
| **androguard**（Python） | 程序化分析 Manifest/DEX/资源 | venv + `pip install androguard` |
| **protoc + python protobuf** | 解析 protobuf 配置（GPU profile 等） | `apt install protobuf-compiler` + pip |

### P1 —— 强烈建议（显著提升分析深度）

| 工具 | 用途 |
|---|---|
| **rizin / radare2** | .so 静态反汇编（命令行友好） |
| **Ghidra（headless）** | .so 反编译为类 C 伪代码（终极目标） |
| **baksmali / smali** | DEX 指令级查看（配合 jadx 交叉验证） |
| **Frida** | 动态 hook：验证云端下发、运行路径（需 root/模拟器） |
| **ai-edge-litert（tflite Python API）** | 直接加载/推理/打印模型 I/O，替代手写 flatbuffer 解析 |
| **flatc**（flatbuffers 编译器） | 通用 flatbuffer 解析 |

### P2 —— 可选（自动化/专项）

- **MobSF**：一键 APK 安全扫描（权限滥用、硬编码密钥、不安全的 IPC）
- **qark / mariana-trench**：源码级安全分析
- **apktool + unicode 资源打包**：改包重打包测试
- **apk-mitm**：抓包环境搭建
- **simplified DEX 调试器 / FART**：脱壳（针对加固包）
- **pandas/jupyter**：批量统计（如对比多个系统 APK）

---

## 4. 环境建议

1. **运行环境**：Linux（Termux 或容器均可），建议 ≥4GB 内存、≥10GB 空闲磁盘
2. **包管理**：务必解决 pip 权限（用 venv 建独立分析环境，避免破坏系统 Python）
3. **网络**：需能访问 GitHub / PyPI / Google（安装工具 + 拉取 aapt2 等）
4. **目录约定**：
   ```
   /workspace/apk/<名称>/        # 解包产物（apktool 输出）
   /workspace/out/<名称>/        # 分析报告、反编译源码、模型解析结果
   /workspace/tools/             # 固定版本工具（记录版本号，保证可复现）
   ```
5. **版本锁定**：每个工具记录版本（如 jadx 1.5.x），分析报告头部注明工具版本，保证结果可复现
6. **可选真机环境**：一台可 root 的小米手机或模拟器，用于 Frida 动态验证（云端配置是否真下发、perflock 是否生效）

---

## 5. 目标能力与验收标准

| 能力 | 输入 | 输出 | 验收标准 |
|---|---|---|---|
| Manifest 解码 | APK | 明文 XML | 与手写解析结果一致，且含 aapt 属性语义 |
| Java 反编译 | APK | src/*.java | 关键类（如 MiWLCManager）可读、可跳转、无乱码 |
| 资源反编译 | APK | res/ 可读文件 + strings.xml | 可看到应用名、字符串、布局 |
| Native 分析 | APK | .so 符号表 + 伪代码 | 能确认 predictJNI 调用链、ProfileManager 逻辑 |
| 模型解析 | .tflite | 结构图 + I/O 说明 | 与本次手写解析结果一致（31→22→22→22→1→sigmoid） |
| protobuf 解码 | .pb | 字段名+值 | 能读出 GraphicsProfilePrivate 的 GPU 频率档位 |
| 网络端点 | APK | 域名/IP 清单 | 覆盖 mcc.inf.miui.com 等已知端点 |
| 安全扫描（P2） | APK | 报告 | 输出权限滥用、敏感信息风险项 |

---

## 6. 推荐的完整工作流

```bash
# 1. 解包
apktool d app.apk -o /workspace/apk/app -f          # 资源+smali+明文Manifest
# 2. Java 源码
jadx -d /workspace/out/app/src app.apk               # 反编译可读源码
# 3. 官方资源确认
aapt2 dump badging app.apk                           # 包名/权限/组件速览
# 4. Native
readelf -sW lib/arm64-v8a/*.so | grep FUNC           # 导出函数
rizin -A -c 'aaa; s main; pdf' libxxx.so            # 反汇编关键函数
# 5. 模型
python3 -c "import ai_edge_litert as t; ..."         # 或 netron 可视化
# 6. protobuf
protoc --decode_raw < profile.gpu                    # 字段探测
# 7. 字符串/端点
strings classes.dex | grep -E "https?://"
# 8. 汇总 → 写报告（工具版本 + 证据标注）
```

---

## 7. 附录：本次会话的替代方案（可复用脚本）

| 脚本 | 作用 |
|---|---|
| `parse_manifest.py` | 二进制 AXML 解析（字符串池 + 元素/属性），支持 Android 12+ 16 字节节点头 |
| `parse_dex.py` | DEX 类清单（类名/父类/访问标志） |
| `dex_methods.py` | 按类名提取方法名（注意 method_id_item=12 字节、method_ids 表在 header +88/+92） |
| `dex_strings.py` | 字符串池关键词检索（用于网络端点/特征证据） |
| `tflite_parse.py`（本次为内联脚本） | flatbuffer 解析（uoffset 二次解引用 + vtable u16 + string=[u32 len][data]） |
| ELF 符号提取（内联） | 解析 .dynsym/.dynstr 拿 JNI 导出函数 |

> 教训记录（供后续环境补足后对照）：
> - AXML 节点头在 Android 12+ 为 16 字节（多了 extension 字段），属性偏移 = node + 16 + attributeStart
> - DEX method_id_item 为 12 字节；class_data_item 需按 uleb128 顺序解析
> - flatbuffer 的 uoffset 是"相对自身存储位置"，vtable 字段为 u16，字符串为 [u32 长度][数据]
