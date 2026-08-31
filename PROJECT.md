# SURF：AI-Marking r20 SAF 官方边界研究工作区

## 当前入口

当前研究协议是 `r20-saf-official-split-2026-08-v4-8q-60m-15t`。它以 8 道正式题中立检验：在相同同题历史评分证据、模型、更新次数与 token 预算下，局部条件规则记忆 CRM 与抽象分级量表记忆 ARM 是否产生不同的新答案评分误差。v1–v3 仅作只读历史审计。

- 应用入口：[AI-Marking](AI-Marking/PROJECT.md)
- 主比较：h=60 的 `normalized_error_ARM − normalized_error_CRM`。
- 研究边界：SAF 官方 split、两个固定模型、三条轨迹、两次终点评分和单次正式运行；只主张已见题目的新答案泛化。
- 记忆边界：CRM 保存未来可观察条件及定性支持/削弱作用；ARM 保存稳定题目级维度及充分/部分/缺失证据；两者都不得保存具体或绝对分数映射。
- 平台边界：提示词逐字匹配规范文档，经持久化 48-call suite 验证后冻结；NM、CRM、ARM 分组独立启动。

## 文档索引

- [r20 研究设计](docs/research-design-r20.md)：官方数据边界、比较条件、冻结、调用设计和分析。
- [r20 提示词设计](AI-Marking/docs/experiment/r20-prompt-design.md)：三模板正文、构念边界、输入输出、记忆预算和冻结检查。
- [r20 平台实现与运行](AI-Marking/docs/experiment/r20-implementation.md)：接口、状态机、调用量、审计边界与资源预期。

## 本地验证

```bash
cd /Users/mac/Desktop/SURF/AI-Marking/backend
.venv/bin/pytest -q --cov=app --cov-report=term-missing
.venv/bin/ruff check app tests
.venv/bin/alembic heads
.venv/bin/alembic upgrade head

cd /Users/mac/Desktop/SURF/AI-Marking/frontend
npm run lint
npm run test:run
npm run build
npm audit --omit=dev

cd /Users/mac/Desktop/SURF/AI-Marking
./start.sh
```
