"""维护 paperlist 数据与渲染。

- ingest()：把 Slack 确认的论文转换为 data/papers.json 记录追加；
- render()：从 data/papers.json + data/taxonomy.json 重新生成全部 markdown 清单。

只改文件，不执行任何 git 命令（git 操作全在 workflow 里）。
"""
import json
import os
import re
from datetime import date, datetime, timedelta

from src.common import PROJECT_ROOT, http_get

PAPERLIST_ROOT = PROJECT_ROOT / "paperlist"
PAPERS_JSON = PAPERLIST_ROOT / "data" / "papers.json"
TAXONOMY_JSON = PAPERLIST_ROOT / "data" / "taxonomy.json"
GROUPS_JSON = PAPERLIST_ROOT / "data" / "groups.json"

# 追踪会议白名单：canonical -> (venue_type, 匹配模式列表)
# 短缩写用 \b 词边界避免误匹配（如 MICRO），长名称直接子串匹配
VENUE_WHITELIST = {
    "MLSys": ("system", [r"\bmlsys\b", r"machine learning and systems"]),
    "EuroSys": ("system", [r"\beurosys\b", r"european conference on computer systems"]),
    "SOSP": ("system", [r"\bsosp\b", r"symposium on operating systems principles"]),
    "OSDI": ("system", [r"\bosdi\b", r"operating systems design and implementation"]),
    "NSDI": ("system", [r"\bnsdi\b", r"networked systems design and implementation"]),
    "SIGCOMM": ("system", [r"\bsigcomm\b"]),
    "ISCA": ("architecture", [r"\bisca\b", r"international symposium on computer architecture"]),
    "MICRO": ("architecture", [r"\bmicro\b", r"microarchitecture"]),
    "HPCA": ("architecture", [r"\bhpca\b", r"high performance computer architecture"]),
    "ASPLOS": ("architecture", [r"\basplos\b", r"architectural support for programming languages"]),
    "RSS": ("robotics", [r"\brss\b", r"robotics:?\s*science and systems"]),
    "CoRL": ("robotics", [r"\bcorl\b", r"conference on robot learning"]),
    "ICRA": ("robotics", [r"\bicra\b", r"international conference on robotics and automation"]),
    "IROS": ("robotics", [r"\biros\b", r"intelligent robots and systems"]),
}
OTHERS_VENUE = "arXiv / Others"

GITHUB_REPO_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.\-]+)")
VENUES_START_YEAR = 2024
LATEST_DAYS = 30


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def today_iso():
    return date.today().isoformat()


def match_venue(venue):
    """把 Semantic Scholar 给的 venue 字符串归一到白名单 canonical 名。

    返回 (canonical 或 None, venue_type)；未命中时 venue_type 为 preprint。
    """
    if not venue:
        return None, "preprint"
    normalized = venue.lower()
    for canonical, (venue_type, patterns) in VENUE_WHITELIST.items():
        if any(re.search(p, normalized) for p in patterns):
            return canonical, venue_type
    return None, "preprint"


def fetch_github_stars(code_url):
    """code_url 是 GitHub 仓库且 PAPERLIST_PAT 存在时，取 stargazers_count 快照。

    任何失败只打印警告并返回 None，不中断收录。
    """
    if not code_url:
        return None
    m = GITHUB_REPO_RE.match(code_url)
    if not m:
        return None
    token = os.environ.get("PAPERLIST_PAT")
    if not token:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    try:
        resp = http_get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Auto-Survey-Agent/1.0",
            },
        )
        count = json.loads(resp).get("stargazers_count")
        return {"count": count, "fetched_at": today_iso()}
    except (RuntimeError, ValueError) as e:
        print(f"[warn] 获取 GitHub star 失败（{code_url}）: {e}")
        return None


def build_record(paper):
    """把确认候选转换为 papers.json 记录。"""
    published = (paper.get("published") or "")[:10]
    canonical, venue_type = match_venue(paper.get("venue"))
    return {
        "id": paper["arxiv_id"],
        "title": paper["title"],
        "authors": paper.get("authors") or [],
        "published": published,
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "venue": canonical or paper.get("venue"),
        "venue_type": venue_type,
        "directions": paper.get("directions") or [],
        "subdirections": paper.get("subdirections") or {},
        "tags": paper.get("tags") or [],
        "code_url": paper.get("code_url"),
        "github_stars": fetch_github_stars(paper.get("code_url")),
        "citation_count": paper.get("citation_count") or 0,
        "score": paper.get("score"),
        "reason": paper.get("reason", ""),
        "summary_zh": paper.get("summary_zh", ""),
        "milestone_reason": paper.get("milestone_reason", ""),
        "added": today_iso(),
        "source": "arxiv-scout",
    }


def ingest(confirmed):
    """把确认论文追加进 data/papers.json（按 id 去重）。有实际修改返回 True。"""
    if not confirmed:
        return False
    papers = load_json(PAPERS_JSON, [])
    existing = {p["id"] for p in papers}
    added = 0
    for c in confirmed:
        if c["arxiv_id"] in existing:
            print(f"[info] {c['arxiv_id']} 已存在于 papers.json，跳过")
            continue
        papers.append(build_record(c))
        existing.add(c["arxiv_id"])
        added += 1
    if not added:
        print("[info] 所有确认论文均已收录，跳过")
        return False
    write_json(PAPERS_JSON, papers)
    print(f"[info] 已向 data/papers.json 追加 {added} 篇论文")
    return True


def build_review_record(entry):
    """把 backfill/seed 待确认条目转换为 papers.json 记录。

    与 build_record 的差异：id 允许 dblp:<key> 兜底，published 可能只有年份，
    venue/venue_type 直接沿用回填阶段的结果，不抓 GitHub star（批量回填限速）。
    """
    record_id = entry.get("arxiv_id") or f"dblp:{entry.get('dblp_key', '')}"
    published = (entry.get("published") or "")[:10]
    canonical, venue_type = match_venue(entry.get("venue"))
    return {
        "id": record_id,
        "title": entry["title"],
        "authors": [a.strip() for a in (entry.get("authors") or []) if a.strip()],
        "published": published or (entry.get("year") or ""),
        "year": int(entry["year"]) if str(entry.get("year") or "").isdigit()
        else (int(published[:4]) if published[:4].isdigit() else None),
        "venue": canonical or entry.get("venue"),
        "venue_type": entry.get("venue_type") or venue_type,
        "directions": entry.get("directions") or [],
        "subdirections": entry.get("subdirections") or {},
        "tags": entry.get("tags") or [],
        "code_url": entry.get("code_url"),
        "github_stars": None,
        "citation_count": entry.get("citation_count") or 0,
        "score": entry.get("score"),
        "reason": entry.get("reason", ""),
        "summary_zh": entry.get("summary_zh", ""),
        "milestone_reason": entry.get("milestone_reason") or "",
        "added": today_iso(),
        "source": entry.get("source") or "dblp-backfill",
    }


def ingest_review(entries):
    """把人工确认过的 backfill/seed 条目追加进 data/papers.json（按 id 去重）。"""
    if not entries:
        return False
    papers = load_json(PAPERS_JSON, [])
    existing = {p["id"] for p in papers}
    added = 0
    for e in entries:
        record = build_review_record(e)
        if not record["id"] or record["id"] == "dblp:":
            print(f"[warn] 条目缺少 id，跳过: {e.get('title', '')[:60]}")
            continue
        if record["id"] in existing:
            continue
        papers.append(record)
        existing.add(record["id"])
        added += 1
    if not added:
        print("[info] 待确认条目均已收录，跳过")
        return False
    write_json(PAPERS_JSON, papers)
    print(f"[info] 已向 data/papers.json 追加 {added} 篇论文（backfill/seed）")
    return True


# ---------- 渲染 ----------

def esc(text):
    """转义表格单元格中的竖线。"""
    return (text or "").replace("|", "\\|")


def paper_url(paper):
    pid = paper["id"]
    if pid.startswith("dblp:"):
        return f"https://dblp.org/rec/{pid[len('dblp:'):]}.html"
    return f"https://arxiv.org/abs/{pid}"


def first_author(paper):
    authors = paper.get("authors") or []
    if not authors:
        return "-"
    return esc(authors[0]) + (" et al." if len(authors) > 1 else "")


def paper_row(paper):
    """方向页 / 最新收录页共用的表格行。"""
    title = f"[{esc(paper['title'])}]({paper_url(paper)})"
    venue = esc(paper.get("venue") or "arXiv")
    tags = ", ".join(paper.get("tags") or []) or "-"
    code = f"[code]({paper['code_url']})" if paper.get("code_url") else "-"
    return (
        f"| {title} | {first_author(paper)} | {paper.get('published') or '-'} "
        f"| {venue} | {esc(tags)} | {code} | {esc(paper.get('summary_zh'))} |"
    )


PAPER_TABLE_HEADER = (
    "| 论文 | 作者 | 发布 | Venue | 标签 | 代码 | TL;DR |\n"
    "| --- | --- | --- | --- | --- | --- | --- |"
)


def by_published_desc(paper):
    return paper.get("published") or ""


def count_by_direction(papers, directions):
    counts = {d["id"]: 0 for d in directions}
    for p in papers:
        for d in p.get("directions") or []:
            if d in counts:
                counts[d] += 1
    return counts


def render_root_readme(papers, directions, group_count):
    counts = count_by_direction(papers, directions)
    last_update = max((p.get("added") or "" for p in papers), default="") or "—"
    map_rows = "\n".join(
        f"| {d['id'].split('-')[0]} | [{esc(d.get('name'))}]({d['file']}) "
        f"| {d.get('tier', 'core')} | {counts[d['id']]} |"
        for d in directions
    )
    content = f"""# Awesome Physical AI Infra Papers

一份持续演化的 **Physical AI Systems / Infra** 研究图谱：VLA/WAM 的推理系统与 serving、加速器与计算架构、高效算法，以及与之相邻的 LLM / 多模态推理系统。

本仓库由自动化巡检 agent 维护：arXiv 每日抓取 → LLM 打分筛选 → Slack 人工 ✅ 确认 → 归类收录。分类体系会随领域发展持续审视与调整。

## 📊 概览

| 指标 | 当前值 |
| --- | --- |
| 收录论文 | {len(papers)} |
| 研究方向 | {len(directions)} |
| 追踪会议 | 15（系统 6 · 架构 4 · 机器人 4 · arXiv / Others） |
| 追踪研究组 | {group_count} |
| 最近更新 | {last_update} |

## 🧭 研究地图

完整地图与分类说明见 [papers/README.md](papers/README.md)；另提供 [按会议 / 年份索引](venues/README.md)。

| # | 方向 | 门槛 | 论文数 |
| ---: | --- | --- | ---: |
{map_rows}

## 🆕 最新收录

见 [papers/LATEST.md](papers/LATEST.md)。

## 🏛️ 会议索引

按年份 × 追踪会议浏览收录论文，见 [venues/README.md](venues/README.md)。

## 🧑‍🔬 领先研究组

领域内有影响力的实验室、公司与开源生态，见 [groups/README.md](groups/README.md)。

## 🗂️ 仓库结构

| 位置 | 用途 |
| --- | --- |
| `papers/` | 按方向组织的论文清单 + 最新收录 |
| `venues/` | 年份 × 会议索引 |
| `groups/` | 领先研究组 / 公司 / 开源生态索引 |
| `data/papers.json` | 机器可读的论文数据库（**唯一数据源**） |
| `data/taxonomy.json` | 分类体系定义（可演化） |
| `data/groups.json` | 机器可读的研究组数据 |
| `docs/` | 收录与分类方法论 |

所有 markdown 清单均由 `data/` 下的 JSON 数据生成，请勿手工编辑表格内容。

## 🔎 收录原则

- 质量优先，不设数量指标；
- 收录门槛分层：core 方向正常评分阈值，milestone 方向（04 / 05）仅收里程碑级工作；
- 每篇论文须有明确的技术定位（方向 + 子类 + 标签）；
- 代码链接仅收录官方实现；
- 分类体系随收录持续审视：不合身的归类会被修正，新方向会被增设。

详细规则见 [docs/METHODOLOGY.md](docs/METHODOLOGY.md)。
"""
    write_text(PAPERLIST_ROOT / "README.md", content)


def render_papers_readme(papers, directions):
    counts = count_by_direction(papers, directions)
    rows = "\n".join(
        f"| {d['id'].split('-')[0]} | [**{esc(d.get('name'))}**]({d['file'].split('/')[-1]}) "
        f"| {d.get('tier', 'core')} | {counts[d['id']]} |"
        for d in directions
    )
    content = f"""# 🧭 研究地图

> {len(papers)} 篇论文 · {len(directions)} 个研究方向 · 每篇论文可属于多个方向

[**📚 最新收录**](LATEST.md) · [**🏛️ 按会议 / 年份索引**](../venues/README.md) · [**🧩 JSON**](../data/papers.json) · [**🏷️ 分类定义**](../data/taxonomy.json)

## 按方向浏览

| # | 研究方向 | 门槛 | 论文数 |
| ---: | --- | --- | ---: |
{rows}

另提供 [按会议 / 年份索引](../venues/README.md)：系统（MLSys / SOSP / OSDI 等）、架构（ISCA / MICRO / HPCA / ASPLOS）、机器人（RSS / CoRL / ICRA / IROS）三个会议群，外加 arXiv / Others。

## 门槛分层

- **core**（01 / 02 / 03 / 06）：方向内论文按正常评分阈值收录；
- **milestone**（04 / 05）：仅收录定义或改写技术路线的里程碑级工作，评分 ≥ 9 且须给出里程碑理由，普通增量不收。

## 分类的演化

分类体系定义在 [`data/taxonomy.json`](../data/taxonomy.json)，不是一成不变的：

- 每篇论文收录时由 LLM 按当前分类体系归类（方向 + 子类 + 标签）；
- 若论文与任何现有方向都不契合，会提出增设 / 合并 / 拆分方向的建议，经人工确认后更新分类并重新归类受影响的论文；
- 一篇论文在技术角色确实不同的情况下，允许出现在多个方向下。
"""
    write_text(PAPERLIST_ROOT / "papers" / "README.md", content)


TIER_NOTE = {
    "core": "核心方向，正常收录门槛。",
    "milestone": "**仅收录里程碑级工作，普通增量不收**。",
}


def render_direction_page(papers, direction):
    tier = direction.get("tier", "core")
    description = direction.get("description", "")
    if description and not description.endswith("。"):
        description += "。"
    note = TIER_NOTE.get(tier, TIER_NOTE["core"])
    own = [p for p in papers if direction["id"] in (p.get("directions") or [])]
    own.sort(key=by_published_desc, reverse=True)

    subdirections = direction.get("subdirections") or []
    if subdirections:
        # 按子类分节；未归子类的论文归入“其他”
        sections = []
        for sub in subdirections:
            entries = [p for p in own if (p.get("subdirections") or {}).get(direction["id"]) == sub["id"]]
            if not entries:
                continue
            rows = "\n".join(paper_row(p) for p in entries)
            sections.append(f"## {sub.get('name')}（{len(entries)}）\n\n{PAPER_TABLE_HEADER}\n{rows}")
        rest = [p for p in own if not (p.get("subdirections") or {}).get(direction["id"])]
        if rest:
            rows = "\n".join(paper_row(p) for p in rest)
            sections.append(f"## 其他（{len(rest)}）\n\n{PAPER_TABLE_HEADER}\n{rows}")
        body = "\n\n".join(sections) if sections else PAPER_TABLE_HEADER
    else:
        rows = "\n".join(paper_row(p) for p in own)
        body = PAPER_TABLE_HEADER + ("\n" + rows if rows else "")
    content = f"""# {direction['id'].split('-')[0]} · {direction.get('name')}

> {description}{note}

[← 研究地图](README.md) · [最新收录](LATEST.md)

{body}
"""
    write_text(PAPERLIST_ROOT / direction["file"], content)


def render_latest(papers):
    cutoff = (date.today() - timedelta(days=LATEST_DAYS)).isoformat()
    recent = [p for p in papers if (p.get("added") or "") >= cutoff]
    recent.sort(key=lambda p: p.get("added") or "", reverse=True)
    if recent:
        body = PAPER_TABLE_HEADER + "\n" + "\n".join(paper_row(p) for p in recent)
    else:
        body = "暂无收录。"
    content = f"""# 🆕 最新收录

> 最近 {LATEST_DAYS} 天内收录的论文，按收录时间倒序。

[← 研究地图](README.md)

{body}
"""
    write_text(PAPERLIST_ROOT / "papers" / "LATEST.md", content)


def venue_bucket(paper):
    """渲染用的 venue 归组：命中白名单用 canonical，否则归入 arXiv / Others。"""
    canonical, _ = match_venue(paper.get("venue"))
    return canonical or OTHERS_VENUE


def render_venues(papers):
    venues_dir = PAPERLIST_ROOT / "venues"
    columns = list(VENUE_WHITELIST) + [OTHERS_VENUE]
    current_year = date.today().year
    # 起始年取配置下限与最早论文年份的较小者（种子论文可能早于 VENUES_START_YEAR）
    paper_years = [p["year"] for p in papers if p.get("year")]
    start_year = min([VENUES_START_YEAR] + paper_years)
    years = list(range(start_year, current_year + 1))

    # year -> venue -> [papers]
    index = {y: {c: [] for c in columns} for y in years}
    for p in papers:
        year = p.get("year")
        if year in index:
            index[year][venue_bucket(p)].append(p)

    header = "| 年份 | " + " | ".join(columns) + " | 合计 |"
    divider = "| --- |" + " --- |" * (len(columns) + 1)
    matrix_rows = []
    for y in years:
        total = sum(len(v) for v in index[y].values())
        year_cell = f"[{y}]({y}.md)" if total else str(y)
        counts = " | ".join(str(len(index[y][c]) or "") for c in columns)
        matrix_rows.append(f"| {year_cell} | {counts} | {total or ''} |")
    content = f"""# 🏛️ 会议索引

> 收录论文按发表年份 × 追踪会议的分布；未命中白名单的论文归入 "{OTHERS_VENUE}"。

[← 返回首页](../README.md)

{header}
{divider}
{chr(10).join(matrix_rows)}
"""
    write_text(venues_dir / "README.md", content)

    # 年份页：仅生成有收录的年份，按会议分节
    for y in years:
        if not any(index[y].values()):
            continue
        sections = []
        for c in columns:
            entries = sorted(index[y][c], key=by_published_desc, reverse=True)
            if not entries:
                continue
            items = "\n".join(
                f"- [{esc(p['title'])}]({paper_url(p)}) — {first_author(p)}, {p.get('published') or '-'}"
                for p in entries
            )
            sections.append(f"## {c}\n\n{items}")
        year_content = (
            f"# 🏛️ {y} 年收录\n\n"
            f"> 按会议分节；未命中白名单的归入 \"{OTHERS_VENUE}\"。\n\n"
            f"[← 会议索引](README.md)\n\n" + "\n\n".join(sections) + "\n"
        )
        write_text(venues_dir / f"{y}.md", year_content)


def render_groups(papers, groups):
    """从 data/groups.json 生成 groups/README.md；representative papers 链到论文记录。"""
    by_id = {p["id"]: p for p in papers}
    sections = []
    for gtype in ("学术实验室", "工业研究", "开源生态"):
        entries = [g for g in groups if g.get("type") == gtype]
        if not entries:
            continue
        blocks = []
        for g in entries:
            lines = [f"### {g.get('name')}", ""]
            if g.get("focus"):
                lines.append(f"- **方向**：{esc(g['focus'])}")
            if g.get("note"):
                lines.append(f"- **简介**：{esc(g['note'])}")
            links = g.get("links") or []
            if links:
                lines.append("- **链接**：" + " · ".join(f"[主页]({u})" for u in links))
            ref_ids = [pid for pid in (g.get("papers") or []) if pid in by_id]
            if ref_ids:
                refs = "；".join(
                    f"[{esc(by_id[pid]['title'])}]({paper_url(by_id[pid])})" for pid in ref_ids
                )
                lines.append(f"- **代表论文（{len(ref_ids)}）**：{refs}")
            blocks.append("\n".join(lines))
        sections.append(f"## {gtype}\n\n" + "\n\n".join(blocks))
    content = f"""# 🧑‍🔬 领先研究组与生态

> 追踪 Physical AI Systems / Infra 方向有持续高影响力产出的学术实验室、工业研究团队与开源生态。
> 数据维护在 [`data/groups.json`](../data/groups.json)，此页由其生成；代表论文均链回本仓库收录记录。

[← 返回首页](../README.md)

{chr(10).join(sections)}
"""
    write_text(PAPERLIST_ROOT / "groups" / "README.md", content)


def render():
    """从 data/papers.json + data/taxonomy.json 重新生成全部 markdown 清单。"""
    taxonomy = load_json(TAXONOMY_JSON, None)
    if not taxonomy:
        print(f"[warn] 未找到 {TAXONOMY_JSON}，跳过渲染")
        return
    papers = load_json(PAPERS_JSON, [])
    directions = taxonomy.get("directions", [])
    groups = load_json(GROUPS_JSON, [])
    group_count = len(groups) if isinstance(groups, list) else 0

    render_root_readme(papers, directions, group_count)
    render_papers_readme(papers, directions)
    for d in directions:
        render_direction_page(papers, d)
    render_latest(papers)
    render_venues(papers)
    render_groups(papers, groups if isinstance(groups, list) else [])
    print(f"[info] 已重新生成 paperlist markdown（{len(papers)} 篇论文，{len(directions)} 个方向）")
