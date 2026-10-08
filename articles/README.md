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

- 第 1 篇：[整體架構圖](images/01-architecture.png)（2960×2080，直接貼進 Medium）

圖檔放在 [`images/`](images/)。SVG 是原稿，要改內容就改 SVG，再跑
`./images/render.sh` 重新輸出 PNG（用 Chrome headless 轉檔，CJK 字型才不會
變成方框；輸出一律 2 倍圖，Medium 在高解析螢幕上才不會糊）。

發佈前建議再補上：

- 第 3 篇：Grafana 的 PacketDrop 儀表板
- 第 6 篇：Suricata IDS 儀表板
- 第 7 篇：Wazuh 的 MITRE ATT&CK 對應畫面
- 第 8 篇：Zulip 收到告警的樣子

**三、連結。** 「系列文：」區塊的 URL 要在發佈後回填。

**四、原始碼。** 發佈時請在每篇結尾附上
<https://github.com/neildeng/solo-devsecops>。
