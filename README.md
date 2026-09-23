# SURF：AI-Marking 实验系统

研究 AI 短答题评分在记忆框架干预下的可靠性。当前支持 `saf-memory-framework-v3`（历史基线）和
`saf-memory-framework-v3-r2`（当前默认协议），执行链唯一入口为
网页 `/memory-study`（`backend/app/experiment/memory_study/`）。

r23 DREsS\_CASE、r20 SAF、r16/r19 等旧协议已退役，其运行 API、执行器、模板和脚本已从代码中移除，数据只作只读历史。
当前协议说明见 [研究概览](PROJECT.md) 与 [V3-r2 研究设计](AI-Marking/docs/experiment/saf-memory-framework-design.md)。

`/memory-study` 使用 Luna 的四个条件流（最多 4 个固定槽）：每个槽内按调用 ID 严格串行，槽间并行；冻结后必须先完成
Luna 真实结构化预检，预检通过后才允许启动。运行中的站点配置不会覆盖已冻结快照，瞬态错误最多自动重试两次，失败调用和快照可按手册恢复。

## 新电脑环境搭建

### 1. 前置依赖

* **Python 3.12**（推荐 pyenv 或 uv）

* **Node.js 22+**

* **PostgreSQL 17+**（推荐 [Postgres.app](https://postgresapp.com/)）

* **Codex CLI**（已登录状态）

### 2. 克隆并安装

```bash
git clone https://github.com/S040930/SURF.git
cd SURF
```

#### 后端

```bash
cd AI-Marking/backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements-prod.txt
pip install --require-hashes -r requirements-dev.txt   # 可选，运行测试用
```

#### 前端

```bash
cd AI-Marking/frontend
npm ci
```

### 3. 配置数据库

```bash
# 创建数据库
createdb -U mac -h localhost -p 5432 ai_marking_experiment

# 复制环境配置
cp AI-Marking/backend/.env.example AI-Marking/backend/.env
```

编辑 `AI-Marking/backend/.env`，根据实际 PostgreSQL 连接信息修改 `DATABASE_URL`。示例：

```
DATABASE_URL=postgresql+psycopg2://mac@localhost:5432/ai_marking_experiment
```

> 端口以本机实际实例为准，不要照抄：仓库里的 `.env` 当前指向 `localhost:5511`。
> `alembic upgrade head` 与 `./start.sh` 都会用这个值，端口写错会连不上库。

### 4. 旧协议数据（可选，只读历史）

当前协议 `saf-memory-framework-v3-r2` 不需要恢复任何旧数据即可运行。旧 V3 项目及共享数据库迁移仍留在
本地库中，只作只读历史；正常情况下无需做任何事。

仓库里**没有**旧协议的离线备份（例如 r20–r23 的 `pg_dump`）：`backups/` 已被 `.gitignore` 排除，克隆不会携带，
本机当前也不存在该文件。若你手上有历史备份，可自行灌入：

```bash
/Applications/Postgres.app/Contents/Versions/17/bin/pg_restore \
  -U mac -h localhost -p 5511 \
  -d ai_marking_experiment --no-owner --no-acl \
  /path/to/your/r20-r23-exp.dump
```

端口与第 3 步 `.env` 的 `DATABASE_URL` 保持一致（此处 5511 是本机实例，不是通用值）。

### 5. 运行迁移

```bash
cd AI-Marking/backend
source .venv/bin/activate
alembic upgrade head
```

### 6. 启动

```bash
cd AI-Marking
./start.sh          # 网页 (http://127.0.0.1:5173) + 管理 API (http://127.0.0.1:8000)；worker 随 API 进程按 Luna 4 槽运行
```

## 验证

```bash
cd AI-Marking/backend
source .venv/bin/activate
python -m pytest -q
ruff check app tests

cd ../frontend
npm run lint
npm test -- --run
npm run build
```

## 项目入口

* [研究概览](PROJECT.md)

* [实验平台详情](AI-Marking/PROJECT.md)

* [v2 清理清单](AI-Marking/docs/experiment/memory-study-v2-cleanup-manifest.md)
