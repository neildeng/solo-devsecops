#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""把文章轉成可以直接貼進 Medium 編輯器的 HTML。

Medium 的編輯器不解析 Markdown —— 它讀的是剪貼簿裡的 rich text。
所以流程是：產生 HTML → 瀏覽器開啟 → 全選複製 → 貼進 Medium。

轉換時處理四件 Medium 做不到或會壞掉的事：

1. **表格**。Medium 完全不支援表格，貼進去會散成一堆文字。窄的轉成等寬
   對齊的程式碼區塊（中日韓字元算兩欄寬，對齊才不會跑掉）；超過寬度上限的
   改用巢狀清單 —— Medium 的程式碼區塊不換行，寬表格會被截掉看不到。
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
OUT = DIR.parent / "build" / "medium"


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
    """太寬的表格 → 巢狀清單。Medium 支援一層縮排，而且不會水平捲動。

    第一欄當項目標題，其餘欄位以「欄名：值」列在下面；兩欄的表格直接併成一行。
    """
    out: list[str] = []
    for row in body:
        lead = row[0] or "—"
        if len(row) == 2:
            out.append(f"- **{lead}** — {row[1]}")
            continue
        out.append(f"- **{lead}**")
        for i in range(1, len(row)):
            if row[i]:
                label = head[i] or f"欄 {i + 1}"
                out.append(f"    - {label}：{row[i]}")
    return out


def preprocess(md: str, limit: int, name: str) -> tuple[str, list[int]]:
    """把表格換成程式碼區塊，並改寫相對連結。回傳 (內容, 過寬的表格寬度清單)。"""
    out: list[str] = []
    buf: list[str] = []
    wide: list[int] = []
    in_code = False

    def flush() -> None:
        if not buf:
            return
        head, body = parse_table(buf)
        rendered = table_to_pre(head, body)
        w = max(width(line) for line in rendered)
        if w > limit:
            # 超過寬度就改用清單：Medium 的程式碼區塊不換行，寬表格會被截掉
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


def convert(path: pathlib.Path, limit: int) -> None:
    md, wide = preprocess(path.read_text(), limit, path.name)
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / (path.stem + ".html")

    css = OUT / "_style.css"
    css.write_text(CSS)
    subprocess.run(
        ["pandoc", "--from", "gfm", "--to", "html5", "--standalone",
         "--metadata", f"title={path.stem}", "--css", "_style.css", "-o", str(dst)],
        input=md, text=True, check=True,
    )
    note = f"  · {len(wide)} 個寬表格改用清單（最寬 {max(wide)} 欄）" if wide else ""
    print(f"{dst.relative_to(OUT.parent.parent)}{note}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="要轉的 .md，省略則全部八篇")
    ap.add_argument("--width", type=int, default=76,
                    help="表格寬度上限，超過改用清單（預設 76，約為 Medium 程式碼區塊不捲動的寬度）")
    args = ap.parse_args()

    if not shutil.which("pandoc"):
        print("需要 pandoc：brew install pandoc", file=sys.stderr)
        return 1

    files = [DIR / f for f in args.files] if args.files else sorted(DIR.glob("0*.md"))
    for f in files:
        if not f.exists():
            print(f"找不到 {f}", file=sys.stderr)
            return 1
        convert(f, args.width)

    print(f"\n產出在 {OUT}/")
    print("貼進 Medium 的方式：瀏覽器開啟 .html → 全選 → 複製 → 貼到空白草稿。")
    print("第一個大標題會成為 Medium 的文章標題。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
