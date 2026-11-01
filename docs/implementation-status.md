# 实现状态与运行证据

2026-11-02 本地集成交付。四份设计文件保留目标与理由，实际行为、偏差与证据以本文件、[交付记录](delivery-evidence.md)、[实测报告](evaluation-report.md)为准。当前交付尚待整体分支复核，不声称最终审查完成。

## 当前实现

Settings/供应商适配/费用账本、SQLite模拟业务、认证/CSRF、严格Schema、单Agent两阶段循环、上下文/混合检索、持久事件worker、员工/审批/调试页面均已实现。6演示账号、8服务、5资源、12政策、22KB、12通用历史；16业务工具，加内部refine_issue与submit_turn。启动、账号、重置/重启见[根README](../README.md)。

所有LLM角色固定官方deepseek-flash；embedding固定SiliconFlow BAAI/bge-m3。本机两家凭据已配置，Settings.load读取gitignored .env/环境，不进入模型或浏览器。embedding缺失/错误明确lexical降级，不伪造向量；其费用不归入DeepSeek账本，也不声称永久免费。

每事件10模型请求、12非终结工具、120秒；整理/决策目标10,000/12,000保守UTF-8字节，不是精确token。必需上下文超容量明确停止，没有长聊天语义摘要。实际身份/范围/版本/租约/生命周期/审批/确认/验收由后端守卫；审批事件仅恢复原问题。已知多目标歧义暂停相关新自动写入；未分配反馈保留最后验证状态但all_resolved=false，页面待澄清。当前聊天持久化，不主动检索个人其他聊天。

## 验证与费用

- 最终程序套件203 passed in23.33s，无warning；API不确定性、闲置到期显示、重置费用边界均先复现失败。
- scripted external model + real Agent/HTTP/browser14checks通过，零付费；受控时钟真实UI另3checks通过。
- 真实DeepSeek HTTP/browser新输入smoke：2用户轮、2完成运行、8模型请求、5工具、1实际模拟解锁、0事件错误、最终resolved；页面重载和真实进程重启状态不变。首个驱动时序错误在既有聊天恢复，未重发/重复执行。[完整证据](delivery-evidence.md)。
- 安装和pip check通过。dev可选httpx2 2.16.0解决Starlette1.10弃用warning；运行时httpx、FastAPI/Starlette未因此升级。浏览器/文档依赖与最小运行分开。
- 原累计USD3账本固定runtime/usage.sqlite。Task5结束USD2.608177868/751请求/0预留；smoke增量USD0.025951501/8请求；之后USD2.634129369/759请求/0预留，余USD0.365870631。重置/评测独立世界/重启不清空迁移费用；估算非供应商账单。

## 正式评测与版本

历史66live measurements覆盖30设计槽位，混合多个实现版本；最新已测630e586共17次/10案例，16程序有效、12目标满足、4Agent事件错误，1次运行有2个必需交接缺失标记，8份旧判断过期/有争议。只有H01b有3次同候选。当前交付的后端不确定性/交接幂等/统计修复有程序回归和正常路径smoke，不能包装成最终30案例通过。grader v4仅程序验证，5/6实测校准属于v3。

Task5自身656请求/USD2.295008760与历史结束快照分开保留。生成器按已归属角色调用计费，current_project_ledger另列。公开摘要在evals/public；完整原件在本机忽略的evals/results，包包含公开投影和可见失败，不含本机数据库或密钥。

检索40查询：有答案35条，lexical命中26/35、hybrid35/35；无答案5/5正确空。实际hybrid38/40，另两条预过滤空不调embedding。D01/D05同候选两模式各2/2目标通过，小样本不证明普遍优势。无两阶段准确性消融、外部独立或真人专家盲测。

## 保留限制

模型仍有协议、调用上限、上下文容量和语言误解；Schema/执行守卫不能消除错误措辞。原审批矛盾、容量失败、H01澄清顺序、历史重复交接、模拟器/grader误判保留。专门歧义守卫依赖模型识别，小样本语义阈值不是证明。实际页面出现字面反斜杠n和英文目标标题，原文安全保留。企业数据/反馈合成，无真实专家联络、个人跨聊天记忆、完整传播仿真、专家接续、真实企业API。

七项工程裁定与错误代价见[决策记录](engineering-decisions.md)。原题逐项映射、模式与截图见[交付记录](delivery-evidence.md)。本次服务URL/PID/日志见[evidence/restart.json](evidence/restart.json)，PID随启动变化。源码archive为runtime/helpdesk-submission.zip，来自tracked HEAD，最终审查有改动后需要重新生成。
