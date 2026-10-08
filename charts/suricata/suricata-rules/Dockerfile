# Stage 1：以與 DaemonSet 相同版本的 Suricata 產生規則並驗證
FROM jasonish/suricata:8.0.6 AS build

COPY update.yaml enable.conf disable.conf modify.conf /etc/suricata/
COPY rules/local.rules /etc/suricata/rules/local.rules
COPY sources.txt /tmp/sources.txt

# 未指定 --no-test，suricata-update 會以 suricata -T 驗證；規則有誤就讓建置失敗
RUN suricata-update update-sources \
    && grep -Ev '^\s*(#|$)' /tmp/sources.txt | xargs -r -n1 suricata-update enable-source \
    && suricata-update --config /etc/suricata/update.yaml --no-reload --fail \
    && suricata-update list-enabled-sources

# Stage 2：只保留產出的規則，由 DaemonSet 的 initContainer 複製到 /var/lib/suricata/rules
FROM alpine:3.22

COPY --from=build /var/lib/suricata/rules/ /rules/
