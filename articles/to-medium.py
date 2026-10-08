#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""把文章轉成可以直接貼進 Medium 編輯器的 HTML。

Medium 的編輯器不解析 Markdown —— 它讀的是剪貼簿裡的 rich text。
所以流程是：產生 HTML → 瀏覽器開啟 → 全選複製 → 貼進 Medium。

轉換時處理四件 Medium 做不到或會壞掉的事：

1. **表格**。Medium 完全不支援表格，貼進去會散成一堆文字。一律轉成巢狀
   清單 —— 試過等寬對齊的程式碼區塊，但 Medium 的 import 會把 `<pre>`
   裡的換行吃掉，整個表格擠成一行。清單是原生元素，不會被動到。
2. **圖片**。相對路徑 Medium 讀不到，改寫成 raw.githubusercontent.com 的
   絕對網址，貼上時 Medium 會自己抓回去。
3. **指向別篇文章的相對連結**。改寫成 GitHub 上的網址。
4. **標題層級**。Medium 只有兩種標題大小，H4 以下會被當內文，所以不要用。

用法：
    ./articles/to-medium.py                 # 全部八篇
    ./articles/to-medium.py 06-suricata.md  # 指定幾篇
    ./articles/to-medium.py --width 72      # 調整表格的寬度上限警告
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import unicodedata

RAW = "https://raw.githubusercontent.com/neildeng/solo-devsecops/main/articles/"
BLOB = "https://github.com/neildeng/solo-devsecops/blob/main/articles/"

DIR = pathlib.Path(__file__).resolve().parent
ROOT = DIR.parent
DEFAULT_OUT = ROOT / "build" / "medium"


def width(s: str) -> int:
    """顯示寬度。CJK 與全形標點算兩欄。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, n: int) -> str:
    return s + " " * max(0, n - width(s))


def parse_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    def cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip().strip("|").split("|")]

    header, *rest = lines
    head = cells(header)
    body = [cells(r) for r in rest[1:]]          # rest[0] 是 |---|---| 分隔列
    cols = max([len(head)] + [len(r) for r in body])
    head += [""] * (cols - len(head))
    body = [r + [""] * (cols - len(r)) for r in body]
    return head, body


def table_to_pre(head: list[str], body: list[list[str]]) -> list[str]:
    """表格 → 等寬對齊的純文字。放進程式碼區塊，所以把行內的反引號去掉。"""
    # 程式碼區塊裡的 Markdown 標記不會被解析，留著只是雜訊
    strip = lambda s: s.replace("`", "").replace("**", "")
    head = [strip(c) for c in head]
    body = [[strip(c) for c in r] for r in body]
    cols = len(head)

    w = [max([width(head[i])] + [width(r[i]) for r in body]) for i in range(cols)]
    sep = "─" * (sum(w) + 2 * (cols - 1))
    out = ["  ".join(pad(head[i], w[i]) for i in range(cols)).rstrip(), sep]
    out += ["  ".join(pad(r[i], w[i]) for i in range(cols)).rstrip() for r in body]
    return out


def table_to_list(head: list[str], body: list[list[str]]) -> list[str]:
    """表格 → 單層清單。

    刻意不用巢狀：Medium 會把巢狀清單壓平，而且在每一組的結尾多生一個
    空項目。所以一列一個項目，第一欄當標題，其餘欄位以「欄名：值」
    串在同一行 —— 文字會自動換行，不像程式碼區塊會被截掉。
    """
    out: list[str] = []
    for row in body:
        lead = row[0] or "—"
        rest = [f"{head[i]}：{row[i]}" if head[i] else row[i]
                for i in range(1, len(row)) if row[i]]
        if not rest:
            out.append(f"- **{lead}**")
        elif len(rest) == 1 and not head[1]:
            out.append(f"- **{lead}** — {rest[0]}")
        elif len(rest) == 1:
            out.append(f"- **{lead}** — {row[1]}")
        else:
            out.append(f"- **{lead}** — " + "；".join(rest))
    return out


def flatten_nested_blocks(md: str) -> tuple[str, int]:
    """把縮排在清單項目裡的程式碼區塊拉到最外層。

    Medium 不支援清單項目裡再放區塊元素 —— 實測縮排的程式碼區塊會變成一個
    空方塊，內容掉到清單外面，而且清單的編號會斷掉另起一個。
    GitHub 上巢狀是正常的，所以不改原始碼，在這裡攤平。

    攤平後編號改以粗體保留（**1.** …），讀起來一樣，但每一塊都是頂層元素。
    """
    lines = md.split("\n")
    out: list[str] = []
    i, n, flattened = 0, len(lines), 0

    while i < n:
        line = lines[i]
        if line.startswith("```"):                    # 頂層區塊原樣帶過
            out.append(line)
            i += 1
            while i < n and not lines[i].startswith("```"):
                out.append(lines[i]); i += 1
            if i < n:
                out.append(lines[i]); i += 1
            continue

        m = re.match(r"^(\d+)\.\s+(.*)$|^([-*])\s+(.*)$", line)
        if not m:
            out.append(line); i += 1
            continue

        # 收集這個清單區塊（項目行 + 其縮排的後續行）
        block, j = [line], i + 1
        while j < n and (lines[j].startswith((" ", "\t")) or not lines[j].strip()
                         or re.match(r"^(\d+\.|[-*])\s", lines[j])):
            if not lines[j].strip() and j + 1 < n and not lines[j + 1].startswith((" ", "\t")) \
               and not re.match(r"^(\d+\.|[-*])\s", lines[j + 1]):
                break
            block.append(lines[j]); j += 1

        if not any(re.match(r"^\s+```", b) for b in block):
            out.extend(block); i = j
            continue

        flattened += 1
        # 退幾格要看實際縮排，不能寫死 —— 多退一格會把區塊內部的層級吃掉
        # （YAML 的兩格縮排被當成清單縮排的一部分）
        indent = min(len(b) - len(b.lstrip(" "))
                     for b in block if re.match(r"^\s+```", b))
        for b in block:
            mk = re.match(r"^(\d+)\.\s+(.*)$", b)
            if mk:
                out.extend(["", f"**{mk.group(1)}.** {mk.group(2)}"])
                continue
            mb = re.match(r"^[-*]\s+(.*)$", b)
            if mb:
                out.extend(["", f"**·** {mb.group(1)}"])
                continue
            ded = b[indent:] if b[:indent].strip() == "" else b.lstrip(" ")
            if ded.startswith("```"):
                out.extend(["", ded])
            else:
                out.append(ded)
        out.append("")
        i = j

    return "\n".join(out), flattened


def preprocess(md: str, limit: int, name: str, tables: str) -> tuple[str, list[int]]:
    """把表格換成程式碼區塊，並改寫相對連結。回傳 (內容, 過寬的表格寬度清單)。"""
    md, flattened = flatten_nested_blocks(md)
    if flattened:
        print(f"  · 攤平 {flattened} 組含程式碼區塊的清單")

    out: list[str] = []
    buf: list[str] = []
    wide: list[int] = []
    in_code = False

    def flush() -> None:
        if not buf:
            return
        head, body = parse_table(buf)
        if tables == "list":
            out.extend([*table_to_list(head, body), ""])
        else:
            rendered = table_to_pre(head, body)
            w = max(width(line) for line in rendered)
            if w > limit:
                wide.append(w)
                out.extend([*table_to_list(head, body), ""])
            else:
                out.extend(["```text", *rendered, "```", ""])
        buf.clear()

    for line in md.split("\n"):
        if line.startswith("```"):
            flush()
            in_code = not in_code
            out.append(line)
            continue
        if not in_code and line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            buf.append(line)
            continue
        flush()
        out.append(line)
    flush()

    text = "\n".join(out)
    # 圖片：相對路徑 → raw 網址
    text = re.sub(r"!\[([^\]]*)\]\((?!https?:)([^)]+)\)", rf"![\1]({RAW}\2)", text)
    # 指向別篇文章的相對連結 → GitHub 網址
    text = re.sub(r"(?<!!)\[([^\]]+)\]\((?!https?:|#)([^)]+\.md)\)", rf"[\1]({BLOB}\2)", text)
    return text, wide


CSS = """
body { max-width: 46em; margin: 3em auto; padding: 0 1.5em;
       font-family: -apple-system, "PingFang TC", "Noto Sans TC", sans-serif;
       font-size: 18px; line-height: 1.8; color: #111; }
h1 { font-size: 2em; } h2 { font-size: 1.5em; margin-top: 2em; } h3 { font-size: 1.2em; }
pre { background: #f6f8fa; padding: 1em; border-radius: 6px; overflow-x: auto;
      font-size: 14px; line-height: 1.5; }
code { font-family: "SF Mono", Menlo, monospace; }
blockquote { border-left: 3px solid #ccc; margin-left: 0; padding-left: 1em; color: #444; }
img { max-width: 100%; }
hr { border: none; border-top: 1px solid #ddd; margin: 2.5em 0; }
"""


def convert(path: pathlib.Path, limit: int, out: pathlib.Path, tables: str) -> None:
    md, wide = preprocess(path.read_text(), limit, path.name, tables)
    out.mkdir(parents=True, exist_ok=True)
    dst = out / (path.stem + ".html")

    # 檔名不以底線開頭：GitHub Pages 的 Jekyll 會略過 _ 開頭的檔案
    (out / "style.css").write_text(CSS)
    # 只給 pagetitle 而不給 title：pandoc 的 title 會另外產生一個 <header><h1>，
    # 與文章自己的 H1 重複，而 Medium 的 import 會拿第一個當標題（變成檔名）
    title = next((l[2:].strip() for l in md.split("\n") if l.startswith("# ")), path.stem)
    subprocess.run(
        # --syntax-highlighting=none 不能省：pandoc 的語法高亮會把程式碼包進
        # <div class="sourceCode">，Medium 把那個 div 渲染成一個空方塊，
        # 而且它替每一行塞的錨點 <a href="#cb5-1"> 也是多餘的。
        # Medium 自己會做語法高亮，不需要 pandoc 先做一次。
        ["pandoc", "--from", "gfm", "--to", "html5", "--standalone",
         "--syntax-highlighting=none", "-V", f"pagetitle={title}",
         "--css", "style.css", "-o", str(dst)],
        input=md, text=True, check=True,
    )
    # 拿掉 class：Medium 會據此自動偵測語言，實測把 shell 猜成 Perl
    html = dst.read_text()
    html = re.sub(r'<pre class="[^"]*">', "<pre>", html)
    html = re.sub(r'<code class="[^"]*">', "<code>", html)
    dst.write_text(html)

    note = f"  · {len(wide)} 個寬表格改用清單（最寬 {max(wide)} 欄）" if wide else ""
    print(f"{dst.relative_to(ROOT)}{note}")


def write_index(out: pathlib.Path, files: list[pathlib.Path]) -> None:
    """系列的目錄頁。這是公開頁面，不要寫轉檔流程之類的內部事項。"""
    rows = []
    for f in files:
        title = next((l[2:].strip() for l in f.read_text().split("\n")
                      if l.startswith("# ")), f.stem)
        # 清單自己有編號，把標題前面的「一個人的 DevSecOps (N)：」拿掉
        title = re.sub(r"^.*?\(\d+\)：", "", title)
        rows.append(f'<li><a href="{f.stem}.html">{title}</a></li>')

    (out / "index.html").write_text(
        '<!doctype html>\n<html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>一個人的 DevSecOps</title>"
        f"<style>{CSS}</style></head><body>\n"
        "<h1>一個人的 DevSecOps</h1>\n"
        "<p>在一台筆電上，用 kind 搭出一套完整的 Kubernetes 偵測與告警鏈，"
        "並且把每一個接點的理由寫清楚。</p>\n"
        "<p>「一個人」不是修飾語，是整套東西的設計前提。它不是企業級 SOC 的縮小版"
        " —— 企業級的那套預設有人輪值、有人調規則、有人在告警進來的時候看一眼。"
        "這裡沒有，所以每個決定都要先過一關：這個東西，一個人維護得下去嗎？</p>\n"
        "<p>不是「照著貼就會動」的教學，是「為什麼要這樣接、不這樣接會怎樣」的紀錄。</p>\n"
        '<p><img src="https://raw.githubusercontent.com/neildeng/solo-devsecops/'
        'main/articles/images/01-architecture.png" alt="整體架構"></p>\n'
        "<h2>系列文</h2>\n"
        "<ol>\n" + "\n".join(rows) + "\n</ol>\n"
        "<hr>\n"
        "<p>所有設定與原始碼："
        '<a href="https://github.com/neildeng/solo-devsecops">'
        "github.com/neildeng/solo-devsecops</a></p>\n"
        "</body></html>\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="要轉的 .md，省略則全部八篇")
    ap.add_argument("--tables", choices=["list", "pre"], default="list",
                    help="表格轉成什麼（預設 list；pre 會用等寬程式碼區塊，"
                         "Medium 的 import 會把換行吃掉，只在手動貼上時堪用）")
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT,
                    help="輸出目錄（預設 build/medium；要發佈到 GitHub Pages 就指定 docs）")
    ap.add_argument("--index", action="store_true",
                    help="另外產生 index.html 目錄頁，給 GitHub Pages 用")
    ap.add_argument("--width", type=int, default=76,
                    help="表格寬度上限，超過改用清單（預設 76，約為 Medium 程式碼區塊不捲動的寬度）")
    args = ap.parse_args()

    if not shutil.which("pandoc"):
        print("需要 pandoc：brew install pandoc", file=sys.stderr)
        return 1

    out = args.out if args.out.is_absolute() else ROOT / args.out
    files = [DIR / f for f in args.files] if args.files else sorted(DIR.glob("0*.md"))
    for f in files:
        if not f.exists():
            print(f"找不到 {f}", file=sys.stderr)
            return 1
        convert(f, args.width, out, args.tables)

    if args.index:
        write_index(out, files)
        # 不要讓 Jekyll 處理，否則底線開頭的檔案與部分路徑會被吃掉
        (out / ".nojekyll").write_text("")
        print(f"{(out / 'index.html').relative_to(ROOT)}")

    print(f"\n產出在 {out}/")
    print("貼進 Medium 的方式：瀏覽器開啟 .html → 全選 → 複製 → 貼到空白草稿。")
    print("或用 Medium 的 import：https://medium.com/p/import 貼上該頁的公開網址。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
