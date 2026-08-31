// ---------------------------------------------------------------------------
// r20 SAF official-split platform
// ---------------------------------------------------------------------------

export type R20ProjectKind = 'pilot_run' | 'formal';
export type R20Condition = 'nm' | 'crm' | 'arm';

export interface R20ModelConfig {
  id: string;
  name: string;
  config_json: Record<string, unknown>;
  config_sha256: string;
  status: string;
  frozen_at: string | null;
  created_at: string | null;
}

export interface R20PromptTemplates {
  scoring: string;
  crm_update: string;
  arm_update: string;
}

export interface R20PromptVersion {
  id: string;
  protocol_id: string;
  parent_version_id: string | null;
  name: string;
  templates_json: R20PromptTemplates;
  templates_sha256: string;
  validated_model_config_ids_json: string[] | null;
  status: string;
  frozen_at: string | null;
  created_at: string | null;
}

export interface R20MemorySnapshot {
  condition: 'crm' | 'arm';
  history_count: number;
  status: string;
  memory_format: 'crm' | 'arm' | null;
  items: Array<Record<string, unknown>> | null;
  visible_token_count: number | null;
  stored_token_count: number | null;
  failure_reason: string | null;
}

export interface R20Memories {
  project_id: string;
  question_id: string;
  question_text: string;
  path: 'main' | 'order';
  available_paths: string[];
  snapshots: Record<'crm' | 'arm', R20MemorySnapshot[]>;
}

export interface R20Call {
  id: number;
  kind: string;
  status: string;
  question_id: string;
  entry_id: string;
  condition: R20Condition;
  path: string;
  history_count: number;
  output_json: Record<string, unknown> | null;
  failure_reason: string | null;
}

export interface R20CallPage {
  items: R20Call[];
  total: number;
  summary: { by_status: Record<string, number>; by_kind: Record<string, number> };
  next_before_id: number | null;
}

export interface R20Failure {
  id: number;
  kind: string;
  status: string;
  model_id: string;
  question_id: string;
  condition: R20Condition;
  trajectory: number;
  history_count: number;
  repeat: number;
  failure_reason: string | null;
}

export interface R20FailurePage {
  items: R20Failure[];
  total: number;
  summary: Record<string, number>;
}

export interface R20PromptValidationSuite {
  id: string;
  protocol_id: string;
  prompt_version_id: string;
  model_config_ids_json: string[];
  status: 'queued' | 'running' | 'passed' | 'failed';
  expected_calls: number;
  completed_calls: number;
  failure_reason: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface R20Project {
  id: string;
  protocol_id: string;
  name: string;
  kind: R20ProjectKind;
  status: string;
  model_config_ids_json: string[];
  prompt_version_id: string;
  prompt_version_name: string;
  pilot_project_id: string | null;
  manifest_json: Record<string, unknown>;
  progress: Record<string, number> | null;
  created_at: string | null;
  frozen_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface R20RunGroup {
  id: number;
  project_id: string;
  question_id: string;
  condition: string;
  status: 'pending' | 'queued' | 'running' | 'completed';
  order_rank: number;
  expected_calls: number;
  progress: Record<string, number>;
  started_at: string | null;
  completed_at: string | null;
}

export interface R20Report {
  status: string;
  project_id: string;
  report: Record<string, unknown> | null;
  report_sha256: string | null;
  reason: string | null;
}
