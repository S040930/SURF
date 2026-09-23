import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { toast } from 'sonner';

import { apiClient } from './client';

export type MemoryStudyKind = 'development' | 'pilot' | 'formal';
export type MemoryStudyProtocol =
  | 'saf-memory-framework-v3'
  | 'saf-memory-framework-v3-r2';

export interface MemoryStudyAudit {
  ready: boolean;
  protocol: string;
  kind: MemoryStudyKind;
  archive_sha256: string;
  manifest_sha256: string;
  selected_questions: string[];
  question_hashes?: Record<string, string>;
  roles: Record<string, string>;
  counts: Record<string, { training: number; test: number }>;
  clean_counts: Record<string, number>;
  excluded_components: number;
  max_scores: Record<string, number>;
  score_ceilings?: Record<string, number>;
  score_floors?: Record<string, number>;
  protocol_fingerprint?: string;
  embedding: {
    model: string;
    revision: string;
    revision_pinned: boolean;
  };
  source_archive: string;
  scoring_context?: {
    protocol_id: string;
    envelope_version: string;
    system_prompt: string;
    payload_fields: string[];
    memory_projection: string;
    instrument_sha256: string;
  };
}

export interface MemoryStudyRuntime {
  online: boolean;
  state?: 'online' | 'recovering' | 'idle' | 'offline';
  status: string;
  global_subprocess_limit: number;
  active_subprocesses: number;
  slots: Record<
    string,
    {
      model: string;
      condition?: string;
      study_id?: string;
      state?: string;
      phase?: string;
      call_id?: number;
      kind?: string;
      attempt?: number;
      retry_after?: string;
      heartbeat_at?: string;
    }
  >;
  worker_id?: string | null;
  last_heartbeat_at?: string | null;
  reason?: string | null;
  /** worker 启动被拒的原因（如槽位被其他 worker 占用），为空表示无阻塞。 */
  blocked_reason?: string | null;
}

export interface MemoryStudyProject {
  id: string;
  protocol_id: string;
  name: string;
  kind: MemoryStudyKind;
  status: string;
  integrity_status: string;
  results_embargoed: boolean;
  data_manifest_sha256: string;
  config_sha256: string;
  config: {
    models: Array<{
      model: string;
      reasoning_effort: string;
      speed_mode: string;
      timeout_seconds: number;
    }>;
    conditions: string[];
    order_variants: string[];
    repeats: number[];
    retrieval_top_k: number;
    embedding_model: string;
    embedding_revision: string;
  };
  expected: Record<string, number>;
  progress: {
    [key: string]: any;
    models?: Record<string, Record<string, any>>;
    eta_seconds?: number | null;
    recent_latency_ms?: { sample_size: number; mean: number | null };
  };
  preflight: MemoryStudyPreflight;
  questions?: MemoryQuestionRun[];
  integrity: { checks?: Record<string, boolean>; audited_at?: string };
  error_summary?: string | null;
  created_at?: string | null;
  frozen_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface MemoryQuestionRun {
  id: number;
  question_id: string;
  status: string;
  error_summary?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface MemoryStudyPreflight {
  status: 'required' | 'running' | 'passed' | 'failed' | string;
  config_sha256?: string;
  invalid_reason?: string;
  started_at?: string;
  completed_at?: string;
  models?: Record<
    string,
    {
      status: string;
      model: string;
      latency_ms?: number;
      fingerprint?: Record<string, string | boolean>;
      failure_code?: string;
      failure_summary?: string;
      stderr_excerpt?: string | null;
      response_excerpt?: string;
    }
  >;
}

export interface MemoryStore {
  id: number;
  model: string;
  question_id: string;
  framework: string;
  condition: string;
  feedback_mode: string;
  order_variant: string;
  status: string;
  committed_count: number;
  history_count: number;
  snapshot_sha256?: string | null;
  snapshot_artifact_sha256?: string | null;
  framework_version?: string | null;
  snapshot_stream_id?: string | null;
  error_summary?: string | null;
  updated_at?: string | null;
}

export interface MemoryStoreDetail extends MemoryStore {
  snapshot?: Record<string, unknown> | null;
  attempts?: Array<{
    id: number;
    call_id: number;
    attempt_number: number;
    status: string;
    requested_model: string;
    request_sha256: string;
    input_tokens?: number | null;
    output_tokens?: number | null;
    total_tokens?: number | null;
    latency_ms?: number | null;
    stderr_excerpt?: string | null;
    error_type?: string | null;
    error_message?: string | null;
  }>;
  write_calls: Array<Record<string, unknown>>;
  retrieval_basis: Array<Record<string, unknown>>;
}

export interface MemoryCall {
  id: number;
  model: string;
  question_id: string;
  answer_id: string;
  condition: string;
  framework: string;
  feedback_mode: string;
  order_variant: string;
  kind: 'memory_write' | 'score';
  repeat: number;
  status: string;
  attempt_count: number;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  latency_ms?: number | null;
  memory_hash?: string | null;
  retrieval?: Array<Record<string, unknown>> | null;
  manual_score?: number | null;
  model_score?: number | null;
  signed_diff?: number | null;
  absolute_diff?: number | null;
  normalized_signed_diff?: number | null;
  normalized_absolute_diff?: number | null;
  model_feedback?: string | null;
  failure_code?: string | null;
  failure_summary?: string | null;
  worker_id?: string | null;
  slot_id?: string | null;
}

export interface MemoryStudyReport {
  study_id: string;
  results_embargoed: boolean;
  status: string;
  report_sha256?: string;
  reason?: string;
  report?: Record<string, any> | null;
}

const root = '/memory-study';
const key = (name: string, id?: string) =>
  id ? ['memory-study', name, id] : ['memory-study', name];

function invalidate(cache: ReturnType<typeof useQueryClient>) {
  return cache.invalidateQueries({ queryKey: ['memory-study'] });
}

export function useMemoryStudyAudit(
  kind: MemoryStudyKind = 'formal',
  protocolId: MemoryStudyProtocol = 'saf-memory-framework-v3',
) {
  return useQuery({
    queryKey: [...key('audit'), kind, protocolId],
    queryFn: async () =>
      (
        await apiClient.get<MemoryStudyAudit>(
          `${root}/audit?kind=${kind}&protocol_id=${protocolId}`,
        )
      ).data,
    staleTime: 5 * 60_000,
  });
}

export function useMemoryStudyRuntime(enabled = true, polling = true) {
  return useQuery({
    queryKey: key('runtime'),
    queryFn: async () =>
      (await apiClient.get<MemoryStudyRuntime>(`${root}/runtime`)).data,
    enabled,
    refetchInterval: (query) =>
      !polling ? false : query.state.data?.online ? 3_000 : 10_000,
  });
}

export function useMemoryStudyProjects() {
  return useQuery({
    queryKey: key('projects'),
    queryFn: async () =>
      (await apiClient.get<MemoryStudyProject[]>(`${root}/projects`)).data,
    refetchInterval: (query) => {
      const projects = query.state.data ?? [];
      return projects.some((project) =>
        ['running', 'paused', 'attention_required'].includes(project.status),
      )
        ? 10_000
        : false;
    },
  });
}

export function useMemoryStudyProject(id: string) {
  return useQuery({
    queryKey: key('project', id),
    queryFn: async () =>
      (await apiClient.get<MemoryStudyProject>(`${root}/projects/${id}`)).data,
    enabled: Boolean(id),
    refetchInterval: (query) =>
      ['running', 'paused', 'attention_required'].includes(
        query.state.data?.status ?? '',
      )
        ? query.state.data?.status === 'running' ? 3_000 : 10_000
        : false,
  });
}

export function useCreateMemoryStudy() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      name: string;
      kind: MemoryStudyKind;
      protocol_id?: MemoryStudyProtocol;
      data_processing_confirmed: true;
      speed_modes?: Record<string, 'standard' | 'fast'>;
      feedback_waves?: Array<'full' | 'no_feedback'>;
    }) =>
      (await apiClient.post<MemoryStudyProject>(`${root}/projects`, payload))
        .data,
    onSuccess: () => {
      invalidate(cache);
      toast.success('SAF 记忆研究项目已创建');
    },
  });
}

export function useMemoryStudyAction() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, action }: { id: string; action: string }) =>
      (await apiClient.post<MemoryStudyProject>(`${root}/projects/${id}/${action}`))
        .data,
    onSuccess: () => {
      invalidate(cache);
      toast.success('研究状态已更新');
    },
  });
}

export function useMemoryStudyQuestionAction() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({
      studyId,
      questionId,
      action,
    }: { studyId: string; questionId: string; action: string }) =>
      (
        await apiClient.post<MemoryStudyProject>(
          `${root}/projects/${studyId}/questions/${encodeURIComponent(questionId)}/${action}`,
        )
      ).data,
    onSuccess: () => {
      invalidate(cache);
      toast.success('题目分片状态已更新');
    },
  });
}

export function useMemoryStudyPreflight() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      (await apiClient.post<MemoryStudyProject>(`${root}/projects/${id}/preflight`)).data,
    onSuccess: (project) => {
      invalidate(cache);
      toast.success(
        project.preflight.status === 'passed'
          ? 'Luna 运行预检通过'
          : '运行预检未通过，请检查失败原因',
      );
    },
  });
}

export function useMemoryStudyAuditProject() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      (await apiClient.post(`${root}/projects/${id}/audit`)).data,
    onSuccess: () => {
      invalidate(cache);
      toast.success('完整性审计已运行');
    },
  });
}

export function useMemoryStudyStores(id: string, enabled = true, polling = true) {
  return useQuery({
    queryKey: key('stores', id),
    queryFn: async () =>
      (await apiClient.get<MemoryStore[]>(`${root}/projects/${id}/memory-stores`))
        .data,
    enabled: Boolean(id) && enabled,
    refetchInterval: enabled && polling ? 8_000 : false,
  });
}

export function useMemoryStudyStore(id: string, storeId: number | null, enabled = true) {
  return useQuery({
    queryKey: [...key('store', id), storeId],
    queryFn: async () =>
      (
        await apiClient.get<MemoryStoreDetail>(
          `${root}/projects/${id}/memory-stores/${storeId}`,
        )
      ).data,
    enabled: Boolean(id) && storeId !== null && enabled,
  });
}

export interface MemoryCallPage {
  items: MemoryCall[];
  next_cursor: number | null;
  total: number;
}

export function useMemoryStudyCalls(
  id: string,
  kind: 'score' | 'memory_write',
  options: {
    enabled?: boolean;
    polling?: boolean;
    cursor?: number | null;
    status?: string;
    questionId?: string;
    model?: string;
    condition?: string;
    feedbackMode?: string;
    limit?: number;
  } = {},
) {
  const {
    enabled = true,
    polling = true,
    cursor,
    status,
    questionId,
    model,
    condition,
    feedbackMode,
    limit = 100,
  } = options;
  return useQuery({
    queryKey: [
      ...key('calls', id),
      kind,
      status ?? 'all',
      questionId ?? 'all',
      model ?? 'all',
      condition ?? 'all',
      feedbackMode ?? 'all',
      cursor ?? null,
      limit,
    ],
    queryFn: async () =>
      (
        await apiClient.get<MemoryCallPage>(
          `${root}/projects/${id}/calls?kind=${kind}&limit=${limit}${
            cursor == null ? '' : `&cursor=${cursor}`
          }${status ? `&status=${encodeURIComponent(status)}` : ''}${
            questionId ? `&question_id=${encodeURIComponent(questionId)}` : ''
          }${model ? `&model=${encodeURIComponent(model)}` : ''}${
            condition ? `&condition=${encodeURIComponent(condition)}` : ''
          }${feedbackMode ? `&feedback_mode=${encodeURIComponent(feedbackMode)}` : ''}`,
        )
      ).data,
    enabled: Boolean(id) && enabled,
    refetchInterval: enabled && polling ? 8_000 : false,
  });
}

export function useMemoryStudyReport(id: string, enabled = true, polling = true) {
  return useQuery({
    queryKey: key('report', id),
    queryFn: async () =>
      (await apiClient.get<MemoryStudyReport>(`${root}/projects/${id}/report`)).data,
    enabled: Boolean(id) && enabled,
    refetchInterval: enabled && polling ? 15_000 : false,
  });
}

export function useRetryMemoryStudyCall() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ studyId, callId }: { studyId: string; callId: number }) =>
      (
        await apiClient.post<MemoryCall>(
          `${root}/projects/${studyId}/calls/${callId}/retry`,
        )
      ).data,
    onSuccess: () => {
      invalidate(cache);
      toast.success('失败调用已保留原 attempt 并重新入队');
    },
  });
}

export function useRecoverMemoryStudyStore() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ studyId, storeId }: { studyId: string; storeId: number }) =>
      (
        await apiClient.post<{ store_id: number; requeued: number; rebuilt: boolean }>(
          `${root}/projects/${studyId}/memory-stores/${storeId}/rebuild`,
        )
      ).data,
    onSuccess: (data) => {
      invalidate(cache);
      toast.success(
        data.rebuilt
          ? '快照校验失败，memory store 已清空并重新排队'
          : 'memory store 已从最后提交点恢复',
      );
    },
  });
}

export function useRetryAllFailedMemoryStudyCalls() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (studyId: string) =>
      (
        await apiClient.post<{ requeued: number; rebuilt_stores?: number[] }>(
          `${root}/projects/${studyId}/retry-failed`,
        )
      ).data,
    onSuccess: (data) => {
      invalidate(cache);
      toast.success(`已将 ${data.requeued} 条失败调用重新入队`);
    },
  });
}

export function useRetryMemoryStudyQuestionCalls() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ studyId, questionId }: { studyId: string; questionId: string }) =>
      (
        await apiClient.post<{ requeued: number; rebuilt_stores?: number[] }>(
          `${root}/projects/${studyId}/questions/${encodeURIComponent(questionId)}/retry-failed`,
        )
      ).data,
    onSuccess: (data) => {
      invalidate(cache);
      toast.success(`题目已重新排队 ${data.requeued} 条失败调用`);
    },
  });
}

export function memoryStudyExportUrl(id: string, kind: 'manifest' | 'results' | 'report' | 'html') {
  return `${root}/projects/${id}/exports/${kind}`;
}

// ------------------------------------------------------------- site config

export type MemoryStudySpeedMode = 'standard' | 'fast';

/**
 * 模型槽位固定为当前协议的 Luna-only 配置；可编辑运行参数。
 * 对应 ms_runner_configs 行；修改只在之后创建/冻结的研究中生效。
 */
export interface MemoryStudyRunnerConfig {
  model: string;
  reasoning_effort: string;
  speed_mode: MemoryStudySpeedMode;
  timeout_seconds: number;
}

/** 站点级 embedding 覆盖，对应 GET /site-config 的解析结果。 */
export interface MemoryStudySiteConfig {
  embedding_backend: 'openai';
  embedding_model: string;
  embedding_revision: string;
  embedding_api_base: string;
  /** 服务端只回传是否已配置密钥，不回显内容。 */
  embedding_api_key_set: boolean;
  embedding_dims: number | null;
  revision_pinned: boolean;
}

/** 只读协议环境信息，对应 GET /config 的 protocol_config()。 */
export interface MemoryStudyProtocolConfig {
  protocol: string;
  tokenizer: string;
  codex_subprocess_limit: number;
  framework_contract: string[];
  official_frameworks: Record<
    string,
    { package: string; revision: string; entrypoint: string }
  >;
  artifact_root: string;
}

export function useMemoryStudyRunners() {
  return useQuery({
    queryKey: key('runners'),
    queryFn: async () =>
      (await apiClient.get<MemoryStudyRunnerConfig[]>(`${root}/runners`)).data,
  });
}

export function useMemoryStudySiteConfig() {
  return useQuery({
    queryKey: key('site-config'),
    queryFn: async () =>
      (await apiClient.get<MemoryStudySiteConfig>(`${root}/site-config`)).data,
  });
}

export function useMemoryStudyProtocolConfig() {
  return useQuery({
    queryKey: key('config'),
    queryFn: async () =>
      (await apiClient.get<MemoryStudyProtocolConfig>(`${root}/config`)).data,
  });
}

export function useCreateMemoryStudyRunner() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (payload: MemoryStudyRunnerConfig) =>
      (await apiClient.post<MemoryStudyRunnerConfig>(`${root}/runners`, payload))
        .data,
    onSuccess: () => {
      invalidate(cache);
      toast.success('模型配置已创建');
    },
  });
}

export function useUpdateMemoryStudyRunner() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (payload: MemoryStudyRunnerConfig) =>
      (
        await apiClient.put<MemoryStudyRunnerConfig>(
          `${root}/runners/${encodeURIComponent(payload.model)}/${payload.speed_mode}`,
          {
            reasoning_effort: payload.reasoning_effort,
            timeout_seconds: payload.timeout_seconds,
          },
        )
      ).data,
    onSuccess: () => {
      invalidate(cache);
      toast.success('模型配置已更新，之后创建/冻结的研究生效');
    },
  });
}

export function useDeleteMemoryStudyRunner() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Pick<MemoryStudyRunnerConfig, 'model' | 'speed_mode'>) =>
      apiClient.delete(
        `${root}/runners/${encodeURIComponent(payload.model)}/${payload.speed_mode}`,
      ),
    onSuccess: () => {
      invalidate(cache);
      toast.success('模型配置已删除，之后创建/冻结的研究回退协议默认值');
    },
  });
}

export function useSaveMemoryStudySiteConfig() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      embedding_backend: 'openai';
      embedding_model: string;
      embedding_revision: string;
      embedding_api_base: string;
      /** 留空表示保留服务端已存的密钥。 */
      embedding_api_key: string;
      embedding_dims: number | null;
    }) =>
      (
        await apiClient.put<MemoryStudySiteConfig>(
          `${root}/site-config`,
          payload,
        )
      ).data,
    onSuccess: () => {
      invalidate(cache);
      toast.success('embedding 配置已保存，新建研究时生效');
    },
  });
}

export function useDeleteMemoryStudyProject() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => apiClient.delete(`${root}/projects/${id}`),
    onSuccess: () => {
      invalidate(cache);
      toast.success('研究已删除');
    },
    onError: (error) => {
      // 409 conflict：项目处于 running，后端返回「请先暂停或终止该项目后再删除」。
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        const detail = error.response.data?.detail as
          | { message?: string }
          | undefined;
        toast.error(detail?.message ?? '请先暂停或终止该项目后再删除');
      }
    },
  });
}
