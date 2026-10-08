# 一個人的 DevSecOps — 系列文草稿

以本 repo 的實作為底，寫成可發佈的系列文章。
風格沿用既有的 Medium 系列：背景說明 → 編號章節 → 指令區塊 →
「畫面會輸出：」→ 刻意保留失敗狀態再排錯 → 小結 → 系列文連結。

## 篇目

| # | 檔案 | 標題 | 主題 |
|---|---|---|---|
| 1 | [01-overview.md](01-overview.md) | 一個人的 DevSecOps (1)：先畫地圖，再動手 | 架構心智模型、資源盤算、為什麼是這三個偵測來源 |
| 2 | [02-zero-trust-network.md](02-zero-trust-network.md) | 一個人的 DevSecOps (2)：零信任的地基 | Cilium CCNP、default deny、編號即順序 |
| 3 | [03-observability.md](03-observability.md) | 一個人的 DevSecOps (3)：看得見才管得住 | Alloy / Loki / Prometheus / Grafana Operator |
| 4 | [04-falco-operator.md](04-falco-operator.md) | 一個人的 DevSecOps (4)：主機層偵測 | falco-operator、規則遮蔽、OCI mirror |
| 5 | [05-k8s-audit.md](05-k8s-audit.md) | 一個人的 DevSecOps (5)：控制平面偵測 | audit policy、四個根因的追查 |
| 6 | [06-suricata.md](06-suricata.md) | 一個人的 DevSecOps (6)：網路層偵測 | Suricata chart、先量再調 |
| 7 | [07-wazuh-brain.md](07-wazuh-brain.md) | 一個人的 DevSecOps (7)：從事件到告警 | Wazuh 規則層、爆發收斂 |
| 8 | [08-alerting-and-fatigue.md](08-alerting-and-fatigue.md) | 一個人的 DevSecOps (8)：最後一哩與告警疲勞 | Grafana alerting、噪音治理、系列總結 |

## 貼到 Medium

**Medium 的編輯器不解析 Markdown** —— 直接貼 `.md` 會看到滿畫面的 `#` 與
`**`。它讀的是剪貼簿裡的 rich text，所以要先轉成 HTML：

```shell
./articles/to-medium.py              # 全部八篇
./articles/to-medium.py 06-suricata.md
```

產出在 `build/medium/*.html`（不進版控）。用瀏覽器開啟 → 全選 → 複製 →
貼進 Medium 的空白草稿。第一個大標題會成為文章標題。

**用貼上，不要用 import。** 實測 Medium 的 import 會把 `<pre>` 裡的換行
吃掉 —— 多行的程式碼區塊會擠成一行，後面還跟著一個空方塊。貼上走的是
另一條程式碼路徑，多行區塊會正確保留。

`docs/` 是同一份轉檔結果，由 GitHub Pages 公開在
<https://neildeng.github.io/solo-devsecops/>，方便直接開網頁全選複製。

**改完 Markdown 要記得重新產生 `docs/` 再 commit**，否則 Pages 上是舊的：

```shell
./articles/to-medium.py --out docs --index
```

轉檔時處理了四件 Medium 做不到的事：

| 問題 | 處理方式 |
|---|---|
| **不支援表格** | 轉成**單層**清單，一列一個項目。試過等寬程式碼區塊（換行被吃掉）與巢狀清單（Medium 壓平層級，而且每組結尾多一個空項目），都不行 |
| 相對路徑的圖片 | 改寫成 `raw.githubusercontent.com` 的絕對網址，貼上時 Medium 會自己抓回去 |
| 指向別篇的相對連結 | 改寫成 GitHub 網址（之後由 `series.yaml` 換成 Medium 連結） |
| 只有兩種標題大小 | H4 以下會被當內文，所以文章只用到 `##` 與 `###` |
| 程式碼區塊會自動偵測語言 | 拿掉 `<pre>` / `<code>` 的 class，實測 Medium 會把 shell 猜成 Perl |

貼完之後值得檢查的三處：**程式碼區塊有沒有被拆成好幾塊**、**圖片有沒有
真的上傳**（而不是只剩一行網址）、**清單的縮排層級對不對**。

## 發佈前要補的事

**一、畫面輸出的來源。** 文章裡的「畫面會輸出：」分成兩種，發佈前請自行確認：

- **實測擷取**：本系列大部分的數字與輸出來自實際執行（例如 Prometheus
  的 `15/29 → 39/39`、Suricata 的 `21147 行 / 13.6 MB`、OCI 的
  `148.4s → 0.0085s`、Wazuh 的 `133:1`）。這些可以直接用。
- **形狀示意**：少數標準輸出（helm 的 `Release ... does not exist`）是
  通用格式，但版本不同字樣會變。建議重跑一次貼上你自己的。

**二、圖片。** 原系列有大量 `Press enter or click to view image in full size`
的截圖。

已完成：

- 八篇的封面圖（1200×630），`images/covers/`。由 `make-covers.py` 依
  `series.yaml` 產生，編號、篇名、以及底部那排元件要亮哪幾個都在腳本裡。
  封面已插在每篇的 H1 之後 —— **Medium 取第一張圖當預覽圖**，所以順序不能換。
- 第 1 篇：[整體架構圖](images/01-architecture.png)（2960×2080）

圖檔放在 [`images/`](images/)。SVG 是原稿，要改內容就改 SVG，再跑
`./images/render.sh` 重新輸出 PNG（用 Chrome headless 轉檔，CJK 字型才不會
變成方框；輸出一律 2 倍圖，Medium 在高解析螢幕上才不會糊）。

封面要改標題或配色就改 `images/make-covers.py`，它會重新產生八張：

```shell
./articles/images/make-covers.py
```

發佈前建議再補上：

- 第 3 篇：Grafana 的 PacketDrop 儀表板
- 第 6 篇：Suricata IDS 儀表板
- 第 7 篇：Wazuh 的 MITRE ATT&CK 對應畫面
- 第 8 篇：Zulip 收到告警的樣子

**三、連結。** 不要手動改各篇結尾的「系列文：」區塊，也不要改 README 的
文章表格 —— 那兩處由腳本產生。發佈一篇就把 URL 填進 [`series.yaml`](series.yaml)，
然後執行：

```shell
./articles/update-series-links.sh
```

八篇文章與 README 會一次更新。url 留空的篇目連到 repo 裡的 Markdown，
填了就連到 Medium；每一篇在自己的清單裡顯示為粗體並標上「（本篇）」。

`--check` 只檢查不寫檔，有差異時回傳非 0，可以掛進 CI。

**四、原始碼。** 發佈時請在每篇結尾附上
<https://github.com/neildeng/solo-devsecops>。
