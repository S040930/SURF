import { Link, useParams } from 'react-router-dom';

import {
  useR21Action,
  useR21Failures,
  useR21Groups,
  useR21Project,
  useR21Report,
  useRetryR21Call,
  useStartR21Project,
} from '@/api/r21';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const PROJECT_PATH = '/Users/mac/Desktop/SURF/AI-Marking';

function formatDuration(seconds?: number | null) {
  if (seconds == null) return '等待首批实测数据';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.ceil((seconds % 3600) / 60);
  return hours ? `${hours} 小时 ${minutes} 分` : `${minutes} 分钟`;
}

function formatLatency(milliseconds?: number | null) {
  if (milliseconds == null) return '—';
  return `${(milliseconds / 1000).toFixed(1)} 秒`;
}

export default function R21ProjectPage() {
  const { projectId = '' } = useParams();
  const project = useR21Project(projectId);
  const groups = useR21Groups(projectId);
  const failures = useR21Failures(projectId);
  const report = useR21Report(projectId);
  const action = useR21Action();
  const start = useStartR21Project();
  const retry = useRetryR21Call();
  const item = project.data;

  if (!item) {
    return <p className="text-muted-foreground">正在读取 r21 项目…</p>;
  }

  const isWaiting = item.status === 'waiting_for_runner';
  const isActive = ['running', 'waiting_for_runner'].includes(item.status);
  const canTerminate = ![
    'completed',
    'completed_with_failures',
    'terminated',
  ].includes(item.status);
  const activeGroup = item.runtime.active_group;
  const runtimeVersion = String(
    item.runtime.runtime?.cli_version ?? '尚未连接',
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <Link
        className="text-sm text-muted-foreground hover:underline"
        to="/research"
      >
        ← 研究项目
      </Link>

      <div>
        <p className="text-sm text-muted-foreground">{item.protocol_id}</p>
        <h1 className="text-3xl font-bold">{item.name}</h1>
        <p className="mt-2 text-muted-foreground">状态：{item.status}</p>
      </div>

      {isWaiting && (
        <Card className="border-amber-500/50 bg-amber-500/5">
          <CardContent className="space-y-2 p-4 text-sm">
            <strong>项目已排队，正在等待 Codex MCP Runner。</strong>
            <p className="text-muted-foreground">
              在 Codex App 中打开并信任项目 {PROJECT_PATH}，保持任务连接即可。
              MCP 连接后会自动运行，不需要发送任何 MCP 命令。
            </p>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>生命周期</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {item.status === 'draft' && (
            <Button
              disabled={action.isPending}
              onClick={() => action.mutate({ id: item.id, action: 'freeze' })}
            >
              冻结项目
            </Button>
          )}
          {item.status === 'frozen' && (
            <Button
              disabled={start.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    '确认运行完整项目？所有实验组将按冻结顺序自动串行执行。',
                  )
                ) {
                  start.mutate(item.id);
                }
              }}
            >
              运行完整项目
            </Button>
          )}
          {isActive && (
            <Button
              disabled={action.isPending}
              onClick={() => action.mutate({ id: item.id, action: 'pause' })}
            >
              暂停
            </Button>
          )}
          {item.status === 'paused' && (
            <Button
              disabled={action.isPending}
              onClick={() => action.mutate({ id: item.id, action: 'resume' })}
            >
              恢复并继续
            </Button>
          )}
          {canTerminate && (
            <Button
              variant="destructive"
              disabled={action.isPending}
              onClick={() => {
                if (window.confirm('确认终止项目？')) {
                  action.mutate({ id: item.id, action: 'terminate' });
                }
              }}
            >
              终止
            </Button>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">MCP Runner</p>
            <p className="mt-1 font-semibold">
              {item.runtime.runner_online ? '已连接' : '未连接'}
            </p>
            <p className="mt-1 truncate text-xs text-muted-foreground">
              {runtimeVersion}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">调用进度</p>
            <p className="mt-1 font-semibold">
              {item.progress.succeeded ?? 0} / {item.progress.total ?? 0}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              失败 {item.progress.failed_terminal ?? 0}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">吞吐量</p>
            <p className="mt-1 font-semibold">
              {item.performance.throughput_calls_per_hour ?? '—'} 调用/小时
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              p50 {formatLatency(item.performance.p50_latency_ms)} · p95{' '}
              {formatLatency(item.performance.p95_latency_ms)}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">预计剩余时间</p>
            <p className="mt-1 font-semibold">
              {formatDuration(item.performance.estimated_remaining_seconds)}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              按本项目实测平均延迟估算
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>当前执行</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {activeGroup ? (
            <p>
              {activeGroup.question_id} · {activeGroup.condition.toUpperCase()}{' '}
              · {activeGroup.status} · {activeGroup.progress.recorded ?? 0}/
              {activeGroup.expected_calls}
            </p>
          ) : (
            <p className="text-muted-foreground">当前没有正在执行的实验组。</p>
          )}
        </CardContent>
      </Card>

      {item.status === 'attention_required' && (
        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle>需要处理的失败</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {(failures.data?.items ?? []).map((call) => (
              <div
                className="flex flex-wrap items-center justify-between gap-3 rounded border p-3"
                key={call.id}
              >
                <div className="text-sm">
                  <p className="font-medium">
                    调用 #{call.id} · {call.question_id} ·{' '}
                    {call.condition.toUpperCase()} · 轨迹 {call.trajectory}
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    {call.failure_reason || '未提供失败摘要'}
                  </p>
                </div>
                <Button
                  disabled={retry.isPending}
                  onClick={() => {
                    if (window.confirm('确认重试此调用并继续完整项目？')) {
                      retry.mutate({ projectId: item.id, callId: call.id });
                    }
                  }}
                >
                  重试并继续
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>实验组（冻结顺序，只读）</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {(groups.data ?? []).map((group) => (
            <div className="rounded border p-3 text-sm" key={group.id}>
              {group.question_id} · {group.condition.toUpperCase()} ·{' '}
              {group.status} · {group.progress.recorded ?? 0}/
              {group.expected_calls}
            </div>
          ))}
        </CardContent>
      </Card>

      {report.data?.report && (
        <Card>
          <CardHeader>
            <CardTitle>冻结报告</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="break-all font-mono text-xs text-muted-foreground">
              SHA-256：{report.data.report_sha256}
            </p>
            <pre className="max-h-96 overflow-auto rounded bg-muted p-3 text-xs">
              {JSON.stringify(report.data.report, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>审计与资源</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          冻结 Runner 记录 model、reasoning effort、Codex CLI 版本、可执行文件
          SHA-256 与隔离参数。全局 MCP 租约保证只有一个 Codex
          子进程执行调用；数据和日志继续按调用流式、有界处理。
        </CardContent>
      </Card>
    </div>
  );
}
