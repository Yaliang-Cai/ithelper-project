# 演示指南

当前版本的一条真实模型 UI 路径：[播放 33.32 秒录屏](evidence/demo-v7-recording/index.html) · [原始 WebM](evidence/demo-v7-recording/real-model-v7.webm) · [消息、回执、版本与费用](evidence/demo-v7-recording/demo-v7.json)。无声、原速连续录制，无剪辑或答案回放。画面中的“模拟业务系统”始终可见；真实调用官方 `deepseek-flash`，企业状态与员工恢复反馈均为合成数据，没有连接真实 Okta。

## 讲解顺序

1. **0–5 秒：身份与输入。** Alice 登录、新建聊天，用全新表述描述自己在公司电脑上的 Okta 锁定。新输入直接进入模型，不携带案例编号。
2. **5–17 秒：查证与操作。** Agent 查询账号和政策，执行一次普通本人账号解锁，保存实际模拟收据；页面仍等待员工重试，未凭解锁就结案。
3. **17–29 秒：恢复反馈。** 开发者输入合成反馈“现在能进去了”，Agent 再次查询后更新为已确认解决。两个阶段保留在同一聊天。
4. **29–33 秒：持久状态。** 页面重载，从历史重新打开该聊天，消息和收据仍在。审批、影响确认与交接可结合已有证据讲解；本录屏只展示账号正常路径。

本次有 2 个 completed `live_agent` 运行、7 次模型请求（4+3）、4 次非终结工具（3+1）、1 次解锁收据、0 个事件错误和 0 个浏览器脚本错误。两个运行保存的 `prompt_version` 均为 `helpdesk-2026-11-02-v7-matched-followup`，不只是检查磁盘上的提示词。运行代码与冻结 `baca519` 的哈希一致；录制时 Git HEAD 为 `abcaf05`（后续仅文档/证据差异）。[最终截图](evidence/demo-v7-recording/employee-v7.png)保留原始回复，仍有账号重复与重复结束邀请，不能称为自然表达已完全解决。

## 面试现场输入

按[根 README](../README.md)启动后，新建聊天，请演示者自行输入问题并按当前追问补充事实。观察单独目标、来源、未解决状态和实际收据；自由文本不是效果凭证。不要为配合演示声称真实系统已经恢复。

可选的讲解方向：`bo.remote` 的 VPN 旧会话会先询问环境，再应创建影响确认卡；必须点击卡片才允许重置。生产库只读申请须明确资源，并在独立浏览器由 `pat.approver` 实际批准，待批期间不能声称已授予。安全禁用或超范围请求应保存有上下文和责任人的本地交接记录，它不会联系真人。这些路径可能失败；当前实测范围与原文见[迭代结果](delivery-iteration-result.md)，不要宣称录屏覆盖它们。

当前实模演示后的累计账本为 **USD2.892116173 / 835 请求 / 0 未结预留**，原 USD3 内剩 **USD0.107883827**。这只是录制结束快照，新的现场交互继续消耗余额；每次完整预留可能大于实际结算，因而也可能提前停止。不要重置或换目录恢复额度。费用是未按缓存折扣降低的保守 token 估算，非供应商账单；embedding 另计。

## 复现与离线检查

录屏驱动使用新的隔离业务库，保留默认业务库和全部历史证据；调用仍计入固定 `runtime/usage.sqlite`。它在每次完整请求预留前检查累计已结与未结费用不超过 USD2.95，原账本 USD3 硬上限不变。默认应用没有增加新预算策略。

```powershell
# 可选浏览器工具，独立于最小运行依赖
python -m pip install playwright
python -m playwright install chromium ffmpeg
# 以下命令会付费；输出目录必须尚不存在，不会覆盖旧录制
python -m scripts.record_demo --output runtime/my-new-demo
```

本次隔离世界保存在本机忽略目录 `runtime/recordings/20261101T224412Z`，录制完成后其服务器已停止。默认服务已用新进程重新启动，原聊天未重置；其历史两次运行仍如实保留旧版本，不据此声称重新实测。录屏只含 UI 像素，无浏览器会话文件或网络 trace。新驱动准备阶段曾因账本列名、缺少 ffmpeg 和 CSP 不允许字符串求值而停止，均在提交消息前；修正驱动/安装工具后仅进行上述一次实模路径。

以下检查不调用真实模型：

```powershell
.venv/Scripts/python.exe -m pip install -e '.[dev]'
.venv/Scripts/python.exe -m pip check
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m evals.runner validate
python scripts/verify_browser.py
python scripts/verify_ui_states.py
```

当前完整程序回归 209 passed（24.26 秒），案例结构验证 31 个变体；脚本模型 + 真实 Agent/HTTP/浏览器 14 项、受控时钟真实 UI 3 项通过。后两者是明确的离线测试，不是模型自主决策证据，也没有另存“实模”录屏。GitHub Actions 只执行安装、pytest 和案例结构检查，不配置凭据或付费请求。

WebM 已在 Chromium 实际播放，并用离线解码检查 12/30 秒画面文字；1440×1000、33.32 秒、3,371,345 字节，无音轨。[播放检查](evidence/demo-v7-recording/playback.json)保留媒体哈希。原 [smoke](evidence/delivery-smoke.json)、截图与 [restart](evidence/restart.json) 全部保持原字节；本次费用增量 USD0.021356400 / 7 请求与 Task1 的 69 请求另列，不混入历史评测。
