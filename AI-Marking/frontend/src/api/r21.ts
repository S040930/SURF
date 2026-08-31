import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { apiClient } from './client';

export type R21Kind = 'pilot_run' | 'formal';

// The active UI uses the expanded r22 pilot API. Legacy r21 endpoints remain
// available for old projects and are not modified.
const API_ROOT = '/r22';

export interface R21Runner {
  id: string;
  name: string;
  config_json: {
    model: string;
    reasoning_effort: string;
    timeout_seconds: number;
  };
  config_sha256: string;
  frozen_runtime_json?: Record<string, unknown>;
  status: string;
}

export interface R21Prompt {
  id: string;
  name: string;
  protocol_id: string;
  templates_json: {
    scoring: string;
    crm_update: string;
    arm_update: string;
  };
  templates_sha256: string;
  status: string;
}

export interface R21Group {
  id: number;
  project_id: string;
  question_id: string;
  condition: string;
  status: string;
  order_rank: number;
  expected_calls: number;
  progress: Record<string, number>;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface R21Runtime {
  runner_online: boolean;
  queued_groups: number;
  worker_id?: string | null;
  connected_at?: string | null;
  last_heartbeat_at?: string | null;
  last_seen_at?: string | null;
  runtime?: Record<string, unknown>;
  active_group?: R21Group | null;
  heartbeat_seconds?: number;
  stale_after_seconds?: number;
  reason?: string | null;
}

export interface R21Performance {
  successful_attempts: number;
  throughput_calls_per_hour?: number | null;
  average_latency_ms?: number | null;
  p50_latency_ms?: number | null;
  p95_latency_ms?: number | null;
  estimated_remaining_seconds?: number | null;
}

export interface R21Project {
  id: string;
  name: string;
  protocol_id: string;
  kind: R21Kind;
  status: string;
  runner_config_id: string;
  runner_config_json: Record<string, unknown>;
  prompt_version_id: string;
  prompt_version_name: string;
  pilot_project_id: string | null;
  manifest_json: Record<string, unknown>;
  progress: Record<string, number>;
  performance: R21Performance;
  runtime: R21Runtime;
}

export interface R21Call {
  id: number;
  kind: string;
  status: string;
  question_id: string;
  condition: string;
  trajectory: number;
  history_count: number;
  repeat: number;
  failure_reason?: string | null;
}

export interface R21Report {
  project_id: string;
  status: string;
  report?: Record<string, unknown> | null;
  report_sha256?: string | null;
}

const key = (name: string, id?: string) =>
  id ? ['r22', name, id] : ['r22', name];
const invalidate = (cache: ReturnType<typeof useQueryClient>) =>
  cache.invalidateQueries({ queryKey: ['r22'] });

export function useR21Runtime() {
  return useQuery({
    queryKey: key('runtime'),
    queryFn: async () => (await apiClient.get<R21Runtime>(`${API_ROOT}/runtime`)).data,
    refetchInterval: 10_000,
  });
}

export function useR21Runners() {
  return useQuery({
    queryKey: key('runners'),
    queryFn: async () =>
      (await apiClient.get<R21Runner[]>(`${API_ROOT}/runner-configs`)).data,
  });
}

export function useR21Prompts() {
  return useQuery({
    queryKey: key('prompts'),
    queryFn: async () =>
      (await apiClient.get<R21Prompt[]>(`${API_ROOT}/prompt-versions`)).data,
  });
}

export function useR21Projects() {
  return useQuery({
    queryKey: key('projects'),
    queryFn: async () =>
      (await apiClient.get<R21Project[]>(`${API_ROOT}/projects`)).data,
    refetchInterval: 10_000,
  });
}

export function useR21Project(id: string) {
  return useQuery({
    queryKey: key('project', id),
    queryFn: async () =>
      (await apiClient.get<R21Project>(`${API_ROOT}/projects/${id}`)).data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useR21Groups(id: string) {
  return useQuery({
    queryKey: key('groups', id),
    queryFn: async () =>
      (await apiClient.get<R21Group[]>(`${API_ROOT}/projects/${id}/groups`)).data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useR21Failures(id: string) {
  return useQuery({
    queryKey: key('failures', id),
    queryFn: async () =>
      (
        await apiClient.get<{ items: R21Call[]; total: number }>(
          `${API_ROOT}/projects/${id}/failures`,
        )
      ).data,
    enabled: Boolean(id),
    refetchInterval: 10_000,
  });
}

export function useR21Report(id: string) {
  return useQuery({
    queryKey: key('report', id),
    queryFn: async () =>
      (await apiClient.get<R21Report>(`${API_ROOT}/projects/${id}/report`)).data,
    enabled: Boolean(id),
    refetchInterval: 30_000,
  });
}

function useR21Mutation<T>(
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

export function useCreateR21Runner() {
  return useR21Mutation(
    (payload: {
      name: string;
      model: string;
      reasoning_effort: string;
      timeout_seconds: number;
    }) => apiClient.post(`${API_ROOT}/runner-configs`, payload),
    'Codex Runner 已创建',
  );
}

export function useFreezeR21Runner() {
  return useR21Mutation(
    (id: string) =>
      apiClient.post(`${API_ROOT}/runner-configs/${id}/freeze?confirm=true`),
    'Runner 已冻结',
  );
}

export function useDeleteR21Runner() {
  return useR21Mutation(
    (id: string) => apiClient.delete(`${API_ROOT}/runner-configs/${id}?confirm=true`),
    'Runner 已删除',
  );
}

export function useCreateR21Prompt() {
  return useR21Mutation(
    (payload: {
      name: string;
      scoring: string;
      crm_update: string;
      arm_update: string;
    }) => apiClient.post(`${API_ROOT}/prompt-versions`, payload),
    '提示词版本已保存',
  );
}

export function useFreezeR21Prompt() {
  return useR21Mutation(
    (id: string) =>
      apiClient.post(`${API_ROOT}/prompt-versions/${id}/freeze?confirm=true`),
    '提示词已通过本地技术门禁并冻结',
  );
}

export function useDeleteR21Prompt() {
  return useR21Mutation(
    (id: string) => apiClient.delete(`${API_ROOT}/prompt-versions/${id}?confirm=true`),
    '提示词已删除',
  );
}

export function useCreateR21Project() {
  return useR21Mutation(
    (payload: {
      name: string;
      kind: R21Kind;
      runner_config_id: string;
      prompt_version_id: string;
      pilot_project_id?: string;
    }) => apiClient.post(`${API_ROOT}/projects`, payload),
    'r21 项目已创建',
  );
}

export function useR21Action() {
  return useR21Mutation(
    ({
      id,
      action,
    }: {
      id: string;
      action: 'freeze' | 'pause' | 'resume' | 'terminate';
    }) =>
      apiClient.post(
        `${API_ROOT}/projects/${id}/${action}${['freeze', 'terminate'].includes(action) ? '?confirm=true' : ''}`,
      ),
    '项目状态已更新',
  );
}

export function useStartR21Project() {
  return useR21Mutation(
    (id: string) => apiClient.post(`${API_ROOT}/projects/${id}/start?confirm=true`),
    '完整项目已入队，Codex MCP 连接后将自动运行',
  );
}

export function useDeleteR21Project() {
  return useR21Mutation(
    (id: string) => apiClient.delete(`${API_ROOT}/projects/${id}?confirm=true`),
    '项目已删除',
  );
}

export function useRetryR21Call() {
  return useR21Mutation(
    ({ projectId, callId }: { projectId: string; callId: number }) =>
      apiClient.post(
        `${API_ROOT}/projects/${projectId}/calls/${callId}/retry?confirm=true`,
      ),
    '失败调用已重试，项目将自动继续',
  );
}
