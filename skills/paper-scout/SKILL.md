---
name: paper-scout
description: 每日论文巡检的检索领域定义与渠道规则。用于指导从 arXiv 等渠道检索 Physical AI Systems / Infra 方向的最新论文。
---

# Paper Scout：论文检索领域定义

## 检索领域

本项目的论文检索领域为 **Physical AI Systems / Infra**，关注支撑 VLA（Vision-Language-Action）与 WAM（World-Action Model / 世界模型）落地运行的**系统与基础设施**，而不是机器人算法本身：

- VLA / WAM 的推理运行时、serving、调度、实时控制回路、端云协同部署
- 面向机器人 / 多模态模型的加速器与计算架构（ISCA / MICRO / HPCA / ASPLOS 系）
- 以效率为目标的 VLA / WAM 算法：token 剪枝、量化 / 蒸馏、action head 加速
- 与具身智能推理有明确技术迁移关系的 LLM / 多模态 serving 系统

检索采用**分组关键词取交集**的方式：`subjects`（研究对象：VLA / world model / embodied AI 等）与 `aspects`（系统侧面：serving / accelerator / quantization 等）两组关键词同时命中，以过滤掉与系统无关的纯算法论文。

## 检索配置

具体的关键词分组与 arXiv 分类见 `config/scout.json`（唯一数据源）。**修改检索范围时，请直接编辑 `config/scout.json` 并同步更新本文件中的领域描述。**

## 顶会白名单

以下会议视为本领域的顶级 venue，在评估影响力时优先参考（系统 / 架构类与机器人类同等权重）：

- 系统类：MLSys, EuroSys, SOSP, OSDI, NSDI, SIGCOMM
- 架构类：ISCA, MICRO, HPCA, ASPLOS
- 机器人类：RSS, CoRL, ICRA, IROS

## 检索渠道优先级

1. **arXiv API**（主要来源，按分类 + 分组关键词 + 提交日期过滤）
2. **Semantic Scholar**（补充作者 h-index、venue、引用数等元数据）
3. **HuggingFace Daily Papers**（可观察社区热度的补充参考）

## 禁止事项

- **禁止使用 Google Scholar 爬虫**：违反其服务条款且极易被封禁，作者指标一律通过 Semantic Scholar API 获取。
