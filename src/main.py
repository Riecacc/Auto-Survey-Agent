"""入口：argparse 子命令 scout / confirm / all。"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 支持以 `python src/main.py` 方式运行时导入 src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import get_env, load_state, save_state
from src.fetch_papers import fetch_new_papers
from src.post_x import post_tweet
from src.rank_papers import rank_papers
from src.slack_notify import check_confirmations, post_candidates, reply_in_thread
from src.update_paperlist import add_papers

SEEN_MAX_ENTRIES = 2000


def require_env(*names):
    """检查必需环境变量，缺失时报清晰错误并以退出码 1 退出。"""
    for name in names:
        try:
            get_env(name)
        except RuntimeError as e:
            print(f"[error] {e}", file=sys.stderr)
            sys.exit(1)


def require_slack_target():
    """Slack 消息目标：SLACK_CHANNEL_ID（频道）或 SLACK_USER_ID（私信）二选一。"""
    if not (os.environ.get("SLACK_CHANNEL_ID") or os.environ.get("SLACK_USER_ID")):
        print("[error] 缺少环境变量：SLACK_CHANNEL_ID 或 SLACK_USER_ID（二选一）", file=sys.stderr)
        sys.exit(1)


def require_slack():
    require_env("SLACK_BOT_TOKEN")
    require_slack_target()


def record_seen(papers):
    """把所有抓到的 id 记入 seen.json（无论是否入选），超上限时淘汰最旧。"""
    seen = load_state("seen")
    now = datetime.now(timezone.utc).isoformat()
    for p in papers:
        seen.setdefault(p["arxiv_id"], now)
    if len(seen) > SEEN_MAX_ENTRIES:
        # 按记录时间升序排序，淘汰最旧的
        ordered = sorted(seen.items(), key=lambda kv: kv[1])
        seen = dict(ordered[len(seen) - SEEN_MAX_ENTRIES:])
    save_state("seen", seen)


def cmd_scout():
    require_env("KIMI_API_KEY")
    require_slack()
    papers = fetch_new_papers()
    if not papers:
        print("[info] 没有新论文，scout 结束")
        return
    candidates = rank_papers(papers)
    post_candidates(candidates)
    record_seen(papers)


def cmd_confirm():
    require_slack()
    confirmed = check_confirmations()
    if not confirmed:
        print("[info] 没有已确认的候选，confirm 结束")
        return
    print(f"[info] 共 {len(confirmed)} 篇候选被确认")
    paperlist_changed = add_papers(confirmed)
    for c in confirmed:
        if c.get("tweet_en"):
            post_tweet(c["tweet_en"])
        try:
            reply_in_thread(c["slack_ts"], "已收录到 paper list ✅")
        except RuntimeError as e:
            print(f"[warn] 线程回复失败（{c['arxiv_id']}）: {e}")
    # 重新加载 candidates 状态并标记为已确认后保存
    confirmed_ids = {c["arxiv_id"] for c in confirmed}
    candidates = load_state("candidates")
    for c in candidates:
        if c["arxiv_id"] in confirmed_ids:
            c["status"] = "confirmed"
    save_state("candidates", candidates)
    # 通知 workflow：paperlist 有修改需要 push
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output and paperlist_changed:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write("paperlist_changed=true\n")


def cmd_all():
    # 确认优先：先处理上一轮候选的 ✅，再抓新论文
    cmd_confirm()
    cmd_scout()


def main():
    parser = argparse.ArgumentParser(description="每日论文巡检 agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scout", help="抓取 + 打分 + 推送 Slack 候选")
    sub.add_parser("confirm", help="轮询 ✅ 确认并收录论文")
    sub.add_parser("all", help="先 confirm 后 scout")
    args = parser.parse_args()
    {"scout": cmd_scout, "confirm": cmd_confirm, "all": cmd_all}[args.command]()


if __name__ == "__main__":
    main()
