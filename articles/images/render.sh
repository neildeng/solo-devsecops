#!/usr/bin/env bash
# 把 SVG 原稿轉成可貼進 Medium 的 PNG。
#
# 用 Chrome headless 而不是 rsvg-convert / cairosvg：那些工具對 CJK 字型的
# 處理不穩，常常出現方框或改用錯誤的字重。Chrome 走系統字型堆疊，
# PingFang TC 會正確渲染。
#
# 用法：./render.sh [檔名...]    省略時轉換目錄下所有 .svg
set -euo pipefail

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DIR="$(cd "$(dirname "$0")" && pwd)"
[[ -x "$CHROME" ]] || { echo "找不到 Chrome：$CHROME" >&2; exit 1; }

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
    targets=("$DIR"/*.svg)
fi

for svg in "${targets[@]}"; do
    svg="$(cd "$(dirname "$svg")" && pwd)/$(basename "$svg")"   # 轉絕對路徑
    svgdir="$(dirname "$svg")"                                   # 產物放在來源旁邊
    base="$(basename "$svg" .svg)"
    # 從 SVG 自己的 width/height 取畫布尺寸，不要在兩個地方各寫一次
    w="$(sed -n 's/.*[^-]width="\([0-9]*\)".*/\1/p' "$svg" | head -1)"
    h="$(sed -n 's/.*[^-]height="\([0-9]*\)".*/\1/p' "$svg" | head -1)"

    cat > "$svgdir/_shot.html" <<EOF
<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;padding:0;background:#fff}img{display:block;width:${w}px;height:${h}px}</style>
</head><body><img src="$(basename "$svg")"></body></html>
EOF

    # force-device-scale-factor=2 產生 2 倍圖，Medium 在高解析螢幕上才不會糊
    "$CHROME" --headless --disable-gpu --hide-scrollbars \
        --force-device-scale-factor=2 --window-size="$w,$h" \
        --default-background-color=FFFFFFFF \
        --screenshot="$svgdir/$base.png" "file://$svgdir/_shot.html" 2>/dev/null

    rm -f "$svgdir/_shot.html"
    echo "$base.png  ($((w*2))x$((h*2)))"
done
