# CiliumClusterwideNetworkPolicy

叢集層級的網路策略。namespace 範圍的放行用 vanilla NetworkPolicy
（`../network-policies/allow-intra-namespace.yaml`），需要 Cilium 專屬能力的才放這裡。

## 檔案

| 檔案 | 內容 |
|---|---|
| `10-infra-baseline.yaml` | DNS (含 L7 proxy)、API Server 雙向、Gateway 轉發 |
| `20-deny-metadata-api.yaml` | 顯式拒絕雲端 metadata endpoint |
| `30-kube-security-egress.yaml` | falcoctl 與映像來源的 FQDN 出網 |
| `40-falco-audit-webhook.yaml` | API Server 送 audit log 給 Falco |
| `50-falcosidekick-zulip.yaml` | Falco 告警送往 Zulip |
| `60-loki-seaweedfs.yaml` | Loki 存取 SeaweedFS 的 S3 介面 |
| `99-default-deny.yaml` | 全叢集 default deny |

**編號即套用順序。** 放行先上、`default-deny` 最後，所以整個目錄一起套用是安全的：

```shell
kubectl apply -f manifests/ccnp/
```

十位數編號留了插入空間，新增應用層策略從 70 開始。

## 為什麼這些只能用 CCNP

| 能力 | 用在哪 |
|---|---|
| `egressDeny` 顯式拒絕，優先於所有 allow | 20，即使日後有人寫寬鬆放行也擋得住 |
| `toEntities: kube-apiserver` | 10，不必寫死 ClusterIP，HA 下也不會漏 |
| `fromEntities: ingress` | 10，Gateway 轉發的流量不屬於任何 Pod |
| `fromEntities: host, remote-node` | 40，audit webhook 的來源是節點不是 Pod |
| `toFQDNs` | 30，CDN 的 IP 會變 |
| `enableDefaultDeny: false` | 全部，讓放行策略可以先單獨上線 |

## 四個踩過的坑

**一、`NotIn` 在標籤不存在時也成立。**
`endpointSelector` 要用 `Exists` + `NotIn` 並用，否則 Cilium 的保留 endpoint
（`reserved:health` / `ingress` / `init`，它們沒有 namespace 標籤）會被一併選中。
health 被擋會讓節點間健康檢查失效，init 被擋會造成偶發的 Pod 啟動失敗。

**二、`egressDeny` 會觸發 default deny。**
依 CRD 定義「某方向有規則時 default deny 預設為 true」，純粹的拒絕策略若不明確
設 `enableDefaultDeny: {egress: false}`，會順帶把被選中的端點推進 default-deny。

**三、L4 的 DNS 放行會壓過 L7。**
同一段流量同時有 L4-only 與 L7 放行時 Cilium 取較寬鬆者，DNS 會繞過 proxy，
`toFQDNs` 就永遠學不到 IP。所以 DNS 只在 10 宣告一次，別處不要再寫。

**四、Gateway 這一跳在策略上是透明的。**
Cilium Gateway 代替客戶端執行「客戶端 → 後端」的檢查，要放行的是真正的目的地
而非 Gateway。實測 `toEntities: ingress`、`toServices`、`toCIDR` 哨兵位址
（`192.192.192.192`）都無效，只有放行到後端才通。
所以走 Gateway 省不掉任何策略，內部通訊直接指向 Service 即可。

## 一條連線要放行兩次

default-deny 同時擋進與出，所以每條連線都要在**來源端的 egress** 與
**目的端的 ingress** 各放行一次。只寫一邊的症狀跟完全沒寫一樣 —— i/o timeout，
看不出少了哪半邊。50 與 60 都是成對的兩條策略，就是這個原因。
