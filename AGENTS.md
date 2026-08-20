# AGENTS.md

本文件供后续参与本项目的 AI agent / 开发者快速上手。详细使用文档见 `README.md`，本文聚焦开发约定与易踩的坑。

## 项目概述

每日论文巡检 agent：GitHub Actions 定时从 arXiv 抓取 Physical AI Infra 方向论文，Kimi API 打分筛选，Slack 推送候选，用户打 ✅ 确认后收录进 `paperlist/` submodule 对应的 awesome list 仓库并可选发推。

## 硬性技术约束

- **纯 Python 标准库，禁止引入 pip 依赖**（urllib/json/xml.etree/hmac 等）。CI 没有安装依赖的步骤，加依赖会直接导致 workflow 失败。
- **仓库内禁止出现任何密钥**。所有凭证通过 GitHub Secrets → 环境变量注入；新增凭证时同步更新 `.github/workflows/daily_scout.yml` 的 env 列表和 `README.md` 的 Secret 汇总表。
- Python 版本：CI 用 3.12，本地 3.9+ 即可。

## 目录结构与模块职责

- `config/scout.json` — 检索配置唯一数据源（分类/关键词/回溯天数/条数上限）。**修改它时需同步更新 `skills/paper-scout/SKILL.md` 的领域描述**。
- `skills/paper-scout/SKILL.md` — 检索领域定义，供人/agent 阅读。
- `skills/paper-ranking/SKILL.md` — 评估规则，**全文会作为 Kimi 打分的 system prompt**，改动直接影响打分行为。
- `src/main.py` — 入口，`scout` / `confirm` / `all` 三个子命令（all = 先 confirm 后 scout）。
- `src/common.py` — HTTP 封装（urllib）、`state/` 读写、环境变量读取。
- `src/fetch_papers.py` — arXiv API + Semantic Scholar batch 补充（429 重试 3 次、失败跳过不中断）。
- `src/rank_papers.py` — Kimi 调用，每批 10 篇，解析失败降级跳过该批。
- `src/slack_notify.py` — 候选卡片发送、✅ reaction 轮询、线程回复；`resolve_channel()` 支持频道（`SLACK_CHANNEL_ID`）与私信（`SLACK_USER_ID`）两种目标。
- `src/update_paperlist.py` — 只改 `paperlist/README.md` 文件，**不执行任何 git 命令**（git 操作全在 workflow 里）。
- `src/post_x.py` — OAuth 1.0a 标准库发推；凭证缺失或 HTTP 错误只警告不抛异常。
- `state/` — `seen.json`（去重，上限 2000 条淘汰最旧）、`candidates.json`（候选与确认状态）。由 workflow 提交回仓库。
- `paperlist/` — git submodule（Riecacc/Awesome-Physical-AI-Infra-Papers）。

## 关键设计决策（改动前先想清楚为什么存在）

1. **Slack 确认用 ✅ reaction 轮询，不用按钮回调**：GitHub Actions 是定时批处理，无常驻进程，收不到 Slack 的 interactivity webhook。
2. **去重发生在打分之前**：`fetch_new_papers()` 先滤掉 `seen.json` 中的 id，重复运行不消耗 Kimi 配额、不重复推送。
3. **引用量对新论文无效**：新论文引用天然为 0，评估主权重在内容质量与主题契合度。不要用 Google Scholar 爬虫（无官方 API，会被封）。
4. **Kimi K3 的调用约束**：不传 `temperature`（K3 固定 1.0）；已设 `reasoning_effort=low` 控制批量打分成本。
5. **Kimi 双 endpoint**：开放平台 `api.moonshot.cn`（按量付费，模型 ID 如 `kimi-k3`）与 Coding Plan `api.kimi.com/coding/v1`（订阅，模型 ID 如 `k3-256k`）通过 `KIMI_API_URL`/`KIMI_MODEL` 切换，代码与两者兼容。
6. **workflow 中 git 推送顺序固定**：先 push paperlist 远端，再提交主仓库的 `state/` + submodule 指针。颠倒顺序会让主仓库短暂指向远端不存在的 commit。
7. **X 发推永不中断流水线**：任何失败只打印警告。

## 常用命令

```bash
python3 -m compileall src          # 语法检查（改动后必跑）
python3 src/main.py all            # 本地完整运行（需配好环境变量）
# 仅验证抓取链路（无需任何密钥）：
python3 -c "from src.fetch_papers import fetch_new_papers; print(len(fetch_new_papers()))"
```

## 运维常见坑（排查先看这里）

- **Slack 改了 scope 必须重新 Install to Workspace**，否则报 `missing_scope`；私信模式需 `im:write` scope。
- **私信报 `messages_tab_disabled`**：app 管理页 Features → App Home → 勾选 Messages Tab（此改动无需 reinstall）。
- **paperlist push 401/403**：`PAPERLIST_PAT` 过期或未对两个仓库授予 Contents 读写。
- **Actions job 不启动**：账号计费问题（私有仓库免费 2000 分钟/月，spending limit 为 $0 会全拦）；public 仓库 Actions 免费。
- **`secrets.XXX` 解析为空**：secret 建在了 Environment 而非 Repository secrets；或在 job 加 `environment: <名字>`。
- GitHub cron 定时可能延迟数分钟到数十分钟，属正常。

## 开发流程约定

- 代码风格：与现有代码一致，中文注释适度，不写 TODO/占位符。
- 改动后必做：`python3 -m compileall src` + 尽量跑通抓取链路验证。
- 修改了 README/本文档涉及的配置、流程、目录结构时，同步更新两个文档。
- 未经用户确认，不执行 `git commit` / `git push`。
