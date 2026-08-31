import { LoaderCircle, PlugZap, Unplug } from 'lucide-react';

import type { R23Runtime } from '@/api/r23';

interface McpConnectionStatusProps {
  runtime?: R23Runtime;
  isLoading?: boolean;
  isError?: boolean;
}

function formatHeartbeat(value?: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export default function McpConnectionStatus({
  runtime,
  isLoading = false,
  isError = false,
}: McpConnectionStatusProps) {
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
  if (runtime.runner_online) {
    return (
      <div
        className="flex items-center gap-3 rounded-lg border border-emerald-500/40 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200"
        role="status"
      >
        <PlugZap className="size-5 shrink-0 text-emerald-600" />
        <div>
          <p className="font-semibold">MCP 已连接</p>
          <p className="mt-0.5 text-xs">
            Worker 正常{heartbeat ? ` · 最近心跳 ${heartbeat}` : ''}
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
          请在 Codex 中启动 ai_marking_r21
          MCP；连接恢复后，已开始的实验会自动继续
          {heartbeat ? ` · 最后心跳 ${heartbeat}` : ''}。
        </p>
      </div>
    </div>
  );
}
