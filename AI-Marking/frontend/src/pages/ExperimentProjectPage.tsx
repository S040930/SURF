import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Download } from 'lucide-react';

import {
  type AgreementReport,
  type ChannelMetrics,
  type ExperimentProject,
  experimentExportUrl,
  useExperimentAction,
  useExperimentCalls,
  useExperimentGroups,
  useExperimentProject,
  useExperimentReport,
  useRetryExperimentCall,
} from '@/api/experiments';
import ShortHash from '@/components/r23/ShortHash';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

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

const CHANNEL_LABELS: Record<string, string> = {
  content: '内容 content',
  organization: '组织 organization',
  language: '语言 language',
};

function MetricTable({
  report,
  channel,
}: {
  report: AgreementReport;
  channel: string;
}) {
  const models = Object.keys(report.per_model).sort();
  const baseline = report.baseline.channels[channel];
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left text-muted-foreground">
          <th className="py-2 pr-3 font-medium">指标（加权）</th>
          {models.map((model) => (
            <th key={model} className="py-2 pr-3 font-medium">
              {model}
            </th>
          ))}
          <th className="py-2 font-medium">篇幅+题目基线</th>
        </tr>
      </thead>
      <tbody>
        {(
          [
            ['加权 QWK', (metrics: ChannelMetrics) => metrics.weighted_qwk],
            ['加权 MAE', (metrics: ChannelMetrics) => metrics.weighted_mae],
            ['Spearman（未加权诊断）', (metrics: ChannelMetrics) => metrics.spearman],
            ['精确一致率', (metrics: ChannelMetrics) => metrics.exact_rate],
            ['±0.5 一致率', (metrics: ChannelMetrics) => metrics.within_half_point_rate],
            ['±1 一致率', (metrics: ChannelMetrics) => metrics.within_one_point_rate],
          ] as const
        ).map(([label, accessor]) => (
          <tr key={label} className="border-b last:border-0">
            <td className="py-2 pr-3">{label}</td>
            {models.map((model) => (
              <td key={model} className="py-2 pr-3 font-mono">
                {accessor(report.per_model[model].channels[channel])?.toFixed(4)}
              </td>
            ))}
            <td className="py-2 font-mono text-muted-foreground">
              {baseline
                ? ('weighted_qwk' in baseline
                  ? label.includes('QWK')
                    ? baseline.weighted_qwk?.toFixed(4)
                    : label.includes('MAE')
                      ? baseline.weighted_mae?.toFixed(4)
                      : label.includes('精确')
                        ? baseline.exact_rate?.toFixed(4)
                        : label.includes('±0.5')
                          ? baseline.within_half_point_rate?.toFixed(4)
                          : baseline.within_one_point_rate?.toFixed(4)
                  : '—')
                : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AgreementReportView({ report }: { report: AgreementReport }) {
  const channels = Object.keys(report.per_model[Object.keys(report.per_model)[0]]?.channels ?? {});
  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">逐维人评一致性（主调用）</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          {channels.map((channel) => (
            <div key={channel} className="space-y-2">
              <p className="font-semibold">{CHANNEL_LABELS[channel] ?? channel}</p>
              <MetricTable report={report} channel={channel} />
              <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                {Object.entries(report.model_comparison[channel] ?? {}).map(
                  ([metric, diff]) => (
                    <span key={metric}>
                      {metric === 'qwk_diff' ? 'QWK 差' : 'MAE 差'}（配对 bootstrap）：
                      <span className="font-mono">
                        {diff.observed_diff.toFixed(4)} [{diff.ci_low.toFixed(4)},{' '}
                        {diff.ci_high.toFixed(4)}]
                      </span>
                    </span>
                  ),
                )}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">复测稳定性</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {Object.entries(report.retest).map(([model, retest]) => (
            <div key={model}>
              <p className="font-semibold">
                {model}（{retest.count} 篇）
              </p>
              <ul className="mt-1 space-y-1 text-muted-foreground">
                {Object.entries(retest.channels).map(([channel, metrics]) => (
                  <li key={channel}>
                    {CHANNEL_LABELS[channel] ?? channel}:完全一致率{' '}
                    <span className="font-mono">{metrics.exact_rate.toFixed(4)}</span> ·
                    平均绝对差 <span className="font-mono">{metrics.mae.toFixed(4)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">解释边界声明</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {report.statements.map((statement) => (
              <li key={statement}>{statement}</li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

export default function ExperimentProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const project = useExperimentProject(projectId ?? '');
  const groups = useExperimentGroups(projectId ?? '');
  const calls = useExperimentCalls(projectId ?? '');
  const report = useExperimentReport(projectId ?? '', true);
  const action = useExperimentAction();
  const retry = useRetryExperimentCall();
  const [tab, setTab] = useState<'monitor' | 'analysis' | 'audit'>('monitor');

  if (project.isLoading) {
    return <p className="text-sm text-muted-foreground">加载中…</p>;
  }
  if (project.isError || !project.data) {
    return <p className="text-sm text-destructive">项目加载失败。</p>;
  }
  const data: ExperimentProject = project.data;
  const progress = data.progress;
  const reportData = report.data;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="space-y-3">
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/experiments">
            <ArrowLeft className="size-4" /> 返回项目列表
          </Link>
        </Button>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold">{data.name}</h1>
          <Badge variant="outline">{data.kind === 'formal' ? '正式' : '技术试点'}</Badge>
          <Badge
            variant={
              data.status === 'completed'
                ? 'default'
                : data.status === 'attention_required'
                  ? 'destructive'
                  : 'secondary'
            }
          >
            {statusLabel(data.status)}
          </Badge>
          {data.results_embargoed && data.kind === 'formal' && (
            <Badge variant="outline">结果封存中</Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          {data.template_id} · 模型{' '}
          {data.runner_bindings.map((binding) => binding.model).join(' / ') || '—'} ·
          快照 <ShortHash value={data.manifest_sha256 ?? undefined} />
          {data.report_sha256 && (
            <>
              {' '}
              · 报告 <ShortHash value={data.report_sha256} />
            </>
          )}
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {(['monitor', 'analysis', 'audit'] as const).map((item) => (
          <Button
            key={item}
            size="sm"
            variant={tab === item ? 'default' : 'outline'}
            onClick={() => setTab(item)}
          >
            {item === 'monitor' ? '运行监控' : item === 'analysis' ? '结果分析' : '调用审计'}
          </Button>
        ))}
        <div className="ml-auto flex flex-wrap gap-2">
          {(['start', 'pause', 'resume', 'terminate'] as const)
            .filter((item) =>
              item === 'start'
                ? ['draft', 'ready', 'frozen'].includes(data.status)
                : item === 'pause'
                  ? data.status === 'running'
                  : item === 'resume'
                    ? ['paused', 'attention_required'].includes(data.status)
                    : !['completed', 'terminated'].includes(data.status),
            )
            .map((item) => (
              <Button
                key={item}
                size="sm"
                variant={item === 'terminate' ? 'destructive' : 'outline'}
                disabled={action.isPending}
                onClick={() => action.mutate({ id: data.id, action: item })}
              >
                {item === 'start'
                  ? '开始实验'
                  : item === 'pause'
                    ? '暂停'
                    : item === 'resume'
                      ? '恢复'
                      : '终止'}
              </Button>
            ))}
        </div>
      </div>

      {tab === 'monitor' && (
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">
                进度 {progress.succeeded ?? 0}/{progress.total ?? 0}
                {data.status === 'running' && '（约 2–3 天串行运行，支持暂停/恢复）'}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {(groups.data ?? []).map((group) => (
                <div key={group.id} className="flex items-center gap-3 text-sm">
                  <span className="w-40 shrink-0">
                    {group.group_key} · #{group.order_rank}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded bg-muted">
                    <div
                      className="h-full rounded bg-primary"
                      style={{
                        width: `${group.expected_calls ? (100 * group.completed_calls) / group.expected_calls : 0}%`,
                      }}
                    />
                  </div>
                  <span className="w-24 shrink-0 text-right text-muted-foreground">
                    {group.completed_calls}/{group.expected_calls}
                  </span>
                  <Badge variant="outline">{group.status}</Badge>
                </div>
              ))}
            </CardContent>
          </Card>
          {data.status === 'completed' && data.kind === 'formal' && (
            <div className="flex flex-wrap gap-2">
              {(
                [
                  ['manifest', '导出 manifest'],
                  ['results', '导出结果 CSV'],
                  ['report', '导出报告 JSON'],
                  ['figures', '导出图表 ZIP'],
                ] as const
              ).map(([kind, label]) => (
                <a key={kind} href={experimentExportUrl(data.id, kind)} download>
                  <Button size="sm" variant="outline">
                    <Download className="size-4" /> {label}
                  </Button>
                </a>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'analysis' && (
        <div className="space-y-4">
          {reportData?.results_embargoed ? (
            <Card>
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                {data.kind === 'pilot_run'
                  ? '技术试点报告已封存：只显示失败率与延迟，不显示分数。'
                  : '正式结果在报告锁定前保持封存。'}
              </CardContent>
            </Card>
          ) : reportData?.report ? (
            reportData.report.report_type === 'human_agreement' ? (
              <AgreementReportView report={reportData.report as AgreementReport} />
            ) : (
              <Card>
                <CardContent className="py-6">
                  <pre className="overflow-auto text-xs">
                    {JSON.stringify(reportData.report, null, 2)}
                  </pre>
                </CardContent>
              </Card>
            )
          ) : (
            <Card>
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                报告尚未生成。
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {tab === 'audit' && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">调用审计（最近 500 条）</CardTitle>
          </CardHeader>
          <CardContent className="overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 pr-3 font-medium">#</th>
                  <th className="py-2 pr-3 font-medium">输入哈希</th>
                  <th className="py-2 pr-3 font-medium">复测</th>
                  <th className="py-2 pr-3 font-medium">状态</th>
                  <th className="py-2 pr-3 font-medium">延迟</th>
                  <th className="py-2 font-medium">失败</th>
                </tr>
              </thead>
              <tbody>
                {(calls.data?.items ?? []).map((item) => (
                  <tr key={item.id} className="border-b last:border-0">
                    <td className="py-2 pr-3">{item.id}</td>
                    <td className="py-2 pr-3">
                      <ShortHash value={item.input_sha256} />
                    </td>
                    <td className="py-2 pr-3">{item.run_index === 1 ? '复测' : ''}</td>
                    <td className="py-2 pr-3">
                      <Badge
                        variant={
                          item.status === 'succeeded'
                            ? 'default'
                            : item.status === 'attention_required'
                              ? 'destructive'
                              : 'secondary'
                        }
                      >
                        {item.status}
                      </Badge>
                    </td>
                    <td className="py-2 pr-3">
                      {item.latency_ms != null ? `${(item.latency_ms / 1000).toFixed(1)}s` : '—'}
                    </td>
                    <td className="py-2">
                      {item.status === 'attention_required' && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            retry.mutate({ projectId: data.id, callId: item.id })
                          }
                        >
                          人工重试
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
