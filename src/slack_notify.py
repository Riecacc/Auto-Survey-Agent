"""Slack 通知：每篇候选论文发一条独立消息，并轮询 ✅ reaction 确认收录。"""
import json

from src.common import get_env, http_get, http_post_json, load_state, save_state

SLACK_API_BASE = "https://slack.com/api"

_channel_cache = None


def resolve_channel():
    """消息目标：优先用 SLACK_CHANNEL_ID（频道）；否则用 SLACK_USER_ID 打开与 bot 的私信。

    conversations.open 需要 im:write scope；私信频道 ID（D 开头）在同一次运行内缓存。
    """
    global _channel_cache
    if _channel_cache:
        return _channel_cache
    channel = get_env("SLACK_CHANNEL_ID", "")
    if channel:
        _channel_cache = channel
        return channel
    user_id = get_env("SLACK_USER_ID")
    resp = slack_api("conversations.open", payload={"users": user_id})
    _channel_cache = resp["channel"]["id"]
    print(f"[info] 已打开与 {user_id} 的私信频道 {_channel_cache}")
    return _channel_cache


def slack_api(method, payload=None, params=None):
    """调用 Slack Web API，检查响应 ok 字段。"""
    token = get_env("SLACK_BOT_TOKEN")
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{SLACK_API_BASE}/{method}"
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    if payload is not None:
        resp = http_post_json(url, payload, headers=headers)
    else:
        resp = http_get(url, headers=headers)
    data = json.loads(resp)
    if not data.get("ok"):
        raise RuntimeError(f"Slack API {method} 失败: {data.get('error')}")
    return data


def _format_hindex(paper):
    h = paper.get("max_author_hindex")
    return str(h) if h is not None else "未知"


def build_candidate_blocks(paper):
    """构建候选论文的 Slack blocks 消息。"""
    arxiv_id = paper["arxiv_id"]
    venue = paper.get("venue") or "arXiv"
    open_source = "是 ✅" if paper.get("code_url") else "否"
    lines = [
        f"*{paper['title']}*",
        f"Score: *{paper.get('score')}* | Venue: {venue} | 作者最高 h-index: {_format_hindex(paper)} | 开源: {open_source}",
        f"<https://arxiv.org/abs/{arxiv_id}|arXiv:{arxiv_id}>",
    ]
    if paper.get("code_url"):
        lines.append(f"Code: {paper['code_url']}")
    if paper.get("summary_zh"):
        lines.append(f"摘要: {paper['summary_zh']}")
    if paper.get("reason"):
        lines.append(f"打分理由: {paper['reason']}")
    lines.append("确认收录请给本条消息打 ✅")
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        }
    ]


def post_candidates(papers):
    """每篇候选发一条独立消息，消息 ts 追加进 candidates 状态。"""
    if not papers:
        print("[info] 没有候选论文，跳过 Slack 通知")
        return
    channel = resolve_channel()
    candidates = load_state("candidates")
    existing = {c["arxiv_id"] for c in candidates if c.get("status") == "pending"}

    for paper in papers:
        if paper["arxiv_id"] in existing:
            print(f"[info] {paper['arxiv_id']} 已在候选列表中，跳过")
            continue
        resp = slack_api("chat.postMessage", payload={
            "channel": channel,
            "text": f"候选论文: {paper['title']}",
            "blocks": build_candidate_blocks(paper),
        })
        candidates.append({
            "arxiv_id": paper["arxiv_id"],
            "slack_ts": resp["ts"],
            "status": "pending",
            "score": paper.get("score"),
            "title": paper["title"],
            "venue": paper.get("venue"),
            "max_author_hindex": paper.get("max_author_hindex"),
            "citation_count": paper.get("citation_count"),
            "code_url": paper.get("code_url"),
            "summary_zh": paper.get("summary_zh"),
            "reason": paper.get("reason"),
            "tweet_en": paper.get("tweet_en"),
            "published": paper.get("published"),
        })
        print(f"[info] 已推送候选 {paper['arxiv_id']}: {paper['title']}")
    save_state("candidates", candidates)


def check_confirmations():
    """对所有 pending 候选轮询 reactions，含 white_check_mark 即确认。返回确认的候选列表。"""
    channel = resolve_channel()
    candidates = load_state("candidates")
    confirmed = []
    for c in candidates:
        if c.get("status") != "pending":
            continue
        try:
            resp = slack_api("reactions.get", params={
                "channel": channel,
                "timestamp": c["slack_ts"],
            })
        except RuntimeError as e:
            # 消息被删等情况：打印警告并跳过该条
            print(f"[warn] 查询 {c['arxiv_id']} 的 reaction 失败，跳过: {e}")
            continue
        reactions = resp.get("message", {}).get("reactions", [])
        if any(r.get("name") == "white_check_mark" for r in reactions):
            confirmed.append(c)
    return confirmed


def reply_in_thread(slack_ts, text):
    """在候选消息的线程里回复。"""
    channel = resolve_channel()
    return slack_api("chat.postMessage", payload={
        "channel": channel,
        "thread_ts": slack_ts,
        "text": text,
    })
