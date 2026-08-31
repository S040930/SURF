import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { CheckCircle2, FlaskConical } from 'lucide-react';

import {
  type R23Kind,
  type R23Project,
  useCreateR23Project,
  useDeleteR23Project,
  useR23DataStatus,
  useR23Projects,
  useR23Rubrics,
  useR23Runners,
  useR23Runtime,
} from '@/api/r23';
import McpConnectionStatus from '@/components/r23/McpConnectionStatus';
import ShortHash from '@/components/r23/ShortHash';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const PROTOCOL_ID = 'r23-dress-case-rubric-sensitivity-2026-08-v1';

function statusLabel(status: string) {
  return (
    {
      draft: '草稿',
      ready: '准备完成',
      frozen: '准备完成',
      running: '运行中',
      paused: '已暂停',
      attention_required: '需要处理',
      analyzing: '生成报告中',
      completed: '已完成',
      terminated: '已终止',
    }[status] ?? status
  );
}

export default function R23ProjectsPage() {
  const navigate = useNavigate();
  const data = useR23DataStatus();
  const runners = useR23Runners();
  const rubrics = useR23Rubrics();
  const projects = useR23Projects();
  const runtime = useR23Runtime();
  const create = useCreateR23Project();
  const remove = useDeleteR23Project();
  const [name, setName] = useState('');
  const [kind, setKind] = useState<R23Kind>('pilot_run');
  const [runnerId, setRunnerId] = useState('');
  const [rubricId, setRubricId] = useState('');
  const [pilotId, setPilotId] = useState('');
  const [confirmed, setConfirmed] = useState(false);

  const availableRunners = runners.data ?? [];
  const availableRubrics = rubrics.data ?? [];
  const completedPilots = (projects.data ?? []).filter(
    (item) =>
      item.kind === 'pilot_run' &&
      item.status === 'completed' &&
      (!runnerId || item.runner_bindings[0]?.runner_config_id === runnerId) &&
      (!rubricId || item.rubric_id === rubricId),
  );
  const selectedRubric = availableRubrics.find((item) => item.id === rubricId);
  const selectedRunner = availableRunners.find((item) => item.id === runnerId);
  const canCreate = Boolean(
    name.trim() &&
    data.data?.ready &&
    selectedRunner &&
    selectedRubric &&
    confirmed &&
    (kind === 'pilot_run' || pilotId),
  );
  const calls =
    kind === 'pilot_run' ? '约 175 个评估槽位' : '最多 2,535 个评估槽位';

  const gates = useMemo(
    () => [
      {
        ok: data.data?.ready === true,
        label: '三份数据文件哈希与九档分布通过',
      },
      {
        ok: Boolean(selectedRunner),
        label: '单个模型配置已选择',
      },
      { ok: Boolean(selectedRubric), label: 'Rubric 已选择' },
      { ok: confirmed, label: '受限数据处理授权已确认' },
    ],
    [confirmed, data.data?.ready, selectedRunner, selectedRubric],
  );

  return (
    <div className="mx-auto max-w-7xl space-y-7">
      <header className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div>
          <p className="text-sm font-medium text-primary">
            DREsS 实验 / 新建项目
          </p>
          <h1 className="mt-2 text-3xl font-bold">创建 DREsS r23 实验</h1>
          <p className="mt-2 text-muted-foreground">
            不需要冻结。创建项目后，点击“开始实验”即可；平台会在开始时自动保存完整运行快照。
          </p>
        </div>
        <McpConnectionStatus
          runtime={runtime.data}
          isLoading={runtime.isLoading}
          isError={runtime.isError}
        />
      </header>

      <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-4">
          <Card className="elevated-card">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-3 text-lg">
                <span className="flex size-7 items-center justify-center rounded-md bg-primary text-sm text-primary-foreground">
                  1
                </span>
                数据校查
              </CardTitle>
            </CardHeader>
            <CardContent>
              {data.isLoading ? (
                <p className="text-sm text-muted-foreground">
                  正在流式校验数据…
                </p>
              ) : data.data?.ready ? (
                <div className="overflow-x-auto rounded-lg border">
                  <table className="w-full min-w-[680px] text-left text-sm">
                    <thead className="bg-muted/60 text-muted-foreground">
                      <tr>
                        <th className="px-4 py-3 font-medium">文件</th>
                        <th className="px-4 py-3 font-medium">SHA-256</th>
                        <th className="px-4 py-3 font-medium">记录数</th>
                        <th className="px-4 py-3 font-medium">九档标签</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {data.data.datasets.map((item) => (
                        <tr key={item.dimension}>
                          <td className="px-4 py-3 font-medium">
                            {item.filename}
                          </td>
                          <td className="px-4 py-3">
                            <ShortHash value={item.sha256} />
                          </td>
                          <td className="px-4 py-3 tabular-nums">
                            {item.rows.toLocaleString()}
                          </td>
                          <td className="px-4 py-3 text-emerald-700 dark:text-emerald-400">
                            <span className="inline-flex items-center gap-1">
                              <CheckCircle2 className="size-4" />
                              完整
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
                  数据门禁未通过：{data.data?.error ?? '无法读取数据状态'}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="elevated-card">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-3 text-lg">
                <span className="flex size-7 items-center justify-center rounded-md bg-primary text-sm text-primary-foreground">
                  2
                </span>
                选择单个模型配置
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="block space-y-2 text-sm font-medium">
                Runner 配置
                <Select value={runnerId} onValueChange={setRunnerId}>
                  <SelectTrigger>
                    <SelectValue placeholder="选择 Runner" />
                  </SelectTrigger>
                  <SelectContent>
                    {availableRunners.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name} · {item.model} · {item.reasoning_effort} ·{' '}
                        {item.speed_mode} · {item.timeout_seconds}s
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <p className="text-xs text-muted-foreground">
                每个项目只绑定一个
                Runner；思考程度、运行速度和超时均来自所选配置，每篇作文使用全新临时会话。
              </p>
            </CardContent>
          </Card>

          <Card className="elevated-card">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-3 text-lg">
                <span className="flex size-7 items-center justify-center rounded-md bg-primary text-sm text-primary-foreground">
                  3
                </span>
                Rubric 与项目类型
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <label className="space-y-2 text-sm font-medium">
                实验名称
                <Input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="例如：r23 技术试点 01"
                />
              </label>
              <label className="space-y-2 text-sm font-medium">
                项目类型
                <Select
                  value={kind}
                  onValueChange={(value) => setKind(value as R23Kind)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="pilot_run">技术试点</SelectItem>
                    <SelectItem value="formal">正式实验</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label className="space-y-2 text-sm font-medium md:col-span-2">
                Rubric 版本
                <Select value={rubricId} onValueChange={setRubricId}>
                  <SelectTrigger>
                    <SelectValue placeholder="选择 Rubric" />
                  </SelectTrigger>
                  <SelectContent>
                    {availableRubrics.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              {kind === 'formal' && (
                <label className="space-y-2 text-sm font-medium md:col-span-2">
                  匹配的已完成技术试点
                  <Select value={pilotId} onValueChange={setPilotId}>
                    <SelectTrigger>
                      <SelectValue placeholder="正式实验必须绑定试点" />
                    </SelectTrigger>
                    <SelectContent>
                      {completedPilots.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
              )}
              {selectedRubric && (
                <div className="md:col-span-2 rounded-lg bg-muted/60 p-3 text-sm">
                  <span className="text-muted-foreground">
                    Rubric SHA-256：
                  </span>{' '}
                  <ShortHash value={selectedRubric.rubric_sha256} />
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="elevated-card">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-3 text-lg">
                <span className="flex size-7 items-center justify-center rounded-md bg-primary text-sm text-primary-foreground">
                  4
                </span>
                确认实验协议
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <dl className="grid gap-3 rounded-lg border p-4 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-muted-foreground">协议 ID</dt>
                  <dd className="mt-1 font-medium break-all">{PROTOCOL_ID}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">固定抽样种子</dt>
                  <dd className="mt-1 font-medium tabular-nums">20260830</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">技术试点</dt>
                  <dd className="mt-1 font-medium">约 175 个评估槽位</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">正式实验</dt>
                  <dd className="mt-1 font-medium">最多 2,535 个评估槽位</dd>
                </div>
              </dl>
              <div className="rounded-lg border border-amber-400/50 bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                作文全文和完整题目不会进入公开导出、应用日志或异常信息；结果在正式报告锁定前保持密封。
              </div>
              <label className="flex cursor-pointer items-start gap-3 text-sm">
                <input
                  className="mt-1 size-4 accent-[var(--primary)]"
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                <span>
                  我已获得使用所选模型服务处理该受限数据的授权，并将遵守研究伦理与保密要求。
                </span>
              </label>
            </CardContent>
          </Card>
        </div>

        <Card className="sticky top-20 min-w-0 elevated-card">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="size-5 text-primary" />
              开始前检查
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <ul className="space-y-3 text-sm">
              {gates.map((gate) => (
                <li key={gate.label} className="flex items-start gap-2">
                  <CheckCircle2
                    className={`mt-0.5 size-4 shrink-0 ${gate.ok ? 'text-emerald-600' : 'text-muted-foreground/50'}`}
                  />
                  <span className={gate.ok ? '' : 'text-muted-foreground'}>
                    {gate.label}
                  </span>
                </li>
              ))}
            </ul>
            <div className="border-t pt-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                只读摘要
              </p>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">类型</dt>
                  <dd>{kind === 'pilot_run' ? '技术试点' : '正式实验'}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">调用规模</dt>
                  <dd className="text-right">{calls}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">模型配置</dt>
                  <dd>1</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">结果盲化</dt>
                  <dd>报告锁定前</dd>
                </div>
              </dl>
            </div>
            <Button
              className="w-full"
              disabled={!canCreate || create.isPending}
              onClick={() => {
                if (!selectedRunner) return;
                create.mutate(
                  {
                    name: name.trim(),
                    kind,
                    runner_config_id: selectedRunner.id,
                    rubric_id: rubricId,
                    ...(kind === 'formal' ? { pilot_project_id: pilotId } : {}),
                    data_processing_confirmed: true,
                  },
                  {
                    onSuccess: (response) => {
                      const project = (response as { data: R23Project }).data;
                      navigate(`/dress/${project.id}`);
                    },
                  },
                );
              }}
            >
              <FlaskConical data-icon="inline-start" />
              创建项目
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              创建项目不会产生模型费用；点击开始实验后才会调用模型。
            </p>
          </CardContent>
        </Card>
      </div>

      <section
        aria-labelledby="existing-projects"
        className="space-y-3 border-t pt-6"
      >
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 id="existing-projects" className="text-xl font-semibold">
              已有 r23 项目
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              历史 r22 项目仍从旧入口访问。
            </p>
          </div>
          <Button asChild variant="outline">
            <Link to="/research">打开 r22 历史入口</Link>
          </Button>
        </div>
        {(projects.data ?? []).length === 0 ? (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
            <FlaskConical className="mx-auto mb-3 size-8 opacity-50" />
            尚无 r23 项目。
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {(projects.data ?? []).map((item) => (
              <Card key={item.id} className="elevated-card">
                <CardContent className="flex items-start justify-between gap-3 p-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link
                        className="truncate font-semibold hover:underline"
                        to={`/dress/${item.id}`}
                      >
                        {item.name}
                      </Link>
                      <Badge variant="secondary">
                        {statusLabel(item.status)}
                      </Badge>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">
                      {item.kind === 'pilot_run' ? '技术试点' : '正式实验'} ·{' '}
                      {item.progress.succeeded ?? 0}/{item.progress.total ?? 0}{' '}
                      逻辑调用
                    </p>
                  </div>
                  {['draft', 'terminated'].includes(item.status) && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={remove.isPending}
                      onClick={() =>
                        window.confirm('删除此项目草稿？') &&
                        remove.mutate(item.id)
                      }
                    >
                      删除
                    </Button>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
