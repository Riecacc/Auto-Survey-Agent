---
name: paper-ranking
description: 论文评估规则。用于指导大模型对候选论文做方向归属判定与 1-10 分打分，决定是否值得收录进 Physical AI Systems / Infra 研究图谱。
---

# Paper Ranking：论文评估规则（Physical AI Systems / Infra）

本 list 的定位是 **Physical AI 的系统与基础设施研究图谱**，而非机器人算法大全。评估分两步：**先判定方向归属，再打分**。

## 第一步：方向归属判定

方向清单（id、名称、描述、tier）会在本 system prompt 末尾以 JSON 形式给出，以该清单为准。对每篇论文：

- 判断它属于清单中的哪个/哪些方向，输出其 id 列表到 `directions`；
- 若方向定义了子类（subdirections），进一步给出每个所属方向的子类 id，输出到 `subdirections`（键为方向 id，值为子类 id）；拿不准或方向无子类时输出 `{}`；
- 若与所有方向都不契合，输出空列表 `[]`，并在 `reason` 中说明原因——该论文不会被收录；
- tier 为 `milestone` 的方向（里程碑模型 / 通用高效 ML 方法）适用高门槛，见下文。

## 第二步：打分（1–10 分）

综合以下维度评估：

1. **内容质量与系统/效率贡献（主权重）**：论文是否对 VLA/WAM 的运行时、serving、调度、硬件加速或推理效率有实质贡献。技术新颖性与扎实程度主导最终分数。
2. **发表 venue**：命中顶会白名单加分。**系统类（MLSys, EuroSys, SOSP, OSDI, NSDI, SIGCOMM）与架构类（ISCA, MICRO, HPCA, ASPLOS）的加分权重不低于机器人类（RSS, CoRL, ICRA, IROS）**——本 list 以系统/infra 为核心。arXiv 预印本不扣分，靠内容质量说话。
3. **作者最高 h-index**：领域内知名学者是加分项，但不应压过内容质量本身。
4. **是否开源**：摘要或 comment 中出现 GitHub 链接是强信号，开源可复现的工作显著加分。
5. **引用数**：仅作参考。当日新论文引用天然为 0，**不要因此扣分**。

## 显式拒收模式（直接给低分，≤4 分）

- 仅优化 agent prompt / prompt engineering 的论文；
- 无系统或效率贡献的纯算法增量（如新刷一个 benchmark 的 policy 结构改动）；
- 与 Physical AI 弱相关的应用论文（仅把现成模型套到某个场景，无 infra 贡献）；
- milestone 方向（tier=milestone）的普通工作：**score 必须 ≥9 且输出 `milestone_reason`**（一句话说明为什么是改写技术路线的里程碑），否则按拒收处理。

## 输出要求

- 输出**严格 JSON 数组**，不要包含任何额外文字或解释。
- 每个元素格式：
  ```json
  {
    "arxiv_id": "...",
    "score": 8,
    "reason": "中文一句话理由",
    "summary_zh": "中文两句话摘要",
    "tweet_en": "英文推文草稿，含 arXiv 链接，不超过 270 字符",
    "directions": ["01-vla-wam-systems-serving"],
    "subdirections": {"01-vla-wam-systems-serving": "serving-runtime"},
    "tags": ["vla-serving", "quantization"],
    "milestone_reason": ""
  }
  ```
- `score` 为 1 到 10 的整数；`directions` 为方向 id 列表（不契合则为 `[]`）；`subdirections` 为方向 id → 子类 id 的映射（无子类或拿不准则为 `{}`）；`tags` 为自由技术标签（短横线小写）；`milestone_reason` 仅 milestone 方向论文填写，其余留空字符串。
