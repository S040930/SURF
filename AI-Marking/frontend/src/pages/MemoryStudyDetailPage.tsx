import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ClipboardCheck, Download, Eye, Gauge, GitCompareArrows, LockKeyhole, Pause, Play, RotateCcw, Server, ShieldAlert, Trash2 } from 'lucide-react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import {
  useRetryAllFailedMemoryStudyCalls,
  memoryStudyExportUrl,
  useDeleteMemoryStudyProject,
  useMemoryStudyAction,
  useMemoryStudyAuditProject,
  useMemoryStudyCalls,
  useMemoryStudyProject,
  useMemoryStudyQuestionAction,
  useMemoryStudyPreflight,
  useMemoryStudyReport,
  useMemoryStudyRuntime,
  useMemoryStudyStore,
  useMemoryStudyStores,
  useRetryMemoryStudyCall,
  useRetryMemoryStudyQuestionCalls,
  useRecoverMemoryStudyStore,
  type MemoryCall,
  type MemoryStudyProject,
  type MemoryStore,
} from '@/api/memoryStudy';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmActionDialog } from '@/components/ConfirmActionDialog';

type DetailTab = 'monitor' | 'memory' | 'scores' | 'report' | 'audit';

const STATUS_LABELS: Record<string, string> = {
  ready: '待冻结', frozen: '已冻结', running: '运行中', paused: '已暂停',
  attention_required: '需要处理', completed: '已完成', terminated: '已终止',
};

const CONDITION_LABELS: Record<string, string> = {
  no_memory: '无记忆', retrieval_full: '检索·完整', mem0_full: 'Mem0·完整',
  amem_full: 'A-MEM·完整', retrieval_no_feedback: '检索·去反馈',
  mem0_no_feedback: 'Mem0·去反馈', amem_no_feedback: 'A-MEM·去反馈',
};

const FAILURE_LABELS: Record<string, string> = {
  store_failed: '记忆库已失败',
  runner_execution_error: '执行器错误',
  runner_unavailable: '执行器不可用',
  runner_interrupted: '执行被中断',
  runtime_drift_error: '运行时指纹漂移',
  lease_expired: '租约过期',
  framework_unavailable: '记忆框架不可用',
  memory_evidence_invalid: '记忆案例无法投影为评分证据',
  invalid_output_or_runtime_error: '输出无效或运行错误',
  authentication_error: '登录或鉴权失败',
  schema_error: 'Schema 不符合要求',
  model_configuration_error: '模型配置错误',
  dependency_error: '依赖版本错误',
  snapshot_validation_error: '快照校验失败',
  output_validation_error: '模型输出校验失败',
  runner_transient: '执行器瞬态错误（已自动重试）',
  framework_transient: '记忆框架瞬态错误（已自动重试）',
  invalid_preflight_output: '预检输出校验失败',
  preflight_error: '预检错误',
};

function failureLabel(code?: string | null) {
  if (!code) return 'unknown';
  return FAILURE_LABELS[code] ?? code;
}

function shortHash(value?: string | null) {
  return value ? `${value.slice(0, 12)}…` : '—';
}

function RetryCountdown({ retryAfter }: { retryAfter: string }) {
  const target = new Date(retryAfter).getTime();
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, Math.ceil((target - Date.now()) / 1000)),
  );

  useEffect(() => {
    const update = () =>
      setRemaining(Math.max(0, Math.ceil((target - Date.now()) / 1000)));
    update();
    const timer = window.setInterval(update, 1_000);
    return () => window.clearInterval(timer);
  }, [target]);

  return (
    <p className="mt-1 text-amber-700">
      {remaining > 0 ? `重试倒计时 ${remaining}s` : '即将重试'}
    </p>
  );
}

function statusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'completed') return 'default';
  if (status === 'attention_required') return 'destructive';
  if (status === 'running') return 'secondary';
  return 'outline';
}

function ProgressBar({ value, total }: { value: number; total: number }) {
  const percent = total ? Math.min(100, (value / total) * 100) : 0;
  return (
    <div className="h-2 overflow-hidden rounded-full bg-muted">
      <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
    </div>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {detail ? <p className="mt-1 text-xs text-muted-foreground">{detail}</p> : null}
    </div>
  );
}

function QuestionShardPanel({ project }: { project: MemoryStudyProject }) {
  const action = useMemoryStudyQuestionAction();
  const retryFailed = useRetryMemoryStudyQuestionCalls();
  const questions = project.questions ?? [];
  if (!questions.length) return null;
  const run = (questionId: string, name: string) =>
    action.mutate({ studyId: project.id, questionId, action: name });
  return (
    <Card>
      <CardHeader className="pb-3"><CardTitle className="text-base">六题独立运行分片</CardTitle></CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {questions.map((question) => {
          const busy = action.isPending || retryFailed.isPending;
          const status = question.status;
          return (
            <div key={question.question_id} className="rounded-lg border p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-sm">{question.question_id}</span>
                <Badge variant={statusVariant(status)}>{STATUS_LABELS[status] ?? status}</Badge>
              </div>
              {question.error_summary ? <p className="mt-2 line-clamp-2 text-xs text-destructive" title={question.error_summary}>{question.error_summary}</p> : null}
              <div className="mt-3 flex flex-wrap gap-1.5">
                {status === 'pending' ? <Button size="xs" disabled={busy} onClick={() => run(question.question_id, 'start')}><Play className="size-3" />启动</Button> : null}
                {status === 'paused' ? <Button size="xs" disabled={busy} onClick={() => run(question.question_id, 'resume')}><Play className="size-3" />恢复</Button> : null}
                {status === 'running' ? <Button size="xs" variant="outline" disabled={busy} onClick={() => run(question.question_id, 'pause')}><Pause className="size-3" />暂停</Button> : null}
                {!['completed', 'terminated'].includes(status) ? <Button size="xs" variant="outline" disabled={busy} onClick={() => run(question.question_id, 'terminate')}><Trash2 className="size-3" />终止</Button> : null}
                {status === 'attention_required' ? <Button size="xs" variant="outline" disabled={busy} onClick={() => retryFailed.mutate({ studyId: project.id, questionId: question.question_id })}><RotateCcw className="size-3" />人工重试失败</Button> : null}
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function MonitorTab({ project, runtime, failedCalls }: { project: MemoryStudyProject; runtime?: ReturnType<typeof useMemoryStudyRuntime>['data']; failedCalls: MemoryCall[] }) {
  const progress = project.progress;
  const modelNames = project.config.models.map((item) => item.model.replace('gpt-6-', ''));
  const configuredSlotLimit = Math.max(1, project.config.models.length) * 4;
  // An offline runtime row may carry the capacity of a different/current
  // project (for example v3 while inspecting the retired v2 project). Use the
  // project's frozen model count until a live lease for this project proves
  // its capacity.
  const runtimeBelongsToProject = Boolean(
    runtime?.online &&
      Object.values(runtime.slots).some((slot) => slot.study_id === project.id),
  );
  const slotLimit = runtimeBelongsToProject && runtime
    ? runtime.global_subprocess_limit
    : configuredSlotLimit;
  const slotDescription = modelNames.length > 1
    ? `${modelNames.join(' / ')} 各自串行；瞬态错误最多自动重试 2 次`
    : `${modelNames[0] ?? 'Luna'} 四个条件流；瞬态错误最多自动重试 2 次`;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <MetricCard label="完成调用" value={`${progress.succeeded ?? 0}/${progress.total ?? 0}`} detail={`${progress.failed ?? 0} 失败`} />
        <MetricCard label="训练评分" value={`${progress.training_score_succeeded ?? 0}/${progress.training_score_total ?? 0}`} detail="评分成功后才更新记忆" />
        <MetricCard label="训练写入" value={`${progress.memory_write_total ?? 0}`} detail="跨独立 memory store" />
        <MetricCard label="测试评分" value={`${progress.test_score_succeeded ?? 0}/${progress.test_score_total ?? 0}`} detail="该题该模型训练完成后开放" />
        <MetricCard label="框架内部 Codex" value={`${progress.framework_invocation_succeeded ?? 0}/${progress.framework_invocation_total ?? 0}`} detail={`${progress.framework_invocation_failed ?? 0} 失败`} />
        <MetricCard
          label={`${slotLimit}槽执行`}
          value={`${runtime?.active_subprocesses ?? 0}/${slotLimit}`}
          detail={
            runtime === undefined
              ? '正在检查执行器状态…'
              : runtime.online
                ? slotDescription
                : runtime.reason ?? 'worker 未连接'
          }
        />
      </div>
      {progress.models ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(progress.models).map(([model, modelProgress]) => (
            <div key={model} className="rounded-lg border bg-card p-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">{model.replace('gpt-6-', '')}</span>
                <Badge variant="outline">{String(modelProgress.phase ?? 'training')}</Badge>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                训练 {String(modelProgress.training_succeeded ?? modelProgress.memory_write_succeeded ?? 0)}/{String(modelProgress.training_total ?? modelProgress.memory_write_total ?? 0)} ·{' '}
                评分 {String(modelProgress.scoring_succeeded ?? modelProgress.score_succeeded ?? 0)}/{String(modelProgress.scoring_total ?? modelProgress.score_total ?? 0)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                吞吐 {modelProgress.recent_throughput_per_minute != null ? `${Number(modelProgress.recent_throughput_per_minute).toFixed(1)}/分钟` : '—'} · ETA {modelProgress.eta_seconds != null ? `${Math.max(1, Math.ceil(Number(modelProgress.eta_seconds) / 60))} 分钟` : '—'}
              </p>
            </div>
          ))}
        </div>
      ) : null}
      {failedCalls.length > 0 ? (
        <Card className="border-destructive/30">
          <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base text-destructive"><ShieldAlert className="size-4" />失败调用（已暂停，等待人工重试，共 {failedCalls.length} 条）</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {failedCalls.slice(0, 5).map((call) => (
              <div key={call.id} className="rounded-md border p-2 text-xs">
                <p className="font-mono">#{call.id} · {call.kind === 'memory_write' ? '记忆写入' : '评分'} · {call.model.replace('gpt-6-', '')} · {call.question_id}</p>
                <p className="mt-1 text-destructive">{failureLabel(call.failure_code)}<span className="ml-1 font-mono text-muted-foreground">{call.failure_code ?? ''}</span></p>
                {call.failure_summary ? <p className="mt-1 line-clamp-2 text-muted-foreground" title={call.failure_summary}>{call.failure_summary}</p> : null}
              </div>
            ))}
            {failedCalls.length > 5 ? <p className="text-xs text-muted-foreground">仅显示最近 5 条，共 {failedCalls.length} 条失败。</p> : null}
          </CardContent>
        </Card>
      ) : null}
      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><Gauge className="size-4 text-primary" />运行进度</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div>
            <div className="mb-2 flex justify-between text-sm"><span>全部调用</span><span className="font-mono text-muted-foreground">{progress.succeeded ?? 0}/{progress.total ?? 0}</span></div>
            <ProgressBar value={progress.succeeded ?? 0} total={progress.total ?? 0} />
          </div>
          <div>
            <div className="mb-2 flex justify-between text-sm"><span>评分调用</span><span className="font-mono text-muted-foreground">{progress.score_succeeded ?? 0}/{progress.score_total ?? 0}</span></div>
            <ProgressBar value={progress.score_succeeded ?? 0} total={progress.score_total ?? 0} />
          </div>
          <div className="grid gap-3 text-sm text-muted-foreground sm:grid-cols-3">
            <p>待处理：<span className="font-mono text-foreground">{progress.pending ?? 0}</span></p>
            <p>运行中：<span className="font-mono text-foreground">{(progress.leased ?? 0) + (progress.running ?? 0)}</span></p>
            <p>状态：<span className="text-foreground">{STATUS_LABELS[project.status] ?? project.status}</span></p>
          </div>
          <div className="grid gap-3 text-sm text-muted-foreground sm:grid-cols-3">
            <p>记忆库已就绪：<span className="font-mono text-foreground">{progress.stores_completed ?? 0}/{progress.stores_total ?? 0}</span></p>
            <p>仍待训练：<span className="font-mono text-foreground">{progress.stores_pending ?? 0}</span></p>
            <p>已失败：<span className="font-mono text-foreground">{progress.stores_failed ?? 0}</span></p>
          </div>
          {(progress.stores_pending ?? 0) > 0 ? <p className="text-xs text-muted-foreground">评分按模型分别门禁：某模型的全部 memory store 完成后，该模型即可开始评分。</p> : null}
          {progress.eta_seconds != null ? <p className="text-xs text-muted-foreground">基于已完成调用的预计剩余时间：<span className="font-mono text-foreground">{Math.max(1, Math.ceil(progress.eta_seconds / 60))} 分钟</span></p> : null}
          {runtime?.slots && Object.keys(runtime.slots).length > 0 ? <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{Object.entries(runtime.slots).map(([slotId, slot]) => <div key={slotId} className="rounded border p-2 text-xs"><div className="flex items-center justify-between"><span className="font-mono">{slotId} · {slot.model.replace('gpt-6-', '')}</span><Badge variant={slot.state === 'failed' ? 'destructive' : slot.state === 'running' ? 'secondary' : 'outline'}>{slot.state ?? 'idle'}</Badge></div><p className="mt-1 text-muted-foreground">{slot.condition ? CONDITION_LABELS[slot.condition] ?? slot.condition : '条件流'} · {slot.phase ?? '等待'}{slot.call_id ? ` · call #${slot.call_id}` : ''}{slot.attempt ? ` · attempt ${slot.attempt}` : ''}</p>{slot.retry_after ? <RetryCountdown retryAfter={slot.retry_after} /> : null}</div>)}</div> : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-3"><CardTitle className="text-base">冻结配置快照</CardTitle></CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
          <p>模型：<span className="font-mono">{project.config.models.map((item) => item.model).join(' / ')}</span></p>
          <p>运行参数：<span className="font-mono">{project.config.models.map((item) => `${item.model.replace('gpt-6-', '')} ${item.reasoning_effort}/${item.speed_mode}/${item.timeout_seconds}s`).join(' · ')}</span></p>
          <p>检索：<span className="font-mono">top {project.config.retrieval_top_k}</span> · 完整案例返回</p>
          <p>Embedding：<span className="font-mono">{project.config.embedding_model}@{project.config.embedding_revision}</span></p>
          <p>协议规模：<span className="font-mono">{project.expected.score_calls} 次评分 / {project.expected.memory_write_calls} 次框架写入</span></p>
          <p>历史顺序 / 复次：<span className="font-mono">{project.config.order_variants.join(', ')} / {project.config.repeats.join(', ')}</span></p>
          <p className="sm:col-span-2 text-muted-foreground">本协议无 token 上限、无中期检查点、无 bootstrap；上下文超限会作为失败记录。</p>
        </CardContent>
      </Card>
    </div>
  );
}

function StoreTable({ stores, onInspect }: { stores: MemoryStore[]; onInspect: (store: MemoryStore) => void }) {
  const [model, setModel] = useState('all');
  const [condition, setCondition] = useState('all');
  const filtered = stores.filter((store) => (model === 'all' || store.model === model) && (condition === 'all' || store.condition === condition));
  const models = [...new Set(stores.map((store) => store.model))];
  return (
    <Card>
      <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><GitCompareArrows className="size-4 text-primary" />记忆库与最终快照</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <select value={model} onChange={(event) => setModel(event.target.value)} className="h-8 rounded-md border bg-background px-2 text-xs" aria-label="筛选模型">
            <option value="all">全部模型</option>{models.map((item) => <option key={item}>{item}</option>)}
          </select>
          <select value={condition} onChange={(event) => setCondition(event.target.value)} className="h-8 rounded-md border bg-background px-2 text-xs" aria-label="筛选条件">
            <option value="all">全部条件</option>{Object.keys(CONDITION_LABELS).filter((item) => item !== 'no_memory').map((item) => <option key={item} value={item}>{CONDITION_LABELS[item]}</option>)}
          </select>
          <span className="self-center text-xs text-muted-foreground">{filtered.length} 个独立流 · 不共享生成记忆</span>
        </div>
        <div className="overflow-auto rounded-md border">
          <table className="w-full min-w-[760px] text-sm">
            <thead><tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground"><th className="px-3 py-2">模型</th><th className="px-3 py-2">题目</th><th className="px-3 py-2">条件</th><th className="px-3 py-2">顺序</th><th className="px-3 py-2">写入</th><th className="px-3 py-2">状态</th><th className="px-3 py-2" /></tr></thead>
            <tbody>{filtered.slice(0, 300).map((store) => <tr key={store.id} className="border-b last:border-0"><td className="px-3 py-2 font-mono text-xs">{store.model.replace('gpt-6-', '')}</td><td className="px-3 py-2 font-mono">{store.question_id}</td><td className="px-3 py-2">{CONDITION_LABELS[store.condition] ?? store.condition}</td><td className="px-3 py-2 font-mono text-xs">{store.order_variant}</td><td className="px-3 py-2 font-mono">{store.committed_count}</td><td className="px-3 py-2"><Badge variant={store.status === 'completed' ? 'default' : store.status === 'failed' ? 'destructive' : 'outline'}>{store.status}</Badge>{store.status === 'failed' && store.error_summary ? <p className="mt-1 line-clamp-2 max-w-[240px] text-xs text-destructive" title={store.error_summary}>{store.error_summary}</p> : null}</td><td className="px-3 py-2 text-right"><Button size="xs" variant="ghost" onClick={() => onInspect(store)}><Eye className="size-3.5" />检查</Button></td></tr>)}</tbody>
          </table>
        </div>
        {filtered.length > 300 ? <p className="text-xs text-muted-foreground">列表显示前 300 条；完整快照仍由数据库和导出账本保留。</p> : null}
      </CardContent>
    </Card>
  );
}

function ScoresTab({ project, calls, total, nextCursor, onNext, failedTotal, retry, retryAll, questionId, onQuestionChange }: { project: MemoryStudyProject; calls: MemoryCall[]; total: number; nextCursor: number | null; onNext: () => void; failedTotal: number; retry: ReturnType<typeof useRetryMemoryStudyCall>; retryAll: ReturnType<typeof useRetryAllFailedMemoryStudyCalls>; questionId: string; onQuestionChange: (value: string) => void }) {
  if (project.results_embargoed) {
    return <Card><CardContent className="flex flex-col items-center gap-3 py-12 text-center"><LockKeyhole className="size-8 text-muted-foreground" /><p className="font-semibold">方向性结果已封存</p><p className="max-w-lg text-sm text-muted-foreground">完整性审计通过前不显示人工分、AI 分、差值或模型反馈。调用状态、延迟和 token 账本仍可核查。</p>{failedTotal > 0 ? <p className="max-w-lg text-sm text-destructive">有 {failedTotal} 条调用失败；重试入口在页面顶部的「研究需要处理」面板。</p> : null}</CardContent></Card>;
  }
  const failedCount = failedTotal;
  return (
    <Card>
      <CardHeader className="pb-3"><CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base"><span>逐次评分差值（本页 {calls.length} / 共 {total}）</span><div className="flex flex-wrap items-center gap-2"><select value={questionId} onChange={(event) => onQuestionChange(event.target.value)} className="h-8 rounded-md border bg-background px-2 text-xs" aria-label="筛选题目"><option value="all">全部题目</option>{(project.questions ?? []).map((question) => <option key={question.question_id} value={question.question_id}>{question.question_id}</option>)}</select>{failedCount > 0 ? <Button size="xs" variant="outline" disabled={retryAll.isPending || project.status === 'running'} onClick={() => retryAll.mutate(project.id)}><RotateCcw className="size-3.5" />重试全部失败（{failedCount}）</Button> : null}</div></CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="overflow-auto rounded-md border"><table className="w-full min-w-[1080px] text-sm"><thead><tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground"><th className="px-3 py-2">模型</th><th className="px-3 py-2">题目</th><th className="px-3 py-2">条件</th><th className="px-3 py-2">顺序/复次</th><th className="px-3 py-2">人工</th><th className="px-3 py-2">AI</th><th className="px-3 py-2">有符号差</th><th className="px-3 py-2">绝对差</th><th className="px-3 py-2">状态</th><th className="px-3 py-2" /></tr></thead><tbody>{calls.map((call) => <tr key={call.id} className="border-b last:border-0"><td className="px-3 py-2 font-mono text-xs">{call.model.replace('gpt-6-', '')}</td><td className="px-3 py-2 font-mono">{call.question_id}</td><td className="px-3 py-2">{CONDITION_LABELS[call.condition] ?? call.condition}</td><td className="px-3 py-2 font-mono text-xs">{call.order_variant} / {call.repeat}</td><td className="px-3 py-2 font-mono">{call.manual_score?.toFixed(2) ?? '—'}</td><td className="px-3 py-2 font-mono">{call.model_score?.toFixed(2) ?? '—'}</td><td className="px-3 py-2 font-mono">{call.signed_diff?.toFixed(2) ?? '—'}</td><td className="px-3 py-2 font-mono">{call.absolute_diff?.toFixed(2) ?? '—'}</td><td className="px-3 py-2"><Badge variant={call.status === 'succeeded' ? 'default' : call.status === 'failed' ? 'destructive' : 'outline'}>{call.status}</Badge></td><td className="px-3 py-2 text-right">{call.status === 'failed' ? <Button size="xs" variant="ghost" disabled={retry.isPending} onClick={() => retry.mutate({ studyId: project.id, callId: call.id })}><RotateCcw className="size-3.5" />重试</Button> : null}</td></tr>)}</tbody></table></div>
        {nextCursor !== null ? <div className="flex justify-end"><Button size="xs" variant="outline" onClick={onNext}>下一页</Button></div> : null}
      </CardContent>
    </Card>
  );
}

function ReportTab({ project, report }: { project: MemoryStudyProject; report: ReturnType<typeof useMemoryStudyReport>['data'] }) {
  if (!report || report.results_embargoed || project.results_embargoed) return <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">报告在完整性审计通过且所有评分成功后解封。</CardContent></Card>;
  const data = report.report ?? {};
  const primary = (data.primary ?? {}) as Record<string, any>;
  const conditionNae = (primary.condition_nae ?? {}) as Record<string, Record<string, Record<string, any>>>;
  const memoryGain = (primary.memory_gain_vs_no_memory ?? {}) as Record<string, Record<string, Record<string, any>>>;
  const secondary = (data.secondary ?? {}) as Record<string, any>;
  const sensitivity = (secondary.history_order_sensitivity ?? {}) as Record<string, Record<string, Record<string, any>>>;
  const runVariation = (secondary.no_memory_run_variation ?? {}) as Record<string, Record<string, any>>;
  const diagnostics = (data.diagnostics ?? {}) as Record<string, any>;
  const trajectories = (diagnostics.training_memory_trajectory ?? {}) as Record<string, Record<string, Record<string, any>>>;
  const sample = data.sample as Record<string, unknown> | undefined;
  const fmt = (value: unknown) => value == null ? '—' : Number(value).toFixed(4);
  type ConditionRow = { model: string; condition: string; estimate?: number; n_answers?: number; excluded_answers?: number };
  type GainRow = ConditionRow & { improved?: number; tied?: number; worse?: number };
  const conditionRows: ConditionRow[] = Object.entries(conditionNae).flatMap(([model, conditions]) => Object.entries(conditions).map(([condition, values]) => ({ model, condition, ...values } as ConditionRow)));
  const gainRows: GainRow[] = Object.entries(memoryGain).flatMap(([model, conditions]) => Object.entries(conditions).map(([condition, values]) => ({ model, condition, ...values } as GainRow)));
  const orderRows = Object.entries(sensitivity).flatMap(([model, conditions]) => Object.entries(conditions).flatMap(([condition, values]) => Object.entries(values.by_question ?? {}).map(([question, detail]: [string, any]) => ({ model, condition, question, ...detail }))));
  const baselineRows = Object.entries(runVariation).flatMap(([model, values]) => Object.entries(values.by_question ?? {}).map(([question, detail]: [string, any]) => ({ model, condition: 'no_memory（运行波动）', question, ...detail })));
  const trajectoryCount = Object.values(trajectories).reduce((total, streams) => total + Object.keys(streams).length, 0);
  const orderVariants = project.config.order_variants;
  return <div className="space-y-4">
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><MetricCard label="核心指标" value="测试集 NAE" detail="答案内平均历史顺序，再对题目等权" /><MetricCard label="核心效应" value="记忆增益" detail="无记忆 NAE − 记忆 NAE；正值为改善" /><MetricCard label="训练轨迹" value={String(trajectoryCount)} detail="评分发生在当前答案写入前" /><MetricCard label="失败调用" value={String(sample?.failed_calls ?? '—')} detail="失败不记为零" /></div>
    <Card><CardHeader className="pb-3"><CardTitle className="text-base">NEW 测试集：条件 NAE</CardTitle></CardHeader><CardContent><div className="overflow-auto rounded border"><table className="w-full text-sm"><thead><tr className="border-b bg-muted/40 text-left"><th className="px-3 py-2">模型</th><th className="px-3 py-2">条件</th><th className="px-3 py-2">NAE</th><th className="px-3 py-2">有效/排除答案</th></tr></thead><tbody>{conditionRows.map((row) => <tr key={`${row.model}-${row.condition}`} className="border-b"><td className="px-3 py-2 font-mono">{row.model.replace('gpt-6-', '')}</td><td className="px-3 py-2">{CONDITION_LABELS[row.condition] ?? row.condition}</td><td className="px-3 py-2 font-mono">{fmt(row.estimate)}</td><td className="px-3 py-2 font-mono">{row.n_answers}/{row.excluded_answers}</td></tr>)}</tbody></table></div></CardContent></Card>
    <Card><CardHeader className="pb-3"><CardTitle className="text-base">记忆相对无记忆的评分影响</CardTitle></CardHeader><CardContent><div className="overflow-auto rounded border"><table className="w-full text-sm"><thead><tr className="border-b bg-muted/40 text-left"><th className="px-3 py-2">记忆条件</th><th className="px-3 py-2">记忆增益</th><th className="px-3 py-2">改善/持平/变差</th><th className="px-3 py-2">有效答案</th></tr></thead><tbody>{gainRows.map((row) => <tr key={`${row.model}-${row.condition}`} className="border-b"><td className="px-3 py-2">{CONDITION_LABELS[row.condition] ?? row.condition}</td><td className="px-3 py-2 font-mono">{fmt(row.estimate)}</td><td className="px-3 py-2 font-mono">{row.improved}/{row.tied}/{row.worse}</td><td className="px-3 py-2 font-mono">{row.n_answers}</td></tr>)}</tbody></table></div></CardContent></Card>
    <Card><CardHeader className="pb-3"><CardTitle className="text-base">历史顺序敏感性</CardTitle></CardHeader><CardContent><p className="mb-3 text-xs text-muted-foreground">无记忆行仅表示重复运行波动，不代表训练顺序效应。</p><div className="overflow-auto rounded border"><table className="w-full text-sm"><thead><tr className="border-b bg-muted/40 text-left"><th className="px-3 py-2">条件</th><th className="px-3 py-2">题目</th><th className="px-3 py-2">{orderVariants.join(' / ')} NAE</th><th className="px-3 py-2">顺序差</th></tr></thead><tbody>{[...orderRows, ...baselineRows].map((row) => <tr key={`${row.model}-${row.condition}-${row.question}`} className="border-b"><td className="px-3 py-2">{CONDITION_LABELS[row.condition] ?? row.condition}</td><td className="px-3 py-2 font-mono">{row.question}</td><td className="px-3 py-2 font-mono">{orderVariants.map((order) => fmt(row.order_nae?.[order])).join(' / ')}</td><td className="px-3 py-2 font-mono">{fmt(row.range)}</td></tr>)}</tbody></table></div></CardContent></Card>
    <Card><CardHeader className="pb-3"><CardTitle className="text-base">训练阶段记忆形成轨迹</CardTitle></CardHeader><CardContent className="space-y-3"><p className="text-sm text-muted-foreground">已记录 {trajectoryCount} 条条件×题目×顺序轨迹。第 t 步评分发生在当前答案写入前；训练结果不进入测试集主指标。</p>{Object.entries(trajectories).flatMap(([model, streams]) => Object.entries(streams).map(([streamKey, stream]) => <details key={`${model}-${streamKey}`} className="rounded border p-2"><summary className="cursor-pointer text-sm font-medium">{streamKey} · {String(stream.n_steps ?? 0)} 步</summary><div className="mt-2 max-h-72 overflow-auto"><table className="w-full text-xs"><thead><tr className="border-b bg-muted/40 text-left"><th className="px-2 py-1">步骤</th><th className="px-2 py-1">答案</th><th className="px-2 py-1">评分前记忆</th><th className="px-2 py-1">NAE</th><th className="px-2 py-1">累计 NAE</th></tr></thead><tbody>{(stream.records ?? []).map((record: any) => <tr key={`${streamKey}-${record.train_step}`} className="border-b"><td className="px-2 py-1 font-mono">{record.train_step}</td><td className="px-2 py-1 font-mono">{record.answer_id}</td><td className="px-2 py-1 font-mono">{record.memory_items_before}</td><td className="px-2 py-1 font-mono">{fmt(record.nae)}</td><td className="px-2 py-1 font-mono">{fmt(record.cumulative_mean_nae)}</td></tr>)}</tbody></table></div></details>))}</CardContent></Card>
    <Card><CardHeader className="pb-3"><CardTitle className="text-base">补充诊断</CardTitle></CardHeader><CardContent className="space-y-2 text-sm"><p>测试集有符号偏差：<span className="font-mono">{JSON.stringify(diagnostics.signed_bias ?? {})}</span></p><p>无反馈 − 完整反馈 NAE：<span className="font-mono">{JSON.stringify(secondary.feedback_ablation ?? {})}</span></p><p className="text-muted-foreground">资源与失败账本保留在报告 JSON 和调用明细中，不进入主结论。</p></CardContent></Card>
    <Card><CardHeader className="pb-3"><CardTitle className="text-base">分析声明</CardTitle></CardHeader><CardContent><ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{((data.statements as string[] | undefined) ?? []).map((item) => <li key={item}>{item}</li>)}</ul></CardContent></Card>
  </div>;
}

export default function MemoryStudyDetailPage() {
  const { studyId = '' } = useParams<{ studyId: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<DetailTab>('monitor');
  const [selectedStore, setSelectedStore] = useState<number | null>(null);
  const [selectedQuestion, setSelectedQuestion] = useState('all');
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [scoreCursor, setScoreCursor] = useState<number | null>(null);
  const projectQuery = useMemoryStudyProject(studyId);
  const terminal = ['completed', 'terminated'].includes(projectQuery.data?.status ?? '');
  const runtime = useMemoryStudyRuntime(true, !terminal);
  const stores = useMemoryStudyStores(studyId, tab === 'memory', !terminal);
  const scores = useMemoryStudyCalls(studyId, 'score', {
    enabled: tab === 'scores',
    polling: !terminal,
    cursor: scoreCursor,
    questionId: selectedQuestion === 'all' ? undefined : selectedQuestion,
  });
  const failedScoresQuery = useMemoryStudyCalls(studyId, 'score', {
    enabled: tab === 'monitor',
    polling: !terminal,
    status: 'failed',
    limit: 1_000,
  });
  const failedWritesQuery = useMemoryStudyCalls(studyId, 'memory_write', {
    enabled: tab === 'monitor',
    polling: !terminal,
    status: 'failed',
    limit: 1_000,
  });
  const report = useMemoryStudyReport(studyId, tab === 'report', !terminal);
  const action = useMemoryStudyAction();
  const preflight = useMemoryStudyPreflight();
  const audit = useMemoryStudyAuditProject();
  const retry = useRetryMemoryStudyCall();
  const retryAll = useRetryAllFailedMemoryStudyCalls();
  const recoverStore = useRecoverMemoryStudyStore();
  const remove = useDeleteMemoryStudyProject();
  const storeDetail = useMemoryStudyStore(studyId, selectedStore, tab === 'memory');
  const project = projectQuery.data;
  const canFreeze = project?.status === 'ready';
  const canStart = project && ['frozen', 'paused', 'attention_required'].includes(project.status);
  const preflightPassed = project?.preflight?.status === 'passed';
  const canPreflight = canStart && (!preflightPassed || project.status === 'attention_required');
  const modelNames = project?.config.models.map((item) => item.model.replace('gpt-6-', '')) ?? [];
  const configuredSlotLimit = Math.max(1, modelNames.length) * 4;
  const runtimeBelongsToProject = Boolean(
    runtime.data?.online &&
      Object.values(runtime.data.slots).some((slot) => slot.study_id === project?.id),
  );
  const slotLimit = runtimeBelongsToProject && runtime.data
    ? runtime.data.global_subprocess_limit
    : configuredSlotLimit;
  const slotDescription = modelNames.length > 1
    ? `${modelNames.join(' / ')} 各 ${4} 个条件流；同一记忆流严格串行`
    : `${modelNames[0] ?? 'Luna'} 四个条件流；同一记忆流严格串行`;
  const displayedScores = useMemo(() => scores.data?.items ?? [], [scores.data]);
  const failedScores = useMemo(
    () => failedScoresQuery.data?.items ?? [],
    [failedScoresQuery.data],
  );
  // Memory-write failures are the ones that take a whole memory store down, so
  // they must be as visible as score failures -- the recovery entry point in
  // particular has to account for them.
  const failedWrites = useMemo(
    () => failedWritesQuery.data?.items ?? [],
    [failedWritesQuery.data],
  );
  const failedAll = useMemo(
    () => [...failedWrites, ...failedScores],
    [failedWrites, failedScores],
  );
  const failureCodes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const call of failedAll) {
      const key = call.failure_code ?? 'unknown';
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [failedAll]);

  if (projectQuery.isLoading) return <p className="text-sm text-muted-foreground">加载研究项目…</p>;
  if (projectQuery.isError || !project) return <p className="text-sm text-destructive">研究项目加载失败。</p>;

  const actionName: string | null =
    project.status === 'running' ? 'pause'
    : canStart && preflightPassed ? (project.status === 'frozen' ? 'start' : 'resume')
    : canFreeze ? 'freeze' : null;
  const actionLabel: string | null = project.status === 'running' ? '暂停'
    : canStart && preflightPassed ? (project.status === 'frozen' ? '开始运行' : '恢复运行')
    : canFreeze ? '冻结项目' : null;

  return <div className="mx-auto max-w-7xl space-y-6"><header className="space-y-3"><Button asChild variant="ghost" size="sm" className="-ml-2"><Link to="/memory-study"><ArrowLeft className="size-4" />返回记忆研究</Link></Button><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-bold">{project.name}</h1><Badge variant="outline">{project.kind}</Badge><Badge variant={statusVariant(project.status)}>{STATUS_LABELS[project.status] ?? project.status}</Badge>{project.results_embargoed ? <Badge variant="outline"><LockKeyhole className="size-3" />结果封存</Badge> : null}</div><p className="text-sm text-muted-foreground">{project.protocol_id} · {project.config.models.map((model) => model.model).join(' / ')} · manifest <span className="font-mono">{shortHash(project.data_manifest_sha256)}</span> · config <span className="font-mono">{shortHash(project.config_sha256)}</span></p></header>
    <Card><CardContent className="flex flex-wrap items-center gap-2 p-3"><Server className="size-4 text-primary" /><span className="text-sm">{slotLimit}槽调用：{runtime.data?.active_subprocesses ?? 0}/{slotLimit}</span><Badge variant={runtime.data?.online ? 'default' : 'outline'}>{runtime.data?.online ? '在线' : '未连接'}</Badge><span className="text-xs text-muted-foreground">{runtime.data?.online ? `${slotDescription}，失败只暂停该题` : runtime.data?.reason ?? '等待 worker 连接'}</span><div className="ml-auto flex flex-wrap gap-2">{canPreflight ? <Button size="sm" variant="default" disabled={preflight.isPending} onClick={() => preflight.mutate(studyId)}><ClipboardCheck className="size-3.5" />{preflightPassed ? '重新预检' : '运行预检'}</Button> : null}{actionName && actionLabel ? <Button size="sm" variant={actionName === 'pause' ? 'outline' : 'default'} disabled={action.isPending} onClick={() => action.mutate({ id: studyId, action: actionName })}>{actionName === 'pause' ? <Pause className="size-3.5" /> : actionName === 'freeze' ? <LockKeyhole className="size-3.5" /> : <Play className="size-3.5" />}{actionLabel}</Button> : null}{['completed', 'terminated'].includes(project.status) ? null : <Button size="sm" variant="outline" disabled={audit.isPending} onClick={() => audit.mutate(studyId)}><ClipboardCheck className="size-3.5" />完整性审计</Button>}{project.protocol_id === 'saf-memory-framework-v2' ? null : <Button size="sm" variant="destructive" disabled={project.status === 'running' || remove.isPending} onClick={() => setDeleteOpen(true)}><Trash2 className="size-3.5" />删除研究</Button>}</div></CardContent></Card>
    {canPreflight ? <Card className="border-amber-300 bg-amber-50/40"><CardContent className="flex flex-wrap items-center gap-3 p-3 text-sm"><ClipboardCheck className="size-4 text-amber-700" /><div className="flex-1"><p className="font-medium">启动前必须通过 {modelNames.join(' / ') || '模型'} 运行预检</p><p className="text-xs text-muted-foreground">预检会产生每个模型一次少量真实结构化调用，并记录延迟、输出哈希和 CLI 指纹。{project.preflight?.invalid_reason ? ` ${project.preflight.invalid_reason}` : project.preflight?.status === 'failed' ? ' 上次预检未通过，请先处理下方模型级错误。' : project.status === 'attention_required' && preflightPassed ? ' 研究曾检测到运行时异常，建议重新验证 CLI 指纹。' : ''}</p></div><Button size="sm" disabled={preflight.isPending} onClick={() => preflight.mutate(studyId)}>{preflight.isPending ? '预检中…' : preflightPassed ? '重新运行预检' : '运行预检'}</Button></CardContent></Card> : null}
    {project.preflight?.models ? <Card><CardHeader className="pb-3"><CardTitle className="text-base">运行预检结果</CardTitle></CardHeader><CardContent className="grid gap-2 sm:grid-cols-2">{project.config.models.map((model) => { const check = project.preflight.models?.[model.model]; return <div key={model.model} className="rounded border p-3 text-xs"><div className="flex items-center justify-between"><span className="font-mono">{model.model}</span><Badge variant={check?.status === 'passed' ? 'default' : 'destructive'}>{check?.status ?? 'missing'}</Badge></div><p className="mt-1 text-muted-foreground">{check?.latency_ms != null ? `${check.latency_ms} ms` : ''}{check?.failure_code ? ` · ${failureLabel(check.failure_code)}` : ''}</p>{check?.failure_summary ? <p className="mt-1 break-words text-destructive">{check.failure_summary}</p> : null}</div>; })}</CardContent></Card> : null}
    <QuestionShardPanel project={project} />
    {project.error_summary || failedAll.length > 0 ? <Card className="border-destructive/40 bg-destructive/5"><CardContent className="flex flex-wrap items-start gap-3 p-3"><ShieldAlert className="mt-0.5 size-4 shrink-0 text-destructive" /><div className="min-w-0 flex-1 text-sm"><p className="font-medium text-destructive">研究需要处理</p>{project.error_summary ? <p className="mt-0.5 break-words text-muted-foreground">{project.error_summary}</p> : null}{failedAll.length > 0 ? <p className="mt-1 text-xs text-muted-foreground">失败调用 {failedAll.length} 条（记忆写入 {failedWrites.length} · 评分 {failedScores.length}）。失败调用会挡住后续调用；人工重试成功后，再点「恢复运行」才会继续。</p> : null}{failureCodes.length > 0 ? <div className="mt-2 flex flex-wrap gap-1.5">{failureCodes.map(([code, count]) => <Badge key={code} variant="outline"><span className="text-destructive">{failureLabel(code)}</span><span className="ml-1 font-mono text-muted-foreground">{count}</span></Badge>)}</div> : null}</div><div className="flex flex-wrap gap-2">{failedAll.length > 0 ? <Button size="sm" variant="outline" disabled={retryAll.isPending || project.status === 'running'} onClick={() => retryAll.mutate(studyId)}><RotateCcw className="size-3.5" />重试全部失败（{failedAll.length}）</Button> : null}{['paused', 'attention_required'].includes(project.status) ? <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ id: studyId, action: 'resume' })}><Play className="size-3.5" />恢复运行</Button> : null}</div></CardContent></Card> : null}
    <div className="flex flex-wrap gap-2">{(['monitor', 'memory', 'scores', 'report', 'audit'] as DetailTab[]).map((item) => <Button key={item} size="sm" variant={tab === item ? 'default' : 'outline'} onClick={() => setTab(item)}>{item === 'monitor' ? '运行监控' : item === 'memory' ? '记忆检查' : item === 'scores' ? '批改明细' : item === 'report' ? '描述性报告' : '完整性审计'}</Button>)}</div>
    {tab === 'monitor' ? <MonitorTab project={project} runtime={runtime.data} failedCalls={failedAll} /> : null}
    {tab === 'memory' ? <div className="space-y-4"><StoreTable stores={stores.data ?? []} onInspect={(store) => setSelectedStore(store.id)} />{selectedStore !== null ? <Card><CardHeader className="flex-row items-center justify-between pb-3"><CardTitle className="text-base">快照检查 · store #{selectedStore}</CardTitle><Button variant="ghost" size="sm" onClick={() => setSelectedStore(null)}>关闭</Button></CardHeader><CardContent>{storeDetail.isLoading ? <p className="text-sm text-muted-foreground">加载快照…</p> : <><pre className="max-h-[36rem] overflow-auto rounded-lg bg-muted p-4 text-xs leading-5">{JSON.stringify(storeDetail.data, null, 2)}</pre>{storeDetail.data?.status === 'failed' ? <Button size="sm" className="mt-3" disabled={recoverStore.isPending || project.status === 'running'} onClick={() => recoverStore.mutate({ studyId, storeId: selectedStore })}><RotateCcw className="size-3.5" />校验并恢复 memory store</Button> : null}</>}</CardContent></Card> : null}</div> : null}
    {tab === 'scores' ? <ScoresTab project={project} calls={displayedScores} total={scores.data?.total ?? 0} nextCursor={scores.data?.next_cursor ?? null} onNext={() => setScoreCursor(scores.data?.next_cursor ?? null)} failedTotal={(failedScoresQuery.data?.total ?? 0) + (failedWritesQuery.data?.total ?? 0)} retry={retry} retryAll={retryAll} questionId={selectedQuestion} onQuestionChange={(value) => { setSelectedQuestion(value); setScoreCursor(null); }} /> : null}
    {tab === 'report' ? <ReportTab project={project} report={report.data} /> : null}
    {tab === 'audit' ? <Card><CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><ClipboardCheck className="size-4 text-primary" />完整性审计</CardTitle></CardHeader><CardContent className="space-y-4">{project.integrity_status === 'passed' ? <p className="flex items-center gap-2 text-sm text-emerald-700"><ShieldAlert className="size-4" />已通过；方向性结果已解封。</p> : <p className="text-sm text-muted-foreground">尚未通过。运行完成后点击“完整性审计”检查所有成功调用、差值、测试只读与协议边界。</p>}<div className="grid gap-2 sm:grid-cols-2">{Object.entries(project.integrity.checks ?? {}).map(([key, value]) => <div key={key} className="flex items-center justify-between rounded border px-3 py-2 text-sm"><span>{key}</span><Badge variant={value ? 'default' : 'outline'}>{value ? '通过' : '待处理'}</Badge></div>)}</div></CardContent></Card> : null}
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>导出：</span><a href={memoryStudyExportUrl(studyId, 'manifest')} download><Button size="xs" variant="outline"><Download className="size-3" />manifest JSON</Button></a>{!project.results_embargoed ? <><a href={memoryStudyExportUrl(studyId, 'results')} download><Button size="xs" variant="outline"><Download className="size-3" />结果 CSV</Button></a><a href={memoryStudyExportUrl(studyId, 'report')} download><Button size="xs" variant="outline"><Download className="size-3" />报告 JSON</Button></a><a href={memoryStudyExportUrl(studyId, 'html')} download><Button size="xs" variant="outline"><Download className="size-3" />HTML</Button></a></> : <span>报告与结果在审计通过后开放</span>}</div>
    <ConfirmActionDialog
      open={deleteOpen}
      onOpenChange={setDeleteOpen}
      title="删除研究"
      description="将删除该研究全部调用记录、记忆库与产物目录，且不可恢复。"
      confirmLabel="确认删除"
      cancelLabel="取消"
      pending={remove.isPending}
      pendingLabel="删除中…"
      dangerous
      onConfirm={() =>
        remove.mutate(studyId, {
          onSuccess: () => navigate('/memory-study'),
        })
      }
    />
  </div>;
}
