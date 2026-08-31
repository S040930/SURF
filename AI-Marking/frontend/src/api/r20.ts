import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { apiClient } from './client';
import type {
  R20FailurePage,
  R20ModelConfig,
  R20Project,
  R20ProjectKind,
  R20PromptTemplates,
  R20PromptValidationSuite,
  R20PromptVersion,
  R20Report,
  R20RunGroup,
} from './types';

export const R20_PROJECTS_KEY = ['r20-projects'] as const;
export const R20_MODELS_KEY = ['r20-model-configs'] as const;
export const R20_PROMPTS_KEY = ['r20-prompt-versions'] as const;

const projectKey = (id: string) => ['r20-project', id] as const;
const failuresKey = (id: string) => ['r20-failures', id] as const;
const groupsKey = (id: string) => ['r20-groups', id] as const;
const promptSuitesKey = (id: string) => ['r20-prompt-validation-suites', id] as const;

const REFRESH_WHILE_ACTIVE = 3000;

export function useR20Projects() {
  return useQuery({
    queryKey: R20_PROJECTS_KEY,
    queryFn: async () => (await apiClient.get<R20Project[]>('/r20/projects')).data,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((item) =>
        ['queued', 'running'].includes(item.status),
      )
        ? REFRESH_WHILE_ACTIVE
        : false,
  });
}

export function useR20Project(id: string) {
  return useQuery({
    queryKey: projectKey(id),
    queryFn: async () => (await apiClient.get<R20Project>(`/r20/projects/${id}`)).data,
    enabled: Boolean(id),
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.status ?? '') ? REFRESH_WHILE_ACTIVE : false,
  });
}

export function useR20Failures(id: string, active: boolean) {
  return useQuery({
    queryKey: failuresKey(id),
    queryFn: async () =>
      (await apiClient.get<R20FailurePage>(`/r20/projects/${id}/failures`)).data,
    enabled: Boolean(id),
    refetchInterval: active ? REFRESH_WHILE_ACTIVE : false,
  });
}

export function useRetryR20Call() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ projectId, callId }: { projectId: string; callId: number }) =>
      (await apiClient.post<R20Project>(`/r20/projects/${projectId}/calls/${callId}/retry`)).data,
    onSuccess: (_, variables) => {
      cache.invalidateQueries({ queryKey: R20_PROJECTS_KEY });
      cache.invalidateQueries({ queryKey: projectKey(variables.projectId) });
      cache.invalidateQueries({ queryKey: failuresKey(variables.projectId) });
      toast.success('失败调用已重新入队');
    },
  });
}

export interface R20ModelConfigPatch {
  name: string;
  config_json: Record<string, unknown>;
  api_key?: string;
}

export function useR20ModelConfigs() {
  return useQuery({
    queryKey: R20_MODELS_KEY,
    queryFn: async () => (await apiClient.get<R20ModelConfig[]>('/r20/model-configs')).data,
  });
}

export function useCreateR20ModelConfig() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (payload: R20ModelConfigPatch) =>
      (await apiClient.post<R20ModelConfig>('/r20/model-configs', payload)).data,
    onSuccess: () => { cache.invalidateQueries({ queryKey: R20_MODELS_KEY }); toast.success('r20 模型配置已创建'); },
  });
}

export function useUpdateR20ModelConfig() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: string; payload: R20ModelConfigPatch }) =>
      (await apiClient.put<R20ModelConfig>(`/r20/model-configs/${id}`, payload)).data,
    onSuccess: () => { cache.invalidateQueries({ queryKey: R20_MODELS_KEY }); toast.success('r20 模型配置已更新'); },
  });
}

export function useCloneR20ModelConfig() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      (await apiClient.post<R20ModelConfig>(`/r20/model-configs/${id}/clone`)).data,
    onSuccess: () => { cache.invalidateQueries({ queryKey: R20_MODELS_KEY }); toast.success('已派生 r20 草稿'); },
  });
}

export function useDeleteR20ModelConfig() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/r20/model-configs/${id}`),
    onSuccess: () => { cache.invalidateQueries({ queryKey: R20_MODELS_KEY }); toast.success('r20 模型配置已删除'); },
  });
}

export function useFreezeR20ModelConfig() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      (await apiClient.post<R20ModelConfig>(`/r20/model-configs/${id}/freeze`)).data,
    onSuccess: () => { cache.invalidateQueries({ queryKey: R20_MODELS_KEY }); toast.success('r20 模型配置已冻结'); },
  });
}

export function useR20PromptVersions() {
  return useQuery({
    queryKey: R20_PROMPTS_KEY,
    queryFn: async () => (await apiClient.get<R20PromptVersion[]>('/r20/prompt-versions')).data,
  });
}

export function useCreateR20PromptVersion() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { name: string; templates: R20PromptTemplates }) =>
      (await apiClient.post<R20PromptVersion>('/r20/prompt-versions', { name: payload.name, ...payload.templates })).data,
    onSuccess: () => { cache.invalidateQueries({ queryKey: R20_PROMPTS_KEY }); toast.success('r20 提示词版本已保存'); },
  });
}

export function useR20PromptValidationSuites(versionId: string) {
  return useQuery({
    queryKey: promptSuitesKey(versionId),
    queryFn: async () =>
      (await apiClient.get<R20PromptValidationSuite[]>(
        `/r20/prompt-versions/${versionId}/validation-suites`,
      )).data,
    enabled: Boolean(versionId),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((suite) =>
        ['queued', 'running'].includes(suite.status),
      )
        ? REFRESH_WHILE_ACTIVE
        : false,
  });
}

export function useCreateR20PromptValidationSuite() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ versionId, model_config_ids }: {
      versionId: string;
      model_config_ids: string[];
    }) => (await apiClient.post<R20PromptValidationSuite>(
      `/r20/prompt-versions/${versionId}/validation-suites`,
      { model_config_ids },
    )).data,
    onSuccess: (_, variables) => {
      cache.invalidateQueries({ queryKey: promptSuitesKey(variables.versionId) });
      toast.success('48-call validation suite 已进入队列');
    },
  });
}

export function useFreezeR20PromptVersion() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      (await apiClient.post<R20PromptVersion>(`/r20/prompt-versions/${id}/freeze`)).data,
    onSuccess: (_, id) => {
      cache.invalidateQueries({ queryKey: R20_PROMPTS_KEY });
      cache.invalidateQueries({ queryKey: promptSuitesKey(id) });
      toast.success('r20 v4 提示词版本已冻结');
    },
  });
}

export function useDeleteR20PromptVersion() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/r20/prompt-versions/${id}`),
    onSuccess: () => {
      cache.invalidateQueries({ queryKey: R20_PROMPTS_KEY });
      toast.success('r20 提示词版本已删除');
    },
  });
}

export function useCreateR20Project() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      name: string;
      kind: R20ProjectKind;
      model_config_ids: string[];
      prompt_version_id: string;
      pilot_project_id?: string;
    }) => (await apiClient.post<R20Project>('/r20/projects', payload)).data,
    onSuccess: () => {
      cache.invalidateQueries({ queryKey: R20_PROJECTS_KEY });
      toast.success('r20 项目已创建');
    },
  });
}

export function useR20Lifecycle() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ projectId, action }: { projectId: string; action: 'freeze' | 'terminate' | 'pause' | 'resume' }) =>
      (await apiClient.post<R20Project>(`/r20/projects/${projectId}/${action}`)).data,
    onSuccess: (_, variables) => {
      cache.invalidateQueries({ queryKey: R20_PROJECTS_KEY });
      cache.invalidateQueries({ queryKey: projectKey(variables.projectId) });
      toast.success('项目状态已更新');
    },
  });
}

export function useR20Groups(projectId: string) {
  return useQuery({
    queryKey: groupsKey(projectId),
    queryFn: async () =>
      (await apiClient.get<R20RunGroup[]>(`/r20/projects/${projectId}/groups`)).data,
    enabled: Boolean(projectId),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((group) =>
        ['queued', 'running'].includes(group.status),
      )
        ? REFRESH_WHILE_ACTIVE
        : false,
  });
}

export function useStartR20Group(projectId: string) {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({ questionId, condition }: { questionId: string; condition: string }) =>
      (await apiClient.post<R20RunGroup>(
        `/r20/projects/${projectId}/groups/${questionId}/${condition}/start`,
      )).data,
    onSuccess: (_, variables) => {
      cache.invalidateQueries({ queryKey: groupsKey(projectId) });
      cache.invalidateQueries({ queryKey: projectKey(projectId) });
      toast.success(`已启动 ${variables.condition.toUpperCase()} 组`);
    },
  });
}

export function useDeleteR20Project() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (projectId: string) =>
      apiClient.delete(`/r20/projects/${projectId}`),
    onSuccess: () => {
      cache.invalidateQueries({ queryKey: R20_PROJECTS_KEY });
      toast.success('项目已删除');
    },
  });
}

export function useR20Report(projectId: string) {
  return useQuery({
    queryKey: ['r20-report', projectId],
    queryFn: async () => (await apiClient.get<R20Report>(`/r20/projects/${projectId}/report`)).data,
    enabled: Boolean(projectId),
  });
}
