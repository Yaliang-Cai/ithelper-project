# Employee IT Helpdesk Agent

一个面向员工的 IT 支持原型。员工在聊天中描述问题，Agent 查询资料、补问缺失信息、执行允许的操作，并跟踪每个问题的进展。企业数据和后台系统是模拟的；模型调用、登录、审批、数据库变更和操作记录实际运行。

普通 FAQ 能回答“怎么做”，但无法确认员工当前的账号状态、决定是否需要审批，或在操作后继续核查。因此这里使用单个 Agent 动态选择工具，程序控制权限与执行条件。

[演示视频](docs/evidence/demo-multi-20261102/walkthrough.webm) · [演示说明](docs/demo.md) · [验证结果与限制](docs/implementation-status.md)

## 支持范围

| 问题 | 处理方式 |
| --- | --- |
| 账号登录 | 查询账号与服务状态；允许解锁本人的普通锁定，随后请员工重试 |
| 应用缓慢 | 结合地区事故、当前状态和知识库，提供适用步骤或说明等待原因 |
| VPN 断连 | 询问网络环境；重置旧会话前，必须由员工点击影响确认卡 |
| 软件与数据权限 | 政策允许的目录权限可直接开通；生产库权限需独立审批人批准 |
| 跨系统故障 | 查询变更、依赖和历史；无法处理时保存上下文并交接到相应团队队列 |

一个聊天可以包含多个问题，分别记录状态。相关问题共享证据，不共享“已解决”结论。员工收到可执行的帮助、明确的等待原因或完整交接信息；只有满足相应验收条件才记为解决。交接保存在本地，不会联系真实 IT 人员。

## 运行

Python 3.12+。以下命令在仓库根目录的 PowerShell 中执行：

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[dev]'
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
# 在 .env 中填写 DEEPSEEK_API_KEY 和 SILICONFLOW_API_KEY
powershell -ExecutionPolicy Bypass -File scripts/run_local.ps1
```

打开 http://127.0.0.1:8000/ 。员工账号：`alice.sales`、`bo.remote`、`chen.data`、`dana.platform`；审批账号：`pat.approver`；调试账号：`dev.debug`。演示密码均为 `DemoPass123!`。审批人使用另一个浏览器会话登录。

LLM 固定为官方 `deepseek-flash`，embedding 为 SiliconFlow `BAAI/bge-m3`。DeepSeek 凭据缺失时明确报错；embedding 不可用时降为关键词检索。凭据保存在本地 `.env`，不提交到仓库。

仓库包含合成源数据和评测案例，不包含本机聊天数据库。首次启动会创建本地业务数据，可以直接输入新问题。关闭页面后可以重新打开历史聊天。服务重启后继续使用原数据库；在途事件需等待租约到期。清空业务演示前先停服务，再运行 `.venv/Scripts/python.exe -m scripts.demo_reset`。业务库为 `runtime/business.sqlite`，费用账本为固定的 `runtime/usage.sqlite`；重置业务不会恢复费用额度。

项目累计模型费用上限为 US$4。本机已用额度和现场演示注意事项见[演示说明](docs/demo.md)。

## 架构与取舍

```text
员工聊天 → 持久事件 → Agent：理解当前消息 → 选择工具并继续调查
                         ↑                   ↓
                   上下文与检索 ← 工具结果、业务状态与操作记录
                                             ↓
                                    回复 / 确认 / 审批 / 交接
```

Python + FastAPI 提供 API 和简单网页，SQLite 保存聊天、业务状态及执行记录。模型先提出问题与反馈的结构化解释，再进入工具循环。拆成两阶段便于定位理解错误和行动错误，代价是增加一次调用；目前没有对照实验证明它比单阶段更准确。

模型决定调查什么，后台事务检查当前身份、政策、批准或确认是否有效。操作与收据一起提交，重试复用已有结果。每轮最多 10 次模型请求、12 次非终结工具调用、120 秒。必需上下文装不下时停止并保留进度，长聊天仍有容量限制。

五类来源各有用途：22 篇 KB 提供操作指导，12 条历史记录用于对照，6 个账号的目录确定身份与设备，8 个服务及事故、变更记录说明现状，12 条政策约束权限；另有 5 个精确资源。KB 和历史使用关键词与向量混合检索，状态和权限按结构化字段查询。

单 Agent 和本地模拟环境便于追踪完整过程，也限制了真实性。未接入企业 SSO、真实业务 API、ITSM 或专家回流。详细模块职责与替代方案见[系统设计](docs/01-system-design.md)。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m evals.runner validate
```

离线测试覆盖身份隔离、审批与确认、幂等、错误恢复、多问题状态和评测器。当前 **221 项通过，31 个案例变体结构有效**；提交后的检查见 [GitHub CI](https://github.com/Yaliang-Cai/ithelper-project/actions/workflows/checks.yml)。离线测试采用模型替身，只能证明程序如何处理给定响应。

真实模型测量按版本保留成功与失败。新版录屏增加多轮登录恢复、同一聊天的新问题，以及两个员工和审批人之间的权限流程；等待外部服务恢复的问题保留未解决状态。试录发现多目标收尾失败后，补充了校验错误对应的问题与服务信息，并增加回归测试。早期 66 次测量来自多个版本，不能算作当前版本成功率。新版演示也不是盲测；回复仍有冗长、重复追问和诊断措辞过强的问题。评测使用合成用户和同模型评分，没有真人盲测。[结果与证据索引](docs/implementation-status.md)列出完整范围。

更多时间优先补同一版本的多轮失败复测和重复测量，核对追问与结构化问题是否对应，再由 IT 专家检查诊断和交接质量。真实系统接入需要补 SSO、外部操作对账和人工接续。现阶段的主要不足是自然对话稳定性和验证广度。
