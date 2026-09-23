# v2 legacy cleanup manifest

This is the exact cleanup scope for the v2 migration. It removes retired r20–r23
runtime/API/template/test/documentation files after the reference scan, while
leaving database rows, Alembic migration history, `backups/r20-r23-*`,
`outputs/saf_memory_study`, `data/SAF2_0`, and ambiguous `exp_*`/`saf2_*`
artifacts untouched.

The active runtime is `backend/app/experiment/memory_study`, exposed only at
`/api/memory-study`.

Removed path groups:

- `backend/app/api/{experiments,r20_archive,r20_platform,r21_platform,r22_platform,r23_platform}.py`
- `backend/app/experiment/r20/`, `backend/app/experiment/r21/`, `backend/app/experiment/r22/`, `backend/app/experiment/r23/`, and `backend/app/experiment/templates/dress_case.py`
- `backend/app/models/{experiments,r20,r21,r22,r23}.py`, `backend/app/schemas/r20.py`, and `backend/app/mcp_server.py`
- retired worker/import/calibration/report scripts under `backend/scripts/` whose names explicitly contain `r20` or `r23`, plus `run_worker.py`, `import_legacy_r23.py`, and `seed_dress_new_rubric.py`
- retired r20/r21/r23 experiment design/runbook documents and r23 QA screenshots under `docs/experiment/`
- retired frontend APIs/components under `frontend/src/api/{experiments,r20,r21,r23}.ts` and `frontend/src/components/r23/`
- retired r20/r23 contract and template fixtures under `backend/tests/`

Alembic files under `backend/alembic/versions/`, database tables/rows, active
SAF outputs, backups, `data/SAF2_0`, and ambiguous `exp_*`/`saf2_*` outputs are
explicitly outside this manifest.
