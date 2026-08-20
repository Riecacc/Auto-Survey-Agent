"""从 arXiv 抓取最新论文，并用 Semantic Scholar 补充作者/venue/引用元数据。"""
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from src.common import PROJECT_ROOT, get_env, http_get, http_post_json, load_state

ARXIV_API = "http://export.arxiv.org/api/query"
S2_BATCH_API = (
    "https://api.semanticscholar.org/graph/v1/paper/batch"
    "?fields=citationCount,venue,publicationVenue,authors.name,authors.hIndex"
)

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

GITHUB_URL_RE = re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.\-]+")


def load_scout_config():
    with open(PROJECT_ROOT / "config" / "scout.json", "r", encoding="utf-8") as f:
        return json.load(f)


def build_arxiv_query(config):
    """组合 arXiv search_query：(cat:X OR cat:Y) AND (all:kw1 OR all:kw2 ...) AND submittedDate:[...]。"""
    cats = " OR ".join(f"cat:{c}" for c in config["arxiv_categories"])
    kws = " OR ".join(f'all:"{k}"' for k in config["keywords"])
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=config["lookback_days"])
    date_range = f"submittedDate:[{start:%Y%m%d}0000 TO {end:%Y%m%d}2359]"
    return f"({cats}) AND ({kws}) AND {date_range}"


def extract_arxiv_id(id_url):
    """从 id URL 提取 arxiv_id，去掉版本号（v1/v2...）便于跨版本去重。"""
    arxiv_id = id_url.rsplit("/abs/", 1)[-1]
    return re.sub(r"v\d+$", "", arxiv_id)


def extract_code_url(text):
    """从文本中提取第一个 GitHub 仓库链接。"""
    if not text:
        return None
    m = GITHUB_URL_RE.search(text)
    return m.group(0) if m else None


def parse_arxiv_feed(xml_bytes):
    """解析 arXiv Atom feed，返回论文 dict 列表。"""
    root = ET.fromstring(xml_bytes)
    papers = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        id_url = entry.findtext(f"{ATOM_NS}id", "")
        title = " ".join((entry.findtext(f"{ATOM_NS}title", "") or "").split())
        summary = " ".join((entry.findtext(f"{ATOM_NS}summary", "") or "").split())
        published = entry.findtext(f"{ATOM_NS}published", "")
        authors = [
            a.findtext(f"{ATOM_NS}name", "")
            for a in entry.findall(f"{ATOM_NS}author")
        ]
        primary_cat_el = entry.find(f"{ARXIV_NS}primary_category")
        primary_category = primary_cat_el.get("term", "") if primary_cat_el is not None else ""
        comment = entry.findtext(f"{ARXIV_NS}comment", "") or ""
        journal_ref = entry.findtext(f"{ARXIV_NS}journal_ref", "") or ""
        papers.append({
            "arxiv_id": extract_arxiv_id(id_url),
            "title": title,
            "summary": summary,
            "authors": authors,
            "published": published,
            "primary_category": primary_category,
            "comment": comment,
            "journal_ref": journal_ref,
            "code_url": extract_code_url(summary + " " + comment),
        })
    return papers


def enrich_with_semantic_scholar(papers):
    """用 Semantic Scholar 批量接口补充 citationCount/venue/作者 h-index。失败不中断。"""
    if not papers:
        return papers
    ids = [f"ARXIV:{p['arxiv_id']}" for p in papers]
    result = None
    for attempt in range(3):
        try:
            resp = http_post_json(S2_BATCH_API, {"ids": ids})
            result = json.loads(resp)
            break
        except RuntimeError as e:
            # 429 限流时重试，其余错误也简单重试，最多 3 次
            print(f"[warn] Semantic Scholar 请求失败（第 {attempt + 1} 次）: {e}")
            if attempt < 2:
                time.sleep(3)
    if result is None:
        print("[warn] Semantic Scholar 补充失败，跳过元数据补充")
        for p in papers:
            p["max_author_hindex"] = None
            p["venue"] = p.get("journal_ref") or None
            p["citation_count"] = 0
        return papers

    by_id = {}
    for paper in papers:
        by_id[paper["arxiv_id"]] = paper
    for i, item in enumerate(result):
        if item is None or i >= len(papers):
            continue
        p = papers[i]
        hindexes = [
            a.get("hIndex") for a in (item.get("authors") or [])
            if a.get("hIndex") is not None
        ]
        p["max_author_hindex"] = max(hindexes) if hindexes else None
        pub_venue = item.get("publicationVenue") or {}
        p["venue"] = item.get("venue") or pub_venue.get("name") or p.get("journal_ref") or None
        p["citation_count"] = item.get("citationCount") or 0
    return papers


def fetch_new_papers():
    """抓取最近论文并去重（state/seen.json），返回新论文 dict 列表。"""
    config = load_scout_config()
    query = build_arxiv_query(config)
    url = (
        f"{ARXIV_API}?search_query={quote(query)}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results={config['max_results']}"
    )
    print(f"[info] arXiv 查询: {url}")
    xml_bytes = http_get(url, headers={"User-Agent": "Auto-Survey-Agent/1.0"})
    papers = parse_arxiv_feed(xml_bytes)
    print(f"[info] arXiv 返回 {len(papers)} 篇论文")

    seen = load_state("seen")
    new_papers = [p for p in papers if p["arxiv_id"] not in seen]
    print(f"[info] 去重后剩余 {len(new_papers)} 篇新论文")

    return enrich_with_semantic_scholar(new_papers)


if __name__ == "__main__":
    papers = fetch_new_papers()
    print(len(papers))
    print(papers[0] if papers else "no papers")
