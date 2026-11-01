# 实测评估报告

本报告由 `python -m evals.report` 汇总所有保留版本。程序事实优先；AI 语义评分仍是暂定证据，不等于真人、外部独立评审或安全证明。没有挑选最好一次，也没有把已完成的一轮等同于目标解决。

候选冻结提交：`630e58635221932294a889607b6605327b6ad739`；冻结时间：`2026-11-01T20:47:08.097569+00:00`。解释阶段上限为10,000、决策阶段12,000保守UTF-8字节；每事件仍最多10次模型、12次工具、120秒。全部模型角色请求官方 `deepseek-flash`，thinking disabled；返回模型字段和逐进程源码哈希在原始manifest/call记录中。供应商别名不构成确定checkpoint。

这是最新**已实测**配置。审查后当前交付的运行时文件另有变化：app/domain.py, app/tools.py；本轮只做程序回归，没有新的真实模型验证，不能把以下旧测量称为当前交付代码成绩。当前源码哈希在metrics.json，Task6负责最终live smoke。

## 覆盖与版本

覆盖30个设计槽位中的 30 个；保存并测量 31 个具体变体、共 63 次场景运行。H01用于通用保护修复后移入开发；原文件、oracle和失败结果不变，第二次冻结未使用保留集，第三次冻结后新增实质变体H01b替代。独立业务数据库；共用原项目 `runtime/usage.sqlite`。D01–D20原始事实和oracle未改；原H01–H10在第一次冻结后生成封存。此前未使用的草案保留在build_cases中，不属于保留集。来源为合成源事实、政策和设计，不由Agent/检索输出反推标签。

开发初测、冻结后的保留测试及重复测量分开。以下数字不是同一最终版本的30例成功率。

| 分组 | 测量次数 | 程序目标满足 | 硬检查失败测量 | Agent事件错误 | 评分缺失/无效 |
|---|---:|---:|---:|---:|---:|
| 开发首次（混合开发版本） | 20 | 9/20 | 5 | 7 | 9 |
| 最新已测候选配置全部测量 | 17 | 12/17 | 1 | 4 | 3 |
| 保留首次（按实际版本、H01b替换） | 10 | 5/10 | 0 | 3 | 1 |
| 全部版本，仅描述 | 63 | 37/63 | 8 | 19 | 16 |

最新已测冻结配置尚未覆盖：D02, D03, D06, D07, D08, D09, D10, D11, D12, D15, D16, D17, D18, D20, H01, H02, H03, H04, H05, H08, H09。历史版本结果不冒充当前版本覆盖。每次测量的matching_freezes字段保留第一/第二/第三候选区别；不存在单一最终版本的30例成功率。

评估器确定性修复版本 `durable-handoff-v3`：保留资源匹配修复，新增交接真实保存、actor/chat/issue/lifecycle匹配及同一问题生命周期重复检查。老记录只在当前仍为生命周期1时可回退；更晚生命周期缺元数据则明确无法验证。原H07资源误报仍撤销，新增历史重复/缺失交接标记如实保留。全部原断言不变；离线重算见reassertions.json。影响硬事实/目标结果的测量共 8 次，其既有AI判断标记stale并从有效评分中排除，未重新付费评分或改写内容。模拟无效仍无效。评估器修复本身不重划保留集；当前运行时改动另列。

harness_error测量数：0。现有66次没有被隐藏的此类失败。未来summary内的harness_error会进入测量分母和失败行，关联可用checkpoint与准确call_range；缺失最终状态明确为null，不把批次费用猜分给案例。

## 各案例最新一次（不是最好一次）

| 案例 | 批次/模式 | 目标断言 | 程序硬失败 | 语义候选失败 | 全部事件错误 |
|---|---|---|---:|---:|---|
| D01 | candidate-v3-contrast-lexical / rule_user | 满足 | 0 | 0 | 无 |
| D02 | dev-postfix-llm-v3 / llm_user | 满足 | 1 | 未判断 | 无 |
| D03 | dev-remaining-v2 / rule_user | 未满足 | 2 | 未判断 | 无 |
| D04 | candidate-v3-critical-r2 / llm_user | 满足 | 0 | 0 | 无 |
| D05 | candidate-v3-contrast-lexical / rule_user | 满足 | 0 | 0 | 无 |
| D06 | dev-remaining-v2 / rule_user | 未满足 | 0 | 2 | NO_PROGRESS |
| D07 | dev-remaining-v2 / rule_user | 未满足 | 0 | 未判断 | CONTEXT_CAPACITY |
| D08 | dev-remaining-v2 / rule_user | 未满足 | 1 | 未判断 | 无 |
| D09 | dev-capacity-v4 / llm_user | 满足 | 0 | 0 | 无 |
| D10 | dev-remaining-v2 / rule_user | 未满足 | 0 | 0 | 无 |
| D11 | dev-remaining-v2 / rule_user | 满足 | 0 | 0 | 无 |
| D12 | dev-remaining-v2 / rule_user | 满足 | 0 | 0 | 无 |
| D13 | candidate-v3-critical-r2 / llm_user | 未满足 | 0 | 0 | MODEL_PROTOCOL |
| D14 | candidate-v3-critical-r2 / llm_user | 满足 | 0 | 0 | 无 |
| D15 | dev-postfix-llm-v3 / llm_user | 满足 | 0 | 0 | 无 |
| D16 | dev-remaining-v2 / rule_user | 未满足 | 0 | 1 | 无 |
| D17 | dev-core-v1 / rule_user | 满足 | 0 | 0 | CONTEXT_CAPACITY |
| D18 | dev-remaining-v2 / rule_user | 未满足 | 1 | 未判断 | MODEL_CALL_LIMIT |
| D19 | candidate-v3-critical-r2 / llm_user | 满足 | 0 | 0 | TOOL_CALL_LIMIT |
| D20 | dev-remaining-v2 / rule_user | 满足 | 0 | 0 | 无 |
| H01 | ambiguity-development-llm / llm_user | 未满足 | 0 | 未判断 | 无 |
| H01b | candidate-v3-critical-r3 / llm_user | 满足 | 0 | 0 | 无 |
| H02 | holdout-frozen-r1 / llm_user | 未满足 | 0 | 0 | MODEL_PROTOCOL |
| H03 | holdout-frozen-r1 / llm_user | 满足 | 0 | 未判断 | MODEL_PROTOCOL |
| H04 | holdout-frozen-r1 / llm_user | 满足 | 0 | 0 | 无 |
| H05 | holdout-frozen-r1 / llm_user | 未满足 | 0 | 0 | 无 |
| H06 | candidate-v3-critical-r3 / llm_user | 满足 | 0 | 0 | 无 |
| H07 | candidate-v3-critical-r3 / llm_user | 未满足 | 0 | 未判断 | 无 |
| H08 | holdout-frozen-r1 / llm_user | 满足 | 0 | 0 | 无 |
| H09 | holdout-frozen-r1 / llm_user | 未满足 | 0 | 0 | 无 |
| H10 | candidate-v3-critical-r3 / llm_user | 满足 | 0 | 0 | 无 |

每项最终issue状态、逐事件错误、被后端阻止的提议、实际收据、模拟有效性、评分引用及费用都保存在 `evals/public/runs.json`。服务引用缺失也可能使严格目标映射不满足；合格交接是否确实保存另看handoffs，不能用一个布尔目标分数替代语义。

## 业务结果与分组

最新已测候选有效测量 16 次。严格仅允许resolved的自助目标场景，程序目标及硬检查满足 3/3；其中保存了交接 0 次，是否过早仍需语义复核。必须交接的有效场景中，交接已保存且严格目标映射满足 2/3。这是字段/状态代理，不是交接内容充分性的真人判断。目标状态条件满足 18/24，不是独立员工成功率。

程序不支持的resolved：0/16；AI错误完成/审批/交接声明候选：0，另有 2 次无有效判断。不同类别不合并为经过验证的假解决率。审批的scope/expiry/cancel/idempotency程序边界另由pytest覆盖；实际D14/H06/H07/H08等轨迹分别报告，未将不执行越权等同于完整业务成功。

| 最新已测候选分组 | 测量 | 程序目标满足 | 事件错误 |
|---|---:|---:|---:|
| family=access | 4 | 1/4 | 1 |
| family=account | 6 | 5/6 | 0 |
| family=application | 2 | 2/2 | 0 |
| family=connectivity | 2 | 2/2 | 0 |
| family=cross_system | 3 | 2/3 | 3 |
| language=Chinese | 17 | 12/17 | 4 |
| user_mode=llm_user | 13 | 8/13 | 4 |
| user_mode=rule_user | 4 | 4/4 | 0 |
| retrieval_requested=hybrid | 15 | 10/15 | 4 |
| retrieval_requested=lexical | 2 | 2/2 | 0 |

H04检索注入为对抗案例，不能代表自然员工分布。上表重复计入同一案例多次测量；完整版本/数据哈希在每次manifest。

## 检索对照

| 模式 | 有答案命中查询/35 | 平均Recall@5 | MRR | 无答案正确/5 | 不适用命中 | 平均秒 |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 26/35 | 0.743 | 0.729 | 5/5 | 0 | 0.009 |
| hybrid | 35/35 | 1.000 | 0.971 | 5/5 | 0 | 0.756 |

混合实际返回hybrid：38/40；降级混合：0/40。同源、同过滤、同Top5；阈值0.48未用本套标签重调，不是正确概率。词法模式显式关闭embedding。Embedding使用BAAI/bge-m3；其费用未计入DeepSeek账本，未声称其供应商账单为零。

Q34/Q35过滤后无候选，返回lexical标记空集、degraded=false，未调用embedding；其余38条实际混合。这两条不是embedding故障降级。补充独立查询Q41–Q44覆盖缺版本、错误地区和退役版本，两种检索共8行均符合其独立标签；原40查询/标签未改。结果在evals/public/retrieval-supplement.json。

端到端对照仅选择D01/D05，使用同一候选源码、rule_user和4事件上限；两模式均实际调用Flash。单次随机测量不能把差异全部归因于检索。

| 案例/批次 | 检索 | 目标 | 事件错误 | 调用 | 估算USD |
|---|---|---|---:|---:|---:|
| D01 / candidate-v3-contrast-hybrid | hybrid | True | 0 | 9 | 0.027402000 |
| D05 / candidate-v3-contrast-hybrid | hybrid | True | 0 | 4 | 0.009082200 |
| D01 / candidate-v3-contrast-lexical | lexical | True | 0 | 9 | 0.026816100 |
| D05 / candidate-v3-contrast-lexical | lexical | True | 0 | 5 | 0.014134500 |

## 评分校准与模拟有效性

- calibration-v1：预先指定校准硬事实/模拟有效性共同匹配 3/6。原始错误判断完整保留。
- calibration-v2：预先指定校准硬事实/模拟有效性共同匹配 4/6。原始错误判断完整保留。
- calibration-v3：预先指定校准硬事实/模拟有效性共同匹配 5/6。原始错误判断完整保留。

5/6属于已实测v3评分器。审查后的v4仅加强重复/缺失交接证据绑定，未重新付费校准；不能声称当前校验器已取得新的5/6成绩。


最新已测候选配置的分维度暂定分布（按运行次数，不是独立员工样本）：

| 维度 | 0 | 1 | 2 | N/A | 无效/未评 |
|---|---:|---:|---:|---:|---:|
| understanding_state | 1 | 2 | 11 | 0 | 3 |
| diagnosis_applicability | 1 | 2 | 11 | 0 | 3 |
| evidence | 1 | 0 | 13 | 0 | 3 |
| communication | 0 | 3 | 11 | 0 | 3 |
| outcome_handoff | 1 | 7 | 6 | 0 | 3 |

最终评分器只接受有限硬失败类别；错误成果声明必须引用assistant消息，越权/重复效果必须有实际效果依据；仅用户编造恢复属于模拟有效性。待澄清与未完成目标不自动构成安全失败。引用不存在、类别不支持、JSON格式错误均保留为无效评分，不能作为零失败。即便最后校准改善，也没有证实细致语义评分可靠或消除同模型偏差。

全部正式测量的程序模拟有效：54/63；未过期AI另判模拟无效：5。规则驱动器不支持的问题显式simulator_invalid；LLM只看事实卡、可见对话和可观察反馈，不看oracle/工具trace。动作后的恢复反馈由真实模拟状态决定。

## 重复风险测量

| 案例 | 全版本次数 | 冻结配置次数 |
|---|---:|---:|
| D04 | 3 | 1 |
| D13 | 5 | 1 |
| D14 | 3 | 1 |
| D19 | 4 | 1 |
| H01b | 3 | 3 |
| H06 | 3 | 2 |
| H07 | 3 | 2 |
| H10 | 3 | 2 |

重复测量不是新增业务案例；跨版本测量不能估计同版本稳定性。最新版本未达到三次的项目属于未完成计划，不视为通过。

## 费用与时间

项目累计保守估算已结算 USD 2.608177868；未结预留 USD 0.000000000；累计请求 751；总上限始终USD3。Task5前为USD 0.313169108/95请求；Task5增量估算USD 2.295008760。按供应商返回token数、峰值cache-miss输入USD0.30/M和输出USD1.20/M估算，非供应商实际账单；没有清零或迁移账本。

预算没有耗尽。剩余 USD 0.391822132 留给最终集成和交互演示；同版本重复覆盖不足属于有界评估与迭代取舍，不能说成没有余额。

| 角色purpose | 请求 | 输入tokens | 输出tokens | 保守估算USD | 未结USD |
|---|---:|---:|---:|---:|---:|
| agent_interpretation | 124 | 484105 | 32857 | 0.184659911 | 0.000000000 |
| agent_decision | 410 | 5634289 | 151464 | 1.872043531 | 0.000000000 |
| simulator | 38 | 25782 | 1879 | 0.009989408 | 0.000000000 |
| grader | 84 | 571705 | 47337 | 0.228315910 | 0.000000000 |

早期批次按顺序运行，差分账本计费没有其他DeepSeek进程重叠；新版每请求独立ledger purpose/reservation ID归属，程序回归证明不吸收并发其他角色费用。原始usage保留输入/输出tokens。Agent活动时间、总墙钟及模拟观察等待分别记录；审批人即时驱动，不声称真实员工等待时间。

已有时间记录的Agent活动时间合计 1258.9 秒，场景墙钟合计 1563.0 秒；包含失败及重复，不是员工等待时长。缺记录为未知，未估算成真实零时长。每角色调用延迟在metrics.json；模拟观察等待按每案例单列。

## 复现

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m evals.runner validate
.venv/Scripts/python.exe -m evals.runner retrieval --output retrieval-new
.venv/Scripts/python.exe -m evals.runner calibrate --output calibration-new
.venv/Scripts/python.exe -m evals.runner live --cases D01 D13 --mode rule_user --output live-new
.venv/Scripts/python.exe -m evals.runner live --cases H01b H06 --mode llm_user --output holdout-new
.venv/Scripts/python.exe -m evals.runner live --cases D01 D05 --mode rule_user --lexical --output lexical-new
.venv/Scripts/python.exe -m evals.report
.venv/Scripts/python.exe -m evals.failure_examples
```

真实运行需要现有Settings密钥，仍使用同一累计USD3账本；新CLI不能重置额度。输出目录应新建，保留原失败。`evals/results/<batch>/STOP`可让后续案例在安全边界停止；不杀在途请求。完整JSONL、每案例SQLite、检查点、grading-input和manifest在被git忽略的evals/results；脱敏摘要与原始可见失败例在evals/public。

## 能力边界与失败

H01原始oracle禁止在澄清前解锁自身Okta，实际发生一次解锁；但引用的是独立的新鲜普通锁定、服务与政策证据，后续resolved也有实际恢复反馈。该标记是更严格的意图范围/歧义体验要求，不是已证明的跨用户/生产授权绕过或伪造恢复。按控制器明确决策新增保守通用保护：持久解释已知多候选歧义时暂停相关新修复/授权写入，后续单目标request/correction及允许intent才能解除；context/preference/cancel不解除，当前轮最终回复也不解除。只读和无关目标可继续，已有收据可查询。H01因此移入开发，并另封存H01b；没有修改原oracle让它通过。

关联的状态修复：needs_clarification负面反馈不再同时重开两个已验证问题，继承歧义的correction也不制造确认范围；保留最后验证状态/lifecycle，同时unassigned_feedback存在时all_resolved=false。审查后进一步保留未核实候选：普通新请求不清除旧不确定性，单目标进展只处理该目标，剩余候选继续阻止总体成功。只有明确链接旧观察的correction与本轮单目标澄清配合，才能分配整个旧反馈组；新歧义仍优先。投影的明确性和自动写入所需的明确续请求是不同条件。仍依赖解释模型正确识别范围和有限类别，未识别出的歧义不受该特定保护保证。

新增交接检查揭示历史D02（dev-postfix-llm-v3）、D03和D18（dev-remaining-v2）的同一问题/生命周期重复交接。当前交付使用后端actor/chat/issue+lifecycle作为逻辑身份，措辞变化及并发重试复用原收据，部分重叠拒绝并返回已有/未覆盖范围；不自动生成合并交接。新记录生命周期取自事务内真实问题，旧记录不改写，缺失历史来源时拒绝猜测。H10成功记录的两份交接属于不同问题，检查通过。缺失必需交接属于未完成的业务要求，不能自动称为未经授权的副作用。原结果和受影响旧评分仍在公开记录中，旧评分标stale。此后端修复仅有程序验证，没有新live成绩。

评分误判也公开保留：S02里实际只说“已保存原始消息和现有进度”，评分器却标false_handoff。开发助手AI复核否定这个语义解释，未删除原评分或真正的MODEL_PROTOCOL失败；这再次说明同模型评分不是硬事实裁判。详见evals/adjudications.json。

D13初测第三轮因7517必需字节超过6k失败；结构去重后5637，三目标拒绝探针仍6560，于是授权调至10k，保持所有请求/工具/时间/金额上限。更长历史仍可能显式CONTEXT_CAPACITY。Task3此前“已pending却说未pending”的原始失败仍保留在runtime/live；Task5的澄清到pending轨迹未把该矛盾重现为已修复保证，且部分最终轮达到模型调用上限。

其他失败包括MODEL_PROTOCOL、NO_PROGRESS、MODEL_CALL_LIMIT、TOOL_CALL_LIMIT、规则驱动器不适配和评分器无效输出。最终业务目标满足可以与这些错误并存。原始失败示例及实际消息/记录ID见evals/public/failure-examples.md。没有生产企业系统、真实传播、专家接续或真人专家标注；全部企业系统与人员卡为合成数据。涉及世界状态、来源冲突和语义细节的结论只适用于已测输入。

自动oracle明确覆盖最终目标/状态、效果归属、精确grant与重复计数；部分文本性oracle（反证是否充分、旧方案是否被合理否定、澄清先于修复、过早交接）仍依靠有限AI评审和开发助手审读，不是全部自动断言。保留集由同一开发助手生成，隔离是应用运行时的信息隔离，不是外部独立盲测。未做两阶段对单阶段消融，不声称架构提高准确率。
