# 一個人的 DevSecOps (4)：主機層偵測

## 背景說明

Falco 看的是**主機上發生了什麼**：哪個行程被執行、哪個檔案被讀、哪個容器裡
開了互動式 shell。它是三個偵測來源裡最直觀的一個 —— 也是最容易被裝成
「每分鐘幾百則噪音」的那一個。

本文做三件事：

1. 用 **falco-operator** 部署，並說明它的五個 CRD 各自負責什麼
2. 解釋 Falco 的**規則遮蔽機制** —— 這是「規則明明對了卻不觸發」的頭號原因
3. 把 OCI artifact 拉取從 148 秒降到 0.008 秒

## 環境必須

1. 已完成第 3 篇（可觀測性）
2. `helm`、`kubectl`、`docker`

## 1. operator 的五個 CRD

falco-operator 把一支 Falco 拆成五種資源，每一種各自獨立更新：

| CRD | 負責 | 本系列用它做什麼 |
|---|---|---|
| `Falco` | DaemonSet 本體 | 調整 Pod 規格、探針 |
| `Config` | `falco.yaml` 的內容 | 輸出目的地、engine 參數、`priority` 門檻 |
| `Plugin` | 事件來源與欄位擴充 | `k8saudit` / `json` / `container` / `k8smeta` |
| `Rulesfile` | 規則檔 | 內建規則集 + 自訂規則，**並宣告載入順序** |
| `Component` | 附屬元件 | metacollector |

這個拆法對偵測工程特別有用的地方在最後一欄：**規則的載入順序可以用欄位
明確宣告，而不是靠檔名或目錄慣例。** 第 4 節會看到，Falco 的規則遮蔽機制
讓順序直接決定「哪一條規則會觸發」，能把它寫成 CR 的一個數字，比記在
腦袋裡可靠得多。

另一個好處是**更新的粒度**。換一份自訂規則只要改 `Rulesfile` 指向的
ConfigMap，不必動到 Plugin 或 DaemonSet；要試一個新 plugin 也不會連帶
重算整份設定。

代價是東西變多了：一支 Falco 要五個 CR 加一支自己維護的 Service（第 5 篇
會用到）。下一節的四個步驟有順序相依，照著走就不會卡。

## 2. 四個步驟，有順序相依

```shell
kubectl create ns kube-security --dry-run=client -o yaml | kubectl apply -f -

# NP：只需要 namespace 內部互連
kubectl apply -n kube-security -f ./manifests/network-policies/allow-intra-namespace.yaml

# 1) Operator 本體，提供五個 CRD
#    (Falco / Component / Rulesfile / Plugin / Config)
kubectl apply --server-side -f \
  https://github.com/falcosecurity/falco-operator/releases/latest/download/quickstart.yaml

# 2) 自訂規則的 ConfigMap，必須在 Rulesfile CR 之前建立
kubectl create configmap koad-rules-syscall -n kube-security \
  --from-file=rules.yaml=./values/falco/rules-syscall.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap koad-rules-k8saudit -n kube-security \
  --from-file=rules.yaml=./values/falco/rules-k8saudit.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

# 3) CR：Config / Plugin / Rulesfile / Falco / Component
kubectl apply -f ./manifests/falco-operator/

# 4) falcosidekick 獨立安裝
helm upgrade --install falcosidekick -n kube-security \
  --repo https://falcosecurity.github.io/charts falcosidekick \
  -f ./values/falcosidekick.yaml
```

第 2 步有一個**只會產生 info 等級訊息**的坑，下一節講。

第 4 步值得特別說明：falcosidekick 是一支獨立的服務，operator 不會幫你把
Falco 指過去。**兩端要自己接起來** —— 在 `Config` CR 裡宣告 `http_output`：

```yaml
apiVersion: instance.falcosecurity.dev/v1alpha1
kind: Config
metadata:
  name: falco-config
  namespace: kube-security
spec:
  config:
    http_output:
      enabled: true
      url: "http://falcosidekick:2801/"
```

## 3. ConfigMap 的鍵名必須正好是 `rules.yaml`

上面第 2 步用的是：

```shell
--from-file=rules.yaml=./values/falco/rules-syscall.yaml
```

而不是直覺的：

```shell
--from-file=./values/falco/rules-syscall.yaml   # ← 鍵名會變成 rules-syscall.yaml
```

`Rulesfile` CR 寫死了 `rules.yaml` 這個鍵名。用檔名當鍵會讓 artifact sidecar
反覆重啟，而日誌裡只有一行：

```
ConfigMap missing expected key
```

**info 等級。** 不是 warning，不是 error。如果你用 `grep -i error` 找問題，
會完全錯過它。

## 4. 規則遮蔽：一個事件只會觸發一條規則

這是 Falco 最反直覺、也最常讓人卡住的機制：

> **Falco 對一個事件最多只觸發一條規則。**

所以當你寫了一條規則、條件完全正確、事件也確實發生了，卻一筆都沒觸發 ——
最可能的原因是**有另一條規則先匹配了它**。

誰先匹配？由**載入順序**決定，先載入的贏。預設順序是：

```
falco_rules.yaml  →  k8s_audit_rules.yaml  →  rules.d/
```

也就是說**內建規則永遠先載入**。你自己寫的規則如果和某條內建規則重疊，
觸發的是內建那條。

還有一個容易誤解的點：

> **`priority:` 只決定規則會不會被載入，不決定誰先匹配。**

把自己的規則 priority 調高沒有用。要讓自訂規則勝出，只有兩條路：
讓它**後於**內建規則載入並明確覆寫，或是直接把內建那條停用。

operator 的 `Rulesfile` CR 用 `priority` 欄位控制載入順序 —— 注意這個
`priority` 和規則裡的 `priority:` 是兩回事：

```yaml
---
apiVersion: artifact.falcosecurity.dev/v1alpha1
kind: Rulesfile
metadata:
  name: falco-rules
spec:
  priority: 10        # 數字越大越晚載入
  ociArtifact: { ... }
---
apiVersion: artifact.falcosecurity.dev/v1alpha1
kind: Rulesfile
metadata:
  name: k8saudit-rules
spec:
  priority: 20
  ociArtifact: { ... }
---
apiVersion: artifact.falcosecurity.dev/v1alpha1
kind: Rulesfile
metadata:
  name: koad-syscall
spec:
  priority: 60        # 自訂規則最後載入
  configMapRef:
    name: koad-rules-syscall
---
apiVersion: artifact.falcosecurity.dev/v1alpha1
kind: Rulesfile
metadata:
  name: koad-k8saudit
spec:
  priority: 61
  configMapRef:
    name: koad-rules-k8saudit
```

順序有兩個硬性要求，兩者都指向「自訂規則必須後於內建載入」：

1. 自訂的 k8saudit 規則用到 `k8s_audit_rules.yaml` 定義的 `kevt` macro，
   先於它載入會**整檔失敗**：
   ```
   LOAD_ERR_VALIDATE: Undefined macro 'kevt' used in filter.
   ```
2. 檔尾用 `enabled: false` 停用內建規則，**覆寫只在後載入時生效**：
   ```yaml
   - rule: Create Privileged Pod
     enabled: false
   ```

套用後務必確認實際產生的順序：

```shell
kubectl -n kube-security exec ds/falco -c falco -- \
  grep -A6 '^rules_files' /etc/falco/falco.yaml
```

我自己在這裡提過一個錯誤的建議：把 `rules.d` 排到最前面。實測直接
`LOAD_ERR_VALIDATE` 失敗 —— 而設定檔裡本來就有一段註解寫明了順序的理由，
**我沒讀就提了建議**。這件事後來在系列裡重複了好幾次，第 8 篇會再回來談。

## 5. 部署很慢？先量，再解

第一次部署 Falco 的時候，Pod 要好幾分鐘才 Ready。「有點慢」是個很模糊的
感覺，所以先把它變成數字。

看 init 容器在做什麼：

```shell
kubectl -n kube-security logs ds/falco -c falcoctl-artifact-install --tail=20
```

它在從 `ghcr.io` 拉 OCI artifact：四個 plugin 加兩個 ruleset，**每個節點
各拉一份**。

直接量一個 5 MB 的 blob：

```shell
# 直連 ghcr.io
time curl -sL -o /dev/null https://ghcr.io/v2/falcosecurity/plugins/plugin/k8saudit/blobs/sha256:...
```

畫面會輸出：

```
real    2m28.412s
```

**148 秒。** 六個 artifact × 四個節點，難怪要十幾分鐘。

### 解法：本機 pull-through mirror

跑一個 registry 當快取：

```yaml
# registry/compose.yaml
services:
  mirror-ghcr-io:
    image: registry:3
    container_name: mirror-ghcr-io
    networks: [kind]
    environment:
      REGISTRY_PROXY_REMOTEURL: https://ghcr.io
    ports:
      - "12345:5000"
networks:
  kind:
    external: true
```

然後把所有 `ociArtifact` 指過去：

```yaml
  ociArtifact:
    reference: falcosecurity/plugins/plugin/k8saudit:latest
    registry:
      name: mirror-ghcr-io:5000
      plainHTTP: true
```

再量一次（快取命中）：

```
real    0m0.008s
```

**148 秒 → 0.0085 秒。** 整個重啟時間從 5 分鐘降到 31 秒。

### 這裡有一個關鍵認知

你可能會想：「我的 containerd 不是已經設了 registry mirror 嗎？」

> **containerd 的 `registry/certs.d` 設定對 Pod 內自己拉 OCI 的元件無效。**

falcoctl 是跑在容器裡的一個程式，它用自己的 HTTP client 去拉 artifact，
**完全不經過 containerd**。所以要讓它走 mirror，三件事缺一不可：

| 要件 | 做什麼 |
|---|---|
| CR 設定 | `registry.name` + `plainHTTP: true` |
| DNS | CoreDNS 的 `hosts` 區塊讓 Pod 解析得到 `mirror-ghcr-io` |
| 網路策略 | CCNP 放行 —— 而且要用 `toCIDR` 不是 `toEntities` |

最後那一點值得說明：mirror 跑在 kind 的 Docker network 裡，**和節點同網段
但它不是節點**。Cilium 把它歸類為 `reserved:world`，所以
`toEntities: [host, remote-node]` 涵蓋不到，只能用 `toCIDR`：

```yaml
  egress:
    - toCIDR:
        - 192.168.247.8/32    # docker inspect mirror-ghcr-io 取得
      toPorts:
        - ports: [{ port: "5000", protocol: TCP }]
```

CoreDNS 的部分要注意：改 CoreDNS 的 ConfigMap 要用 `kubectl replace`
而不是 `apply`（`apply` 會因為 last-applied 註記而產生衝突）：

```
hosts {
   192.168.247.8 mirror-ghcr-io
   fallthrough
}
```

## 6. 兩個 Pod 規格上的坑

**一、預設的 livenessProbe 會殺掉 artifact sidecar。**

artifact 容器的預設探針是 45 秒。在沒有 mirror 的情況下拉一個 plugin 要
148 秒 —— 探針會在它拉完之前就把它殺掉，然後無限重啟。症狀是
`CrashLoopBackOff`，但日誌裡看起來「正在下載中」一切正常。

```yaml
  podTemplateSpec:
    spec:
      initContainers:
        - name: artifact-operator
          livenessProbe:
            failureThreshold: 60
```

**二、`podTemplateSpec` 必須包含 `containers`。**

即使你只想改 initContainer，也要把 `containers` 寫出來，否則 CR 會被拒絕：

```yaml
      containers:
        - name: falco
          tty: true
```

## 7. 驗證

```shell
# 規則有載入，且覆寫生效（Create Privileged Pod 應為 false）
kubectl -n kube-security exec ds/falco -c falco -- \
  falco --list 2>/dev/null | grep -c "Rule"

# 載入順序：數字越大越晚，自訂規則必須在 k8saudit-rules 之後
kubectl -n kube-security exec ds/falco -c falco -- \
  grep -A6 '^rules_files' /etc/falco/falco.yaml
```

最後做一次真實觸發 —— 建立一個特權 Pod：

```shell
kubectl run privileged-probe --image=busybox:1.36 --restart=Never -n default \
  --overrides='{"spec":{"containers":[{"name":"p","image":"busybox:1.36","securityContext":{"privileged":true},"command":["sh","-c","sleep 5"]}]}}'
```

然後看 Falco 的輸出：

```shell
kubectl -n kube-security logs ds/falco -c falco --tail=20 | grep -i privileg
```

**偵測工具的驗證一律要用「真的觸發一次」，不能只看規則數量。**
規則載入成功與規則會觸發是兩回事 —— 這是第 3 篇那句「CR 的 status 不是
功能的 status」在偵測領域的版本。

## 小結

- **五個 CRD，各自獨立更新**：`Falco` / `Config` / `Plugin` / `Rulesfile` /
  `Component`。對偵測工程最有價值的是 `Rulesfile.priority` —— 規則的載入順序
  可以寫成一個數字，而順序直接決定哪一條規則會觸發。
- **一個事件只觸發一條規則，先載入的贏**：`priority:` 只決定規則載不載入，
  不決定誰先匹配。自訂規則必須後於內建載入，否則不是整檔失敗就是被遮蔽。
- **把「慢」變成一個有單位的數字**：從「有點久」到「148 秒 vs 0.008 秒」，
  中間沒有任何猜測 —— 看 init 容器在做什麼、看探針設定、直接量一個 blob。
  數字出來之後解法自己就浮出來了。
- **Pod 內自拉 OCI 的元件不經過 containerd**：mirror 要在 CR、DNS、
  網路策略三個地方同時設好，缺一個都不會動。

下一篇把控制平面接進來：Kubernetes Audit。

<!-- series:start -->
系列文：

- [一個人的 DevSecOps (1)：先畫地圖，再動手](01-overview.md)
- [一個人的 DevSecOps (2)：零信任的地基](02-zero-trust-network.md)
- [一個人的 DevSecOps (3)：看得見才管得住](03-observability.md)
- **一個人的 DevSecOps (4)：主機層偵測（本篇）**
- [一個人的 DevSecOps (5)：控制平面偵測](05-k8s-audit.md)
- [一個人的 DevSecOps (6)：網路層偵測](06-suricata.md)
- [一個人的 DevSecOps (7)：從事件到告警](07-wazuh-brain.md)
- [一個人的 DevSecOps (8)：最後一哩與告警疲勞](08-alerting-and-fatigue.md)
<!-- series:end -->

想要知道更多，可參考以下資源：

- [Falco Operator](https://falco.org/docs/setup/operator/)
- [Falco Rules](https://falco.org/docs/rules/)
- [falcoctl](https://github.com/falcosecurity/falcoctl)
- KOAD 攻擊情境規則集（本系列使用的自訂規則，連結待補）
