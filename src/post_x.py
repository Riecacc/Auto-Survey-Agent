"""发布推文到 X（Twitter）。凭证缺失或 HTTP 错误时只警告返回 None，绝不中断流水线。"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import quote

from src.common import get_env, http_post_json

TWEET_URL = "https://api.twitter.com/2/tweets"

X_CREDENTIALS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def _pct(s):
    """OAuth percent-encoding。"""
    return quote(str(s), safe="")


def _oauth1_header(api_key, api_secret, access_token, access_secret):
    """构造 OAuth 1.0a HMAC-SHA1 签名的 Authorization 头。"""
    oauth_params = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    # 签名基串：POST&<url-encoded URL>&<url-encoded 参数串>（JSON body 不参与签名）
    param_str = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(oauth_params.items()))
    base_string = "&".join(["POST", _pct(TWEET_URL), _pct(param_str)])
    signing_key = f"{_pct(api_secret)}&{_pct(access_secret)}"
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()
    oauth_params["oauth_signature"] = signature
    header_str = ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth_params.items()))
    return f"OAuth {header_str}"


def post_tweet(text):
    """发推；凭证缺失或失败时打印警告并返回 None。"""
    creds = {}
    for name in X_CREDENTIALS:
        try:
            creds[name] = get_env(name)
        except RuntimeError:
            print("[info] 跳过发推（未配置 X 凭证）")
            return None
    try:
        auth = _oauth1_header(
            creds["X_API_KEY"], creds["X_API_SECRET"],
            creds["X_ACCESS_TOKEN"], creds["X_ACCESS_SECRET"],
        )
        resp = http_post_json(
            TWEET_URL,
            {"text": text[:280]},
            headers={"Authorization": auth},
        )
        tweet_id = json.loads(resp).get("data", {}).get("id")
        print(f"[info] 已发推: {tweet_id}")
        return tweet_id
    except Exception as e:
        # 例如 free tier 限制等，只警告不中断
        print(f"[warn] 发推失败（已跳过）: {e}")
        return None
