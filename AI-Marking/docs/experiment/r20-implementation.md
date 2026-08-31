# r20 implementation

The active research API is `/api/r20`. r20 has independent model, prompt,
prompt-validation-suite, validation-call, project, record, stream, snapshot, call, attempt, exposure, and
locked-report tables. Only r20 data and endpoints are active.

Before freezing, place the pinned official archive at `data/SAF2_0.zip` or set
`R20_SAF_ARCHIVE_PATH`. The builder rejects any other archive hash, unexpected
raw split count, ineligible seen question, or seen/unseen boundary change.

Formal creation requires two distinct frozen model configurations, a frozen
prompt version, and a completed pilot with identical model/prompt hashes. A
unique formal signature prevents a second formal project for the same protocol
configuration. Freeze binds the data manifest, two model snapshots,
prompt version, analysis source hash, question roles, trajectories, probes, and
balanced stream order. Queue rechecks the data and analysis hashes.

Before project creation, both model rows must be frozen. Prompt candidates have
clipboard CRLF/CR line endings and outer blank lines normalized, then must match
the three normative document blocks byte for byte. A mismatch response identifies
the affected prompt section. A persistent suite runs
exactly 48 logical calls over the two frozen models and two development questions:
CRM and ARM each update high→low→middle continuously, then NM/CRM/ARM score two
held-out answers. It checks only schema, token, construct, update continuity and
information boundaries; it never computes an effect contrast or applies an
accuracy threshold. Exactly one completed, passed suite is required to freeze,
and its two model IDs must exactly match the later project's model IDs.
The suite persists after every logical call and performs one provider request at a
time. Its wall time is approximately 48 times the configured provider latency plus
retries; CPU usage is negligible, resident memory is expected below 256 MB, and
database growth is bounded to 48 compact trial rows plus attempts per candidate.
The normative template text and semantic review criteria are specified in
[`r20-prompt-design.md`](r20-prompt-design.md). The editor starts empty; the
operator saves, validates, and freezes an explicit version before project binding.
The prompt editor starts from empty textareas and all three templates must be
filled in before a version can be saved; only an explicitly saved, validated, and
frozen prompt version can be bound to a project. There is no backend-owned
default prompt.

The deterministic semantic gate uses high-precision lexical rules rather than a
second model or fuzzy classifier. It rejects explicit fixed-grade mappings,
broad-dimension CRM conditions, and conditional-rule ARM criteria while preserving
scientific numbers that are not tied to score, point, mark, credit, or percentage
language. ARM anchor triplets are checked per dimension against the frozen 37-token
combined limit; failures report the item number and observed count so a retry can
shorten the longest anchor. The normative prompt asks for roughly 14 tokens per
anchor to leave boundary safety margin. Invalid memory is rejected and retried
without truncation or repair.

Existing rows are migrated to protocol v1 and remain readable for historical
audit, but all lifecycle mutations and worker leases reject them. New v2 projects
run one independently started `question × condition` group at a time, so NM, CRM,
and ARM are never implicitly launched as one combined run.

### Token calibration

r20 validates memory and feedback lengths with the fixed `o200k_base` encoding in
`tiktoken==0.13.0`, independent of either configured model's provider tokenizer.
The frozen development-corpus derivation and limits live in
[`r20-token-calibration.json`](r20-token-calibration.json). Recompute it with:

```bash
cd backend
TIKTOKEN_CACHE_DIR=/path/to/tokenizer-cache \
PYTHONPATH=. .venv/bin/python scripts/calibrate_r20_token_limits.py \
  ../../data/SAF2_0.zip ../docs/experiment/r20-prompt-design.md
```

The task streams the archive through the existing bounded dataset builder and keeps
only 309 unique text strings in memory; it normally completes in under one second
on a laptop, uses under 100 MB RAM after tokenizer initialization, and writes no
output unless redirected. Any changed result requires updating the artifact, limits,
normative templates, and development trials before freezing.

Until successful completion, the API suppresses the manifest and rejects call
reads. The worker persists a distinct SHA-256 for every actual retry payload.
When the last stream completes, it immediately writes an immutable report with
input, report, and analysis-code hashes. The explicit stream order is a fixed
base permutation cyclically rotated across questions, so every model/trajectory/
condition combination occupies every order position once per 18 questions. A
failure report has no confirmatory core and retains terminal failures/resources.
Pilot reports expose technical resources, parser stability and automatic memory
constraint checks only; pilot calls and directional effects remain blinded. A
later GET only reads the saved report. A technically failed pilot can create one auditable replacement pilot
using an unused frozen reserve question; the failed run is never overwritten.

### Auto retry

API failures are retried automatically at most twice in total: the initial
attempt plus one scheduled retry every
`R20_AUTO_RETRY_SECONDS` (default 60) seconds. The worker also paces provider
request starts across its loops (`R20_MIN_REQUEST_INTERVAL_SECONDS`, default 1)
so bursts do not trip provider account rate limits. On a retriable failure the
call moves to `retry_pending` with a `next_retry_at` deadline instead of failing
terminally; `next_retry` only returns a call once its deadline has passed, so
`has_retry_pending` keeps the stream from completing early. Configuration
errors (`ConfigurationGatewayError`) never auto-retry — they fail terminally
immediately, since retrying cannot help. Ambiguous failures
(`AmbiguousGatewayError`, e.g. provider timeouts) do auto-retry: the request
may never have been processed, and re-executing the logical call is idempotent
per call row, so a later retry can recover it. After the second failed attempt
the call becomes `failed_terminal` and waits for the operator. Manual retries
reset the counter to zero, so an operator intervention restores a fresh
automatic retry. The gateway also performs at most two inner attempts for
output-validation failures (a short delay before the second), so an invalid
JSON/schema response is corrected once without repeatedly billing long
re-inferences. The per-call timeout can be raised at runtime without touching
frozen model configs via `R20_TIMEOUT_SECONDS` (seconds); it overrides
`timeout_seconds` for the frozen project snapshots.

### Manual per-call retry

A `failed_terminal` call can be manually retried once per request via
`POST /api/r20/projects/{project_id}/calls/{call_id}/retry`. The call moves to
`retry_pending` with `next_retry_at` cleared (immediate execution) and its
auto-retry counter reset, its stream returns to `pending` with a cleared
lease, and the worker re-executes it in the original deterministic order
(`next_retry` walks `call_sequence`). Retried attempts continue numbering past
the existing attempt records so the original audit rows are never overwritten.
If the retried call is a memory update and succeeds, its downstream
`blocked_dependency` calls in the same stream are unblocked to `retry_pending`;
if it fails again it stays `failed_terminal` and the downstream rows remain
blocked. `test_score` retries only run once their required snapshot exists.

Retry is allowed only while the project is `running`, `paused`, or
`completed_with_failures`. In the latter case the locked report is deleted and
the project returns to `running` so a fresh report is locked after the retried
streams complete. A retry queued while `paused` stays `retry_pending` until the
project resumes; the worker then executes it before any new work, because the
retry gate pauses the whole experiment while any retry is pending. `blocked_dependency` rows are not directly retryable: the
operator must retry the upstream failed call that caused the block. Every
retry is recorded as an `R20Exposure` row with action `manual_retry`. The
blinded failures list `GET /api/r20/projects/{project_id}/failures` exposes
call metadata and failure reasons only, never inputs, outputs, or teacher data.
`terminated` projects cannot retry.

### Pause, resume, delete

`POST /api/r20/projects/{project_id}/pause` pauses a `queued` or `running`
project into `paused`; the worker stops leasing its streams and releases any
in-flight lease without executing further calls. `POST
/api/r20/projects/{project_id}/resume` returns a `paused` project to `queued`
for the worker to pick up again. Both actions are recorded as `R20Exposure`
rows. `DELETE /api/r20/projects/{project_id}` permanently removes a project
and all of its records, streams, snapshots, calls, attempts, report and
exposure rows. A running or queued project is terminated first; a project
still bound as the pilot of a formal project is rejected with 409. Neither
action is reversible.

The v4 frozen clean grid contains 11,520 formal calls and 1,020 pilot calls
(12,540 total). Formal questions use 60 shared train+validation memory answers,
15 test endpoints, probes at histories 20 and 40, and final history 60. Pilot
questions use 20 memory answers, 5 endpoints, one probe at history 10, and final
history 20. The per-question formulas are `12×60 + 18×5×2 + 36×15 = 1,440`
for formal and `12×20 + 18×5×1 + 36×5 = 510` for pilot.

Validation commands:

```bash
cd backend
.venv/bin/ruff check app tests
.venv/bin/pytest -q
.venv/bin/alembic upgrade head

cd ../frontend
npm run lint
npm run test:run
npm run build
```
