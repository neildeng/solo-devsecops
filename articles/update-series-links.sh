#!/usr/bin/env bash
# 依 series.yaml 重新產生各篇文章的「系列文」區塊與 README 的文章表格。
#
# 只改 series.yaml，然後執行這支腳本。被改寫的範圍是兩個標記之間：
#   <!-- series:start -->  ...  <!-- series:end -->
# 標記是 HTML 註解，GitHub 與 Medium 都不會顯示。
#
# 用法：./articles/update-series-links.sh [--check]
#   --check  只檢查有沒有過時的區塊，不寫檔（給 CI 用，有差異時回傳非 0）
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
command -v yq >/dev/null || { echo "需要 yq" >&2; exit 1; }

CHECK=0
[[ "${1:-}" == "--check" ]] && CHECK=1

python3 - "$DIR" "$CHECK" <<'PY'
import re, subprocess, sys, pathlib

dir_ = pathlib.Path(sys.argv[1])
check_only = sys.argv[2] == "1"
root = dir_.parent

def yq(expr):
    return subprocess.run(["yq", "-r", expr, str(dir_ / "series.yaml")],
                          capture_output=True, text=True, check=True).stdout.strip()

series = yq(".series")
n = int(yq(".articles | length"))
items = []
for i in range(n):
    items.append({
        "file":  yq(f".articles[{i}].file"),
        "title": yq(f".articles[{i}].title"),
        "url":   yq(f".articles[{i}].url") or "",
    })

START, END = "<!-- series:start -->", "<!-- series:end -->"


def replace_block(path: pathlib.Path, body: str) -> bool:
    """把標記之間的內容換成 body。回傳是否有變動。"""
    text = path.read_text()
    block = f"{START}\n{body}\n{END}"
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        print(f"  ✗ {path.relative_to(root)} 找不到 series 標記", file=sys.stderr)
        return False
    new = pattern.sub(lambda _: block, text)
    if new == text:
        return False
    if not check_only:
        path.write_text(new)
    return True


changed = []

# ---- 各篇文章的「系列文」清單 ----
for cur in items:
    lines = ["系列文：", ""]
    for idx, a in enumerate(items, 1):
        label = f'{series} ({idx})：{a["title"]}'
        if a["file"] == cur["file"]:
            lines.append(f"- **{label}（本篇）**")
        elif a["url"]:
            lines.append(f'- [{label}]({a["url"]})')
        else:
            # 還沒發佈：連到 repo 裡的 Markdown
            lines.append(f'- [{label}]({a["file"]})')
    if replace_block(dir_ / cur["file"], "\n".join(lines)):
        changed.append(cur["file"])

# ---- README 的文章表格 ----
head = ["| # | 篇名 | 主題 | Medium |", "|---|---|---|---|"]
subjects = {
    "01-overview.md":            "架構心智模型、單機 kind 的資源陷阱",
    "02-zero-trust-network.md":  "Cilium CCNP、default deny、四個不報錯的坑",
    "03-observability.md":       "Alloy / Loki / Prometheus / Grafana Operator",
    "04-falco-operator.md":      "falco-operator、規則遮蔽、OCI mirror",
    "05-k8s-audit.md":           "稽核政策、17 條規則不觸發的四個根因",
    "06-suricata.md":            "Suricata chart、先量再調",
    "07-wazuh-brain.md":         "Wazuh 規則層、爆發收斂",
    "08-alerting-and-fatigue.md":"Grafana alerting、噪音治理、總結",
}
rows = []
for idx, a in enumerate(items, 1):
    link = f'[已發佈]({a["url"]})' if a["url"] else "—"
    rows.append(f'| {idx} | [{a["title"]}](articles/{a["file"]}) | '
                f'{subjects.get(a["file"], "")} | {link} |')
if replace_block(root / "README.md", "\n".join(head + rows)):
    changed.append("README.md")

published = sum(1 for a in items if a["url"])
if check_only:
    if changed:
        print("以下檔案的系列連結與 series.yaml 不一致：", file=sys.stderr)
        for c in changed:
            print(f"  {c}", file=sys.stderr)
        sys.exit(1)
    print(f"系列連結與 series.yaml 一致（已發佈 {published}/{n} 篇）")
else:
    print(f"已更新 {len(changed)} 個檔案（已發佈 {published}/{n} 篇）")
    for c in changed:
        print(f"  {c}")
PY
