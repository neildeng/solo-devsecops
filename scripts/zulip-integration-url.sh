#!/bin/bash
# 產生 Zulip incoming webhook 整合網址 (等同 UI 的「為整合產生網址」)
# bot 與頻道不存在時會建立，已存在則沿用，可重複執行
#
# 用法: ./zulip-integration-url.sh <integration> <stream> [bot 短名] [topic]
# topic 省略時由整合自行決定 (例如 grafana 用 [alertname])
# 範例: ./zulip-integration-url.sh grafana AIOps grafana
#
# 環境變數:
#   ZULIP_URL_SCHEME  產出網址的 scheme，預設 http://
#                     叢集內的消費端 (Grafana、falcosidekick) 走 Service port 80，
#                     沒有 TLS；用 https:// 會連到 Gateway 並因自簽憑證而失敗。
#                     host 仍維持正式網域，否則過不了 Django 的 ALLOWED_HOSTS 檢查，
#                     叢集內要靠 CoreDNS rewrite 把它導到 Service。
#                     要給叢集外使用時設 ZULIP_URL_SCHEME=https://
#   ZULIP_NAMESPACE   Zulip 所在 namespace，預設 zulip
#   ZULIP_OWNER_EMAIL realm owner 的 email，預設 admin@the-one.local
#   DRY_RUN           非空值時最後 rollback，不實際建立 bot 與頻道
set -euo pipefail

INTEGRATION=${1:?usage: $0 <integration> <stream> [bot-short-name] [topic]}
STREAM=${2:?usage: $0 <integration> <stream> [bot-short-name] [topic]}
BOT_SHORT_NAME=${3:-$INTEGRATION}
TOPIC=${4:-}
NAMESPACE=${ZULIP_NAMESPACE:-zulip}
OWNER_EMAIL=${ZULIP_OWNER_EMAIL:-admin@the-one.local}
URL_SCHEME=${ZULIP_URL_SCHEME:-http://}

kubectl exec -i -n "$NAMESPACE" zulip-0 -c zulip -- \
    env INTEGRATION="$INTEGRATION" STREAM="$STREAM" BOT_SHORT_NAME="$BOT_SHORT_NAME" \
        TOPIC="$TOPIC" OWNER_EMAIL="$OWNER_EMAIL" DRY_RUN="${DRY_RUN:-}" \
        URL_SCHEME="$URL_SCHEME" \
    su zulip -c '/home/zulip/deployments/current/manage.py shell' <<'EOF' | grep '^URL=' | cut -d= -f2-
import os
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction

from zerver.actions.create_user import do_create_user
from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib.streams import create_stream_if_needed
from zerver.models import Realm, UserProfile
from zerver.models.realms import get_fake_email_domain

integration = os.environ["INTEGRATION"]
stream_name = os.environ["STREAM"]
short_name = os.environ["BOT_SHORT_NAME"]
topic = os.environ["TOPIC"]

with transaction.atomic():
    realm = Realm.objects.get(string_id="")
    owner = UserProfile.objects.get(realm=realm, delivery_email=os.environ["OWNER_EMAIL"])

    stream, _ = create_stream_if_needed(realm, stream_name, acting_user=owner)

    # 與 UI 建立 bot 時相同的 email 格式: <短名>-bot@<FAKE_EMAIL_DOMAIN>
    bot_email = f"{short_name}-bot@{get_fake_email_domain(realm.host)}"
    bot = UserProfile.objects.filter(realm=realm, delivery_email__iexact=bot_email).first()
    if bot is None:
        bot = do_create_user(
            bot_email,
            None,
            realm,
            short_name,
            bot_type=UserProfile.INCOMING_WEBHOOK_BOT,
            bot_owner=owner,
            acting_user=owner,
        )
    if bot.bot_type != UserProfile.INCOMING_WEBHOOK_BOT or not bot.is_active:
        raise SystemExit(f"{bot_email} is not an active incoming webhook bot")

    # 頻道若為私人頻道，bot 必須是訂閱者才能發訊息
    bulk_add_subscriptions(realm, [stream], [bot], acting_user=owner)

    params = {"api_key": bot.api_key, "stream": stream.id}
    # 帶空的 topic= 會讓訊息落在「一般聊天」，省略時各整合才會用自己的 topic (例如 grafana 用 [alertname])
    if topic:
        params["topic"] = topic
    query = urlencode(params)
    # 預設用 http://；Zulip 自己的 EXTERNAL_URI_SCHEME 是 https://，
    # 那是給瀏覽器用的，叢集內的整合走 Service 沒有 TLS
    scheme = os.environ.get("URL_SCHEME") or settings.EXTERNAL_URI_SCHEME
    print(f"URL={scheme}{realm.host}/api/v1/external/{integration}?{query}")

    if os.environ["DRY_RUN"]:
        transaction.set_rollback(True)
EOF
