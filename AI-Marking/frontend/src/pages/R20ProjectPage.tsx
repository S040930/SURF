import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  useDeleteR20Project,
  useR20Failures,
  useR20Groups,
  useR20Lifecycle,
  useR20Project,
  useR20Report,
  useRetryR20Call,
  useStartR20Group,
} from '@/api/r20';
import type { R20RunGroup } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmActionDialog } from '@/components/ConfirmActionDialog';

const kindLabels: Record<string, string> = {
  formal: '8题正式实验',
  pilot_run: '2题技术试点',
};

const statusLabels: Record<string, string> = {
  failed_terminal: '失败',
  retry_pending: '待重试',
};

const CONDITIONS = ['nm', 'crm', 'arm'] as const;
const PROTOCOL_ID = 'r20-saf-official-split-2026-08-v4-8q-60m-15t';

const conditionLabels: Record<string, string> = {
  nm: 'NM',
  crm: 'CRM',
  arm: 'ARM',
};

const conditionTitles: Record<string, string> = {
  nm: 'NM 无记忆',
  crm: 'CRM 条件规则记忆',
  arm: 'ARM 抽象维度记忆',
};

const groupStatusLabels: Record<string, string> = {
  pending: '待运行',
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
};

export default function R20ProjectPage() {
  const navigate = useNavigate();
  const { projectId = '' } = useParams();
  const project = useR20Project(projectId);
  const report = useR20Report(projectId);
  const lifecycle = useR20Lifecycle();
  const retry = useRetryR20Call();
  const remove = useDeleteR20Project();
  const groups = useR20Groups(projectId);
  const startGroup = useStartR20Group(projectId);
  const item = project.data;
  const active = ['queued', 'running'].includes(item?.status ?? '');
  const failures = useR20Failures(projectId, active);
  const [retryTarget, setRetryTarget] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (!item) return <p className="text-muted-foreground">正在读取 r20 项目…</p>;
  const activeProtocol = item.protocol_id === PROTOCOL_ID;
  const action = activeProtocol && item.status === 'draft' ? 'freeze' : null;
  const progress = item.progress ?? {};
  const succeeded = progress.succeeded ?? 0;
  const total = progress.total ?? 0;
  const failed = progress.failed_terminal ?? 0;
  const retryPending = progress.retry_pending ?? 0;
  const failedRows = failures.data?.items ?? [];
  const canRetry = ['running', 'paused', 'completed_with_failures'].includes(item.status);
  const percent = total ? Math.round((succeeded / total) * 100) : 0;
  const groupRows = (groups.data ?? []).reduce<{ question: string; cells: Record<string, R20RunGroup> }[]>(
    (rows, group) => {
      const row = rows.find((r) => r.question === group.question_id);
      if (row) {
        row.cells[group.condition] = group;
      } else {
        rows.push({ question: group.question_id, cells: { [group.condition]: group } });
      }
      return rows;
    },
    [],
  );
  const anyActive = (groups.data ?? []).some((group) =>
    ['queued', 'running'].includes(group.status),
  );
  const canRunGroup = (group: R20RunGroup) =>
    group.status === 'pending' &&
    !anyActive &&
    !startGroup.isPending &&
    activeProtocol &&
    ['frozen', 'running'].includes(item.status);

  return <div className="mx-auto max-w-5xl space-y-6">
    <div><p className="text-sm text-muted-foreground">{item.protocol_id}</p><h1 className="text-3xl font-bold">{item.name}</h1><p className="mt-2 text-muted-foreground">状态：{item.status} · {kindLabels[item.kind] ?? item.kind}</p></div>
    {!activeProtocol && <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">这是历史协议项目，仅供审计；不能冻结、运行、重试或删除。</div>}
    <Card><CardHeader><CardTitle>执行进度</CardTitle></CardHeader><CardContent className="space-y-3">
      <div>
        <div className="flex justify-between text-sm text-muted-foreground">
          <span>已成功 {succeeded} / {total} 次调用</span>
          <span>{percent}%</span>
        </div>
        <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${percent}%` }} />
        </div>
      </div>
      <p className="text-sm text-muted-foreground">
        已记录 {progress.recorded ?? 0} 次调用
        {failed > 0 && <> · <span className="text-destructive">失败 {failed}</span></>}
        {retryPending > 0 && <> · 待重试 {retryPending}</>}
      </p>
    </CardContent></Card>
    <Card><CardHeader><CardTitle>冻结与执行</CardTitle></CardHeader><CardContent className="space-y-4"><p className="text-sm text-muted-foreground">数据、两模型、提示词、分析代码、轨迹、探针和调用顺序在冻结时绑定。运行中不展示调用、记忆或中间指标。NM、CRM、ARM 按题目分成独立实验组，一次只运行一个，全部完成后统一生成锁定报告。</p>{activeProtocol && <div className="flex flex-wrap gap-2">{action && <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ projectId, action })}>冻结官方数据清单</Button>}{['queued', 'running'].includes(item.status) && <Button variant="outline" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ projectId, action: 'pause' })}>暂停项目</Button>}{item.status === 'paused' && <Button variant="outline" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ projectId, action: 'resume' })}>恢复运行</Button>}<Button variant="destructive" disabled={remove.isPending} onClick={() => setConfirmDelete(true)}>删除项目</Button></div>}</CardContent></Card>
    <Card><CardHeader><CardTitle>题目 × 实验组</CardTitle></CardHeader><CardContent>
      {groupRows.length === 0 ? (
        <p className="text-sm text-muted-foreground">正在准备题目 × 实验组。每个 NM、CRM、ARM 单元格都可以单独开始，且同一时间只运行一个实验组。</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-2 pr-4 font-medium">题目</th>
                {CONDITIONS.map((condition) => (
                  <th key={condition} className="px-3 py-2 font-medium" title={conditionTitles[condition]}>
                    {conditionLabels[condition]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groupRows.map((row) => (
                <tr key={row.question} className="border-b last:border-b-0">
                  <td className="py-2 pr-4 font-mono text-xs">{row.question}</td>
                  {CONDITIONS.map((condition) => {
                    const group = row.cells[condition];
                    if (!group) return <td key={condition} className="px-3 py-2" />;
                    const groupSucceeded = group.progress.succeeded ?? 0;
                    const groupRecorded = group.progress.recorded ?? 0;
                    return (
                      <td key={condition} className="px-3 py-2 align-top">
                        <div className="flex flex-col items-start gap-1">
                          <span className="text-xs">
                            {groupStatusLabels[group.status] ?? group.status}
                            {group.status === 'running' || group.status === 'queued' ? (
                              <span className="ml-1 text-muted-foreground">
                                {groupSucceeded}/{group.expected_calls}
                              </span>
                            ) : null}
                          </span>
                          {group.status === 'pending' && (
                            <Button
                              size="sm"
                              disabled={!canRunGroup(group)}
                              onClick={() =>
                                startGroup.mutate({ questionId: row.question, condition })
                              }
                            >
                              开始 {conditionLabels[condition]} 实验
                            </Button>
                          )}
                          {group.status === 'completed' && (
                            <span className="text-xs text-muted-foreground">
                              {groupRecorded} 次调用
                            </span>
                          )}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CardContent></Card>
    <Card><CardHeader><CardTitle>失败调用与手动重试</CardTitle></CardHeader><CardContent>
      {failedRows.length === 0 ? (
        <p className="text-sm text-muted-foreground">没有失败或待重试的调用。</p>
      ) : (
        <ul className="space-y-2">
          {failedRows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
              <span className="rounded bg-muted px-2 py-0.5 text-xs">{statusLabels[row.status] ?? row.status}</span>
              <span className="text-xs text-muted-foreground">{row.kind === 'memory_update' ? '记忆更新' : '评分'} · h={row.history_count} · 轨迹{row.trajectory} · {row.condition}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{row.failure_reason ?? '无原因'}</span>
              {activeProtocol && row.status === 'failed_terminal' && canRetry && (
                <Button size="sm" variant="outline" onClick={() => setRetryTarget(row.id)} disabled={retry.isPending}>重试</Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </CardContent></Card>
    <Card><CardHeader><CardTitle>锁定报告</CardTitle></CardHeader><CardContent>{report.data?.report ? <pre className="max-h-[32rem] overflow-auto rounded bg-muted p-4 text-xs">{JSON.stringify(report.data.report, null, 2)}</pre> : <p className="text-sm text-muted-foreground">{report.data?.reason ?? '报告将在全部调用成功完成时自动生成。'}</p>}</CardContent></Card>
    <ConfirmActionDialog
      open={retryTarget !== null}
      onOpenChange={(open) => !open && setRetryTarget(null)}
      title="重试失败调用"
      description={`将重新执行第 ${retryTarget} 号调用。`}
      confirmLabel="重新执行"
      cancelLabel="取消"
      pending={retry.isPending}
      pendingLabel="正在重新入队…"
      onConfirm={() => {
        if (retryTarget === null) return;
        retry.mutate(
          { projectId, callId: retryTarget },
          {
            onSuccess: () => {
              setRetryTarget(null);
            },
          },
        );
      }}
    />
    <ConfirmActionDialog
      open={confirmDelete}
      onOpenChange={(open) => !open && setConfirmDelete(false)}
      title="删除项目"
      description={`将永久删除「${item.name}」及其全部调用、记忆和报告，且无法恢复。运行中的项目会先被终止。`}
      confirmLabel="永久删除"
      cancelLabel="取消"
      dangerous
      pending={remove.isPending}
      pendingLabel="正在删除…"
      onConfirm={() =>
        remove.mutate(projectId, {
          onSuccess: () => navigate('/research'),
        })
      }
    />
  </div>;
}
