#!/system/bin/sh
# ============================================================
# AnCLI APK 分析基础环境（轻量）
# Java 17 JRE + pip（绕过 PEP 668 直接可用）+ binutils/protoc
# + jadx（DEX → Java 源码，最大痛点，一个 zip 即可）
# ============================================================
set -e
export DEBIAN_FRONTEND=noninteractive

echo "=============================================="
echo "  APK 分析基础环境安装（预计 1-3 分钟）"
echo "=============================================="

# ---------- 1. Java + 基础工具 ----------
echo "[1/3] apt: Java 17 + binutils + protoc + pip ..."
apt-get update -y -q
apt-get install -y -q --no-install-recommends \
    openjdk-17-jre-headless binutils protobuf-compiler \
    python3-pip python3-venv unzip zip wget ca-certificates file

# ---------- 2. pip 直接可用（绕过 PEP 668 + 清华源）----------
echo "[2/3] pip 配置（break-system-packages + 清华源）"
pip config set global.break-system-packages true 2>/dev/null || true
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null || true
echo "  pip 版本: $(pip --version 2>&1)"

# ---------- 3. jadx（DEX → Java 可读源码）----------
echo "[3/3] jadx"
JADX_URL=$(python3 - <<'PYEOF' 2>/dev/null || true
import json, urllib.request
req = urllib.request.Request("https://api.github.com/repos/skylot/jadx/releases/latest",
                             headers={"User-Agent": "ancli-apk-basics"})
try:
    rel = json.load(urllib.request.urlopen(req, timeout=20))
    for a in rel.get("assets", []):
        if a["name"].endswith(".zip") and "no-jre" not in a["name"]:
            print(a["browser_download_url"])
            break
except Exception:
    pass
PYEOF
)
if [ -n "$JADX_URL" ]; then
    wget -q "$JADX_URL" -O /tmp/jadx.zip
    mkdir -p /opt/apk-tools/jadx
    unzip -q -o /tmp/jadx.zip -d /opt/apk-tools/jadx
    ln -sf /opt/apk-tools/jadx/bin/jadx /usr/local/bin/jadx
    echo "  jadx: $(jadx --version 2>&1 | head -1)"
else
    echo "  [WARN] jadx 下载失败（网络？），Java 已就绪可稍后手动安装"
fi

# ---------- 环境状态入口 ----------
mkdir -p /workspace/apk /workspace/out /workspace/tools 2>/dev/null || true
cat > /usr/local/bin/apk-analyzer <<'EOF'
#!/bin/bash
echo "== AnCLI APK 分析基础环境 =="
echo "Java:     $(java -version 2>&1 | head -1)"
echo "pip:      $(pip --version 2>&1)"
echo "jadx:     $(jadx --version 2>&1 | head -1)"
echo "binutils: strings/readelf/objdump/nm"
echo "protoc:   $(protoc --version 2>&1)"
echo ""
echo "pip 安装包直接可用，例如:"
echo "  pip install androguard"
echo "  pip install ai-edge-litert"
echo "jadx 反编译:"
echo "  jadx -d /workspace/out/app/src app.apk"
echo "工作区: /workspace/apk(解包) /workspace/out(产物) /workspace/tools(固定版本工具)"
EOF
chmod 755 /usr/local/bin/apk-analyzer

echo ""
echo "=============================================="
echo "  [OK] 基础环境就绪！输入 apk-analyzer 查看状态"
echo "=============================================="
