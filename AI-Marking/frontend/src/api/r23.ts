import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { apiClient } from './client';

const API_ROOT = '/r23';
// Starting a project re-hashes the restricted corpus and writes an immutable
// run snapshot. The runbook budgets up to two minutes for that local work.
const START_REQUEST_TIMEOUT_MS = 180_000;

export type R23Kind = 'pilot_run' | 'formal';

export interface R23DatasetStatus {
  dimension: string;
  filename: string;
  sha256: string;
  bytes: number;
  rows: number;
  fields: string[];
  label_distribution_x2: Record<string, number>;
  null_counts: Record<string, number>;
  status: string;
}

export interface R23DataStatus {
  protocol_id: string;
  ready: boolean;
  root_display?: string;
  datasets: R23DatasetStatus[];
  license_readme_present?: boolean;
  operator_confirmation_required: boolean;
  privacy?: string;
  error?: string;
}

export interface R23Runtime {
  runner_online: boolean;
  worker_id?: string | null;
  connected_at?: string | null;
  last_heartbeat_at?: string | null;
  last_seen_at?: string | null;
  heartbeat_seconds?: number;
  stale_after_seconds?: number;
  reason?: string | null;
}

export interface R23Runner {
  id: string;
  name: string;
  model: string;
  reasoning_effort: 'low' | 'medium' | 'high';
  speed_mode: 'standard' | 'fast';
  timeout_seconds: number;
  config_sha256: string;
  status: 'ready' | 'draft' | 'frozen';
}

export interface R23Rubric {
  id: string;
  name: string;
  protocol_id: string;
  rubric: string;
  rubric_sha256: string;
  status: 'ready' | 'draft' | 'frozen';
}

export interface R23Binding {
  id: string;
  runner_config_id: string;
  position: number;
  model: string;
  reasoning_effort: 'low' | 'medium' | 'high';
  speed_mode: 'standard' | 'fast';
  timeout_seconds: number;
  config_sha256: string;
}

export interface R23Project {
  id: string;
  protocol_id: string;
  name: string;
  kind: R23Kind;
  status: string;
  rubric_id: string;
  pilot_project_id?: string | null;
  runner_bindings: R23Binding[];
  data_processing_confirmed: boolean;
  data_status: R23DataStatus;
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

export interface R23Group {
  id: number;
  model_binding_id: string;
  dimension: string;
  run_index: number;
  status: string;
  order_rank: number;
  expected_calls: number;
  completed_calls: number;
}

export interface R23Call {
  id: number;
  model_binding_id: string;
  run_group_id: number;
  run_index: number;
  status: string;
  input_sha256: string;
  attempt_count: number;
  latency_ms?: number | null;
  failure_code?: string | null;
  failure_summary?: string | null;
  score_x2?: { content: number; organization: number; language: number };
}

export interface R23Cell {
  model_binding_id: string;
  dimension: 'content' | 'organization' | 'language';
  evidence_level: string;
  n: number;
  mpa: number;
  mpa_ci95: [number, number];
  qwk_intended_label_recovery: number;
  mae: number;
  spearman_rho: number;
  extreme_level_difference: number;
  trend: Array<{ case_level: number; mean: number; ci95: [number, number] }>;
  h1_pass: boolean;
  h2_pass: boolean;
}

export interface R23AnalysisReport {
  protocol_id: string;
  analysis_status: string;
  bootstrap_replicates: number;
  cells: R23Cell[];
  organization_selectivity: Array<{
    model_binding_id: string;
    si: number;
    ci95: [number, number];
    h3_pass: boolean;
  }>;
  stable_sensitivity: Record<string, boolean>;
  interpretation_guardrail: string;
}

export interface R23ReportEnvelope {
  results_embargoed: boolean;
  status: string;
  report?: R23AnalysisReport | Record<string, unknown> | null;
  report_sha256?: string;
  figure_sha256?: string;
}

const key = (name: string, id?: string) =>
  id ? ['r23', name, id] : ['r23', name];
const invalidate = (cache: ReturnType<typeof useQueryClient>) =>
  cache.invalidateQueries({ queryKey: ['r23'] });

export function useR23DataStatus() {
  return useQuery({
    queryKey: key('data-status'),
    queryFn: async () =>
      (await apiClient.get<R23DataStatus>(`${API_ROOT}/data-status`)).data,
    staleTime: 5 * 60_000,
  });
}

export function useR23Runtime() {
  return useQuery({
    queryKey: key('runtime'),
    queryFn: async () =>
      (await apiClient.get<R23Runtime>(`${API_ROOT}/runtime`)).data,
    refetchInterval: 10_000,
  });
}

export function useR23Runners() {
  return useQuery({
    queryKey: key('runners'),
    queryFn: async () =>
      (await apiClient.get<R23Runner[]>(`${API_ROOT}/runner-configs`)).data,
  });
}

export function useR23Rubrics() {
  return useQuery({
    queryKey: key('rubrics'),
    queryFn: async () =>
      (await apiClient.get<R23Rubric[]>(`${API_ROOT}/rubrics`)).data,
  });
}

export function useR23Projects() {
  return useQuery({
    queryKey: key('projects'),
    queryFn: async () =>
      (await apiClient.get<R23Project[]>(`${API_ROOT}/projects`)).data,
    refetchInterval: 10_000,
  });
}

export function useR23Project(id: string) {
  return useQuery({
    queryKey: key('project', id),
    queryFn: async () =>
      (await apiClient.get<R23Project>(`${API_ROOT}/projects/${id}`)).data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useR23Groups(id: string) {
  return useQuery({
    queryKey: key('groups', id),
    queryFn: async () =>
      (await apiClient.get<R23Group[]>(`${API_ROOT}/projects/${id}/groups`))
        .data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useR23Calls(id: string) {
  return useQuery({
    queryKey: key('calls', id),
    queryFn: async () =>
      (
        await apiClient.get<{
          results_embargoed: boolean;
          items: R23Call[];
        }>(`${API_ROOT}/projects/${id}/calls`)
      ).data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useR23Report(id: string, enabled = true) {
  return useQuery({
    queryKey: key('report', id),
    queryFn: async () =>
      (
        await apiClient.get<R23ReportEnvelope>(
          `${API_ROOT}/projects/${id}/report`,
        )
      ).data,
    enabled: Boolean(id) && enabled,
    refetchInterval: 30_000,
  });
}

function useR23Mutation<T>(
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

export function useCreateR23Runner() {
  return useR23Mutation(
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

export function useDeleteR23Runner() {
  return useR23Mutation(
    (id: string) =>
      apiClient.delete(`${API_ROOT}/runner-configs/${id}?confirm=true`),
    'Runner 已删除',
  );
}

export function useCreateR23Rubric() {
  return useR23Mutation(
    (payload: { name: string; rubric: string }) =>
      apiClient.post(`${API_ROOT}/rubrics`, payload),
    'Rubric 已保存',
  );
}

export function useDeleteR23Rubric() {
  return useR23Mutation(
    (id: string) => apiClient.delete(`${API_ROOT}/rubrics/${id}?confirm=true`),
    'Rubric 已删除',
  );
}

export function useCreateR23Project() {
  return useR23Mutation(
    (payload: {
      name: string;
      kind: R23Kind;
      runner_config_id: string;
      rubric_id: string;
      pilot_project_id?: string;
      data_processing_confirmed: true;
    }) => apiClient.post(`${API_ROOT}/projects`, payload),
    'r23 项目已创建',
  );
}

export function useRepeatR23Project() {
  return useR23Mutation(
    (id: string) =>
      apiClient.post(`${API_ROOT}/projects/${id}/repeat?confirm=true`),
    '已创建独立重复运行',
  );
}

export function useR23Action() {
  return useR23Mutation(
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

export function useDeleteR23Project() {
  return useR23Mutation(
    (id: string) => apiClient.delete(`${API_ROOT}/projects/${id}?confirm=true`),
    '项目已删除',
  );
}

export function useRetryR23Call() {
  return useR23Mutation(
    ({ projectId, callId }: { projectId: string; callId: number }) =>
      apiClient.post(
        `${API_ROOT}/projects/${projectId}/calls/${callId}/retry?confirm=true`,
      ),
    '调用已进入人工重试队列',
  );
}

export function r23ExportUrl(
  projectId: string,
  kind: 'manifest' | 'results' | 'report' | 'figure',
) {
  return `/api${API_ROOT}/projects/${projectId}/exports/${kind}`;
}
