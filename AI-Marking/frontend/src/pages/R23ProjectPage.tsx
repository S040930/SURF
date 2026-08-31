import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileLock2,
  LoaderCircle,
  LockKeyhole,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  Square,
} from 'lucide-react';

import {
  type R23AnalysisReport,
  type R23Cell,
  r23ExportUrl,
  useR23Action,
  useR23Calls,
  useR23Groups,
  useR23Project,
  useR23Report,
  useRepeatR23Project,
  useRetryR23Call,
  useR23Runtime,
} from '@/api/r23';
import McpConnectionStatus from '@/components/r23/McpConnectionStatus';
import ShortHash from '@/components/r23/ShortHash';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

type Tab = 'monitor' | 'results' | 'audit';
const DIMENSIONS = ['content', 'organization', 'language'] as const;
const DIMENSION_LABELS = {
  content: 'Content',
  organization: 'Organization',
  language: 'Language',
};

function statusLabel(status: string) {
  return (
    {
      draft: '草稿',
      ready: '准备完成',
      frozen: '准备完成',
      pending: '待运行',
      leased: '运行中',
      running: '运行中',
      paused: '已暂停',
      attention_required: '需要处理',
      analyzing: '生成报告中',
      succeeded: '已完成',
      completed: '已完成',
      terminated: '已终止',
    }[status] ?? status
  );
}

function formatNumber(value?: number | null, digits = 3) {
  return value == null || Number.isNaN(value) ? '—' : value.toFixed(digits);
}

function formatLatency(value?: number | null) {
  return value == null ? '—' : `${(value / 1000).toFixed(1)} 秒`;
}

function isAnalysisReport(value: unknown): value is R23AnalysisReport {
  return Boolean(
    value &&
    typeof value === 'object' &&
    'cells' in value &&
    Array.isArray((value as R23AnalysisReport).cells),
  );
}

function TrendChart({
  cells,
  modelNames,
}: {
  cells: R23Cell[];
  modelNames: Record<string, string>;
}) {
  const width = 860;
  const height = 330;
  const margin = { left: 58, right: 24, top: 30, bottom: 48 };
  const x = (level: number) =>
    margin.left + ((level - 1) / 4) * (width - margin.left - margin.right);
  const y = (score: number) =>
    margin.top + ((5 - score) / 4) * (height - margin.top - margin.bottom);
  const colors = ['#4f46e5', '#0f9f9a'];

  return (
    <div className="overflow-x-auto">
      <svg
        className="min-w-[700px] w-full"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-labelledby="r23-trend-title r23-trend-description"
      >
        <title id="r23-trend-title">CASE 预设等级与模型平均预测分数</title>
        <desc id="r23-trend-description">
          模型趋势线，误差棒表示 95% 聚类 bootstrap 置信区间。
        </desc>
        {[1, 2, 3, 4, 5].map((tick) => (
          <g key={tick}>
            <line
              x1={margin.left}
              x2={width - margin.right}
              y1={y(tick)}
              y2={y(tick)}
              stroke="currentColor"
              opacity="0.12"
              strokeDasharray="4 4"
            />
            <text
              x={margin.left - 12}
              y={y(tick) + 4}
              textAnchor="end"
              className="fill-muted-foreground text-[12px]"
            >
              {tick.toFixed(1)}
            </text>
            <text
              x={x(tick)}
              y={height - 18}
              textAnchor="middle"
              className="fill-muted-foreground text-[12px]"
            >
              {tick.toFixed(1)}
            </text>
          </g>
        ))}
        {cells.map((cell, index) => {
          const color = colors[index % colors.length];
          const points = cell.trend
            .map((point) => `${x(point.case_level)},${y(point.mean)}`)
            .join(' ');
          return (
            <g key={cell.model_binding_id}>
              {cell.trend.map((point) => (
                <g key={point.case_level}>
                  <line
                    x1={x(point.case_level)}
                    x2={x(point.case_level)}
                    y1={y(point.ci95[0])}
                    y2={y(point.ci95[1])}
                    stroke={color}
                    strokeWidth="1.5"
                    opacity="0.55"
                  />
                  <line
                    x1={x(point.case_level) - 4}
                    x2={x(point.case_level) + 4}
                    y1={y(point.ci95[0])}
                    y2={y(point.ci95[0])}
                    stroke={color}
                    opacity="0.55"
                  />
                  <line
                    x1={x(point.case_level) - 4}
                    x2={x(point.case_level) + 4}
                    y1={y(point.ci95[1])}
                    y2={y(point.ci95[1])}
                    stroke={color}
                    opacity="0.55"
                  />
                </g>
              ))}
              <polyline
                points={points}
                fill="none"
                stroke={color}
                strokeWidth="2.5"
                strokeLinejoin="round"
              />
              {cell.trend.map((point) => (
                <circle
                  key={point.case_level}
                  cx={x(point.case_level)}
                  cy={y(point.mean)}
                  r="3.5"
                  fill={color}
                />
              ))}
              <g transform={`translate(${margin.left + index * 300} 13)`}>
                <line
                  x1="0"
                  x2="24"
                  y1="0"
                  y2="0"
                  stroke={color}
                  strokeWidth="3"
                />
                <text x="32" y="4" className="fill-foreground text-[12px]">
                  {modelNames[cell.model_binding_id] ?? `模型 ${index + 1}`}
                </text>
              </g>
            </g>
          );
        })}
        <text
          x={width / 2}
          y={height - 2}
          textAnchor="middle"
          className="fill-muted-foreground text-[12px]"
        >
          CASE 预设等级
        </text>
        <text
          x="14"
          y={height / 2}
          textAnchor="middle"
          transform={`rotate(-90 14 ${height / 2})`}
          className="fill-muted-foreground text-[12px]"
        >
          平均预测分数
        </text>
      </svg>
    </div>
  );
}

function ResultsPanel({
  report,
  modelNames,
}: {
  report: R23AnalysisReport;
  modelNames: Record<string, string>;
}) {
  const [dimension, setDimension] =
    useState<(typeof DIMENSIONS)[number]>('organization');
  const cells = report.cells.filter((cell) => cell.dimension === dimension);
  return (
    <div className="space-y-5 print-report">
      {dimension === 'language' && (
        <div className="flex gap-3 rounded-lg border border-amber-400/50 bg-amber-50 p-4 text-sm text-amber-950 dark:bg-amber-950/30 dark:text-amber-100">
          <AlertTriangle className="mt-0.5 size-5 shrink-0" />
          <p>
            <strong>探索性分析：</strong>Language 缺少配对结构，并存在篇幅与
            prompt 混杂；不得作为确认性结论。
          </p>
        </div>
      )}
      <Card className="elevated-card">
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>目标等级与模型预测分数</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              均值与 95% 分层聚类 bootstrap CI
            </p>
          </div>
          <div
            className="flex rounded-lg bg-muted p-1"
            role="tablist"
            aria-label="结果维度"
          >
            {DIMENSIONS.map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={dimension === item}
                onClick={() => setDimension(item)}
                className={`rounded-md px-3 py-1.5 text-sm font-medium ${dimension === item ? 'bg-background text-primary shadow-sm' : 'text-muted-foreground'}`}
              >
                {DIMENSION_LABELS[item]}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          <TrendChart cells={cells} modelNames={modelNames} />
        </CardContent>
      </Card>

      <Card className="elevated-card">
        <CardHeader>
          <CardTitle>预注册指标</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="border-y bg-muted/50 text-muted-foreground">
              <tr>
                <th className="px-3 py-3 font-medium">维度</th>
                <th className="px-3 py-3 font-medium">模型</th>
                <th className="px-3 py-3 font-medium">N</th>
                <th className="px-3 py-3 font-medium">MPA [95% CI]</th>
                <th className="px-3 py-3 font-medium">QWK*</th>
                <th className="px-3 py-3 font-medium">MAE</th>
                <th className="px-3 py-3 font-medium">Spearman ρ</th>
                <th className="px-3 py-3 font-medium">判定</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {report.cells.map((cell) => (
                <tr key={`${cell.dimension}-${cell.model_binding_id}`}>
                  <td className="px-3 py-3 font-medium">
                    {DIMENSION_LABELS[cell.dimension]}
                  </td>
                  <td className="px-3 py-3">
                    {modelNames[cell.model_binding_id] ?? cell.model_binding_id}
                  </td>
                  <td className="px-3 py-3 tabular-nums">{cell.n}</td>
                  <td className="px-3 py-3 tabular-nums">
                    {formatNumber(cell.mpa)} [{formatNumber(cell.mpa_ci95[0])},{' '}
                    {formatNumber(cell.mpa_ci95[1])}]
                  </td>
                  <td className="px-3 py-3 tabular-nums">
                    {formatNumber(cell.qwk_intended_label_recovery)}
                  </td>
                  <td className="px-3 py-3 tabular-nums">
                    {formatNumber(cell.mae)}
                  </td>
                  <td className="px-3 py-3 tabular-nums">
                    {formatNumber(cell.spearman_rho)}
                  </td>
                  <td className="px-3 py-3">
                    <Badge
                      variant={
                        cell.h1_pass && cell.h2_pass ? 'default' : 'secondary'
                      }
                    >
                      {cell.h1_pass && cell.h2_pass
                        ? 'H1/H2 通过'
                        : '未同时通过'}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted-foreground">
            * QWK 仅表示 CASE 预设标签恢复，不是与人工评分的一致性。
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="elevated-card">
          <CardHeader>
            <CardTitle>Organization Rubric Selectivity</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {report.organization_selectivity.map((item) => (
              <div
                key={item.model_binding_id}
                className="flex items-center justify-between gap-4 rounded-lg border p-3 text-sm"
              >
                <div>
                  <p className="font-medium">
                    {modelNames[item.model_binding_id] ?? item.model_binding_id}
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    SI = {formatNumber(item.si)} [{formatNumber(item.ci95[0])},{' '}
                    {formatNumber(item.ci95[1])}]
                  </p>
                </div>
                <Badge variant={item.h3_pass ? 'default' : 'secondary'}>
                  {item.h3_pass ? 'H3 通过' : 'H3 未通过'}
                </Badge>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card className="elevated-card">
          <CardHeader>
            <CardTitle>解释边界</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-muted-foreground">
            <p>{report.interpretation_guardrail}</p>
            <p>
              只有某维度在两个模型上同时通过 H1 与
              H2，才可称为稳定敏感；Organization 还必须通过 H3 才可称为
              rubric-selective。
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default function R23ProjectPage() {
  const { projectId = '' } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('monitor');
  const project = useR23Project(projectId);
  const groups = useR23Groups(projectId);
  const calls = useR23Calls(projectId);
  const report = useR23Report(projectId, project.data?.status === 'completed');
  const runtime = useR23Runtime();
  const action = useR23Action();
  const repeat = useRepeatR23Project();
  const retry = useRetryR23Call();
  const item = project.data;
  const modelNames = useMemo(
    () =>
      Object.fromEntries(
        (item?.runner_bindings ?? []).map((binding) => [
          binding.id,
          `${binding.model} · ${binding.reasoning_effort} · ${binding.speed_mode} · ${binding.timeout_seconds}s`,
        ]),
      ),
    [item?.runner_bindings],
  );

  if (!item)
    return <p className="text-sm text-muted-foreground">正在读取 r23 项目…</p>;
  const reportValue = report.data?.report;
  const analysis = isAnalysisReport(reportValue) ? reportValue : null;
  const total = item.progress.total ?? 0;
  const succeeded = item.progress.succeeded ?? 0;
  const percent = total ? Math.round((succeeded / total) * 100) : 0;
  const failed = item.progress.attention_required ?? 0;
  const callItems = calls.data?.items ?? [];
  const attentionCalls = callItems.filter(
    (call) => call.status === 'attention_required',
  );
  const attentionCount = Math.max(failed, attentionCalls.length);
  const recentCalls = callItems.filter(
    (call) => call.status !== 'attention_required',
  );
  const active = item.status === 'running';
  const canTerminate = !['completed', 'terminated'].includes(item.status);
  const dimensionProgress = DIMENSIONS.map((dimension) => {
    const matching = (groups.data ?? []).filter(
      (group) => group.dimension === dimension,
    );
    const expected = matching.reduce(
      (sum, group) => sum + group.expected_calls,
      0,
    );
    const completed = matching.reduce(
      (sum, group) => sum + group.completed_calls,
      0,
    );
    return {
      dimension,
      expected,
      completed,
      percent: expected ? Math.round((completed / expected) * 100) : 0,
    };
  });

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <Link
        to="/dress"
        className="text-sm text-muted-foreground hover:underline"
      >
        ← DREsS 实验
      </Link>
      <header className="flex flex-wrap items-start justify-between gap-5">
        <div>
          <p className="text-sm text-muted-foreground">{item.protocol_id}</p>
          <h1 className="mt-2 text-3xl font-bold">{item.name}</h1>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge>{item.kind === 'pilot_run' ? '技术试点' : '正式实验'}</Badge>
            <Badge variant="secondary">{statusLabel(item.status)}</Badge>
            {item.results_embargoed && (
              <Badge variant="outline" className="gap-1">
                <LockKeyhole className="size-3" />
                结果密封
              </Badge>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2 print-hide">
          {['draft', 'ready', 'frozen'].includes(item.status) && (
            <Button
              disabled={action.isPending || !runtime.data?.runner_online}
              onClick={() =>
                window.confirm(
                  '确认开始实验？平台会自动保存运行快照，随后产生模型调用费用。',
                ) && action.mutate({ id: item.id, action: 'start' })
              }
            >
              {action.isPending ? (
                <LoaderCircle
                  className="animate-spin"
                  data-icon="inline-start"
                />
              ) : (
                <Play data-icon="inline-start" />
              )}
              {action.isPending ? '正在生成运行快照…' : '开始实验'}
            </Button>
          )}
          {active && (
            <Button
              variant="outline"
              disabled={action.isPending}
              onClick={() => action.mutate({ id: item.id, action: 'pause' })}
            >
              <Pause data-icon="inline-start" />
              暂停
            </Button>
          )}
          {item.status === 'paused' && (
            <Button
              disabled={action.isPending}
              onClick={() => action.mutate({ id: item.id, action: 'resume' })}
            >
              <Play data-icon="inline-start" />
              恢复
            </Button>
          )}
          {item.status === 'completed' && (
            <Button
              variant="outline"
              disabled={repeat.isPending}
              onClick={() =>
                window.confirm(
                  '确认创建一次独立重复运行？新项目将使用相同模型配置、Rubric 和确定性样本。',
                ) &&
                repeat.mutate(item.id, {
                  onSuccess: (response) =>
                    navigate(
                      `/dress/${(response as { data: { id: string } }).data.id}`,
                    ),
                })
              }
            >
              <RotateCcw data-icon="inline-start" />
              再次运行
            </Button>
          )}
          {canTerminate && (
            <Button
              variant="destructive"
              disabled={action.isPending}
              onClick={() =>
                window.confirm(
                  '确认终止项目？已完成的 attempt 审计仍会保留。',
                ) && action.mutate({ id: item.id, action: 'terminate' })
              }
            >
              <Square data-icon="inline-start" />
              终止
            </Button>
          )}
          {item.manifest_sha256 && (
            <Button asChild variant="outline">
              <a href={r23ExportUrl(item.id, 'manifest')}>
                <Download data-icon="inline-start" />
                导出 Manifest
              </a>
            </Button>
          )}
          {!item.results_embargoed && (
            <Button asChild>
              <a href={r23ExportUrl(item.id, 'report')}>
                <Download data-icon="inline-start" />
                导出分析包
              </a>
            </Button>
          )}
        </div>
      </header>
      <McpConnectionStatus
        runtime={runtime.data}
        isLoading={runtime.isLoading}
        isError={runtime.isError}
      />

      <div className="border-b print-hide" role="tablist" aria-label="项目详情">
        {(
          [
            { id: 'monitor', label: '运行监控' },
            { id: 'results', label: '结果分析' },
            { id: 'audit', label: '调用审计' },
          ] as const
        ).map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            aria-selected={tab === entry.id}
            onClick={() => setTab(entry.id)}
            className={`border-b-2 px-5 py-3 text-sm font-medium ${tab === entry.id ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {tab === 'monitor' && (
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
          <div className="space-y-4">
            <Card className="elevated-card">
              <CardHeader>
                <CardTitle>完成进度</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {dimensionProgress.map((entry) => (
                  <div
                    key={entry.dimension}
                    className="grid grid-cols-[110px_1fr_52px] items-center gap-3 text-sm"
                  >
                    <span>{DIMENSION_LABELS[entry.dimension]}</span>
                    <div className="h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary transition-[width]"
                        style={{ width: `${entry.percent}%` }}
                      />
                    </div>
                    <span className="text-right tabular-nums">
                      {entry.percent}%
                    </span>
                  </div>
                ))}
                <div className="grid gap-3 border-t pt-4 sm:grid-cols-3">
                  <div>
                    <p className="text-xs text-muted-foreground">成功调用</p>
                    <p className="mt-1 text-2xl font-semibold tabular-nums">
                      {succeeded.toLocaleString()}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">需要处理</p>
                    <p
                      className={`mt-1 text-2xl font-semibold tabular-nums ${attentionCount ? 'text-destructive' : ''}`}
                    >
                      {attentionCount.toLocaleString()}
                    </p>
                    {attentionCount > 0 && (
                      <Button
                        className="mt-2"
                        size="sm"
                        variant="outline"
                        onClick={() => setTab('audit')}
                      >
                        查看并重试
                      </Button>
                    )}
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">总进度</p>
                    <p className="mt-1 text-2xl font-semibold tabular-nums">
                      {percent}%
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card className="elevated-card">
              <CardHeader>
                <CardTitle>运行组（固定顺序）</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-2 sm:grid-cols-2">
                {(groups.data ?? [])
                  .filter((group) => group.expected_calls > 0)
                  .map((group) => (
                    <div
                      key={group.id}
                      className="rounded-lg border p-3 text-sm"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">
                          {
                            DIMENSION_LABELS[
                              group.dimension as keyof typeof DIMENSION_LABELS
                            ]
                          }{' '}
                          · 模型{' '}
                          {item.runner_bindings.find(
                            (binding) => binding.id === group.model_binding_id,
                          )?.position ?? '?'}
                        </span>
                        <Badge variant="secondary">
                          {group.run_index ? '复测' : '主调用'}
                        </Badge>
                      </div>
                      <p className="mt-2 text-muted-foreground">
                        {group.completed_calls}/{group.expected_calls} ·{' '}
                        {statusLabel(group.status)}
                      </p>
                    </div>
                  ))}
              </CardContent>
            </Card>
          </div>
          <aside className="space-y-4">
            <Card className="elevated-card">
              <CardHeader>
                <CardTitle className="text-base">实时运行</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">队列状态</span>
                  <span>{statusLabel(item.status)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">p50 延迟</span>
                  <span>{formatLatency(item.performance.p50_latency_ms)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">p95 延迟</span>
                  <span>{formatLatency(item.performance.p95_latency_ms)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">已完成</span>
                  <span className="tabular-nums">
                    {succeeded}/{total}
                  </span>
                </div>
              </CardContent>
            </Card>
            <Card className="elevated-card">
              <CardHeader>
                <CardTitle className="text-base">协议与审计（只读）</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div>
                  <p className="text-muted-foreground">Manifest SHA-256</p>
                  <p className="mt-1 break-all">
                    <ShortHash value={item.manifest_sha256} />
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground">Report SHA-256</p>
                  <p className="mt-1 break-all">
                    <ShortHash value={item.report_sha256} />
                  </p>
                </div>
                <div className="rounded-lg bg-muted/60 p-3 text-xs text-muted-foreground">
                  结果正式完成并锁定报告前，分析接口不会返回分数或中间效应。
                </div>
              </CardContent>
            </Card>
          </aside>
        </div>
      )}

      {tab === 'results' &&
        (analysis ? (
          <ResultsPanel report={analysis} modelNames={modelNames} />
        ) : (
          <Card className="border-primary/20 bg-primary/[0.025]">
            <CardContent className="flex min-h-80 flex-col items-center justify-center p-8 text-center">
              <span className="flex size-14 items-center justify-center rounded-full bg-primary/10 text-primary">
                <FileLock2 className="size-7" />
              </span>
              <h2 className="mt-5 text-xl font-semibold">结果保持密封</h2>
              <p className="mt-2 max-w-xl text-sm text-muted-foreground">
                正式实验的分数、趋势和中间效应只有在全部调用完成、5,000
                次分层聚类 bootstrap
                运行并锁定报告后才会显示。技术试点永不显示评分结果。
              </p>
              {reportValue && !analysis && (
                <div className="mt-5 rounded-lg border bg-background p-4 text-sm">
                  <CheckCircle2 className="mr-2 inline size-4 text-emerald-600" />
                  技术试点报告已锁定，仅含格式、失败率与延迟。
                </div>
              )}
            </CardContent>
          </Card>
        ))}

      {tab === 'audit' && (
        <div className="space-y-4">
          <div className="flex gap-3 rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
            <ShieldCheck className="size-5 shrink-0 text-primary" />
            <p>
              调用审计仅显示状态、错误类型、延迟、输入哈希和 attempt
              次数；作文、完整 prompt 与密封期评分均不返回。
            </p>
          </div>
          {attentionCalls.length > 0 && (
            <Card className="border-destructive/40 bg-destructive/[0.025]">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-destructive">
                  <AlertTriangle className="size-5" />
                  需要处理的调用（{attentionCalls.length}）
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {attentionCalls.map((call) => (
                  <div
                    key={call.id}
                    className="grid gap-3 rounded-lg border bg-background p-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-center"
                  >
                    <div className="min-w-0 space-y-1 text-sm">
                      <p className="font-medium">
                        调用 #{call.id} ·{' '}
                        {modelNames[call.model_binding_id] ?? '—'} ·{' '}
                        {call.run_index ? '复测' : '主调用'}
                      </p>
                      <p className="text-destructive">
                        {call.failure_summary ?? '调用需要人工检查。'}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        attempt {call.attempt_count} · 输入{' '}
                        <ShortHash value={call.input_sha256} />
                      </p>
                    </div>
                    <Button
                      size="sm"
                      disabled={retry.isPending}
                      onClick={() =>
                        window.confirm(
                          '人工重试会保留原 attempt 与输入哈希，确认继续？',
                        ) &&
                        retry.mutate({
                          projectId: item.id,
                          callId: call.id,
                        })
                      }
                    >
                      <RotateCcw data-icon="inline-start" />
                      {retry.isPending ? '正在重试…' : '重试此调用'}
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
          <Card className="elevated-card">
            <CardHeader>
              <CardTitle>最近调用</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto p-0">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="border-b bg-muted/50 text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 font-medium">调用</th>
                    <th className="px-4 py-3 font-medium">模型</th>
                    <th className="px-4 py-3 font-medium">阶段</th>
                    <th className="px-4 py-3 font-medium">状态</th>
                    <th className="px-4 py-3 font-medium">延迟</th>
                    <th className="px-4 py-3 font-medium">输入 SHA-256</th>
                    <th className="px-4 py-3 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {recentCalls.map((call) => (
                    <tr key={call.id}>
                      <td className="px-4 py-3 tabular-nums">#{call.id}</td>
                      <td className="px-4 py-3">
                        {modelNames[call.model_binding_id] ?? '—'}
                      </td>
                      <td className="px-4 py-3">
                        {call.run_index ? '复测' : '主调用'}
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          variant={
                            call.status === 'attention_required'
                              ? 'destructive'
                              : 'secondary'
                          }
                        >
                          {statusLabel(call.status)}
                        </Badge>
                        {call.failure_summary && (
                          <p className="mt-1 max-w-60 text-xs text-destructive">
                            {call.failure_summary}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {formatLatency(call.latency_ms)}
                      </td>
                      <td className="px-4 py-3">
                        <ShortHash value={call.input_sha256} />
                      </td>
                      <td className="px-4 py-3">—</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
