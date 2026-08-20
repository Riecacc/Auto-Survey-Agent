"""维护 paperlist/README.md 的论文表格。只改文件，不执行任何 git 命令。"""
import re
from datetime import datetime, timezone

from src.common import PROJECT_ROOT

README_PATH = PROJECT_ROOT / "paperlist" / "README.md"
SECTION_HEADER = "## Papers"
TABLE_HEADER = "| Date | Title | Venue | Code |"
TABLE_DIVIDER = "| --- | --- | --- | --- |"


def build_row(paper):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    venue = paper.get("venue") or "-"
    code = f"[code]({paper['code_url']})" if paper.get("code_url") else "-"
    title = paper["title"].replace("|", "\\|")
    return f"| {date} | [{title}](https://arxiv.org/abs/{paper['arxiv_id']}) | {venue} | {code} |"


def add_papers(papers):
    """把确认的论文追加进 paperlist/README.md 的 ## Papers 表格。有实际修改返回 True。"""
    if not papers:
        return False
    content = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else ""
    # 从已有表格行中提取 arxiv id 用于去重
    existing_ids = set(re.findall(r"arxiv\.org/abs/([^\s)|]+)", content))

    new_rows = [build_row(p) for p in papers if p["arxiv_id"] not in existing_ids]
    if not new_rows:
        print("[info] 所有确认论文均已存在于 paperlist，跳过")
        return False

    lines = content.rstrip("\n").splitlines()
    if SECTION_HEADER not in content:
        # 无 ## Papers 小节则在末尾追加（含表头）
        if lines:
            lines.append("")
        lines.append(SECTION_HEADER)
        lines.append("")
        lines.append(TABLE_HEADER)
        lines.append(TABLE_DIVIDER)

    # 找到表格末尾（表格 divider 之后连续的表格行），在其后追加新行
    insert_at = None
    for i, line in enumerate(lines):
        if line.strip() == SECTION_HEADER:
            insert_at = i + 1
            # 跳过空行与表头/divider/已有数据行
            while insert_at < len(lines) and (lines[insert_at].strip() == "" or lines[insert_at].lstrip().startswith("|")):
                insert_at += 1
            break
    if insert_at is None:
        lines.extend(new_rows)
    else:
        lines[insert_at:insert_at] = new_rows

    README_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[info] 已向 paperlist/README.md 追加 {len(new_rows)} 篇论文")
    return True
