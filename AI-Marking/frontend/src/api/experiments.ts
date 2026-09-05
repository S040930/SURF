import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { apiClient } from './client';

const API_ROOT = '/experiments';
// Starting a project re-audits the restricted corpus and materializes the
// frozen sampling plan. The runbook budgets up to two minutes for that work.
const START_REQUEST_TIMEOUT_MS = 180_000;

export type ExperimentKind = 'pilot_run' | 'formal';

export interface DatasetFileSpec {
  file: string;
  sha256: string;
  bytes: number;
  rows: number;
}

export interface DatasetAuditReport {
  dataset_key: string;
  datasets: DatasetFileSpec[];
  columns: string[];
  raw_rows: number;
  empty_essay_rows: number;
  conflict_groups: number;
  conflict_rows: number;
  merged_duplicate_records: number;
  unique_inputs: number;
  distinct_prompts: number;
  low_tail_inputs: number;
  strata_sizes: Record<string, number>;
  total_column_mismatches: {
    count: number;
    in_scoring_pool: number;
    detail: Array<Record<string, unknown>>;
    rule: string;
  };
}

export interface ContractChannel {
  key: string;
  label: string;
}

export interface ContractManifest {
  channels: ContractChannel[];
  grid_min_x2: number;
  grid_max_x2: number;
  score_values: number[];
  schema_sha256: string;
}

export interface DatasetStatus {
  dataset_key: string;
  name?: string;
  access_level?: string;
  license_note?: string;
  ready: boolean;
  report?: DatasetAuditReport;
  contract?: ContractManifest;
  operator_confirmation_required?: boolean;
  error?: string;
}

export interface ExperimentTemplate {
  template_id: string;
  name: string;
  dataset_key: string;
  runner_count: number;
  require_runner_alignment: boolean;
  sampling_seed: string;
  contract: ContractManifest;
}

export interface ExperimentRuntime {
  runner_online: boolean;
  worker_id?: string | null;
  last_heartbeat_at?: string | null;
  reason?: string | null;
}

export interface ExperimentRunner {
  id: string;
  name: string;
  model: string;
  reasoning_effort: 'low' | 'medium' | 'high';
  speed_mode: 'standard' | 'fast';
  timeout_seconds: number;
  config_sha256: string;
  status: 'ready' | 'draft' | 'frozen';
}

export interface ExperimentRubric {
  id: string;
  template_id: string;
  name: string;
  rubric: string;
  rubric_sha256: string;
  status: 'ready' | 'draft' | 'frozen';
}

export interface ExperimentBinding {
  id: string;
  runner_config_id: string;
  position: number;
  model: string;
  reasoning_effort: 'low' | 'medium' | 'high';
  speed_mode: 'standard' | 'fast';
  timeout_seconds: number;
  config_sha256: string;
}

export interface ExperimentProject {
  id: string;
  template_id: string;
  name: string;
  kind: ExperimentKind;
  status: string;
  dataset_revision_id?: string | null;
  rubric_id?: string | null;
  pilot_project_id?: string | null;
  runner_bindings: ExperimentBinding[];
  read_only: boolean;
  source_system: string;
  source_project_id?: string | null;
  data_processing_confirmed: boolean;
  manifest_sha256?: string | null;
  manifest_summary: Record<string, unknown>;
  results_embargoed: boolean;
  report_sha256?: string | null;
  progress: Record<string, number>;
  performance: {
    p50_latency_ms?: number | null;
    p95_latency_ms?: number | null;
  };
  started_at?: string | null;
  completed_at?: string | null;
}

export interface ExperimentGroup {
  id: number;
  binding_id: string;
  group_key: string;
  run_index: number;
  status: string;
  order_rank: number;
  expected_calls: number;
  completed_calls: number;
}

export interface ExperimentCall {
  id: number;
  binding_id: string;
  run_group_id: number;
  run_index: number;
  status: string;
  input_sha256: string;
  attempt_count: number;
  latency_ms?: number | null;
  failure_code?: string | null;
  failure_summary?: string | null;
  scores?: Record<string, number>;
}

export interface CalibrationPoint {
  score_x2: number;
  score: number;
  mean_label: number;
  count: number;
  weight: number;
}

export interface ChannelMetrics {
  weighted_qwk: number;
  weighted_mae: number;
  unweighted_mae: number;
  spearman: number;
  exact_rate: number;
  within_half_point_rate: number;
  within_one_point_rate: number;
  calibration: CalibrationPoint[];
}

export interface BootstrapDiff {
  observed_diff: number;
  ci_low: number;
  ci_high: number;
  replicates: number;
}

export interface RetestChannel {
  exact_rate: number;
  mae: number;
  transition_matrix: number[][];
}

export interface AgreementReport {
  template_id: string;
  report_type: string;
  design: Record<string, unknown>;
  sample_diagnostics: Record<string, unknown>;
  per_model: Record<
    string,
    { count: number; channels: Record<string, ChannelMetrics> }
  >;
  model_comparison: Record<string, Record<string, BootstrapDiff>>;
  baseline: {
    description: string;
    disclosure: string;
    channels: Record<
      string,
      Omit<ChannelMetrics, 'calibration' | 'spearman' | 'unweighted_mae'>
    >;
  };
  retest: Record<string, { count: number; channels: Record<string, RetestChannel> }>;
  statements: string[];
}

export interface ReportEnvelope {
  results_embargoed: boolean;
  status: string;
  report?: AgreementReport | Record<string, unknown> | null;
  report_sha256?: string;
  figures?: Array<{ name: string; sha256: string }>;
}

const key = (name: string, id?: string) =>
  id ? ['experiments', name, id] : ['experiments', name];
const invalidate = (cache: ReturnType<typeof useQueryClient>) =>
  cache.invalidateQueries({ queryKey: ['experiments'] });

export function useExperimentDatasets() {
  return useQuery({
    queryKey: key('datasets'),
    queryFn: async () =>
      (await apiClient.get<DatasetStatus[]>(`${API_ROOT}/datasets`)).data,
    staleTime: 5 * 60_000,
  });
}

export function useDatasetRevision(datasetKey: string, enabled = true) {
  return useQuery({
    queryKey: key('revision', datasetKey),
    queryFn: async () =>
      (
        await apiClient.get<{
          dataset: { id: string; key: string; name: string; access_level: string };
          revision: {
            id: string;
            revision_label: string;
            audit: DatasetAuditReport;
          };
          contract: ContractManifest & { id: string };
        }>(`${API_ROOT}/datasets/${datasetKey}/revision`)
      ).data,
    enabled: enabled && Boolean(datasetKey),
    staleTime: 5 * 60_000,
  });
}

export function useExperimentTemplates() {
  return useQuery({
    queryKey: key('templates'),
    queryFn: async () =>
      (await apiClient.get<ExperimentTemplate[]>(`${API_ROOT}/templates`)).data,
  });
}

export function useExperimentRuntime() {
  return useQuery({
    queryKey: key('runtime'),
    queryFn: async () =>
      (await apiClient.get<ExperimentRuntime>(`${API_ROOT}/runtime`)).data,
    refetchInterval: 10_000,
  });
}

export function useExperimentRunners() {
  return useQuery({
    queryKey: key('runners'),
    queryFn: async () =>
      (await apiClient.get<ExperimentRunner[]>(`${API_ROOT}/runner-configs`)).data,
  });
}

export function useExperimentRubrics(templateId?: string) {
  return useQuery({
    queryKey: [...key('rubrics'), templateId ?? 'all'],
    queryFn: async () =>
      (
        await apiClient.get<ExperimentRubric[]>(
          `${API_ROOT}/rubrics${templateId ? `?template_id=${templateId}` : ''}`,
        )
      ).data,
  });
}

export function useExperimentProjects() {
  return useQuery({
    queryKey: key('projects'),
    queryFn: async () =>
      (await apiClient.get<ExperimentProject[]>(`${API_ROOT}/projects`)).data,
    refetchInterval: 10_000,
  });
}

export function useExperimentProject(id: string) {
  return useQuery({
    queryKey: key('project', id),
    queryFn: async () =>
      (await apiClient.get<ExperimentProject>(`${API_ROOT}/projects/${id}`)).data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useExperimentGroups(id: string) {
  return useQuery({
    queryKey: key('groups', id),
    queryFn: async () =>
      (
        await apiClient.get<ExperimentGroup[]>(
          `${API_ROOT}/projects/${id}/groups`,
        )
      ).data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useExperimentCalls(id: string) {
  return useQuery({
    queryKey: key('calls', id),
    queryFn: async () =>
      (
        await apiClient.get<{
          results_embargoed: boolean;
          items: ExperimentCall[];
        }>(`${API_ROOT}/projects/${id}/calls`)
      ).data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useExperimentReport(id: string, enabled = true) {
  return useQuery({
    queryKey: key('report', id),
    queryFn: async () =>
      (
        await apiClient.get<ReportEnvelope>(
          `${API_ROOT}/projects/${id}/report`,
        )
      ).data,
    enabled: Boolean(id) && enabled,
    refetchInterval: 30_000,
  });
}

function useExperimentMutation<T>(
  fn: (payload: T) => Promise<unknown>,
  message: string,
) {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      invalidate(cache);
      toast.success(message);
    },
  });
}

export function useCreateExperimentRunner() {
  return useExperimentMutation(
    (payload: {
      name: string;
      model: string;
      reasoning_effort: 'low' | 'medium' | 'high';
      speed_mode: 'standard' | 'fast';
      timeout_seconds: number;
    }) => apiClient.post(`${API_ROOT}/runner-configs`, payload),
    'Codex Runner 已保存',
  );
}

export function useDeleteExperimentRunner() {
  return useExperimentMutation(
    (id: string) =>
      apiClient.delete(`${API_ROOT}/runner-configs/${id}?confirm=true`),
    'Runner 已删除',
  );
}

export function useCreateExperimentRubric() {
  return useExperimentMutation(
    (payload: { template_id: string; name: string; rubric: string }) =>
      apiClient.post(`${API_ROOT}/rubrics`, payload),
    'Rubric 已保存',
  );
}

export function useCreateExperimentProject() {
  return useExperimentMutation(
    (payload: {
      name: string;
      kind: ExperimentKind;
      template_id: string;
      dataset_revision_id: string;
      rubric_id: string;
      runner_config_ids: string[];
      pilot_project_id?: string;
      data_processing_confirmed: true;
    }) => apiClient.post(`${API_ROOT}/projects`, payload),
    '统一实验项目已创建',
  );
}

export function useExperimentAction() {
  return useExperimentMutation(
    ({
      id,
      action,
    }: {
      id: string;
      action: 'start' | 'pause' | 'resume' | 'terminate';
    }) =>
      apiClient.post(
        `${API_ROOT}/projects/${id}/${action}${['start', 'terminate'].includes(action) ? '?confirm=true' : ''}`,
        undefined,
        action === 'start' ? { timeout: START_REQUEST_TIMEOUT_MS } : undefined,
      ),
    '项目状态已更新',
  );
}

export function useDeleteExperimentProject() {
  return useExperimentMutation(
    (id: string) => apiClient.delete(`${API_ROOT}/projects/${id}?confirm=true`),
    '项目已删除',
  );
}

export function useRetryExperimentCall() {
  return useExperimentMutation(
    ({ projectId, callId }: { projectId: string; callId: number }) =>
      apiClient.post(
        `${API_ROOT}/projects/${projectId}/calls/${callId}/retry?confirm=true`,
      ),
    '调用已进入人工重试队列',
  );
}

export function experimentExportUrl(
  projectId: string,
  kind: 'manifest' | 'results' | 'report' | 'figures',
) {
  return `/api${API_ROOT}/projects/${projectId}/exports/${kind}`;
}
