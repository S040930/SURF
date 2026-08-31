# r20：显式评分记忆的题目内标签适应研究方案

> **Protocol ID:** `r20-saf-official-split-2026-08-v4-8q-60m-15t`  
> **Document status:** paper-oriented, confirmatory protocol; implementation-aligned as of 2026-08-15  
> **Target evidence:** short paper / empirical NLP or educational-AI venue  
> **Implementation companion:** [`../AI-Marking/docs/experiment/r20-implementation.md`](../AI-Marking/docs/experiment/r20-implementation.md)

## Material Passport

| Item | Frozen value |
|---|---|
| Dataset | SAF 2.0 English short-answer feedback |
| Data source commit | `09949e912d266777a752ac7d51cfcddb4b3f6978` |
| Archive SHA-256 | `c0841b36acdbe4adff9d96ede366fbe52de5671d859642783266204b4c9f5ec0` |
| Official split-map SHA-256 | `16dd229ec06d43436a00e2de27d373d3b0780045ab36c39885c19d45df91c68f` |
| Formal / development / pilot / unscheduled questions | 8 / 2 / 2 / 14 (from 26 seen questions) |
| Conditions | NM (no memory), CRM (compact rule memory), ARM (abstract rubric memory) |
| Memory source per question | 60 same-question clean train+validation answers for formal; 20 for pilot |
| Main endpoint | 15 fixed clean same-question unseen answers after history `h=60` |
| Primary metric | normalized absolute error (NAE) against the historical teacher score |
| Primary contrast | ARM minus CRM NAE; lower than zero favors ARM |
| Uncertainty | 10,000 fixed-seed crossed question/group bootstrap replicates |
| Model policy | two distinct, operator-chosen frozen OpenAI-compatible model configurations; no fixed vendor or model ID |

## 1. Research objective and scope

Large language models can score a short answer from a question and reference answer, but they do not automatically recover a particular assessor's local scoring policy. This study neutrally compares two forms of **explicit, bounded external memory**, updated from the same previously scored answers of the *same question*: local conditional-rule memory (CRM) and abstract rubric memory (ARM).

The study is a within-question, paired benchmark experiment. It does **not** train model parameters. Here, “training” means revealing historical score and feedback for a sequence of answers to an external-memory updater. The resulting memory is used only for later, unseen answers to that same question.

The target estimand is agreement with SAF's historical tutor-provided score, not objective correctness, pedagogical quality, a teacher's universal grading ability, or a causal effect on learners. The result may support a claim about *same-question label adaptation on this frozen benchmark only*. It must not be described as teacher replacement, improved learning, cross-course generalization, or cross-question transfer.

## 2. Research questions, hypotheses, and claims

### RQ1 — memory representation

After 60 labeled same-question answers, does ARM produce lower error than CRM on new answers to the same question?

- **H1 (directional):** the question-macro mean of `NAE_ARM − NAE_CRM` is below zero.
- **Primary estimand:**
  `Δ_ARM−CRM = mean_q(mean_{m,g,a,t}[NAE_ARM − NAE_CRM | q, h=60])`,
  where `q` is question, `m` model, `g` dataset group identifier, `a` answer, and `t` trajectory.
- **Decision rule:** report the estimate and its two-sided 95% percentile bootstrap interval. The paper may call ARM “more accurate on the frozen benchmark” only if the estimate is below zero *and* the full interval excludes zero. Otherwise, report the estimate as inconclusive; do not convert a non-significant interval into equivalence.

### RQ2 — whether either learned memory helps

Do the two memory conditions, averaged as a prespecified family, reduce error compared with an otherwise identical no-memory scorer?

- **H2 (directional, secondary):** the question-macro mean of `((NAE_ARM + NAE_CRM) / 2) − NAE_NM` is below zero.
- **Decision rule:** same interval rule as RQ1, labeled secondary. This does not test ARM and CRM independently against NM; those separate comparisons are descriptive unless added as explicitly multiplicity-controlled hypotheses before formal freeze.

### RQ3 — robustness and mechanism (exploratory)

How do effects differ by model, question, trajectory, and memory history (`h=20`, `h=40`, `h=60`), and do memory outputs remain within their structural constraints? These analyses generate explanations and follow-up hypotheses; they are not confirmatory tests.

### Interpretation boundaries

An observed ARM−CRM difference is a comparison of two **complete intervention packages**: their representation, update prompt, serialization, and bounded rendering. It does not isolate any individual field, phrase, or reasoning behavior as the causal mechanism. The historical labels may themselves contain assessor inconsistency; they are a comparison target, not an established ground truth.

## 3. Experimental design

### 3.1 Units and nesting

The primary inferential unit is the **question**, not an individual answer or API call. There are 8 formal questions. Within each question, the same test answer is scored under all three conditions, all trajectories, and both frozen models, yielding paired comparisons. Dataset group identifiers are additionally resampled in the uncertainty procedure because multiple responses can share a group identifier.

```text
Question q (equal weight in primary estimate)
├── three independently ordered 60-answer memory trajectories
│   ├── CRM updater → CRM snapshot at h = 20, 40, 60
│   └── ARM updater → ARM snapshot at h = 20, 40, 60
└── same unseen test answers
    ├── NM: no learned memory
    ├── CRM: same-question CRM snapshot
    └── ARM: same-question ARM snapshot
```

No memory, snapshot, answer, teacher score, feedback, model output, or conversation state may move between questions, conditions, trajectories, projects, or models except where this protocol expressly supplies the same current answer as a paired test case.

### 3.2 Conditions

| Condition | What the scorer receives beyond the fixed scoring prompt | What updates after a labeled training answer |
|---|---|---|
| **NM** | No learned memory | Nothing |
| **CRM** | Up to six local future-observable rules (`condition`, `effect=supports|weakens`, `importance`, `guidance`) | A replacement CRM snapshot built from the current answer, its historical score/feedback, and the prior CRM snapshot |
| **ARM** | Up to six stable question-level dimensions (`criterion`, `importance`, structured `sufficient`, `partial`, and `missing` anchors) | A replacement ARM snapshot built from the current answer, its historical score/feedback, and the prior ARM snapshot |

All three conditions use the same question, reference answer, current student answer, maximum score, frozen scoring template, decoding temperature `0`, output schema, model configuration, test order, and endpoint. NM differs only in receiving the explicit no-memory marker rather than learned content.

### 3.3 Context isolation and label blinding

Every scoring and update request is a new API request. The scoring request is an explicit allowlist containing only the question, reference answer, current answer, maximum score, and condition-specific rendered memory. It never receives teacher score, teacher feedback, learner/group ID, hidden `support` text, prior raw answers, or prior model scores.

Only an update request receives the current training answer's historical teacher score and feedback. Test-answer labels are retained for offline error calculation and never enter scoring or memory updates. An update's `support` field is stored for audit but is not rendered into a later scoring prompt. This separation prevents the endpoint's label from becoming an in-context demonstration.

### 3.4 Memory intervention constraints

CRM and ARM snapshots are replacement snapshots rather than unbounded transcripts. Each has at most six items, at most 441 scoring-visible tokens and 618 stored tokens. The fixed `o200k_base` encoding from `tiktoken==0.13.0` measures field values only, not JSON structure; the frozen calibration artifact records its SHA-256, corpus hash, and derivation. CRM conditions and ARM criteria/anchors must contain valid question or reference content. The system rejects generic anchors, cross-construct structures, duplicate cores, concrete or absolute scoring language, copies of 18-token-or-longer answer text, invalid importance labels, and overlong fields. Invalid model output is retried as a new request; it is never silently truncated or repaired.

Both memories learn only teacher-supported qualitative assessment policy. CRM is one local, future-observable answer condition plus its qualitative effect. ARM is one stable question-level assessment dimension plus sufficient, partial, and missing observable evidence. Neither may retain scores, maximum scores, points, deductions, percentages, bands, or fixed grade mappings.

These constraints make the intervention inspectable and limit direct storage of individual answers. They do not establish that every surviving item is semantically supported by the historical feedback; a paper using this protocol should report the automated checks and clearly label any additional human faithfulness audit as a separate, preregistered analysis.

## 4. Dataset, split contract, and leakage control

### 4.1 Why this split answers the research question

SAF 2.0 supplies question text, one reference answer, student answers, historical tutor scores, and textual feedback. For each of the 26 **seen questions**, the dataset contains original-training answers and a separate `unseen_answers` file for the *same question*. The central evaluation is therefore not “learn on question A and test on question B.” It is:

```text
same question q
├── training answers: score + feedback available only to the updater
└── unseen answers: labels withheld from the model; used only for evaluation
```

This design tests whether a local scoring memory learned from prior assessed answers can improve scoring of different future answers to the same prompt. The archive also has five `unseen_questions`; they are sealed and excluded because question-specific memories are not intended to transfer to a new question. Any claim about cross-question generalization requires a distinct, future protocol with a shared cross-question memory and a held-out-question endpoint.

### 4.2 Source reconstruction and exclusions

The pinned archive contains 2,127 original-training, 375 unseen-answer, and 479 unseen-question records. The published Hugging Face split map defines 1,700 train and 427 validation rows; after reconstructing stable XML identities, 44 ambiguous duplicate tuples are excluded instead of guessed. The implementation validates these expected totals before freezing.

Before roles are assigned, answers are normalized with NFKC, case-folding, punctuation-to-space, and whitespace collapse. Exact duplicates, shared learner/group components, and near duplicates among answers of at least 20 tokens (sequence similarity ≥ .95 and token 5-gram Jaccard ≥ .90) form joint components. Any component crossing train/validation and unseen-answer boundaries is excluded on both sides. Conflicting duplicate components and incompatible same-group answers are also excluded; same-label within-split duplicates retain the earliest source row. Thus, apparent gains cannot be attributed to an identical or near-identical answer appearing on both sides of the endpoint.

### 4.3 Question roles and sample size

Roles are frozen by question ID before execution. Formal questions are `1.6`, `2.4`, `5.11`, `6.3`, `4.3`, `4.1_LM_v1.0`, `6.3_IPP`, and `8.1_MM`; pilot questions are `5.7` and `4.13`; development questions remain `5.12` and `4.3_LM`. Formal questions require at least 60 clean train+validation answers and 15 clean unseen-answer endpoints; pilot questions require at least 20 and 5.

| Role | Questions | Use | Access to labels during protocol development |
|---|---:|---|---|
| Development | 2 | fixed 48-call construct and boundary validation only | permitted inside the server-side suite |
| Pilot | 2 | blinded technical end-to-end run | no directional effect display |
| Formal | 8 | confirmatory analysis | no protocol tuning |
| Unscheduled | 14 | not run in this cost-constrained protocol | not exposed or used as replacements |
| Sealed unseen question | 5 | excluded from r20 | no project access |

After cleaning, the current frozen data has 1,619 clean train answers, 406 clean validation answers, and 349 clean unseen-answer endpoints. For each selected formal or pilot question, one deterministic hash order selects a single memory pool from the union of clean train and validation answers; the three trajectories permute that same pool. Formal pools contain exactly 60 answers and tests contain exactly 15 endpoints; pilot pools contain 20 and tests contain 5. No unselected answer is used to form a memory snapshot or scoring request.

Validation labels are not formal endpoints and are not used for prompt selection or tuning. For the eight formal and two pilot questions, clean validation answers are merged into the predeclared memory pool and then treated exactly like selected training answers; development-question validation remains reserved for the 48-call structural prompt suite.

## 5. Procedure

### 5.1 Development and protocol freeze

The operator first saves and freezes two distinct model configurations, then creates a candidate from the three exact normative blocks in `r20-prompt-design.md`. The database hash must match that document byte for byte. A prompt can freeze only after exactly one persistent suite has completed and passed with the same two models that will be bound to the project. The suite has 48 logical calls: two models × two development questions; CRM and ARM each update three examples continuously in high→low→middle order, followed by NM, CRM, and ARM scoring on two held-out answers. “Passed” means schema, token, construct, continuous-update, and information-boundary checks succeeded. The suite has no accuracy threshold and does not calculate or display ARM−CRM.

Development may repair prompt wording, schemas, parsers, and memory constraints only by creating a new prompt hash and rerunning the complete suite. It may not use formal questions, formal endpoint labels, or formal condition effects. After pilot acceptance, the data manifest, selected questions, trajectories, probes, model snapshots, prompt version, analysis-code hash, and stream order are frozen. Formal runs cannot be used to revise any of these items. Reducing and resampling the formal design created this separate v4 protocol; v1, v2, and v3 prompts, projects, and results remain read-only historical records and cannot satisfy a v4 gate or be pooled with v4. The earlier pilot included `5.11`; its results are not used in v4. The v4 pilot uses `5.7` and `4.13`.

### 5.2 Blinded technical pilot and readiness gate

The pilot executes the complete pipeline on two isolated questions but exposes only technical telemetry: request/parse success, automatic memory-constraint checks, latency, token use, cost estimates, and failure metadata. It must not expose MAE, condition ranking, directional effects, or learning curves.

The pilot is a **workflow verification**, not a throughput guarantee. The v4 formal run contains 11,520 logical calls and the v4 pilot contains 1,020 calls, for 12,540 calls before the unchanged 48-call development validation suite. Pilot success alone cannot establish API capacity at formal scale. Before a formal project is queued, the paper should report a predeclared operational readiness record for the selected providers: observed pilot error types, mean and p95 latency, configured worker concurrency, expected formal duration/cost, and the provider's rate-limit evidence. A recommended gate is: no unresolved configuration/identity failure, no unhandled parser or persistence failure, and a documented throughput estimate that fits the available execution window. This is a reproducibility/feasibility gate, not an outcome-based gate.

The code persists every logical call and every attempt, leases streams, and recovers expired leases conservatively. Each request has at most two automatic attempts: a 60-second scheduled retry for transport failures, plus a single inner retry for invalid-output errors. Terminal failures require an explicit, auditable manual per-call retry; the final report has no confirmatory core while any terminal failure remains. Consequently, API instability cannot be hidden by deletion, silent skipping, imputation, or a partial effect estimate.

### 5.3 Formal memory formation and tests

For each formal question, one deterministic 60-answer pool from clean train+validation data is permuted three ways. At each position, CRM and ARM independently update from their own preceding snapshot and the current answer's historical score and feedback. Snapshots at `h=20`, `h=40`, and `h=60` are then read-only. The pilot uses a 20-answer pool, probes at `h=10`, and final history `h=20`.

At `h=20` and `h=40`, five deterministic probes from the fixed 15 formal endpoints are scored once for exploratory learning curves. At `h=60`, all 15 fixed endpoints are scored twice per condition and trajectory. The pilot scores its five fixed endpoints once at `h=10` and twice at `h=20`. Repeats are averaged before calculating error, mitigating provider nondeterminism while retaining each attempt for audit. The no-memory condition is re-scored in each trajectory/time block so temporal API drift is balanced rather than confounded with memory condition.

For a formal question the protocol schedules `12×60 + 18×5×2 + 36×15 = 1,440` logical calls; for a pilot question it schedules `12×20 + 18×5×1 + 36×5 = 510`. The frozen totals are 11,520 formal calls and 1,020 pilot calls. Two models are part of the current robustness design and are equally weighted in the aggregate; results are also reported per model. They are not a random sample of all models, so model-level results do not license a claim about the whole model population.

### 5.4 Order, failures, and stopping

For each question, all model × trajectory × condition streams are placed in one deterministic base order. That order is cyclically rotated by question index. Because the base order has 18 combinations, the 8-question formal run samples the first 8 rotation positions. This balances the order deterministically but does not fully counterbalance every position; it also does not eliminate vendor-level temporal drift.

A failed update blocks its downstream calls in that stream; a successful audited retry reopens them. A completed formal project with any terminal failures receives `completed_with_failures` and a failure/resource report only—no primary estimate or confidence interval. The protocol has no early stopping for favorable or unfavorable effects. No question substitutions are permitted: a technically failed pilot or formal project is an operational failure record rather than a reason to select an unscheduled question.

## 6. Outcomes and statistical analysis

### 6.1 Endpoint construction

For each complete `model × question × group × answer × condition × trajectory` cell at `h=60`, the two model scores are averaged. Let `y` be the historical teacher score, `ŷ_c` the averaged score under condition `c`, and `M_q` the maximum observed score in the selected memory pool for question `q`.

```text
NAE_c = |ŷ_c − y| / M_q
```

The maximum score is derived from training data only; a test label is never consulted when constructing the scoring request. Paired differences are computed within the same model, question, group, answer, and trajectory before any aggregation. Values are averaged first within question and then equally across questions, so questions with more unseen answers cannot dominate the primary result.

### 6.2 Confirmatory analyses

| Analysis | Contrast | Population statement allowed |
|---|---|---|
| Primary | `NAE_ARM − NAE_CRM` | 8 frozen formal questions, conditional on frozen models and protocol |
| Secondary | `(NAE_ARM + NAE_CRM)/2 − NAE_NM` | same frozen benchmark |
| Supporting scale check | raw absolute error, ARM − CRM | same frozen benchmark; not a replacement primary outcome |

For the primary and secondary contrasts, uncertainty is a two-sided 95% percentile interval from 10,000 deterministic crossed bootstrap draws. Each draw resamples questions and dataset group IDs with replacement, preserves paired rows, computes a per-question mean, then takes the equal-weight mean across resampled questions. The random seed is `r20-question-group-bootstrap-v1`. No p-value, equivalence conclusion, or superiority margin is implied unless such a threshold is explicitly added before formal freeze.

### 6.3 Mandatory reporting and robustness checks

The locked report will contain the primary and secondary estimates, bootstrap interval, per-model estimates, per-question ARM−CRM values, leave-one-question-out estimates, trajectory estimates, raw-error contrast, exploratory `h=10/20` results, latency summaries, token/cost estimates, failures, and memory-constraint counts. A paper should also provide the number of complete endpoint cells, the count and reason for all retries/failures, all frozen hashes, selected question IDs, and exact model/provider identities.

The following are exploratory or diagnostic, not grounds to revise the primary claim after seeing results: individual model contrasts; history curves; per-question win/loss summaries; variation across trajectories; removal of one question; and human review of stored memory support. Any subgroup or post-hoc hypothesis must be labeled as such.

## 7. Validity threats and mitigations

| Threat | Why it matters | Protocol response | Residual limitation |
|---|---|---|---|
| Train/test answer leakage | Could inflate memory benefit | joint duplicate/group exclusion, sealed endpoint labels, same-question holdout | semantic paraphrases below thresholds may remain |
| Label exposure | Could turn test scoring into label lookup | strict scoring allowlist; teacher fields only in training updates | API/provider behavior outside request content cannot be independently audited |
| Unequal information or prompt treatment | Could make representation comparison unfair | paired inputs, shared scoring prompt, equal item/token limits, same evidence order | CRM and ARM remain full intervention packages |
| API drift / stochastic outputs | Could masquerade as memory effect | temperature 0, two endpoint repeats, balanced stream order, three trajectories | provider nondeterminism and outages remain possible |
| Pseudoreplication | Hundreds of calls can exaggerate certainty | question-macro estimate and crossed bootstrap | only 8 formal questions limit precision; intervals will be wider than v3 |
| Dataset and label validity | Historical scores are not universal truth | narrow claim language; preserve feedback provenance | no independent regrading or inter-rater reliability estimate |
| Model selection | Two models may be unrepresentative | model snapshots and separate results | no inference to all LLMs or vendors |
| Operational failure | Long runs may fail after a small pilot succeeds | persistent call/attempt state, manual retries, no partial core report | operator must establish real provider capacity before formal run |

## 8. Reproducibility, governance, and paper artifacts

Before formal execution, archive or disclose (subject to dataset and provider terms): this protocol version; archive and split-map hashes; cleaning/exclusion log; question roles; trajectory and probe IDs; prompt templates and hashes; model configuration snapshots including returned-model expectation; analysis-code hash; worker concurrency; timestamps; and package/runtime versions. The active implementation targets Python 3.12 and Node.js 22, uses pinned dependency files, and requires no GPU; dataset preparation is streaming/bounded by question, expected to use under 1 GB RAM and under 2 GB disk.

The paper must state the SAF license/terms, whether free-text answers and feedback may be transmitted to each selected API provider, the provider's retention/training policy, data-processing region, key-management practice, and any institutional ethics determination. If those facts are unavailable, the work should not claim that external API processing has been approved.

Recommended paper supplements are: (1) protocol and deviation log; (2) frozen manifest and exclusion log; (3) prompts and schemas; (4) aggregate result tables and bootstrap code; (5) machine-readable resource/failure report; and (6) a disclosure of any researcher decisions made after pilot and before formal freeze. Raw student answers and feedback should only be released if the source license and privacy review permit it.

## 9. Non-claims and next study

This protocol does not answer whether memory transfers across questions, courses, languages, teachers, or student cohorts. It does not compare parameter fine-tuning with memory, test long-term memory accumulation across a curriculum, measure fairness, or establish educational benefit. The five sealed unseen-question files cannot answer these questions because the intervention itself is deliberately question-specific.

A subsequent cross-question study must change the intervention and endpoint together: define a shared subject-level memory, hold entire questions out before any prompt/model decisions, use the held-out question labels only for evaluation, and treat question-level generalization as the primary unit. It should be presented as a new protocol rather than as an extension of the r20 result.

## 10. Deviations policy

Any departure after formal freeze—including model/provider change, prompt change, dataset reconstruction change, altered concurrency that changes provider behavior, changed retry policy, reserve substitution, or altered analysis code—creates a new protocol version or is reported as a deviation. It may be useful engineering work, but it cannot be pooled silently with the confirmatory r20 estimate. A formal run interrupted by terminal calls remains an operational result, not a partial confirmatory experiment.
