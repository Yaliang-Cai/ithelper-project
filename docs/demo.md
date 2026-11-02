# 演示说明

[播放录屏](evidence/demo-v7-recording/index.html) · [下载 WebM](evidence/demo-v7-recording/real-model-v7.webm)

录屏为 33.32 秒原速连续画面，无声。它调用真实 DeepSeek Flash，展示本人 Okta 账号解锁、重试反馈和历史恢复；企业状态与“现在能进去了”的反馈是合成的。审批、VPN 确认和交接不在这段录屏中。

## 录屏内容

1. 员工描述公司电脑上无法登录 Okta。
2. Agent 查询账号和政策，完成一次允许的解锁，请员工重试。
3. 员工反馈恢复后，Agent 核查并将该问题记为解决。
4. 重载页面，从历史重新打开聊天，消息和操作记录仍在。

两轮共 7 次模型请求、一次解锁，无事件错误。提示词为 v7；录屏对应 v0.1.2 复发验收修复之前的运行代码，后续修复未重新录制。[消息、工具与运行版本](evidence/demo-v7-recording/demo-v7.json)、[截图](evidence/demo-v7-recording/employee-v7.png)和[媒体校验](evidence/demo-v7-recording/playback.json)可供核对。

## 现场演示

按[README](../README.md)启动服务并打开 http://127.0.0.1:8000/ 。所有账号密码为 `DemoPass123!`，可以直接输入新问题，无需案例编号。

| 账号 | 可以观察的流程 |
| --- | --- |
| `alice.sales` | 普通账号锁定；解锁后仍需重试反馈 |
| `bo.remote` | VPN 断连；环境询问、影响确认卡、一次重置与后续观察 |
| `chen.data` | Grafana 与指定生产数据库权限；两个目标分别推进 |
| `dana.platform` | Jenkins、Tableau 等跨系统问题；调查与必要交接 |
| `pat.approver` | 在独立浏览器会话批准或拒绝精确权限申请 |
| `dev.debug` | 查看实际运行、工具结果和费用 |

现场讲解重点是下一步为什么这样做、用了哪些资料、实际状态是否改变，以及哪些问题仍未解决。遇到失败可直接查看调试记录。上述流程是产品支持范围，当前真实模型测量范围见[验证结果](implementation-status.md)。

本机录屏结束时的模型预算剩约 **US$0.108**。每次请求先预留费用，完整对话可能提前触及上限。多场现场演示前需要重新确定预算并调整项目限额；仅给供应商账户充值不会提高代码中的 US$3 上限。不要通过清空或更换费用账本恢复额度。

## 可选复现工具

以下工具只供复现，观看已有录屏不需要安装。

```powershell
python -m pip install playwright
python -m playwright install chromium ffmpeg
# 离线浏览器检查，不调用付费模型
python scripts/verify_browser.py
python scripts/verify_ui_states.py
# 重新录制会付费，输出目录必须尚不存在
python -m scripts.record_demo --output runtime/my-new-demo
```

录制驱动使用隔离的业务库，调用仍计入原项目账本；它为新请求预留后要求累计费用不超过 US$2.95。已有录像文件、历史测量和默认聊天不被覆盖。程序回归命令见 README，开发中的修复与早期运行记录见[集成记录](delivery-evidence.md)。
