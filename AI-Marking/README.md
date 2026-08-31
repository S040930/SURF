# AI-Marking r23

当前默认入口是 DREsS_CASE Rubric Sensitivity r23 平台。每个项目使用一个 Codex 模型配置、同一 Rubric 和固定抽样，检验 Content、Organization、Language 三个维度的单调敏感性与区分度。r22 历史项目仍可从 `/research` 访问。

## 最短工作流

1. 运行 `./start.sh`，打开 `http://127.0.0.1:5173/dress`。
2. 在“数据与审计”确认三份 DREsS_CASE 文件通过哈希与配对门禁。
3. 保存一个或多个 Codex Runner 配置，为每个配置选择 `low`、`medium` 或 `high` 思考程度、`standard` 或 `fast` 运行速度，并设置 30–1800 秒的单篇调用超时（默认 120 秒）；项目一次选择一个配置，不需要人工冻结。
4. 勾选受限数据处理授权并创建技术试点；点击“开始实验”时，平台自动保存运行快照，随后才会产生模型费用。
5. 在 Codex App 中打开并信任 `/Users/mac/Desktop/SURF/AI-Marking`，保持任务连接；MCP worker 自动串行执行。
6. 技术试点完成后，创建完全匹配的正式实验。完成后可点“再次运行”创建使用相同配置与样本的独立重复项目；正式结果在完整统计报告锁定前保持密封。

平台不保存 API Key；实际模型调用由已登录的 Codex CLI 通过全新 `codex exec --ephemeral` 会话完成。超时、断线、CLI 漂移或非法输出不会静默重试，而会进入 `attention_required` 等待人工处理。

研究方案见 [`docs/experiment/r23-dress-case-rubric-sensitivity.md`](docs/experiment/r23-dress-case-rubric-sensitivity.md)，部署、资源、API 与验收见 [`docs/experiment/r23-platform-runbook.md`](docs/experiment/r23-platform-runbook.md)。
