# IT Helpdesk Agent 文档入口

[根README：安装、账号、运行与边界](../README.md)。以下材料本地阅读，不发布。

1. [实现状态](implementation-status.md)：当前行为、默认值、费用和限制。
2. [真实评测报告](evaluation-report.md)：历史版本、实际模式、分子/分母、失败与未测。
3. [集成交付证据](delivery-evidence.md)：新输入真实HTTP/browser smoke、重启、原题逐项映射。
4. [七项工程裁定](engineering-decisions.md)：理由及判断错误代价，与用户确认产品选择分开。
5. [系统设计](01-system-design.md)：需求、架构、状态、来源、政策与执行。
6. [评估设计](02-evaluation.md)：30设计槽位、驱动器、断言、评分和实验口径。
7. [答辩与扩展](03-defense-and-extensions.md)：选择依据与未实现扩展。
8. [提示词与契约](04-prompts-and-contracts.md)：概念设计与实际Schema差别。

![实际架构](architecture.svg)

四设计文件保留开发前的理由和概念示例，开头加当前实现对照；它们不是已通过成绩。HTML来自Markdown，入口index.html。可选文档工具：pip install markdown-it-py beautifulsoup4，然后从仓库根执行python docs/_tools/render_docs.py。原题见[候选题目](../agentic_ai_take_home_candidate_instructions_v20260421_01.html)。
