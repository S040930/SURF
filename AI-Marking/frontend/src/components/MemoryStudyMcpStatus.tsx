import { LoaderCircle, PlugZap, RefreshCw, Unplug } from 'lucide-react';

import type { MemoryStudyRuntime } from '@/api/memoryStudy';

interface MemoryStudyMcpStatusProps {
  runtime?: MemoryStudyRuntime;
  isLoading?: boolean;
  isError?: boolean;
}

function formatHeartbeat(value?: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export default function MemoryStudyMcpStatus({
  runtime,
  isLoading = false,
  isError = false,
}: MemoryStudyMcpStatusProps) {
  if (isLoading) {
    return (
      <div
        className="flex items-center gap-3 rounded-lg border bg-muted/40 px-4 py-3 text-sm"
        role="status"
      >
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
        <span>正在检测 MCP 连接…</span>
      </div>
    );
  }

  if (isError || !runtime) {
    return (
      <div
        className="flex items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive"
        role="status"
      >
        <Unplug className="size-5 shrink-0" />
        <div>
          <p className="font-semibold">MCP 状态检查失败</p>
          <p className="mt-0.5 text-xs">请确认后端服务正在运行。</p>
        </div>
      </div>
    );
  }

  const heartbeat = formatHeartbeat(runtime.last_heartbeat_at);
  if (runtime.online) {
    return (
      <div
        className="flex items-center gap-3 rounded-lg border border-emerald-500/40 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200"
        role="status"
      >
        <PlugZap className="size-5 shrink-0 text-emerald-600" />
        <div>
          <p className="font-semibold">MCP 已连接</p>
          <p className="mt-0.5 text-xs">
            SAF 串行执行器正常{heartbeat ? ` · 最近心跳 ${heartbeat}` : ''}
          </p>
        </div>
      </div>
    );
  }

  if (runtime.state === 'recovering') {
    return (
      <div
        className="flex items-center gap-3 rounded-lg border border-sky-400/60 bg-sky-50 px-4 py-3 text-sm text-sky-900 dark:bg-sky-950/30 dark:text-sky-200"
        role="status"
      >
        <RefreshCw className="size-5 shrink-0 animate-pulse text-sky-600" />
        <div>
          <p className="font-semibold">执行器自动恢复中</p>
          <p className="mt-0.5 text-xs">
            执行进程正在重启并接管研究，恢复后自动继续；无需人工干预
            {heartbeat ? ` · 最后心跳 ${heartbeat}` : ''}。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-3 rounded-lg border border-amber-400/60 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:bg-amber-950/30 dark:text-amber-200"
      role="status"
    >
      <Unplug className="size-5 shrink-0" />
      <div>
        <p className="font-semibold">MCP 未连接</p>
        <p className="mt-0.5 text-xs">
          请在本机启动记忆研究 worker；连接恢复后，已开始的研究会自动继续
          {heartbeat ? ` · 最后心跳 ${heartbeat}` : ''}。
        </p>
      </div>
    </div>
  );
}
