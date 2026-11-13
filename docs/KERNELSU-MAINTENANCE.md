# AnCLI KernelSU 官方目录维护

AnCLI 保留在自己的公开 GitHub 仓库中，KernelSU Modules 只读取仓库元数据和 Release，并把通过校验的版本同步到官方目录。首次提交需要人工审核；模块进入 `listed` 后，后续不可变 Release 会自动同步。

## 一次性配置

1. 在 GitHub 仓库中启用 `Settings → General → Releases → Enable release immutability`。
2. 在 [KernelSU Developer Portal](https://developers.kernelsu.org/) 的 **My repositories** 中安装并授权 KernelSU Modules App。
3. 确认根目录存在 `module.yaml`，并保持以下字段一致：

   ```yaml
   id: ancli
   name: AnCLI
   description: README.md
   icon: assets/ancli-logo.png
   ```

4. 在门户点击 **预检**，确认没有错误后再点击 **提交收录**，提交模块 ID `ancli`。

Release 不可变功能不会追溯修改旧版本；启用后要发布一个更高版本号的新 Release。AnCLI 当前版本使用 `v1.2.610` 和 `versionCode=22`。

## 发布新版本

在修改代码、模块元数据或运行时模板后，按下面顺序操作：

```bash
# 1. 在 CHANGELOG.md 顶部添加一个 ## Unreleased — ... 区块
python scripts/prepare_release.py v1.2.610 22

# 2. 本地构建，默认文件名为 ancli-22-v1.2.610.zip
python scripts/build_module.py

# 3. 基础检查
python -m py_compile src/ancli-core.py
python -m pytest tests/ -q
sh -n src/module/customize.sh
sh -n src/module/service.sh
sh -n src/module/uninstall.sh

# 4. 检查 ZIP 根目录
unzip -l ancli-22-v1.2.610.zip | head

# 5. 提交、打标签并推送；标签会触发 .github/workflows/kernelsu-release.yml
git add -A
git commit -m "release: v1.2.610 (versionCode 22)"
git tag -a v1.2.610 -m "v1.2.610"
git push origin main
git push origin v1.2.610
```

工作流会自动完成：

1. 从 `module.prop` 读取 `id`、`version` 和 `versionCode`；
2. 同步源码并构建模块 ZIP；
3. 从 `CHANGELOG.md` 只提取当前版本的 Release 正文；
4. 使用 KernelSU 官方 validator 做预检；
5. 创建 Draft Release、上传 ZIP、校验 SHA-256 后发布；
6. 检查 GitHub 返回的 `immutable=true`；
7. 输出官方资产名和摘要哈希。

官方工作流生成的资产名称为：

```text
ancli-<versionCode>-<version>.zip
```

例如：

```text
ancli-22-v1.2.610.zip
```

因此 `update.json` 的 `zipUrl` 必须指向这个完整文件名。`scripts/prepare_release.py` 会自动同步它。

## 首次收录后的维护

- **模块已经 `listed` 后**：不需要再次提交收录，发布新的不可变 Release 即可。
- **每次升级**：必须提高 `versionCode`；`version` 可以使用 `v1.2.608`、`v1.2.601` 这类三段版本号。
- **不可变 Release 出错**：不能替换 ZIP、删除资产后重新上传或复用 Tag。修复后提高 `versionCode`，创建新的 Tag 和 Release。
- **同步异常**：先看 GitHub 对应 Tag 提交上的 `KernelSU Modules` Check Run，再在门户点击 **Sync now**。不要反复重新提交同一个模块 ID。
- **元数据变更**：修改 `module.yaml`、README 或图标后，推送默认分支，再点击门户的 **Sync now**；不需要重新发布模块版本。
- **版本变更**：修改 `module.prop`、`update.json` 或 ZIP 内容时，必须走完整的新版本发布流程。
- **设备升级验证**：发布后先读取 Release 资产和 SHA-256，再在至少一台已 Root 设备上安装；验证 `module.prop`、`ancli --version`、WebUI、Agent wrapper、重启后命令和用户数据保留。

## 失败处理

| 现象 | 处理 |
| --- | --- |
| `release-not-immutable` | 检查 GitHub 是否真的开启 Release immutability；旧 Release 不会被追溯转换，发布更高版本。 |
| `release-exists` | Tag 已有已发布 Release，不能重跑同一个 Tag；提高版本并新建 Tag。 |
| `version-not-newer` | `versionCode` 没有高于历史最高值；提高 `versionCode`，不能复用旧值。 |
| `asset-pattern` | 确认 Release 只有一个匹配 `ancli-*.zip` 的 ZIP，且 `update.json` 使用官方生成的文件名。 |
| `module-id-mismatch` | 检查 `module.yaml` 的 `id`、ZIP 根目录 `module.prop` 的 `id` 和仓库登记 ID 都是 `ancli`。 |
| `description-not-found` | 检查 `module.yaml: description` 指向根目录存在的 Markdown 文件。 |
| `icon-not-found` | 检查 `module.yaml: icon` 指向仓库中存在的图片文件。 |

## 不要做的事

- 不要修改已经发布的不可变 Release；
- 不要移动或复用已使用过的 Tag；
- 不要降低 `versionCode`；
- 不要把完整历史 `CHANGELOG.md` 直接作为单个版本的 Release 正文；
- 不要让 `update.json` 指向不存在或旧版本 ZIP；
- 不要在模块升级时删除 `/data/local/tmp/ancli/rootfs`、`installed.json`、密钥和用户 Agent 配置。
