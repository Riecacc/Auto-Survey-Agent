# Auto-Survey-Agent

每日论文巡检 agent：自动抓取 Physical AI Infra（机器人基础设施）方向的最新论文，用大模型打分筛选，推送到 Slack 等待人工确认，确认后收录进论文列表并可选发推。纯 Python 标准库实现，无任何 pip 依赖。

## 架构与流程

每天由 GitHub Actions 定时运行（北京时间约 09:17），流程如下：

```
arXiv API ──> 去重(state/seen.json) ──> Semantic Scholar 补充元数据
      │
      ▼
Kimi (Moonshot) API 按评估规则打分（≥ 阈值进入候选）
      │
      ▼
每篇候选发一条独立 Slack 消息（ts 存入 state/candidates.json）
      │
      ▼  （人工给候选消息打 ✅）
下一次运行（或手动触发）轮询 Slack reactions
      │
      ▼
确认的论文：追加进 paperlist/README.md 表格 ──> 尝试发推（X 凭证缺失时跳过）
      │                                        └──> Slack 线程回复"已收录"
      ▼
state/ 与更新后的 paperlist submodule 指针提交回主仓库；paperlist 修改直接 push 到其 main
```

目录结构：

- `config/scout.json` — 检索配置的唯一数据源（arXiv 分类、关键词、回溯天数、最大结果数）
- `skills/paper-scout/SKILL.md` — 检索领域定义与渠道规则
- `skills/paper-ranking/SKILL.md` — 影响力评估规则（作为打分模型的 system prompt）
- `src/` — 全部实现代码（标准库 only）
- `state/` — 运行状态（`seen.json` 去重记录、`candidates.json` 候选与确认状态），由 workflow 提交回仓库
- `paperlist/` — git submodule，论文列表仓库（[Awesome-Physical-AI-Infra-Papers](https://github.com/Riecacc/Awesome-Physical-AI-Infra-Papers)）

设计要点：

- **密钥一律不放仓库**（即使私有仓库也不行）：submodule、fork、误推都会导致泄露，且 GitHub 会对泄露 token 触发合作方自动吊销。所有凭证通过 GitHub Secrets 注入为环境变量，仓库内零密钥。
- **Slack 确认用 reaction 轮询而非按钮回调**：GitHub Actions 是定时批处理，没有常驻进程，收不到 Slack 按钮的 webhook 回调。轮询方案零额外组件，代价是确认最长延迟一天（可随时手动 Run workflow 立即处理）。
- **评估指标对"当日新论文"的适配**：新论文引用量天然为 0，因此引用数仅作参考维度；主权重放在内容质量与主题契合度，辅以作者 h-index、venue、是否开源等先行指标。详见 `skills/paper-ranking/SKILL.md`。
- **不使用 Google Scholar 爬虫**：无官方 API，爬取会被封，不可靠。

## 配置清单

所有配置都在仓库 **Settings → Secrets and variables → Actions → Secrets 标签页 → New repository secret** 添加。

### 关于 Secrets 页面（必读）

- **不需要新建 Environment**。workflow 里的 `${{ secrets.XXX }}` 默认读仓库级（Repository）secrets；只有 job 里显式写了 `environment: 名字` 才会去读 Environment secrets。注意别进错页面：**Settings → Environments** 确实只有 "New environment" 按钮，正确入口是 **Settings → Secrets and variables → Actions**。
  - 若你是受限的企业组织账号、确实只能用 Environment secrets：创建一个名为 `production` 的 environment 并在其中添加全部 secrets，然后在 `.github/workflows/daily_scout.yml` 的 `scout` job 下加一行 `environment: production`。
- **用 Secret 而不是 Variable**。凭证类必须放 Secret（加密存储、日志自动打码）；`KIMI_API_URL`、`KIMI_MODEL` 等虽不敏感，但 workflow 引用的是 `secrets.` 上下文，放 Variable 会读不到——统一全放 Secret 标签页即可。

### Secret 汇总

| Secret | 必需 | 获取方式 |
| --- | --- | --- |
| `KIMI_API_KEY` | 是 | 按量付费用户在 [Moonshot 开放平台](https://platform.moonshot.cn/) 创建 API Key；**Coding Plan 订阅用户**在 [Kimi Code Console](https://www.kimi.com/code/console) 创建（最多 5 个 key，**只显示一次，当场保存**） |
| `KIMI_API_URL` | 否 | API 地址。默认开放平台 `https://api.moonshot.cn/v1/chat/completions`；**Coding Plan 用户必须设为** `https://api.kimi.com/coding/v1/chat/completions` |
| `KIMI_MODEL` | 否 | 打分模型。默认 `kimi-k3`；**Coding Plan 用户设为** `k3-256k` |
| `SCORE_THRESHOLD` | 否 | 入选分数阈值（1-10），默认 `7` |
| `SLACK_BOT_TOKEN` | 是 | 见下方「Slack 配置」 |
| `SLACK_CHANNEL_ID` | 二选一 | 发频道时用：频道 ID（`C`/`G` 开头，频道详情页底部可见） |
| `SLACK_USER_ID` | 二选一 | **发私信时用**：个人资料页 ⋯ → Copy member ID（`U` 开头）。两个都填时优先用频道 |
| `PAPERLIST_PAT` | 是 | 见下方「PAPERLIST_PAT 配置」 |
| `X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_SECRET` | 否 | 见下方「X 配置（可选）」；**未配置则自动跳过发推，不影响其他功能** |

### Kimi 配置（两种计费方式二选一）

| | 开放平台（按量付费） | Coding Plan（订阅） |
| --- | --- | --- |
| `KIMI_API_KEY` 来源 | platform.moonshot.cn | Kimi Code Console |
| `KIMI_API_URL` | `https://api.moonshot.cn/v1/chat/completions`（默认，可不设） | `https://api.kimi.com/coding/v1/chat/completions` |
| `KIMI_MODEL` | `kimi-k3`（默认，可不设） | `k3-256k` |

注意事项：

- **K3 需要充值解锁**：开放平台最低充值 10 元；新用户赠送的 15 元代金券**不可用于 K3**。未充值时打分环节会报权限错误，可改用 `kimi-k2-0711-preview`。
- **Coding Plan 选 `k3-256k` 而非 `k3`**：1M 上下文版本消耗约两倍配额，批量打分用不到那么长的上下文。
- **Coding Plan 配额与你本地的 Kimi Code CLI 共享**：每周自动刷新，另有每 5 小时约 300–1200 次请求的滚动窗口。本项目每天约 5 次调用（50 篇 / 每批 10 篇），占比可忽略。
- **官方定位提醒**：Coding Plan 面向编程场景设计，非编码的产品化集成官方建议走开放平台。本项目为个人极低频使用，实际风险很小；如日后任务加重（如全文精读），换成开放平台 key 即可，代码无需改动。
- K3 始终开启思考模式，代码已设 `reasoning_effort=low` 控制耗时与成本；K3 固定 `temperature=1.0`，不要显式传 temperature。

### Slack 配置

1. 在 [api.slack.com/apps](https://api.slack.com/apps) 创建 app：**选 From scratch（或 Starter app），不要选 AI agent**——AI agent 模板是给"住在 Slack 里的 AI 对话助手"用的，本项目里 Slack 只是通知/确认渠道，bot 不接收任何回调。
2. **OAuth & Permissions → Bot Token Scopes** 添加：
   - `chat:write`（发消息）
   - `reactions:read`（读 ✅ 确认）
   - `channels:history`（频道消息历史）
   - `im:write`（**私信模式必需**，用于 `conversations.open`）
   - `groups:history`（仅私有频道需要）
3. 点 **Install to Workspace** 安装，复制 `xoxb-` 开头的 Bot User OAuth Token 即 `SLACK_BOT_TOKEN`。**之后每次修改 scope 都要重新 Install 一次才生效。**
4. 选择消息接收方式（二选一）：
   - **频道**：把 bot 拉进目标频道（`/invite @你的bot`），频道详情页底部复制 ID（`C`/`G` 开头）填 `SLACK_CHANNEL_ID`；
   - **私信**：个人资料页 → 右上角 ⋯ → **Copy member ID**（`U` 开头）填 `SLACK_USER_ID`，脚本会自动通过 `conversations.open` 打开与 bot 的私信频道。
5. **不需要**执行 Slack 引导里的 "run your app locally"：那是给托管型 app（Socket Mode / 事件回调）用的。本项目是 GitHub Actions 单向调用 Slack Web API，无需本地服务、无需 Event Subscriptions / Interactivity。

Slack 常见问题：

- **私信显示"xx 已关闭私信功能，所以你无法回复这些消息"**：app 的 Messages Tab 未开启。进入 app 管理页 → **Features → App Home → Show Tabs**，勾选 **Messages Tab**（及"允许用户从消息标签页发送消息"）。确认流程只依赖 reaction 通常不受影响，但建议打开以消除警告。
- **验证 token 是否有效**（本地一条命令，无需起服务）：
  ```bash
  curl -s -H "Authorization: Bearer xoxb-你的token" https://slack.com/api/auth.test
  # 返回 "ok":true 即有效
  ```

### PAPERLIST_PAT 配置

workflow 默认的 `GITHUB_TOKEN` 无法推送外部仓库，需创建 fine-grained PAT 供 checkout submodule 和 push 使用：

1. GitHub 头像 → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**；
2. **Repository access** 选 "Only select repositories"，勾选 **Auto-Survey-Agent** 和 **Awesome-Physical-AI-Infra-Papers** 两个仓库；
3. **Permissions** 中 **Contents 设为 Read and write**；
4. 建议有效期 90 天或 1 年。**PAT 到期当天 workflow 会在 push 步骤报 401/403**，届时重新生成并更新 secret 即可。

paperlist 更新机制：确认的论文追加到 `paperlist/README.md` 的 Papers 表格（日期 / 标题带 arXiv 链接 / venue / 代码链接，按 arXiv id 去重），直接 push 到该仓库 `main`；随后主仓库提交 `state/` 与**更新后的 submodule 指针**，保证两边一致。

### X 配置（可选）

- 在 [X Developer Portal](https://developer.x.com/) 创建 app，拿到 API Key/Secret 与 Access Token/Secret 四个值，对应填入四个 secret。
- Free tier 只能调 `POST /2/tweets`（OAuth 1.0a），每月约 500 条，每天几篇足够。
- 未配置时自动跳过发推，不影响其他环节；配置后也可以随时删除 secret 停用。

## 首次运行与验证

1. 配好 secret 后，仓库 **Actions** 标签页 → **Daily Paper Scout** → **Run workflow** 手动触发；
2. 成功标志：Slack 收到若干论文卡片，仓库出现 `chore: update scout state...` 自动提交；
3. 给任意一条卡片打 **✅**，再手动 Run 一次：该论文应被追加到 paperlist 仓库 README 的表格中，原消息线程回复"已收录到 paper list ✅"；
4. 之后每天定时自动运行；给卡片打 ✅ 后最迟次日收录，也可随时手动 Run workflow 立即处理。

## 本地运行

```bash
# 配置好上述环境变量后（X 凭证可选）
python src/main.py all        # 先处理 ✅ 确认，再抓取新论文
python src/main.py scout      # 仅抓取 + 打分 + 推送候选
python src/main.py confirm    # 仅轮询确认并收录
```

## 如何调整

- **修改检索领域**：编辑 `config/scout.json` 中的 `arxiv_categories` 与 `keywords`，并同步更新 `skills/paper-scout/SKILL.md` 中的领域描述。
- **修改评估规则**：编辑 `skills/paper-ranking/SKILL.md`，该文件全文会作为打分模型的 system prompt。
- **调整入选阈值**：设置 `SCORE_THRESHOLD` secret（默认 7）。
- **切换 Kimi 计费方式/模型**：改 `KIMI_API_URL`、`KIMI_MODEL`、`KIMI_API_KEY` 三个 secret，代码无需改动。

## 已知限制

- GitHub Actions 的 cron 在高负载时可能延迟数分钟到数十分钟，不适合精确时点（本项目无影响）。
- Semantic Scholar 免费 API 有速率限制，每日一次的频率不会触发；个别论文查不到时跳过补充字段而不中断。
- Kimi 输出解析失败的批次会跳过并打印警告，不会中断整体流程；ARXiv 抓取与 Slack 通知不受影响。
