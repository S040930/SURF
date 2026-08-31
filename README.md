# SURF：AI-Marking 实验研究

## 数据库恢复

R23 实验结果已导出至 `outputs/r23_experiment_results.dump`（pg_dump 自定义格式）。

```bash
# 需要 PostgreSQL 17+（Postgres.app）和对应版本的 pg_dump
/Applications/Postgres.app/Contents/Versions/17/bin/pg_restore \
  -U mac -h localhost -p 5511 \
  -d ai_marking_experiment --no-owner --no-acl \
  outputs/r23_experiment_results.dump
```

## 项目入口

参见 [AI-Marking](AI-Marking/PROJECT.md) 和 [PROJECT.md](PROJECT.md)。