# r21 Codex memory limits

r21 uses its own fixed, local `o200k_base` validation limits. They are intentionally
more permissive than the archived r20 calibration because Codex structured responses
frequently needed more room for complete, valid memory items.

| Value | r21 maximum |
| --- | ---: |
| Scoring feedback | 180 tokens |
| CRM `condition` / ARM `criterion` | 48 tokens |
| `support` | 48 tokens |
| CRM `effect` + `guidance` | 72 tokens |
| ARM three anchors combined | 72 tokens |
| Scoring-visible memory snapshot | 720 tokens |
| Stored snapshot including `support` | 960 tokens |

At most six memory items remain permitted. The validator still rejects duplicate
items, grade mappings, answer copying, non-atomic CRM rules, and conditional ARM
criteria. These limits are fixed in each frozen r21 project through its prompt,
schema, and analysis hashes; changing them requires a new prompt/project version.

When creating the matching prompt version, use the same numbers in the `scoring`,
`crm_update`, and `arm_update` text so the model is instructed to stay within the
validator rather than relying on retries.
