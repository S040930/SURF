import axios, { type AxiosError } from 'axios';
import { QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
});

interface ApiValidationError {
  loc?: Array<string | number>;
  msg?: string;
}

interface ApiErrorResponse {
  detail?: string | string[] | ApiValidationError[] | { message?: string };
}

/**
 * 从 AxiosError 中提取面向用户的友好错误信息。
 * 优先取 FastAPI 标准 detail 字段，回退到 error.message。
 * 兼容 FastAPI 校验错误的数组 detail（[{loc, msg}, ...]）。
 */
function resolveErrorMessage(error: AxiosError<ApiErrorResponse>): string {
  const status = error.response?.status;
  const detail = error.response?.data?.detail;

  // 无响应（网络错误 / 超时）
  if (!error.response) {
    if (error.code === 'ECONNABORTED') {
      return '请求超时，请检查网络后重试';
    }
    return '网络连接异常，请检查网络后重试';
  }

  let detailMessage: string | undefined;
  if (typeof detail === 'string') {
    detailMessage = detail;
  } else if (Array.isArray(detail)) {
    const first = detail[0];
    if (first && typeof first === 'object' && 'msg' in first) {
      const field = Array.isArray(first.loc)
        ? first.loc.filter((p) => p !== 'body').join('.')
        : '';
      detailMessage = field ? `${field}: ${first.msg}` : first.msg;
    }
  } else if (detail && typeof detail === 'object' && 'message' in detail) {
    detailMessage = detail.message;
  }

  // 按状态码分类给出语义化文案
  if (status === 401) {
    return detailMessage ?? '请求未授权';
  }
  if (status === 403) {
    return '没有权限执行此操作';
  }

  if (status === 404) {
    return detailMessage ?? '请求的资源不存在';
  }
  if (status && status >= 500) {
    return detailMessage ?? '服务器异常，请稍后重试';
  }

  return detailMessage ?? error.message ?? '请求失败，请重试';
}

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorResponse>) => {
    const message = resolveErrorMessage(error);
    // 不记录请求体，避免密钥配置失败时把 API Key 写入浏览器控制台。
    console.error('[API Error]', {
      method: error.config?.method,
      url: error.config?.url,
      status: error.response?.status,
      code: error.code,
      message,
    });

    toast.error(message);

    return Promise.reject(error);
  },
);

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        const status = axios.isAxiosError(error) ? error.response?.status : undefined;
        return failureCount < 1 && (!status || status >= 500);
      },
      retryDelay: 1000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      // mutation 错误统一由响应拦截器 toast，页面无需重复处理
      retry: 0,
    },
  },
});
