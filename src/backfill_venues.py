"""历史回填工具：从系统/架构顶会（DBLP）拉取 2024 至今的论文，初筛 + Kimi 精筛生成待确认清单。

子命令：
  fetch           只跑 DBLP 拉取 + 本地关键词初筛，产出 state/backfill_prefilter.json（不需密钥）
  rank            读初筛清单，调 Kimi 精筛，产出 state/backfill_review.json（需 KIMI_API_KEY）
  all             先 fetch 后 rank
  seed <path>     种子清单入库辅助：读取用户 JSON 清单，用 arXiv API 补全元数据，
                  产出 state/seed_review.json（主流程不自动跑）

调试范围控制（环境变量）：BACKFILL_VENUES="ISCA,MICRO"、BACKFILL_YEARS="2024" 可缩小抓取范围。
"""
import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

# 支持以 `python src/backfill_venues.py` 方式运行时导入 src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import PROJECT_ROOT, get_env, http_get, load_state, save_state
from src.fetch_papers import ARXIV_API, extract_code_url, parse_arxiv_feed
from src.rank_papers import BATCH_SIZE, call_kimi, parse_ranking_json

DBLP_API = "https://dblp.org/search/publ/api"
DBLP_MAX_RETRIES = 3
REQUEST_INTERVAL = 1.5  # 礼貌抓取：每个请求间隔秒数
START_YEAR = 2024

# 回填只覆盖系统类 + 架构类 10 个会议；venue_type 会写入产出条目
VENUES = {
    "MLSys": "system",
    "EuroSys": "system",
    "SOSP": "system",
    "OSDI": "system",
    "NSDI": "system",
    "SIGCOMM": "system",
    "ISCA": "architecture",
    "MICRO": "architecture",
    "HPCA": "architecture",
    "ASPLOS": "architecture",
}

# 6 方向分类体系。优先读 paperlist/data/taxonomy.json；
# 若其内容还是旧的 9 方向（并行改造未完成），用这里的硬编码兜底并打印警告。
DIRECTIONS_FALLBACK = [
    {"id": "01-vla-wam-systems-serving", "name": "VLA/WAM 系统与 Serving",
     "description": "面向 VLA / 世界模型 / 机器人基础模型的推理与服务系统、运行时、部署设施"},
    {"id": "02-vla-wam-accelerators", "name": "VLA/WAM 加速器与架构",
     "description": "面向 VLA/世界模型/多模态大模型负载的加速器、GPU/FPGA/存算架构与硬件优化"},
    {"id": "03-vla-wam-efficient-algorithms", "name": "VLA/WAM 高效算法",
     "description": "面向 VLA/世界模型的量化、稀疏、蒸馏、推测解码、高效注意力等算法-系统协同优化"},
    {"id": "04-vla-wam-foundation-models", "name": "VLA/WAM 基座模型",
     "description": "milestone：具有领域影响力的 VLA / 世界模型本体工作（仅收录里程碑级，需 score≥9）"},
    {"id": "05-general-efficient-ml", "name": "通用高效 ML 方法",
     "description": "milestone：影响面超出具身智能的通用高效 ML 系统/算法工作（仅收录里程碑级，需 score≥9）"},
    {"id": "06-general-ml-systems", "name": "通用 ML 系统与 Serving",
     "description": "LLM / 多模态 serving 与推理系统，需与具身智能推理有明确技术迁移关系"},
]
EXPECTED_DIRECTION_IDS = [d["id"] for d in DIRECTIONS_FALLBACK]
MILESTONE_DIRECTIONS = {"04-vla-wam-foundation-models", "05-general-efficient-ml"}

CORE_SCORE_THRESHOLD = 7        # core 方向（01/02/03/06）入选线
MILESTONE_SCORE_THRESHOLD = 9   # milestone 方向（04/05）入选线，且必须有 milestone_reason

# ---- 本地初筛关键词表（按需调整）----
# subject 词：论文研究对象与 Physical AI / 大模型相关
SUBJECT_KEYWORDS = [
    "robot", "embodied", "vision-language-action", "vla", "world model", "manipulation",
    "locomotion", "autonomous driving", "self-driving", "autonomous vehicle", "humanoid",
    "multimodal", "multi-modal", "vision-language", "llm", "large language model",
    "foundation model", "inference", "serving", "video generation", "diffusion",
    "transformer", "agent", "motion planning", "slam",
]
# aspect 词：论文切入点与系统/架构/效率相关
ASPECT_KEYWORDS = [
    "serving", "inference", "runtime", "accelerator", "fpga", "gpu", "npu", "tpu",
    "scheduling", "memory", "cache", "quantization", "sparsity", "sparse", "real-time",
    "edge", "distributed", "compilation", "compiler", "kernel", "parallel", "latency",
    "throughput", "energy-efficient", "hardware", "chip", "interconnect", "prefetch",
    "batching", "speculative", "offload", "datacenter", "data center", "cluster",
    "training", "pipeline",
]
# 强命中词：标题直接命中即认为主题相关（无需再配 aspect 词）
STRONG_KEYWORDS = [
    "vision-language-action", "vla", "robot", "embodied", "world model",
    "humanoid", "manipulation", "locomotion", "autonomous driving",
]


def _compile_keywords(keywords):
    """编译关键词为正则（词边界匹配，避免 vla/edge 等短词误伤）。"""
    return [(kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)) for kw in keywords]


SUBJECT_RES = _compile_keywords(SUBJECT_KEYWORDS)
ASPECT_RES = _compile_keywords(ASPECT_KEYWORDS)
STRONG_RES = _compile_keywords(STRONG_KEYWORDS)

ARXIV_EE_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?")


def load_directions():
    """读取 6 方向分类体系；taxonomy.json 仍是旧 9 方向时回退到硬编码并警告。"""
    path = PROJECT_ROOT / "paperlist" / "data" / "taxonomy.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ids = [d.get("id") for d in data.get("directions", [])]
        if ids == EXPECTED_DIRECTION_IDS:
            return data["directions"]
        print(f"[warn] {path} 的方向体系与预期 6 方向不符（当前 {len(ids)} 个），"
              "使用硬编码兜底（并行 taxonomy 改造可能尚未完成）")
    except (OSError, json.JSONDecodeError) as e:
        print(f"[warn] 读取 taxonomy.json 失败（{e}），使用硬编码 6 方向兜底")
    return DIRECTIONS_FALLBACK


def target_venues_years():
    """确定抓取范围，支持 BACKFILL_VENUES / BACKFILL_YEARS 环境变量缩小范围（调试用）。"""
    venues = list(VENUES)
    years = list(range(START_YEAR, datetime.now(timezone.utc).year + 1))
    env_venues = get_env("BACKFILL_VENUES", "")
    if env_venues:
        venues = [v.strip() for v in env_venues.split(",") if v.strip() in VENUES]
    env_years = get_env("BACKFILL_YEARS", "")
    if env_years:
        years = [int(y.strip()) for y in env_years.split(",") if y.strip()]
    return venues, years


def extract_arxiv_id_from_ee(ee):
    """从电子版链接（ee）推断 arxiv_id，推断不出返回 None。"""
    if not ee:
        return None
    m = ARXIV_EE_RE.search(ee)
    return m.group(1) if m else None


def parse_dblp_hits(data, venue, year):
    """解析 DBLP 搜索 API 返回的 JSON，提取论文元数据（标题/作者做 HTML 实体反转义）。"""
    hits = data.get("result", {}).get("hits", {})
    if int(hits.get("@total", 0)) == 0:
        return []
    raw_hits = hits.get("hit", [])
    if isinstance(raw_hits, dict):  # 只有 1 条时 DBLP 返回 dict 而非 list
        raw_hits = [raw_hits]
    papers = []
    for h in raw_hits:
        info = h.get("info", {})
        # 作者字段可能是单个 dict 或 dict 列表
        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [html.unescape(a.get("text", "") if isinstance(a, dict) else str(a)) for a in authors_raw]
        ee = info.get("ee")
        if isinstance(ee, list):
            ee = ee[0] if ee else None
        title = html.unescape((info.get("title") or "").strip())
        # DBLP 标题末尾常带句点，去掉便于后续展示与匹配
        title = re.sub(r"\.$", "", title)
        papers.append({
            "dblp_key": info.get("key", ""),
            "title": title,
            "authors": authors,
            "year": str(info.get("year") or year),
            "venue": info.get("venue") or venue,
            "venue_type": VENUES[venue],
            "doi": info.get("doi"),
            "ee": ee,
            "arxiv_id": extract_arxiv_id_from_ee(ee),
        })
    return papers


def fetch_venue_year(venue, year):
    """用 DBLP 搜索 API 拉取某会议某年的论文列表。

    查询方式说明：采用 DBLP 发布搜索 API 的 facet 语法
    `q=venue:{VENUE}: year:{YYYY}:`，DBLP 会将其改写为 venue/year 的 facet 精确匹配
    （`:facetid:venue:"VENUE"`）。DBLP 单页最多返回 100 条（h 参数设更大也会被截断），
    需用 f 参数分页直到拿满 @total。
    DBLP 偶发对突发请求返回非 JSON/限流响应，每页做递增间隔重试。
    """
    collected = []
    offset = 0
    total = None
    while total is None or len(collected) < total:
        url = f"{DBLP_API}?q={quote(f'venue:{venue}: year:{year}:')}&format=json&h=100&f={offset}"
        page = None
        last_err = None
        for attempt in range(DBLP_MAX_RETRIES):
            try:
                resp = http_get(url, headers={"User-Agent": "Auto-Survey-Agent/1.0"})
                page = json.loads(resp)
                break
            except (RuntimeError, json.JSONDecodeError) as e:
                last_err = e
                wait = 5 * (attempt + 1)
                print(f"[warn] DBLP 请求失败（{venue} {year} f={offset}，第 {attempt + 1} 次，{wait}s 后重试）: {e}")
                time.sleep(wait)
        if page is None:
            print(f"[warn] DBLP 抓取最终失败，{venue} {year} 已得 {len(collected)} 篇（可能不完整）: {last_err}")
            break
        hits = page.get("result", {}).get("hits", {})
        total = int(hits.get("@total", 0))
        papers = parse_dblp_hits(page, venue, year)
        if not papers:
            break
        collected.extend(papers)
        offset += len(papers)
        if len(collected) < total:
            time.sleep(REQUEST_INTERVAL)
    return collected


def fetch_all_venues():
    """抓取全部目标会议+年份，返回合并去重后的论文列表。"""
    venues, years = target_venues_years()
    all_papers = []
    for venue in venues:
        for year in years:
            papers = fetch_venue_year(venue, year)
            print(f"[info] DBLP {venue} {year}: {len(papers)} 篇")
            all_papers.extend(papers)
            time.sleep(REQUEST_INTERVAL)
    # 按 dblp_key 去重（venue facet 理论上不重复，防御性处理）
    seen_keys = set()
    deduped = []
    for p in all_papers:
        if p["dblp_key"] and p["dblp_key"] not in seen_keys:
            seen_keys.add(p["dblp_key"])
            deduped.append(p)
    print(f"[info] DBLP 共抓取 {len(deduped)} 篇（去重后）")
    return deduped


def prefilter(papers):
    """本地关键词初筛（不耗 Kimi 配额）。

    命中规则：标题强命中 STRONG 词（robot/embodied/VLA 等直接相关），
    或 subject 词与 aspect 词各命中至少 1 个（大模型 × 系统交叉）。
    """
    kept = []
    for p in papers:
        title = p["title"]
        strong = [kw for kw, r in STRONG_RES if r.search(title)]
        subject = [kw for kw, r in SUBJECT_RES if r.search(title)]
        aspect = [kw for kw, r in ASPECT_RES if r.search(title)]
        if strong:
            reason = f"强命中: {', '.join(strong)}"
        elif subject and aspect:
            reason = f"subject={subject} aspect={aspect}"
        else:
            continue
        entry = dict(p)
        entry["matched_keywords"] = {"strong": strong, "subject": subject, "aspect": aspect}
        entry["prefilter_reason"] = reason
        kept.append(entry)
    print(f"[info] 初筛: {len(papers)} 篇 -> {len(kept)} 篇进入 LLM 精筛候选")
    return kept


def build_rank_messages(batch, directions):
    """构造精筛 prompt：system 描述 6 方向体系与分层门槛，user 给论文元数据。"""
    dir_lines = "\n".join(
        f"- {d['id']}（{d.get('name', '')}）: {d.get('description', '')}" for d in directions
    )
    system = (
        "你是 Physical AI Systems / Infra 方向的研究图谱编辑。我们维护一个 6 方向分类体系：\n"
        f"{dir_lines}\n\n"
        "分层门槛：\n"
        "- core 方向（01/02/03/06）：正常收录，质量与契合度俱佳给 7 分以上；\n"
        "- milestone 方向（04/05）：只收里程碑级工作，score 必须 ≥9 且必须填写 milestone_reason；\n"
        "- 显式拒收（score ≤4）：仅优化 agent prompt 的工作、与系统/架构/效率无关的弱相关应用论文。\n\n"
        "请为每篇论文输出严格 JSON 数组，每个元素字段：\n"
        '{"dblp_key", "score"(0-10 整数), "directions"(方向 id 数组), "tags"(短标签数组), '
        '"reason"(一句话中文理由), "summary_zh"(2-3 句中文摘要), '
        '"milestone_reason"(仅 milestone 方向填写，否则为 null), '
        '"arxiv_id"(若能从 ee 链接推断，否则 null), "code_url"(若摘要/链接中有代码仓库，否则 null)}。'
        "只输出 JSON，不要输出其他内容。"
    )
    user = "请评估以下论文：\n" + json.dumps(
        [
            {
                "dblp_key": p["dblp_key"],
                "title": p["title"],
                "authors": p["authors"],
                "venue": p["venue"],
                "year": p["year"],
                "ee": p["ee"],
                "arxiv_id": p["arxiv_id"],
            }
            for p in batch
        ],
        ensure_ascii=False,
        indent=2,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def rank_batch(batch, directions):
    """对一批初筛论文做 Kimi 精筛；解析失败返回空列表并警告，不中断。"""
    try:
        content = call_kimi(build_rank_messages(batch, directions))
        results = parse_ranking_json(content)
        if not isinstance(results, list):
            raise ValueError("模型输出不是 JSON 数组")
        return results
    except Exception as e:
        print(f"[warn] 批次精筛失败，已跳过该批（{len(batch)} 篇）: {e}")
        return []


def passes_threshold(item):
    """分层过滤：core 方向 score≥7；milestone 方向 score≥9 且有 milestone_reason。"""
    score = item.get("score") or 0
    dirs = set(item.get("directions") or [])
    if dirs & MILESTONE_DIRECTIONS:
        if score >= MILESTONE_SCORE_THRESHOLD and item.get("milestone_reason"):
            return True
    if dirs - MILESTONE_DIRECTIONS:
        return score >= CORE_SCORE_THRESHOLD
    return False


def rank_prefilter(papers):
    """分批精筛初筛清单，合并元数据并按分层门槛过滤，返回待确认条目。"""
    if not papers:
        return []
    directions = load_directions()
    by_key = {p["dblp_key"]: p for p in papers}
    ranked = []
    for i in range(0, len(papers), BATCH_SIZE):
        batch = papers[i:i + BATCH_SIZE]
        print(f"[info] 精筛批次 {i // BATCH_SIZE + 1}（{len(batch)} 篇）")
        for item in rank_batch(batch, directions):
            dblp_key = item.get("dblp_key")
            if dblp_key not in by_key:
                continue
            entry = dict(by_key[dblp_key])
            entry.update({
                "score": item.get("score"),
                "directions": item.get("directions") or [],
                "tags": item.get("tags") or [],
                "reason": item.get("reason", ""),
                "summary_zh": item.get("summary_zh", ""),
                "milestone_reason": item.get("milestone_reason"),
                # arxiv_id / code_url 优先用本地从 ee 推断的，模型输出兜底
                "arxiv_id": entry.get("arxiv_id") or item.get("arxiv_id"),
                "code_url": item.get("code_url"),
                "source": "dblp-backfill",
            })
            if passes_threshold(entry):
                ranked.append(entry)
    ranked.sort(key=lambda p: p.get("score") or 0, reverse=True)
    print(f"[info] 精筛完成，{len(ranked)} 篇通过分层门槛进入待确认清单")
    return ranked


def print_summary(papers):
    """按会议+年份打印候选数汇总。"""
    counts = {}
    for p in papers:
        key = (p["venue"], p["year"])
        counts[key] = counts.get(key, 0) + 1
    print("[info] 待确认清单汇总（会议/年份 -> 候选数）:")
    for (venue, year), n in sorted(counts.items()):
        print(f"  {venue} {year}: {n}")


def cmd_fetch():
    papers = fetch_all_venues()
    kept = prefilter(papers)
    save_state("backfill_prefilter", kept)
    print(f"[info] 初筛清单已写入 state/backfill_prefilter.json（{len(kept)} 篇）")


def cmd_rank():
    try:
        get_env("KIMI_API_KEY")
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
    papers = load_state("backfill_prefilter")
    if not papers:
        print("[error] state/backfill_prefilter.json 为空或不存在，请先运行 fetch", file=sys.stderr)
        sys.exit(1)
    print(f"[info] 读取初筛清单 {len(papers)} 篇")
    ranked = rank_prefilter(papers)
    save_state("backfill_review", ranked)
    print_summary(ranked)
    print(f"[info] 待确认清单已写入 state/backfill_review.json（{len(ranked)} 篇）")


def cmd_all():
    cmd_fetch()
    cmd_rank()


def ingest_seed(list_path):
    """种子清单入库辅助：读取用户 JSON 清单，用 arXiv API 补全元数据，写 state/seed_review.json。

    输入格式：[{"arxiv_id": "...", "note": "可选理由"}, ...]，条目也可只含 title。
    directions 默认 ["04-vla-wam-foundation-models"]，供人工审阅时调整。
    """
    with open(list_path, "r", encoding="utf-8") as f:
        seeds = json.load(f)
    if not isinstance(seeds, list):
        raise ValueError("种子清单必须是 JSON 数组")

    # 收集有 arxiv_id 的条目，批量查 arXiv 补全元数据
    with_id = [s for s in seeds if s.get("arxiv_id")]
    meta_by_id = {}
    for i in range(0, len(with_id), 20):
        ids = [s["arxiv_id"] for s in with_id[i:i + 20]]
        url = f"{ARXIV_API}?id_list={','.join(ids)}&max_results={len(ids)}"
        print(f"[info] arXiv 查询种子元数据（{len(ids)} 条）")
        xml_bytes = http_get(url, headers={"User-Agent": "Auto-Survey-Agent/1.0"})
        for p in parse_arxiv_feed(xml_bytes):
            meta_by_id[p["arxiv_id"]] = p
        if i + 20 < len(with_id):
            time.sleep(REQUEST_INTERVAL)

    review = []
    for s in seeds:
        arxiv_id = s.get("arxiv_id")
        meta = meta_by_id.get(arxiv_id, {}) if arxiv_id else {}
        if arxiv_id and not meta:
            print(f"[warn] arXiv 未返回 {arxiv_id} 的元数据，按原样保留")
        if not arxiv_id and not s.get("title"):
            print(f"[warn] 种子条目既无 arxiv_id 也无 title，已跳过: {s}")
            continue
        review.append({
            "arxiv_id": arxiv_id,
            "title": meta.get("title") or s.get("title"),
            "authors": meta.get("authors", []),
            "summary": meta.get("summary", ""),
            "published": meta.get("published", ""),
            "code_url": meta.get("code_url") or extract_code_url(s.get("note", "")),
            "note": s.get("note", ""),
            "directions": s.get("directions") or ["04-vla-wam-foundation-models"],
            "source": "seed",
        })
    save_state("seed_review", review)
    print(f"[info] 种子清单已写入 state/seed_review.json（{len(review)} 条），"
          "directions 默认 04-vla-wam-foundation-models，请人工审阅调整")
    return review


def main():
    parser = argparse.ArgumentParser(description="历史回填：从系统/架构顶会拉取 2024 至今的相关论文")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="DBLP 拉取 + 本地初筛，产出 state/backfill_prefilter.json")
    sub.add_parser("rank", help="Kimi 精筛初筛清单，产出 state/backfill_review.json（需 KIMI_API_KEY）")
    sub.add_parser("all", help="先 fetch 后 rank")
    seed_parser = sub.add_parser("seed", help="种子清单补全元数据，产出 state/seed_review.json")
    seed_parser.add_argument("list_path", help="种子清单 JSON 路径")
    args = parser.parse_args()
    if args.command == "seed":
        ingest_seed(args.list_path)
    else:
        {"fetch": cmd_fetch, "rank": cmd_rank, "all": cmd_all}[args.command]()


if __name__ == "__main__":
    main()
