---
name: paper-scout
description: 每日论文巡检的检索领域定义与渠道规则。用于指导从 arXiv 等渠道检索 Physical AI Infra（机器人基础设施方向）的最新论文。
---

# Paper Scout：论文检索领域定义

## 检索领域

本项目的论文检索领域为 **Physical AI Infra**（机器人基础设施方向），关注支撑机器人与具身智能系统的基础技术，包括但不限于：

- 机器人学习（robot learning）与视觉-语言-动作模型（vision-language-action）
- 机器人基础模型（robot foundation model）与世界模型（world model）
- 仿真与 sim2real 迁移
- 操作（manipulation）、运动（locomotion）、遥操作（teleoperation）

## 检索配置

具体的关键词列表与 arXiv 分类见 `config/scout.json`（唯一数据源）。**修改检索范围时，请直接编辑 `config/scout.json` 并同步更新本文件中的领域描述。**

## 顶会顶刊白名单

以下会议与期刊视为本领域的顶级 venue，在评估影响力时优先参考：

- RSS (Robotics: Science and Systems)
- CoRL (Conference on Robot Learning)
- ICRA (IEEE International Conference on Robotics and Automation)
- IROS (IEEE/RSJ International Conference on Intelligent Robots and Systems)
- NeurIPS, ICML, ICLR
- T-RO (IEEE Transactions on Robotics)
- RA-L (IEEE Robotics and Automation Letters)
- IJRR (International Journal of Robotics Research)

## 检索渠道优先级

1. **arXiv API**（主要来源，按分类 + 关键词 + 提交日期过滤）
2. **Semantic Scholar**（补充作者 h-index、venue、引用数等元数据）
3. **HuggingFace Daily Papers**（可观察社区热度的补充参考）

## 禁止事项

- **禁止使用 Google Scholar 爬虫**：违反其服务条款且极易被封禁，作者指标一律通过 Semantic Scholar API 获取。
