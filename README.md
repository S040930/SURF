# SURF：AI-Marking 实验系统

研究 AI 模型在 DREsS\_CASE 数据集上的评分可靠性（rubric sensitivity）。当前协议为 r23 DREsS\_CASE。

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

### 4. 恢复 R23 实验结果

```bash
/Applications/Postgres.app/Contents/Versions/17/bin/pg_restore \
  -U mac -h localhost -p 5432 \
  -d ai_marking_experiment --no-owner --no-acl \
  outputs/r23_experiment_results.dump
```

### 5. 运行迁移

```bash
cd AI-Marking/backend
source .venv/bin/activate
alembic upgrade head
```

### 6. 启动

```bash
cd AI-Marking
./start.sh          # 网页 (http://127.0.0.1:5173) + 管理 API (http://127.0.0.1:8000)
./start.mcp.sh      # 串行 Worker（需在 Codex App 中信任项目）
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

* [r23 运行手册](AI-Marking/docs/experiment/r23-platform-runbook.md)

