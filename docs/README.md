# 项目文档

提交时以仓库根目录的 [README](../README.md) 为入口。原题要求可运行代码、README 和面试演示；本目录的 HTML 只是 Markdown 的本地阅读版本，不需要单独提交整个文档站。

## 建议阅读

- [演示说明](demo.md)：录屏、登录账号与现场操作。
- [验证结果与限制](implementation-status.md)：哪些能力已运行，哪些结论仍缺证据。
- [系统设计](01-system-design.md)：模块分工、数据、状态和执行边界。需要详细技术说明时查阅。

## 实测记录

- [当前提示词迭代](delivery-iteration-result.md)：v6 的七案测量、v7 的两案复测及失败原文。
- [历史评测](evaluation-report.md)：较早版本的场景、检索对照与评分记录。
- [集成记录](delivery-evidence.md)：浏览器、重启、CI 修复和原题对应关系。

## 开发与答辩参考

[评估设计](02-evaluation.md)、[提示词与契约](04-prompts-and-contracts.md)、[取舍与扩展](03-defense-and-extensions.md)、[工程决策记录](engineering-decisions.md)、[开发与测试流程](superpowers/plans/2026-11-02-helpdesk-implementation.md)。这些是详细参考，不要求评审逐页阅读。

HTML 由 Markdown 生成。修改文档后可运行 `python docs/_tools/render_docs.py`，需要 `markdown-it-py` 和 `beautifulsoup4`。原题：[Candidate Instructions](../agentic_ai_take_home_candidate_instructions_v20260421_01.html)。
