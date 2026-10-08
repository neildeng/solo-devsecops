#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""產生每一篇的封面圖（1200×630）。

為什麼要專用封面：Medium 的預覽圖是寬幅裁切，直式的架構圖會被切掉上下，
而且標題區還會壓在圖上。1200×630 是 Medium 與各家社群平台共用的比例，
照這個尺寸做就不會被裁。

篇名與編號讀自 series.yaml，所以改標題只要改那一份。

用法：
    ./articles/images/make-covers.py          # 產生 SVG 再轉 PNG
    ./articles/images/make-covers.py --svg    # 只產生 SVG
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import unicodedata

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "covers"
SERIES = HERE.parent / "series.yaml"

W, H = 1200, 630
PAD = 72

# 底部那排元件。每篇亮起自己負責的那幾個，其餘維持暗色 ——
# 讀者在列表上就能看出這一篇在整條鏈的哪個位置。
CHIPS = ["Cilium", "Falco", "K8s Audit", "Suricata", "Alloy", "Loki", "Wazuh", "Grafana"]
HIGHLIGHT = {
    "01-overview.md":             CHIPS,                        # 總覽：全亮
    "02-zero-trust-network.md":   ["Cilium"],
    "03-observability.md":        ["Alloy", "Loki", "Grafana"],
    "04-falco-operator.md":       ["Falco"],
    "05-k8s-audit.md":            ["K8s Audit"],
    "06-suricata.md":             ["Suricata"],
    "07-wazuh-brain.md":          ["Wazuh"],
    "08-alerting-and-fatigue.md": ["Grafana"],
}
ACCENT = {
    "01-overview.md": "#38bdf8", "02-zero-trust-network.md": "#a78bfa",
    "03-observability.md": "#34d399", "04-falco-operator.md": "#fbbf24",
    "05-k8s-audit.md": "#818cf8", "06-suricata.md": "#22d3ee",
    "07-wazuh-brain.md": "#fb7185", "08-alerting-and-fatigue.md": "#fb923c",
}


def text_px(s: str, size: float) -> float:
    """粗估顯示寬度。CJK 約一個字寬，拉丁字母約 0.55 倍。"""
    return sum(size if unicodedata.east_asian_width(c) in "WF" else size * 0.55 for c in s)


def read_series() -> list[dict]:
    """只用到 file / title / 編號，不想為此引入 YAML 相依，自己讀。"""
    items: list[dict] = []
    for line in SERIES.read_text().split("\n"):
        line = line.strip()
        if line.startswith("- file:"):
            items.append({"file": line.split(":", 1)[1].strip()})
        elif line.startswith("title:") and items:
            items[-1]["title"] = line.split(":", 1)[1].strip()
    return [i for i in items if "title" in i]


def cover(idx: int, item: dict) -> str:
    accent = ACCENT.get(item["file"], "#38bdf8")
    on = set(HIGHLIGHT.get(item["file"], []))

    # 底部元件列：先量寬度再置中
    size, cpad, gap = 22, 18, 14
    widths = [text_px(c, size) + cpad * 2 for c in CHIPS]
    total = sum(widths) + gap * (len(CHIPS) - 1)
    x = (W - total) / 2
    chips = []
    for c, cw in zip(CHIPS, widths):
        lit = c in on
        chips.append(
            f'<rect x="{x:.0f}" y="500" width="{cw:.0f}" height="44" rx="22" '
            f'fill="{accent if lit else "#1e293b"}" fill-opacity="{0.18 if lit else 1}" '
            f'stroke="{accent if lit else "#334155"}" stroke-width="1.5"/>'
            f'<text x="{x + cw / 2:.0f}" y="528" text-anchor="middle" font-size="{size}" '
            f'fill="{accent if lit else "#64748b"}" font-weight="{600 if lit else 400}">{c}</text>'
        )
        x += cw + gap

    title = item["title"]
    tsize = 76 if text_px(title, 1) <= 8 else 66

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}"
     font-family="-apple-system, BlinkMacSystemFont, 'PingFang TC', 'Noto Sans TC', 'Helvetica Neue', Arial, sans-serif">
  <rect width="{W}" height="{H}" fill="#0f172a"/>
  <rect x="0" y="0" width="{W}" height="6" fill="{accent}"/>

  <!-- 編號放右上角當水印：放左邊會和上面那行標籤疊在一起 -->
  <text x="{W - PAD}" y="232" font-size="160" font-weight="800" text-anchor="end"
        fill="{accent}" fill-opacity="0.16">{idx:02d}</text>

  <text x="{PAD}" y="152" font-size="26" fill="#94a3b8" letter-spacing="3">一個人的 DevSecOps</text>
  <text x="{PAD}" y="306" font-size="{tsize}" font-weight="700" fill="#f8fafc">{title}</text>

  <line x1="{PAD}" y1="376" x2="{W - PAD}" y2="376" stroke="#1e293b" stroke-width="2"/>
  <text x="{PAD}" y="424" font-size="25" fill="#94a3b8">三個偵測來源　·　兩條資料路徑　·　一個出口</text>
  <text x="{W - PAD}" y="424" font-size="22" fill="#475569" text-anchor="end">github.com/neildeng/solo-devsecops</text>

  {"".join(chips)}
</svg>
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", action="store_true", help="只產生 SVG，不轉 PNG")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    items = read_series()
    if not items:
        print("series.yaml 讀不到任何篇目", file=sys.stderr)
        return 1

    paths = []
    for i, item in enumerate(items, 1):
        p = OUT / (pathlib.Path(item["file"]).stem + "-cover.svg")
        p.write_text(cover(i, item))
        paths.append(p)
        print(f"{p.relative_to(HERE.parent.parent)}")

    if not args.svg:
        subprocess.run([str(HERE / "render.sh"), *map(str, paths)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
