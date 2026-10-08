# 一個人的 DevSecOps (3)：看得見才管得住

## 背景說明

偵測工具產生的是**事件**，事件要有地方放、有辦法查、有人看得懂，才會變成
**情報**。所以在裝 Falco、Suricata、Wazuh 之前，先把這一層立起來。

這一層有四個角色，分工很清楚：

| 角色 | 元件 | 負責 |
|---|---|---|
| 蒐集 | Alloy | 從節點把日誌與指標撈出來 |
| 日誌倉儲 | Loki + SeaweedFS | 全量原始日誌，供事後獵捕 |
| 指標倉儲 | Prometheus | 時序數字，供趨勢與告警 |
| 呈現 | Grafana Operator | 儀表板、資料來源、告警 —— 全部用 CR 管理 |

本文的主軸是：**怎麼確認它真的看得見。** 這聽起來像廢話，但本文會示範
兩個「綠燈亮著、實際上少了一半資料」的案例，而它們都不會報錯。

## 環境必須

1. 已完成第 2 篇（Cilium + CCNP）
2. `helm`、`kubectl`、`yq`、`jq`

## 1. SeaweedFS 作為 Loki 的物件儲存

Loki 需要 S3 相容的後端。在單機環境用 MinIO 或 SeaweedFS 都可以，本系列
用 SeaweedFS，因為它輕。

```shell
kubectl create ns seaweedfs --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install seaweedfs -n seaweedfs \
  --repo https://seaweedfs.github.io/seaweedfs/helm seaweedfs \
  --version 4.48.0 \
  -f ./values/seaweedfs.yaml
```

裝好之後把 S3 的憑證取出來給 Loki 用：

```shell
# 從 SeaweedFS 的 s3.config 取出 identity "loki" 的 AK/SK
AK=$(kubectl -n seaweedfs get secret seaweedfs-s3-secret \
  -o jsonpath='{.data.seaweedfs_s3_config}' | base64 -d \
  | jq -r '.identities[] | select(.name=="loki") | .credentials[0].accessKey')
SK=$(kubectl -n seaweedfs get secret seaweedfs-s3-secret \
  -o jsonpath='{.data.seaweedfs_s3_config}' | base64 -d \
  | jq -r '.identities[] | select(.name=="loki") | .credentials[0].secretKey')

# dry-run + apply：secret 已存在時直接更新，可重複執行
kubectl create secret generic loki-s3-credentials -n observability \
  --from-literal=AWS_ACCESS_KEY_ID="$AK" \
  --from-literal=AWS_SECRET_ACCESS_KEY="$SK" \
  --dry-run=client -o yaml | kubectl apply -f -
```

`--dry-run=client -o yaml | kubectl apply -f -` 這個組合在整個系列會一直
出現。它讓每一行指令都**可以重複執行**，這在一個人維運的環境裡很重要：
你不會記得自己上次做到哪，但你會記得「整份貼下去就對了」。

別忘了第 2 篇的規矩 —— Loki 要連 SeaweedFS，所以 `60-loki-seaweedfs.yaml`
是**成對**的兩條 CCNP。

## 2. k8s-monitoring：一個 chart，三個 Alloy

Grafana 的 `k8s-monitoring` chart 會依你啟用的功能部署數個 Alloy 實例：

| 實例 | 角色 |
|---|---|
| `alloy-metrics` | 抓指標 |
| `alloy-logs` | DaemonSet，讀節點上的日誌檔 |
| `alloy-singleton` | 叢集事件等單例工作 |

```shell
helm upgrade --install k8s-monitoring -n observability \
  --repo https://grafana.github.io/helm-charts k8s-monitoring --version 4.5.2 \
  -f ./values/k8s-monitoring.yaml
```

這裡有一個**在後面幾篇會反覆踩到**的設計，先講清楚：

> **chart 只在「有功能用到該目的地」的 collector 上產生對應的 writer 元件。**

具體而言：`alloy-logs` 如果沒有啟用任何送往 Loki 的 chart 功能（例如
`podLogs`），那麼 `loki.write.localloki` 這個元件在這支 Alloy 裡**根本不存在**。
你在 `extraConfig` 裡引用它，Alloy 會直接啟動失敗：

```
component "loki.write.localloki" does not exist or is out of scope
```

解法是自己宣告一個：

```river
// 名稱刻意不叫 localloki，以免日後啟用 podLogs 時與 chart 產生的元件撞名
loki.write "extra_localloki" {
  endpoint {
    url               = "http://loki-gateway.observability.svc.cluster.local/loki/api/v1/push"
    tenant_id         = "soc"
    retry_on_http_429 = true
  }
  external_labels = {
    "cluster" = "the-one",
  }
}
```

## 3. Alloy 的 positions 要自己持久化

`alloy-logs` 是 DaemonSet，它記錄每個檔案讀到哪裡（positions）。預設的
`storagePath` 是 `/tmp/alloy`，**容器重啟就沒了**。

後果不是「少資料」而是「**重複資料**」：Pod 重啟後從頭讀一次，同一筆事件
會在下游出現兩次。實測確認過。

因為 positions 本來就是逐節點的狀態，而 DaemonSet 的 Pod 永遠回到同一個
節點，所以用 hostPath 比 PVC 更合適：

```yaml
alloy-logs:
  alloy:
    storagePath: /var/lib/alloy
    mounts:
      extra:
        - name: alloy-positions
          mountPath: /var/lib/alloy
  controller:
    volumes:
      extra:
        - name: alloy-positions
          hostPath:
            path: /var/lib/alloy-positions
            type: DirectoryOrCreate
```

**驗證這件事本身有個陷阱**，值得記一筆。我第一次驗證的方式是：刪掉 Pod、
等它回來、看 offset 有沒有從 0 開始。結果看起來是對的 —— 但我讀到的是
**還在終止中的舊 Pod**，DaemonSet 的新舊 Pod 同名。

正確做法是等到一個**名字不同**的 Pod 進入 Ready 再讀。這是本系列第一次
遇到「對照組本身不成立」的問題，之後還會遇到好幾次。

## 4. Prometheus：15/29 的綠燈

Prometheus 裝好之後，Grafana 上的 target 面板看起來一片綠。但實際數一數：

```shell
kubectl -n observability exec deploy/prometheus-server -c prometheus-server -- \
  wget -qO- 'http://localhost:9090/api/v1/targets?state=active' \
  | jq '[.data.activeTargets[] | select(.health=="up")] | length,
        (.data.activeTargets | length)'
```

畫面會輸出：

```
15
29
```

**29 個 target，只有 15 個是 up。** 面板上看不出來，因為沒抓到的 job 連
時間序列都不存在 —— 它不是「畫出一條為 0 的線」，而是**整個 job 消失**。

> **指標系統的「沒有資料」與「值是 0」看起來一樣，但意義完全相反。**

## 5. 根因：node IP 上的 target 不是 Pod endpoint

第 2 篇寫的 Prometheus 放行策略長這樣：

```yaml
  egress:
    - toEndpoints:
        - {}
```

`toEndpoints: [{}]` 的意思是「所有 Pod endpoint」。問題是有一整類 target
**不是 Pod endpoint**：

| target | 埠 | 跑在哪 |
|---|---|---|
| kubelet | 10250 | 節點 |
| node-exporter | 9100 | hostNetwork |
| cilium-agent / envoy | 9962-9965 | hostNetwork |

它們的流量目的地是 **node IP**，`toEndpoints` 涵蓋不到。補上：

```yaml
    # node IP 上的 target 不是 Pod endpoint，toEndpoints 涵蓋不到
    #   host        = Prometheus 自己所在的 node
    #   remote-node = 叢集裡的其他 node
    # 兩個都要，少任何一個就會剩下對應的那幾個 node 抓不到
    - toEntities:
        - host
        - remote-node
```

`host` 與 `remote-node` 要一起寫。只寫 `remote-node` 的話，Prometheus
自己所在的那個節點上的 target 會抓不到 —— 而那只佔四分之一，在面板上
更難發現。

套用之後再數一次：

```
39
39
```

從 15/29 變成 39/39。多出來的 10 個 target 是補上放行之後才被服務發現
納入的。

## 6. 第二個綠燈：Hubble 指標的 serviceMonitor 陷阱

Cilium 的 Hubble 可以輸出 Prometheus 指標（`hubble_drop_total` 等），
這是後面做 PacketDrop 儀表板的資料來源。開啟方式：

```shell
helm upgrade cilium -n kube-system ... \
  --set hubble.metrics.enabled='{drop:sourceContext=pod;destinationContext=pod,flow,dns}'
```

然後你會很自然地想把它接到 Prometheus Operator：

```shell
  --set hubble.metrics.serviceMonitor.enabled=true   # ← 不要這樣做
```

**這一行會讓 `hubble_drop_total` 完全消失，而且沒有任何錯誤訊息。**

原因在 chart 的 template（`templates/hubble/metrics-service.yaml`）：

```
{{- if not .Values.hubble.metrics.serviceMonitor.enabled }}
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "9965"
{{- end }}
```

**`prometheus.io/scrape` 註記只在 `serviceMonitor.enabled` 為 false 時才加上。**
如果你用的是 annotation-based 的服務發現（本系列的 Prometheus 就是），
開啟 serviceMonitor 等於把唯一的入口關掉。

這個坑我一開始判斷錯了 —— 我說「chart 不會幫 hubble 的 Service 加註記」，
實際讀了 template 才發現它會，只是被條件擋住。**「沒有加上」與「加了但被
條件關掉」在現象上一樣，在解法上完全不同。**

## 7. 第三個陷阱：空字串標籤與不存在的標籤等價

做 PacketDrop 儀表板時想按「目的地 Pod」分組，於是設了
`destinationContext=pod`。套用之後查詢，發現結果裡根本沒有 `destination`
這個標籤，於是判斷「設定沒生效」。

實際上它生效了。正確的理解是：

> **Prometheus 的資料模型中，空字串標籤與不存在的標籤等價。**

當封包的目的地不是叢集內的 Pod（例如外部 IP），`destination` 就是空字串，
而空字串在查詢結果裡看起來就像這個標籤不存在。

驗證方式是製造一筆**目的地確實是 Pod** 的丟包：

```shell
kubectl run drop-probe --image=busybox:1.36 --restart=Never -n default \
  --command -- sh -c 'wget -T 3 -qO- http://loki-gateway.observability:80'
```

這條連線會被 default deny 擋下，而且來源與目的都是 Pod。再查一次就看到
`destination="observability/loki-gateway-xxx"` 了。

## 8. Grafana Operator：用 CR 管理一切

Grafana 本身用 operator 部署，儀表板、資料來源、告警規則、通知政策全部
是 CR。這對一個人維運的環境特別重要：**所有設定都在 Git 裡，重建環境不需要
記得自己在 UI 上點過什麼。**

```shell
helm upgrade --install grafana-operator -n observability \
  --repo https://grafana.github.io/helm-charts grafana-operator --version 5.25.0
```

然後是資料夾與資料來源：

```yaml
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaFolder
metadata:
  name: devsecops
  namespace: observability
spec:
  instanceSelector:
    matchLabels:
      dashboards: "grafana"
  # 明確設定 uid：沒有 .spec.uid 時 reconciler 可能接管同名的既有資料夾
  uid: devsecops
  title: DevSecOps
```

儀表板指向資料夾用 `folderRef`，值是該 CR 的 **`metadata.name`，不是 title**：

```yaml
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDashboard
metadata:
  name: cilium-packetdrop
  namespace: observability
spec:
  instanceSelector:
    matchLabels:
      dashboards: "grafana"
  folderRef: devsecops
  resyncPeriod: 5m
  json: |
    { ... }
```

## 9. 儀表板設 `editable: false` 是刻意的

`resyncPeriod: 5m` 代表每五分鐘 operator 會以 CR 的內容覆寫 Grafana 上的
版本，**而且不會有任何警告**。

如果儀表板是可編輯的，有人在 UI 上改了、很滿意、去泡咖啡，回來發現改動
不見了 —— 連錯誤訊息都沒有。所以本系列所有 operator 管理的儀表板都設
`"editable": false`，讓它在 UI 上就是唯讀的。

真的要從 UI 取回調整，得在下次 resync 前把 JSON 匯出寫回檔案：

```shell
kubectl -n observability exec deploy/grafana-deployment -- \
  curl -s -u "admin:$GF_PASS" \
  http://localhost:3000/api/dashboards/uid/cilium-packetdrop | jq .dashboard
```

## 10. 驗證：CR 的 status 不是功能的 status

這一節是整個系列最重要的一條方法論，後面每一篇都會再碰到它。

套用儀表板之後：

```shell
kubectl get grafanadashboard cilium-packetdrop -n observability -o jsonpath='{.status}' | jq
```

畫面會輸出：

```json
{
  "conditions": [
    {
      "message": "Dashboard was successfully applied to 1 instances",
      "reason": "ApplySuccessful",
      "status": "True",
      "type": "DashboardSynchronized"
    }
  ]
}
```

`ApplySuccessful`。但這只證明一件事：**operator 把 CR 的內容寫進 Grafana 了。**
它不證明查詢寫對了、不證明指標存在、不證明面板畫得出東西。

> **控制器的職責是「把 CR 的內容寫進目標系統」，寫進去就算成功；
> 內容對不對是目標系統的事。**

要驗功能，就得真的跑一次查詢。Grafana 有一個 `/api/ds/query` 端點可以用：

```shell
kubectl port-forward -n observability svc/grafana-service 13000:3000 &

curl -s -u "admin:$GF_PASS" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:13000/api/ds/query -d '{
    "queries": [{
      "refId": "A",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "expr": "sum(increase(hubble_drop_total[1h]))",
      "intervalMs": 60000, "maxDataPoints": 100
    }],
    "from": "now-1h", "to": "now"
  }' | jq '.results.A | {error, frames: (.frames | length)}'
```

有 frames、沒有 error，才叫做「會動」。

## 小結

- **可觀測性要在偵測工具之前立起來**：事件要有地方放、有辦法查，才會變成情報。
  蒐集、日誌、指標、呈現，四個角色分工清楚。
- **綠燈不等於看得見**：Prometheus 的 15/29、Hubble 的 serviceMonitor 註記、
  Prometheus 的空字串標籤 —— 三個案例都是「畫面正常但少了資料」。指標系統的
  「沒有資料」與「值是 0」長得一樣，意義相反。
- **CR 的 status 不是功能的 status**：`ApplySuccessful` 只證明寫進去了。
  要驗功能就跑一次真實查詢，有資料回來才算數。

下一篇開始接第一個偵測來源：Falco。

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

- [Grafana Alloy](https://grafana.com/docs/alloy/latest/)
- [Grafana Operator](https://grafana.github.io/grafana-operator/)
- [Loki](https://grafana.com/docs/loki/latest/)
- [Hubble Metrics](https://docs.cilium.io/en/stable/observability/metrics/)
