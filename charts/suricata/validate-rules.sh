#!/usr/bin/env bash
# 用 chart 實際渲染的 suricata.yaml 驗證 rules image，任何一條規則載入失敗即回傳非 0。
# 建置階段的 suricata -T 使用 image 預設設定，抓不到 chart 缺少變數 (如 $HTTP_SERVERS) 這類問題。
#
# 用法：./validate-rules.sh [rules-image-tag]   (預設讀 values.yaml 的 rules.image.tag)
set -euo pipefail

CHART_DIR="$(cd "$(dirname "$0")" && pwd)"
SURICATA_IMAGE="$(yq '.image.repository + ":" + .image.tag' "$CHART_DIR/values.yaml")"
RULES_REPO="$(yq '.rules.image.repository' "$CHART_DIR/values.yaml")"
RULES_TAG="${1:-$(yq '.rules.image.tag' "$CHART_DIR/values.yaml")}"
RULES_IMAGE="$RULES_REPO:$RULES_TAG"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; docker rm -f "$WORK_CONTAINER" >/dev/null 2>&1 || true' EXIT
WORK_CONTAINER="validate-rules-$$"

helm template suricata "$CHART_DIR" --show-only templates/configmap.yaml \
    | yq '.data."suricata.yaml"' > "$WORK/suricata.yaml"

docker create --name "$WORK_CONTAINER" "$RULES_IMAGE" >/dev/null
docker cp "$WORK_CONTAINER:/rules" "$WORK/rules"

echo "Validating $RULES_IMAGE with $SURICATA_IMAGE and chart config..."
docker run --rm \
    -v "$WORK/suricata.yaml:/etc/suricata/suricata.yaml:ro" \
    -v "$WORK/rules:/var/lib/suricata/rules:ro" \
    --entrypoint suricata "$SURICATA_IMAGE" \
    -T -c /etc/suricata/suricata.yaml -l /tmp > "$WORK/test.log" 2>&1 || true

summary="$(grep -E 'rules successfully loaded' "$WORK/test.log" || true)"
echo "${summary#*detect: }"

failed="$(sed -nE 's/.* ([0-9]+) rules failed.*/\1/p' <<<"$summary")"
if [[ -z "$summary" || "${failed:-1}" != "0" ]]; then
    echo "Validation FAILED. Errors:" >&2
    # 依錯誤類型歸納；head 提早關閉 pipe 會觸發 SIGPIPE，不能讓它影響 exit code
    grep -E '^(Error|E):' "$WORK/test.log" \
        | sed -E 's/"[^"]*"/"…"/g; s/(error parsing signature).*/\1 …/' \
        | sort | uniq -c | sort -rn | head -20 >&2 || true
    exit 1
fi
echo "Validation passed."
