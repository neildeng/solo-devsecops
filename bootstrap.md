# DevSecOps

## Bootstrap Cluster

```shell
bash bootstrap.sh
```

### Metrics Server

```shell
helm upgrade --install metrics-server -n kube-system \
  --repo https://kubernetes-sigs.github.io/metrics-server/ metrics-server \
  -f - <<EOF
---
args:
  - --kubelet-insecure-tls
  - --kubelet-preferred-address-types=InternalIP,Hostname
EOF
```

### CoreDNS Custom Config

```shell
kubectl replace -n kube-system -f ./manifests/coredns/coredns-configmap.yaml
kubectl rollout restart -n kube-system deploy/coredns
```

### Cloud Provider Kind

```shell
cd cloud-provider-kind
NET_MODE=kind docker compose up -d
cd -
```

### NetworkPolicy - Default Deny All

```shell
# 檔名編號即套用順序：放行先上，default-deny (99) 最後，
# 所以整個目錄一起套用是安全的
kubectl apply -f manifests/ccnp/
```

#### 直接看即時的 drop 流（更完整，不受 event 保留時間限制）

```shell
kubectl -n kube-system exec ds/cilium -- cilium-dbg monitor --type drop
```

### Cert-manager/Trusted-manager

```shell
kubectl create ns cert-manager --dry-run=client -o yaml | kubectl apply -f -

# NP：只需要 namespace 內部互連。
kubectl apply -n cert-manager -f ./manifests/network-policies/allow-intra-namespace.yaml
  
helm upgrade --install cert-manager -n cert-manager \
  --repo https://charts.jetstack.io cert-manager --version v1.21.2 \
  --set crds.enabled=true \
  --set config.gatewayAPI.enabled=true

helm upgrade --install trust-manager -n cert-manager \
  --repo https://charts.jetstack.io trust-manager --version v0.25.0 \
  -f - <<EOF
---
podDisruptionBudget:
  enabled: true
secretTargets:
  enabled: false
  authorizedSecretsAll: false
  authorizedSecrets: []
EOF
```

### step-certificates/step-issuer

這兩份 values **不在版控裡**（見 `.gitignore`）：`step-certificates.yaml`
會包含 CA 的 root 與 intermediate 私鑰，`step-issuer.yaml` 則要對應到那把
CA，兩者都必須在你自己的機器上產生。

需要 `step` CLI（`brew install step` 或見 smallstep 官方安裝說明）。

```shell
kubectl create ns smallstep --dry-run=client -o yaml | kubectl apply -f -

# NP：只需要 namespace 內部互連。
kubectl apply -n smallstep -f ./manifests/network-policies/allow-intra-namespace.yaml
```

產生 step-ca 的 values：

```shell
# 一組隨機密碼，同時給 CA 與 provisioner 用。
# 重建 CA 時這組密碼會換掉，簽過的憑證要一併重簽。
PASS_FILE="$(mktemp)"
openssl rand -hex 10 > "$PASS_FILE"

step ca init \
    --deployment-type=standalone \
    --name="the-one" \
    --dns=ca.the-one.k8s \
    --dns=step-certificates.smallstep.svc.cluster.local \
    --dns=localhost \
    --dns=127.0.0.1 \
    --address=':9000' \
    --password-file="$PASS_FILE" \
    --provisioner="the-one" \
    --provisioner-password-file="$PASS_FILE" \
    --acme --remote-management \
    --helm > values/step-certificates.yaml

rm -f "$PASS_FILE"

# 憑證期限：最長 10 年，預設 30 天
yq e -i '.inject.config.files."ca.json".authority.claims.maxTLSCertDuration = "87600h"' values/step-certificates.yaml
yq e -i '.inject.config.files."ca.json".authority.claims.defaultTLSCertDuration = "720h"' values/step-certificates.yaml
```

step-issuer 的 values 由上一步的產出推導，不要手寫：

```shell
CA_BUNDLE="$(yq '.inject.certificates.root_ca' values/step-certificates.yaml | base64)"
KID="$(yq -r '.inject.config.files."ca.json".authority.provisioners[] | select(.type == "JWK") | .key.kid' values/step-certificates.yaml)"

sed -e "s|REPLACE_WITH_BASE64_ROOT_CA|$CA_BUNDLE|" \
    -e "s|REPLACE_WITH_PROVISIONER_KID|$KID|" \
    values/step-issuer.yaml.example > values/step-issuer.yaml
```

安裝：

```shell
helm upgrade --install step-certificates -n smallstep \
  --repo https://smallstep.github.io/helm-charts/ step-certificates --version 1.30.1 \
  -f ./values/step-certificates.yaml

helm upgrade --install step-issuer -n smallstep \
  --repo https://smallstep.github.io/helm-charts/ step-issuer --version 1.11.0 \
  -f ./values/step-issuer.yaml
```

### Default Gateway

```shell
kubectl apply -f ./manifests/gw-default-with-cilium.yaml
#kubectl apply -f ./manifests/gw-default-with-cloud-provider-kind.yaml

# Cilium Hubble UI
kubectl apply -f ./manifests/cilium/hubble-ui-http-route.yaml
```

### Trust the self-signed CA

```shell
kubectl -n smallstep get cm step-certificates-certs -o yaml | yq 'del(.metadata.namespace)' | kubectl -n cert-manager apply -f -

kubectl apply -f - <<EOF
---
apiVersion: trust.cert-manager.io/v1alpha1
kind: Bundle
metadata:
  name: trust-ca
spec:
  sources:
    - configMap:
        name: "step-certificates-certs"
        key: "root_ca.crt"
  target:
    configMap:
      key: "ca.crt"
    namespaceSelector:
      matchLabels:
        trust.cert-manager.io/inject: "trust-ca"
EOF
```

## install seaweedfs

```shell

kubectl create ns seaweedfs --dry-run=client -o yaml | kubectl apply -f -

# NP：只需要 namespace 內部互連。
# default-deny 與 DNS 一律由 CCNP 統一處理 (01 / 02)：
#   - default-deny-all 與 ccnp-default-deny 完全重複
#   - DNS 不能在這裡用 L4 放行，會壓過 CCNP 的 L7 rules.dns，
#     使 DNS proxy 失效，連帶讓 toFQDNs 永遠學不到 IP
kubectl apply -n seaweedfs \
  -f ./manifests/network-policies/allow-intra-namespace.yaml

# 管理者帳密從不進版控的 credentials.env 讀取 (格式見 credentials.env.example)
set -a; source ./manifests/credentials.env; set +a
kubectl create secret generic seaweedfs-credentials \
  -n seaweedfs \
  --from-file=seaweedfs_s3_config=./manifests/seaweedfs/s3.config \
  --from-literal=admin_username="$SEAWEEDFS_ADMIN_USERNAME" \
  --from-literal=admin_password="$SEAWEEDFS_ADMIN_PASSWORD"

helm upgrade --install seaweedfs \
  --repo https://seaweedfs.github.io/seaweedfs/helm seaweedfs --version 4.48.0 \
  -n seaweedfs --create-namespace \
  -f ./values/seaweedfs.yaml

```

## Mailpit - SMTP/POP3/IMAP Server

```shell
kubectl create ns mailpit --dry-run=client -o yaml | kubectl apply -f -

# NP
kubectl apply -n mailpit \
  -f ./manifests/network-policies/allow-intra-namespace.yaml

helm upgrade --install mailpit -n mailpit ./charts/mailpit

```

## Observability
```shell

kubectl create ns observability --dry-run=client -o yaml | kubectl apply -f -

# NP
kubectl apply -n observability -f ./manifests/network-policies/allow-intra-namespace.yaml

```

## k8s-monitoring

```shell
helm upgrade --install k8s-monitoring -n observability \
  --repo https://grafana.github.io/helm-charts k8s-monitoring --version 4.5.2 \
  -f ./values/k8s-monitoring.yaml
```

### Prometheus

```shell
helm upgrade --install prometheus -n observability \
  oci://ghcr.io/prometheus-community/charts/prometheus --version 29.35.0 \
  -f ./values/prometheus.yaml
```

### Loki

```shell
# 從 SeaweedFS 的 s3.config 取出 identity "loki" 的 AK/SK
S3_CONFIG=./manifests/seaweedfs/s3.config
LOKI_S3_AK=$(jq -er '.identities[] | select(.name == "loki") | .credentials[0].accessKey' "$S3_CONFIG")
LOKI_S3_SK=$(jq -er '.identities[] | select(.name == "loki") | .credentials[0].secretKey' "$S3_CONFIG")

# dry-run + apply：secret 已存在時直接更新，可重複執行
kubectl create secret generic loki-credentials -n observability \
  --from-literal=LOKI_S3_AK="$LOKI_S3_AK" \
  --from-literal=LOKI_S3_SK="$LOKI_S3_SK" \
  --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install loki -n observability \
  --repo https://grafana-community.github.io/helm-charts/ loki --version 18.13.7 \
  -f ./values/loki.yaml

```

## Grafana Operator with Grafana

```shell
helm upgrade -i grafana-operator \
  oci://ghcr.io/grafana/helm-charts/grafana-operator --version 5.25.0 \
  -n observability --create-namespace
```

```shell
# Grafana CRD (管理者帳密從 credentials.env 讀取，heredoc 不加引號才會展開變數)
set -a; source ./manifests/credentials.env; set +a
kubectl apply -f - <<EOF
---
apiVersion: grafana.integreatly.org/v1beta1
kind: Grafana
metadata:
  name: grafana
  namespace: observability
  labels:
    dashboards: "grafana"
spec:
  httpRoute:
    spec:
      parentRefs:
        - name: default
          namespace: kube-system
      hostnames:
        - grafana.the-one.k8s
  config:
    log:
      mode: "console"
    server:
      # alert 通知中 Generator / Silence 連結使用的網址
      root_url: "https://grafana.the-one.k8s"
    security:
      admin_user: "${GRAFANA_ADMIN_USER}"
      admin_password: "${GRAFANA_ADMIN_PASSWORD}"
    smtp:
      enabled: "true"
      host: "mailpit.mailpit.svc.cluster.local:1025"
      from_address: "grafana@the-one.local"
      from_name: "The One on Grafana"
EOF

kubectl apply -n observability -f ./manifests/grafana-operator/GrafanaDatasource/loki.yaml
kubectl apply -n observability -f ./manifests/grafana-operator/GrafanaDatasource/prometheus.yaml


```

## Suricata

網路層的偵測來源，與 Falco（主機層）、K8s Audit（控制平面）互補。
三者都進 Loki 供查詢，alert 另外送進 Wazuh 做分級與收斂。

**視野限制先寫在這裡**：Cilium native routing 下同節點的 Pod 互連不經過
eth0，Suricata 只看得到**跨節點與進出叢集**的流量。驗證規則時務必讓
來源與目的落在不同節點，否則會得到「規則沒問題但一筆都沒觸發」的假象。

rules image 由 suricata-update 產生 (ET Open + local.rules)。
規則內容進 image 而不是 ConfigMap：ET Open 展開後約 53000 條、十餘 MB，
遠超過 ConfigMap 的 1 MiB 上限，而且 image tag 本身就是可回滾的版本。

```shell
# tag 以建置日期為版本，需與 suricata/values.yaml 的 rules.image.tag 一致
RULES_TAG=2026.09.30.3
docker build -t harbor.example.com/security/suricata-rules:$RULES_TAG ./charts/suricata/suricata-rules

# 以 chart 實際的 suricata.yaml 驗證，任何規則載入失敗都會回傳非 0。
# 建置階段的 suricata -T 用的是 image 預設設定，抓不到 chart 少宣告
# address-group 變數這類問題，所以這一步不能省。
./charts/suricata/validate-rules.sh $RULES_TAG

kind load docker-image -n the-one harbor.example.com/security/suricata-rules:$RULES_TAG

helm upgrade --install suricata -n kube-security ./charts/suricata
```

### 驗證

```shell
# 1) 規則載入：應為 "N rules successfully loaded, 0 rules failed"
kubectl logs -n kube-security -l app.kubernetes.io/name=suricata -c suricata --tail=50 \
    | grep "rules successfully loaded"

# 2) 煙霧測試：從一個節點上的 Pod ping 另一個節點上的 Pod（同節點不會被看到），
#    eve.json 應出現 sid 1000001
kubectl exec -n kube-security ds/suricata -c suricata -- \
    sh -c 'grep -c "K8s Suricata ICMP Test" /var/log/suricata/eve.json'

# 3) 事件組成：alert 佔比應遠低於其他 event_type；
#    若 STREAM / HTTP 協定異常又佔多數，表示 disable.conf 沒進到 rules image
kubectl exec -n kube-security ds/suricata -c suricata -- \
    sh -c 'grep -o "\"event_type\":\"[a-z_]*\"" /var/log/suricata/eve.json | sort | uniq -c | sort -rn'

# 4) 磁碟：logrotate sidecar 應把 eve.json 壓在 values 的 logs.rotate.size 以內
kubectl exec -n kube-security ds/suricata -c suricata -- ls -lh /var/log/suricata/

# 5) 進到 Wazuh：alert 應解碼為 rule 100201 以上（而非兜底規則 1002）
kubectl exec -n kube-security wazuh-manager-master-0 -c wazuh-manager -- \
    sh -c 'grep -o "\"id\":\"1002[0-9][0-9]\"" /var/ossec/logs/alerts/alerts.json | sort | uniq -c'
```

儀表板是 `manifests/grafana-operator/GrafanaDashboard/suricata-ids.yaml`，
由下方 Grafana 那一段的 `kubectl apply -f .../GrafanaDashboard/` 一併套用。
它同時讀 Wazuh 索引（判斷之後）與 Loki（判斷之前），所以要等
`GrafanaDatasource/wazuh.yaml` 與 `loki.yaml` 都就緒才有資料。

`local.rules` 改動後**必須重建 rules image 並換 tag** —— DaemonSet 是從
image 的 `/rules` 複製規則，改檔案不重建等於沒改。

## Install falco

Falco 由 **falco-operator** 部署，不要再用 falco 這個 Helm chart。
兩者會搶同一個名為 `falco` 的 DaemonSet（Helm 用標籤、operator 用
ownerReferences），而且 Falco CR 帶 `blockOwnerDeletion: true`，
拆解時順序弄反會留下孤兒資源。

四個步驟有順序相依，不能跳：

```shell
kubectl create ns kube-security --dry-run=client -o yaml | kubectl apply -f -

# NP
kubectl apply -n kube-security -f ./manifests/network-policies/allow-intra-namespace.yaml

# 1) Operator 本體，提供五個 CRD
#    (Falco / Component / Rulesfile / Plugin / Config)
helm upgrade --install falco-operator \
  --repo https://falcosecurity.github.io/charts falco-operator --version 0.3.1 \
  -n falco-operator --create-namespace

# 2) KOAD 規則的 ConfigMap，必須在 Rulesfile CR 之前建立。
#    鍵名必須正好是 rules.yaml —— Rulesfile CR 寫死這個名字。
#    用檔名當鍵會讓 artifact sidecar 反覆重啟，而日誌裡只有一行
#    info 等級的 "ConfigMap missing expected key"，不會有錯誤。
kubectl -n kube-security create configmap koad-rules-syscall \
  --from-file=rules.yaml=./values/falco/rules-syscall.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n kube-security create configmap koad-rules-k8saudit \
  --from-file=rules.yaml=./values/falco/rules-k8saudit.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

# 3) CR：Config / Plugin / Rulesfile / Falco / Component，
#    以及 k8saudit 的 NodePort Service —— operator 的 Falco CR
#    沒有 service 欄位，那支 Service 要自己維護。
kubectl apply -f ./manifests/falco-operator/

# 4) falcosidekick 不再是 falco chart 的子 chart，要獨立安裝。
#    Falco 端靠 Config CR 的 http_output 指過來，chart 會自動串接的
#    那件事 operator 不會做。
helm upgrade --install falcosidekick \
  --repo https://falcosecurity.github.io/charts falcosidekick --version 0.14.0 \
  -n kube-security -f ./values/falcosidekick.yaml
```

### 部署很慢的話

artifact sidecar 是在 Pod 內用自己的 OCI client 拉 plugin 與 ruleset，
**不經過 containerd**，所以 `registry/certs.d` 那套 containerd mirror
設定完全幫不上忙。

實測單一個 5 MB 的 plugin blob 直連 ghcr.io 要 **148 秒**，而本機 mirror
快取命中是 **0.008 秒**。四個 plugin 加兩個 ruleset、每個節點各拉一份，
差別就是「十幾分鐘」與「幾秒」。

`manifests/falco-operator/` 裡的 CR 已指向 `mirror-ghcr-io:5000`
(`registry/compose.yaml` 起的 pull-through proxy)，需要兩項相依：

```shell
# Pod 要能解析 mirror-ghcr-io。CoreDNS 的 hosts 區塊負責這件事，
# IP 來自 docker inspect mirror-ghcr-io（kind network）。
kubectl replace -f ./manifests/coredns/coredns-configmap.yaml
kubectl -n kube-system rollout restart deploy/coredns

# 網路放行。mirror 與節點同網段但不是節點，Cilium 給它
# reserved:world，所以用 toCIDR 而非 toEntities。
kubectl apply -f ./manifests/ccnp/91-falco-oci-mirror.yaml
```

mirror 不可用時，把 CR 裡的 `registry.name` 改回 `ghcr.io` 並移除
`plainHTTP` 即可，只是會慢。

### 驗證

```shell
# 規則有載入，且覆寫生效（Create Privileged Pod 應為 false）
kubectl -n kube-security exec ds/falco -c falco -- \
  falco -c /etc/falco/falco.yaml -L 2>/dev/null \
  | jq -r '.rules[] | select(.info.name|test("KOAD|Create Privileged Pod")) | "\(.info.enabled)\t\(.info.name)"' | head

# 載入順序：數字越大越晚，KOAD 必須在 k8saudit-rules 之後
# （它用到 kevt macro，且要覆寫內建規則）
kubectl -n kube-security exec ds/falco -c falco -- ls /etc/falco/rules.d/

# 稽核 webhook 有沒有通（四個節點都該在 endpoints 裡）
kubectl -n kube-security get endpoints falco-k8saudit-webhook
```

## Zulip

```shell
kubectl create ns zulip --dry-run=client -o yaml | kubectl apply -f -

# NP
kubectl apply -n zulip \
  -f ./manifests/network-policies/allow-intra-namespace.yaml
  
# 管理者帳密從不進版控的 credentials.env 讀取 (格式見 credentials.env.example)
set -a; source ./manifests/credentials.env; set +a

kubectl create secret -n zulip generic zulip-secrets \
    --from-literal=secret-key="$ZULIP_SECRET_KEY" \
    --from-literal=admin-password="$ZULIP_ADMIN_PASSWORD" \
    --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install zulip -n zulip \
  oci://ghcr.io/zulip/helm-charts/zulip --version 2.3.0 \
  -f ./values/zulip.yaml
  
kubectl apply -f ./manifests/zulip/httproute-zulip.yaml
```

### 整合 Zulip 與 Falco

```shell
kubectl apply -f ./manifests/ccnp/50-falcosidekick-zulip.yaml

ZULIP_URL=$(bash ./scripts/zulip-integration-url.sh slack_incoming SOC falco)
kubectl -n kube-security create secret generic falco-zulip-webhook --from-literal=SLACK_WEBHOOKURL="$ZULIP_URL" --dry-run=client -o yaml | kubectl apply -f -

# Zulip 的 webhook 設定在 falcosidekick，不在 Falco，
# 所以這裡重裝的是 falcosidekick 而非 falco。
helm upgrade --install falcosidekick -n kube-security \
  --repo https://falcosecurity.github.io/charts falcosidekick --version 0.14.0 \
  -f ./values/falcosidekick.yaml
```

### 整合 Zulip 、Mailpit 到 Grafana

```shell
kubectl -n observability apply -f - <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: contacts
stringData:
  alert-mails: "foo@the-one.local"
EOF

ZULIP_URL=$(bash ./scripts/zulip-integration-url.sh grafana SOC grafana)
kubectl -n observability create secret generic zulip-grafana-webhook --from-literal=url="$ZULIP_URL" --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -n observability -f ./manifests/grafana-operator/GrafanaContactPoint/default.yaml
```

### Dashboard

```shell
kubectl apply -n observability -f ./manifests/grafana-operator/GrafanaFolder
kubectl apply -n observability -f ./manifests/grafana-operator/GrafanaDashboard/
```

## Wazuh

```shell
helm upgrade --install wazuh -n kube-security \
  --repo https://morgoved.github.io/wazuh-helm wazuh --version 2.0.7 \
  -f ./values/wazuh.yaml
```
