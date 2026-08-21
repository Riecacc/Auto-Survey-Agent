"""调用 Kimi（Moonshot）API 按评估规则给论文打分，筛选候选。"""
import json
import re

from src.common import PROJECT_ROOT, get_env, http_post_json

KIMI_API_URL_DEFAULT = "https://api.moonshot.cn/v1/chat/completions"
BATCH_SIZE = 10
CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# milestone 方向的高门槛：score ≥ 9 且必须给出 milestone_reason
MILESTONE_SCORE_THRESHOLD = 9
TAXONOMY_PATH = PROJECT_ROOT / "paperlist" / "data" / "taxonomy.json"


def load_ranking_rules():
    """读取 skills/paper-ranking/SKILL.md 全文作为 system prompt。"""
    with open(PROJECT_ROOT / "skills" / "paper-ranking" / "SKILL.md", "r", encoding="utf-8") as f:
        return f.read()


def load_taxonomy():
    """读取 paperlist/data/taxonomy.json 的方向清单；缺失时降级返回 None（不带方向清单打分）。"""
    if not TAXONOMY_PATH.exists():
        print(f"[warn] 未找到 {TAXONOMY_PATH}，本次打分不带方向清单（向后兼容模式）")
        return None
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("directions", [])


def build_system_prompt(rules, directions):
    """system prompt = 评估规则全文 + 方向清单（id / 名称 / 描述 / tier / 子类）。"""
    if not directions:
        return rules
    catalog = [
        {
            "id": d["id"],
            "name": d.get("name", ""),
            "description": d.get("description", ""),
            "tier": d.get("tier", "core"),
            "subdirections": [
                {"id": s["id"], "name": s.get("name", "")}
                for s in (d.get("subdirections") or [])
            ],
        }
        for d in directions
    ]
    return rules + "\n\n## 方向清单（taxonomy）\n\n" + json.dumps(catalog, ensure_ascii=False, indent=2)


def call_kimi(messages):
    api_key = get_env("KIMI_API_KEY")
    # 开放平台（按量付费）：https://api.moonshot.cn/v1/chat/completions（默认）
    # Coding Plan（订阅）：设为 https://api.kimi.com/coding/v1/chat/completions，
    # 并将 KIMI_MODEL 设为 k3-256k（k3 的 1M 上下文版本消耗约两倍配额）
    api_url = get_env("KIMI_API_URL", KIMI_API_URL_DEFAULT)
    model = get_env("KIMI_MODEL", "kimi-k3")
    # 注意：K3 固定 temperature=1.0，官方建议不要显式传 temperature；
    # K3 始终开启思考，reasoning_effort=low 可降低批量打分的耗时与成本
    payload = {"model": model, "messages": messages, "reasoning_effort": "low"}
    resp = http_post_json(api_url, payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=120)
    data = json.loads(resp)
    return data["choices"][0]["message"]["content"]


def parse_ranking_json(content):
    """剥离 markdown 代码围栏后解析 JSON 数组。"""
    cleaned = CODE_FENCE_RE.sub("", content.strip()).strip()
    return json.loads(cleaned)


def rank_batch(papers_batch, system_prompt):
    """对一批论文打分；解析失败时返回空列表并打印警告，不中断。"""
    user_content = (
        "请按 system 中的评估规则先判定方向归属再为以下论文打分，输出严格 JSON 数组"
        "（每个元素含 arxiv_id, score, reason, summary_zh, tweet_en, directions,"
        " subdirections, tags, milestone_reason 字段）：\n"
        + json.dumps(papers_batch, ensure_ascii=False, indent=2)
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    try:
        content = call_kimi(messages)
        results = parse_ranking_json(content)
        if not isinstance(results, list):
            raise ValueError("模型输出不是 JSON 数组")
        return results
    except Exception as e:
        print(f"[warn] 批次打分失败，已跳过该批（{len(papers_batch)} 篇）: {e}")
        return []


def passes_threshold(paper, threshold, milestone_ids):
    """过滤逻辑：directions 全部落在 milestone 方向时需 score≥9 且 milestone_reason 非空，
    其余（含无方向清单的兼容模式）沿用 SCORE_THRESHOLD。"""
    score = paper.get("score") or 0
    directions = paper.get("directions") or []
    if milestone_ids and directions and all(d in milestone_ids for d in directions):
        return score >= MILESTONE_SCORE_THRESHOLD and bool(paper.get("milestone_reason"))
    return score >= threshold


def rank_papers(papers):
    """分批打分，合并结果，按 score 降序，仅返回通过门槛的候选。"""
    if not papers:
        return []
    directions = load_taxonomy()
    system_prompt = build_system_prompt(load_ranking_rules(), directions)
    milestone_ids = {d["id"] for d in (directions or []) if d.get("tier") == "milestone"}
    threshold = int(get_env("SCORE_THRESHOLD", "7"))

    by_id = {p["arxiv_id"]: p for p in papers}
    ranked = []
    for i in range(0, len(papers), BATCH_SIZE):
        batch = papers[i:i + BATCH_SIZE]
        print(f"[info] 打分批次 {i // BATCH_SIZE + 1}（{len(batch)} 篇）")
        for item in rank_batch(batch, system_prompt):
            arxiv_id = item.get("arxiv_id")
            if arxiv_id not in by_id:
                continue
            paper = dict(by_id[arxiv_id])
            paper["score"] = item.get("score")
            paper["reason"] = item.get("reason", "")
            paper["summary_zh"] = item.get("summary_zh", "")
            paper["tweet_en"] = item.get("tweet_en", "")
            paper["directions"] = item.get("directions") or []
            paper["subdirections"] = item.get("subdirections") or {}
            paper["tags"] = item.get("tags") or []
            paper["milestone_reason"] = item.get("milestone_reason", "")
            ranked.append(paper)

    ranked.sort(key=lambda p: p.get("score") or 0, reverse=True)
    candidates = [p for p in ranked if passes_threshold(p, threshold, milestone_ids)]
    print(f"[info] 共 {len(ranked)} 篇完成打分，{len(candidates)} 篇通过门槛进入候选")
    return candidates
