# 一個人的 DevSecOps (8)：最後一哩與告警疲勞

## 背景說明

偵測有了、判斷有了，剩下最後一哩：**把告警送到你會看到的地方。**

這一哩看起來最簡單，實際上是最容易讓前面七篇白做的一哩。原因很簡單：
一個被洗版的頻道等於一個被靜音的頻道，而被靜音的頻道等於沒有告警系統。

本文做三件事：

1. 把 Wazuh 的判斷結果經 Grafana 送出去，以及為什麼繞這一圈
2. 拆解這條鏈上**四個靜默失敗點**
3. 用實測數字談告警疲勞：**每分鐘 300 則 → 只剩真實事件**

然後收尾整個系列。

## 環境必須

1. 已完成第 7 篇（Wazuh 規則層）
2. 一個通知目的地。本系列用 Zulip（聊天室）+ Mailpit（本機 SMTP）

## 1. 為什麼不用 Wazuh 原生的 integration

最直覺的做法是讓 Wazuh 自己發通知——它有 `<integration>` 和
`<email_alerts>`。試了之後遇到兩個阻礙：

1. chart 沒有開 `integration` / `email` 的設定入口
2. Wazuh 的 `ossec.conf` **沒有 `conf.d` 機制**——要加任何一段，只能把整份
   293 行接管進 values 並**永久維護**

接管 293 行設定檔是一筆長期負債：上游每次改版你都要手動 diff。

這時候回頭問一個問題很有幫助：

> **「讓 Wazuh 當告警大腦」要的是什麼？**
> 是**判斷邏輯在 Wazuh**，不是**封包從 Wazuh 的網路介面出去**。

我一開始把這兩件事綁在一起，才會覺得非改 `ossec.conf` 不可。

拆開之後，中間路線就出現了：**Wazuh 只負責判斷，Grafana 負責送。**

```
Falco / Suricata / K8s Audit
  → Alloy → syslog
  → Wazuh 規則層（爆發收斂 + 抑制 + MITRE）
  → Grafana（Elasticsearch 資料來源讀 wazuh-alerts-4.x-*）
  → DEFAULT contact point → Zulip + Mailpit
```

多了一跳，但**決策仍然在 Wazuh**。而且副作用反而更好：通知路徑變成單一出口，
分組、靜音、重送間隔都在同一個地方設。原本 falcosidekick 一套、Wazuh 一套的話，
抑制規則會散在兩處。

> **繞路不一定是妥協。** 先問清楚自己要的是哪個維度，再決定要硬改還是繞。

## 2. 意外的好消息：OpenSearch 可以直接當 Elasticsearch 用

Wazuh 的 indexer 是 OpenSearch，而 Grafana 自 8.x 之後移除了 OpenSearch 的
內建支援（要裝外掛）。本來以為得想辦法裝外掛或改走 Infinity 資料來源。

實際測下來不用：**Wazuh 的 indexer 已經開了
`compatibility.override_main_response_version`**，回報的版本號偽裝成
Elasticsearch 7，Grafana 內建的 Elasticsearch 資料來源可以直接連。

值得記的不是這個結論，是找到它的方式：

> **「先去問服務自己回報什麼」比「查文件說支援什麼」快。** 一個 `curl /` 就看到了。

## 3. 靜默失敗點一：`valuesFrom` 的層級

資料來源要用 secret 裡的帳密。grafana-operator 的寫法是：

```yaml
spec:
  datasource:
    name: Wazuh
    uid: wazuh-alerts
    type: elasticsearch
    url: https://wazuh-indexer.kube-security.svc.cluster.local:9200
    jsonData:
      timeField: "@timestamp"
      index: "wazuh-alerts-4.x-*"
      tlsSkipVerify: true
    basicAuth: true
    basicAuthUser: ${INDEXER_USERNAME}
    secureJsonData:
      basicAuthPassword: ${INDEXER_PASSWORD}
  # valuesFrom 本身在 spec 層級
  valuesFrom:
    - targetPath: basicAuthUser          # ← 但這是「相對於 spec.datasource」
      valueFrom:
        secretKeyRef:
          name: wazuh-indexer-cred
          key: INDEXER_USERNAME
    - targetPath: secureJsonData.basicAuthPassword
      valueFrom:
        secretKeyRef:
          name: wazuh-indexer-cred
          key: INDEXER_PASSWORD
```

兩個層級不同，很容易寫混：

| 欄位 | 位置 |
|---|---|
| `valuesFrom` | `spec` 層級（放進 `spec.datasource` 會被 strict decoding 擋下） |
| `targetPath` | **相對於 `spec.datasource`**，所以不加 `datasource.` 前綴 |

寫成 `spec.datasource.valuesFrom` 還好，至少會報錯。
但 `targetPath` 多寫一層前綴**完全不報錯**，只會得到空的 `basicAuthUser`
和 401。

## 4. 靜默失敗點二：`bucketAggs` 不能留空

告警規則長這樣：

```yaml
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaAlertRuleGroup
metadata:
  name: wazuh-high-severity
spec:
  folderRef: devsecops
  interval: 1m
  rules:
    - title: Wazuh 高嚴重度告警
      condition: C
      data:
        - refId: A
          datasourceUid: wazuh-alerts
          model:
            query: "rule.level:>=13"
            metrics: [{ id: "1", type: "count" }]
            bucketAggs:
              - id: "2"
                type: date_histogram
                field: "@timestamp"
                settings: { interval: "5m" }
            timeField: "@timestamp"
```

`bucketAggs` 留空會得到：

```
invalid query, missing metrics and aggregations
```

但這個錯誤**只出現在規則的 `health` 欄位**，CR 的 `status` 仍然是
`ApplySuccessful`。看 CR 狀態會以為一切正常。

```shell
# 看 CR status：ApplySuccessful，沒問題
kubectl get grafanaalertrulegroup wazuh-high-severity -n observability -o jsonpath='{.status}'

# 看規則的 health：這裡才看得到錯
curl -s -u "admin:$GF_PASS" http://127.0.0.1:13000/api/v1/provisioning/alert-rules \
  | jq '.[] | {title, health, lastError}'
```

## 5. 靜默失敗點三：預設通知路由的 receiver 是 `empty`

這是整條鏈**最容易漏**的一環。

沒有 `GrafanaNotificationPolicy` 的話：告警照常評估、照常
`state=firing`、面板上一片紅，**但不會送到任何地方**。收件匣是空的。

```yaml
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaNotificationPolicy
metadata:
  name: default
  namespace: observability
spec:
  instanceSelector:
    matchLabels:
      dashboards: "grafana"
  route:
    receiver: DEFAULT
    group_by: ["alertname", "grafana_folder"]
    group_wait: 30s
    group_interval: 5m
    repeat_interval: 4h
```

## 6. 靜默失敗點四：這個 CR 的欄位是 snake_case

`group_by` / `group_wait` / `group_interval` / `repeat_interval`。

周邊的 Grafana CR 多半是 camelCase，這裡例外。寫成 `groupBy` 的話——
你猜對了——不會報錯。

---

四個失敗點的共同形狀：

> **「設定好了」和「生效了」之間有四道關，而四道關都用沉默表示失敗。**

驗證只能靠端到端。本系列的做法是製造一次真實爆發，一路看到 Mailpit 收信：

```shell
# 1) 觸發：特權 Pod 製造爆發
kubectl run burst-probe --image=busybox:1.36 --restart=Never -n default \
  --overrides='{"spec":{"containers":[{"name":"p","image":"busybox:1.36","securityContext":{"privileged":true},"command":["sh","-c","for i in $(seq 1 40); do ls /etc/shadow; done; sleep 3"]}]}}'

# 2) Wazuh 是否產生收斂後的告警
kubectl exec -n kube-security wazuh-manager-worker-0 -c wazuh-manager -- \
  sh -c 'grep -o "\"id\":\"1001[23][0-9]\"" /var/ossec/logs/alerts/alerts.json | sort | uniq -c'

# 3) Grafana 規則是否 firing
curl -s -u "admin:$GF_PASS" http://127.0.0.1:13000/api/prometheus/grafana/api/v1/rules \
  | jq '.data.groups[].rules[] | {name, state, health}'

# 4) 信真的寄出去了嗎
curl -s http://127.0.0.1:8025/api/v1/messages | jq '.messages[0] | {Subject, Created}'
```

**只有第 4 步沒有替代品。** 前三步都有「局部看起來正常」的外觀。

## 7. 告警疲勞：先治本，再調門檻

這一節是本文的另一個主軸，而且它是我真的把聊天室洗版之後學到的。

### 先把「吵」變成一個有單位的數字

```shell
kubectl -n kube-security logs ds/falco -c falco --since=3m \
  | grep -c '"priority"'
```

量到的結果：**每分鐘約 300 則**。再拆組成：

| 來源 | 佔比 |
|---|---|
| 健康探針（`pg_isready` / `ping_liveness` / `rabbitmq-diagnostics`） | **87%** |
| `runc` 觸發的 Mount in Container | 少量 |
| wazuh-agent 的例行指令（`netstat` / `last` / `df`） | 少量 |
| 真實事件 | 極少 |

### 兩層處理，順序不能顛倒

| 層 | 做法 | 效果 |
|---|---|---|
| **規則層（治本）** | 排除健康探針、排除 `runc`、把 wazuh-agent 加進既有的基礎設施清單 | 1738/2000 → 3 分鐘 12 筆 |
| **通道層** | `minimumpriority` 由 `notice` 改為 `critical` | Warning/Error 不再進聊天室 |

**順序不能顛倒。** 只調門檻的話，噪音仍然灌進 Loki、Wazuh 與索引，
只是人看不到——**那是把問題藏起來，不是解決。** 規則層修好之後，整條管線
的負載都下降。

### 排除條件的寫法：用指令內容，不用 parent

```yaml
- macro: koad_is_health_probe
  condition: >
    proc.cmdline contains "pg_isready" or
    proc.cmdline contains "ping_liveness" or
    proc.cmdline contains "ping_readiness" or
    proc.cmdline contains "rabbitmq-diagnostics" or
    proc.cmdline contains "/health/" or
    proc.cmdline contains "healthcheck.sh"
```

exec 探針由 CRI 喚起，`proc.pname` 多半是空的，不是可靠的判別依據。
而**攻擊者開一個互動 shell 不會長得像 `pg_isready`**，所以比對指令字串
反而精準。

### 通道層的分界線

> **聊天室是「要人看的」通道。**

高頻低特異性的訊號（shell in container、sensitive file read）是**獵捕素材**，
該留在 Loki 與 Wazuh 供查詢，不該進通知流。

代價是 Warning 級的真實偵測不會主動通知，要靠人去查——**這是刻意的取捨，
不是遺漏**。

### 刻意留窄

修完後仍有約 4 筆/分的 Warning。**沒有繼續擴大排除**，因為那會讓
「有人在那個容器裡開 shell」變成盲點。4 筆/分的 Warning 不進聊天室，
成本可接受。

> **降噪的終點不是 0。** 把真陽性一起消掉的降噪是在製造盲點。

### 關掉重複的那一條

Wazuh 接手判斷之後，falcosidekick 的 slack / smtp 輸出一定要關，不然是
雙重通知，而且形狀完全不同——**一邊逐筆，一邊收斂**（第 7 篇的 133:1）。

做法是把 `minimumpriority` 設為 `emergency`——規則集裡沒有用到該等級，
等於實質停用，但不必刪掉整段設定（將來要回復只改一個字）。WebUI 保留，
它仍是看原始事件流的地方。

## 8. 驗證要驗三件事，不是兩件

改門檻這種事，雙向驗證不夠，要三向：

```
1. 噪音消失   1738/2000 → 3 分鐘 12 筆，聊天室 0 筆
2. 真陽性仍在 剩下的 12 筆是 netstat / last / df —— 同樣的規則形狀、
              不同的指令，證明規則對非探針的 shell 依然有效
3. 通知仍通   建立特權 Pod → Critical → 4 筆 Slack + 2 筆 SMTP
```

第 2 項是意外的收穫：**剩餘的噪音本身就是真陽性的證據**，不必另外設計測試。

第 3 項則是因為改了門檻，必須確認「該通知的還會通知」。

驗第 3 項時我踩了一個很典型的坑：只查一個 falcosidekick 副本，看到 0 筆就
以為通知斷了。兩個副本加總才看到 4 筆——**流量負載平衡到兩個副本，只看一個
等於只看一半。**

## 9. 這套系統真的會抓到東西

最後講一個在驗證儀表板時撈到的真實問題，它剛好把整個系列串起來。

ET Open 出現一筆不是環境噪音的命中：

```
ET INFO Outgoing Basic Auth Base64 HTTP Password detected unencrypted
  severity 1 → Wazuh rule 100203 level 12
  10.244.0.189 → 10.244.2.190:2802
  Host: falco.the-one.k8s   Referer: https://falco.the-one.k8s/events/
```

是某個 WebUI。**Gateway 在前面終止了 TLS，但往後端那一跳是明文 HTTP**，
Basic Auth 的憑證就這樣以 base64 在叢集網路裡傳輸。

為什麼只有 IDS 看得到：

- 從瀏覽器看，網址列是 `https`，Referer 也是 `https`——完全正常
- Falco 看不到：那是網路上的事，不是主機上的事
- Hubble 看得到連線，但看不到 header 裡有 `Authorization: Basic`

> **這正是第 1 篇說的：補一個偵測來源的價值，不是多一個告警，
> 是多一種別人看不到的視角。**

## 10. 系列總結：一個人的 DevSecOps 能到哪裡

### 做出來的東西

```
三個偵測來源  Falco（主機）/ K8s Audit（控制平面）/ Suricata（網路）
兩個資料倉儲  Loki（全量，供獵捕）/ Wazuh 索引（判斷過的，供告警）
一個判斷層    Wazuh 規則：分級、爆發收斂、抑制、MITRE 對應
一個出口      Grafana → Zulip / Mailpit
一層地基      Cilium CCNP default deny + 逐條放行
```

整套約 1150m CPU / 2.6 GiB，跑在一台筆電的 kind 上。

### 四條反覆出現的方法論

**一、把模糊的抱怨變成一個有單位的數字，解法就自己浮出來了。**

- 「falco 部署有點久」→ `148 秒 vs 0.008 秒` → 用 mirror
- 「訊息轟炸」→ `每分鐘 300 則，87% 是健康探針` → 修規則
- 「新裝一個 IDS」→ `alert 佔 0.75%，其中 70% 是環境造成的` → 三項調整

第三個的差別是**還沒有人抱怨**。裝完就先量，省掉的是「噪音灌進系統幾天
之後才回頭清」的那一段。

**二、靜態證據不能證明動態行為。**

這條在系列裡出現了四次，每次換一個面貌：

| 看到的 | 以為 | 實際 |
|---|---|---|
| `status: ApplySuccessful` | 儀表板會動 | 只證明寫進去了 |
| 規則載入成功 | 規則會觸發 | 可能被遮蔽 |
| 檔案 grep 得到新內容 | 規則生效了 | analysisd 還抱著舊的 |
| 中間節點都健康 | 鏈路是通的 | 最後一跳沒接 |

> **中間節點的健康狀態，不能加總成鏈路的健康狀態。**

**三、量測工具自己會壞，而且不報錯。**

系列過程中有八次判斷錯誤，全部來自「把量測工具的輸出當成事實」：

| 我看到的那一份 | 不是 |
|---|---|
| 單一副本 / 單一節點 / 單次取樣 | 全部 |
| 快取的憑證 | 現在 |
| 磁碟上的設定檔 | 執行中的那一份 |

空間、時間、狀態——同一個錯誤的三個面向。

**四、與其逐一驗證假設，不如先找出一個「成功的案例」。**

17 條規則只有 1 條會觸發時，問題從「為什麼都不觸發」變成「它和其他 16 條
差在哪」——後者是可回答的問題。**一個能動的對照組，比五次猜測有用。**

### 它的極限

誠實地說清楚這套東西做不到什麼：

- **沒有真人輪值。** 告警送到聊天室之後就沒有人了。這不是技術問題。
- **威脅情報是靜態的。** Wazuh 的 mitre.db 止於 2023-05，ET Open 要手動更新。
- **視野有洞。** Cilium native routing 下同節點的 Pod 互連 Suricata 看不到。
- **規則調校永遠沒有做完的一天。** 每裝一個新元件就會多一批噪音。

但它能做到一件最重要的事：**當某件事發生時，你有地方可以查，而且查得到。**
對一個人維運的環境來說，這已經足夠改變很多決策。

### 給想開始的人

如果你要從頭做一次，我的建議是：

1. **不要照順序裝完再調。** 每裝一個就量一次、調一次、驗一次。
2. **網路策略先上。** 它會逼你說清楚每一條連線的理由。
3. **最後才接通知。** 在規則調好之前接上，只會得到一個被靜音的頻道。
4. **每個坑都寫下來。** 本系列的價值不在那些 YAML，在那些「為什麼」。

## 小結

- **繞路不一定是妥協**：上游不給擴充點時，先問自己真正要的是哪個維度。
  「判斷在 Wazuh」和「封包從 Wazuh 出去」是兩件事。
- **四個靜默失敗點**：`targetPath` 的層級、空 `bucketAggs` 只現於 `health`、
  預設 receiver 是 `empty`、snake_case 欄位。驗證只能靠端到端。
- **先治本再調門檻**：只調門檻是把問題藏起來。而且降噪的終點不是 0——
  把真陽性一起消掉的降噪是在製造盲點。
- **驗證要三向**：噪音消失、真陽性仍在、通知仍通。只驗前兩項，你會得到一個
  很安靜但不會響的系統。

---

寫到這裡，這套東西在我的筆電上跑了幾天，抓到一個真實的設定問題，
也讓我對「偵測」這件事的理解從「裝工具」變成「設計訊號」。

如果你也想試，所有設定都在 repo 裡。歡迎指出我寫錯的地方——這個領域
一個人走得慢，一起走才走得遠。

系列文：

- 一個人的 DevSecOps (1)：先畫地圖，再動手
- 一個人的 DevSecOps (2)：零信任的地基
- 一個人的 DevSecOps (3)：看得見才管得住
- 一個人的 DevSecOps (4)：主機層偵測
- 一個人的 DevSecOps (5)：控制平面偵測
- 一個人的 DevSecOps (6)：網路層偵測
- 一個人的 DevSecOps (7)：從事件到告警
- 一個人的 DevSecOps (8)：最後一哩與告警疲勞

想要知道更多，可參考以下資源：

- [Grafana Alerting](https://grafana.com/docs/grafana/latest/alerting/)
- [grafana-operator CRD 參考](https://grafana.github.io/grafana-operator/docs/api/)
- [Falcosidekick](https://github.com/falcosecurity/falcosidekick)
- [Alert Fatigue（SRE Book: Monitoring Distributed Systems）](https://sre.google/sre-book/monitoring-distributed-systems/)
