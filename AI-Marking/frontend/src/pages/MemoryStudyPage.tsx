import { useState } from 'react';
import { ArrowRight, BrainCircuit, CheckCircle2, Database, LockKeyhole, Play, Server, ShieldCheck, Trash2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';

import {
  type MemoryStudyKind,
  type MemoryStudyProtocol,
  useCreateMemoryStudy,
  useDeleteMemoryStudyProject,
  useMemoryStudyAudit,
  useMemoryStudyProjects,
  useMemoryStudyRuntime,
} from '@/api/memoryStudy';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

const KIND_LABELS: Record<MemoryStudyKind, string> = {
  development: '开发题',
  pilot: '试点题',
  formal: '正式六题',
};

const PROTOCOL_LABELS: Record<MemoryStudyProtocol, string> = {
  'saf-memory-framework-v3': 'V3 原协议',
  'saf-memory-framework-v3-r2': 'V3-r2（当前默认）',
};

const MODEL_BINDINGS: { model: string; label: string }[] = [
  { model: 'gpt-6-luna', label: 'Luna' },
];

const SPEED_LABELS: Record<string, string> = {
  standard: '标准 (standard)',
  fast: '快速 (fast)',
};

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  ready: '待冻结',
  frozen: '已冻结',
  running: '运行中',
  paused: '已暂停',
  attention_required: '需要处理',
  completed: '已完成',
  terminated: '已终止',
};

function statusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'completed') return 'default';
  if (status === 'attention_required') return 'destructive';
  if (status === 'running') return 'secondary';
  return 'outline';
}

function shortHash(value?: string) {
  return value ? `${value.slice(0, 12)}…` : '—';
}

function ProtocolCard({ protocolId }: { protocolId: MemoryStudyProtocol }) {
  return (
    <Card className="border-primary/20 bg-primary/[0.03]">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="size-4 text-primary" />
          冻结协议边界
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm text-muted-foreground sm:grid-cols-2">
        <p>正式规模：6 题 × 40 条训练 × 10 条测试；每个训练/测试答案各评分 1 次。</p>
        <p>协议：{protocolId === 'saf-memory-framework-v3-r2' ? 'V3-r2 固定六题、连续分数上下限和独立评分仪器指纹。' : 'V3 历史基线，只用于读取与分析。'}</p>
        <p>条件：无记忆、确定性检索、Mem0、A-MEM；反馈实验作为独立 wave，默认先运行完整反馈。</p>
        <p>正式使用 V4 的两种固定历史顺序（数据集原始顺序与 seed=42 随机顺序）；每题可独立启动、暂停、终止和人工重试。训练先评分再更新记忆，测试在该题该模型训练完成后开始。</p>
        <p>开发/试点固定为 70 次评分、160 次官方框架写入；正式基础 wave 为 2,400 次评分、1,440 次记忆写入；含无反馈 wave 时为 4,200 次评分、2,880 次记忆写入。</p>
        <p>不提供 token 上限、检查点、bootstrap 或显著性检验配置。</p>
        <p>Luna 占四个固定条件槽；同一题目×模型×条件流严格串行，四个槽可并行推进。</p>
        <p>冻结后先运行一次 Luna 真实结构化预检；预检费用与延迟会进入审计账本。</p>
        <p>导出只含 ID、分数、差值、token 和资源账本，不含学生正文或完整 prompt。</p>
      </CardContent>
    </Card>
  );
}

export default function MemoryStudyPage() {
  const navigate = useNavigate();
  const [kind, setKind] = useState<MemoryStudyKind>('pilot');
  const [protocolId, setProtocolId] = useState<MemoryStudyProtocol>('saf-memory-framework-v3-r2');
  const [name, setName] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [bindings, setBindings] = useState<Record<string, 'standard' | 'fast'>>({});
  const [includeNoFeedback, setIncludeNoFeedback] = useState(true);
  const audit = useMemoryStudyAudit(kind, protocolId);
  const projects = useMemoryStudyProjects();
  const runtime = useMemoryStudyRuntime();
  const create = useCreateMemoryStudy();
  const remove = useDeleteMemoryStudyProject();
  const canPilot = true;
  // Formal six-question runs do not require a completed pilot. Freeze,
  // preflight, and the final integrity audit remain mandatory.
  const canFormal = true;
  const kindAllowed = kind === 'development' || (kind === 'pilot' ? canPilot : canFormal);

  const selectedName = name || `${protocolId}-${kind}`;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div>
          <p className="text-sm font-medium text-primary">独立 SAF 2.0 研究平台</p>
          <h1 className="mt-2 text-3xl font-bold">记忆框架实验</h1>
          <p className="mt-2 max-w-3xl text-muted-foreground">
            在同一历史人工批改材料上比较普通案例检索、Mem0、A-MEM 与无记忆基线。
            新协议使用独立数据清单、记忆库、调用账本和描述性报告，不改变旧实验。
          </p>
        </div>
        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Server className="size-4 text-primary" /> 四槽执行器
            </div>
            <div className="flex items-end justify-between">
              <span className="text-sm text-muted-foreground">Codex worker</span>
              <Badge variant={runtime.data?.online ? 'default' : 'outline'}>
                {runtime.data?.online ? '在线' : '未连接'}
              </Badge>
            </div>
          <p className="text-xs text-muted-foreground">
              {runtime.data?.active_subprocesses ?? 0}/{runtime.data?.global_subprocess_limit ?? 4} 个活动调用 ·{' '}
              {runtime.data?.online
                ? 'Luna 4 个条件流；同一记忆流严格串行'
                : runtime.data === undefined
                  ? '正在检查执行器状态…'
                  : runtime.data?.reason ?? '等待 worker 启动'}
            </p>
          </CardContent>
        </Card>
      </header>

      <ProtocolCard protocolId={protocolId} />

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Database className="size-4 text-primary" />
              数据清洗与抽样审计
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg bg-muted/60 p-3">
                <p className="text-xs text-muted-foreground">归档</p>
                <p className="mt-1 font-semibold">{audit.data?.source_archive ?? 'SAF2_0.zip'}</p>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">{shortHash(audit.data?.archive_sha256)}</p>
              </div>
              <div className="rounded-lg bg-muted/60 p-3">
                <p className="text-xs text-muted-foreground">当前抽样</p>
                <p className="mt-1 font-semibold">{audit.data?.selected_questions.length ?? '—'} 题</p>
                <p className="mt-1 text-xs text-muted-foreground">{KIND_LABELS[kind]}</p>
              </div>
              <div className="rounded-lg bg-muted/60 p-3">
                <p className="text-xs text-muted-foreground">数据门禁</p>
                <p className="mt-1 flex items-center gap-1 font-semibold">
                  {audit.data?.ready ? <CheckCircle2 className="size-4 text-emerald-600" /> : null}
                  {audit.isLoading ? '审计中…' : audit.data?.ready ? '通过' : '未通过'}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">新种子、互斥角色、标签不入 prompt</p>
                <p className={`mt-1 text-xs ${audit.data?.embedding.revision_pinned ? 'text-muted-foreground' : 'text-destructive'}`}>
                  {audit.data?.embedding.revision_pinned
                    ? `embedding revision 已固定：${audit.data.embedding.revision}`
                    : 'embedding revision 未固定；冻结前必须配置不可变 revision'}
                </p>
              </div>
            </div>
            {audit.isError ? (
              <p className="text-sm text-destructive">归档审计失败，请先处理数据或哈希门禁。</p>
            ) : audit.data ? (
              <div className="flex flex-wrap gap-2">
                {audit.data.selected_questions.map((question) => (
                  <Badge key={question} variant="outline" className="font-mono">
                    {question} · {audit.data?.counts[question]?.training}/
                    {audit.data?.counts[question]?.test}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">正在读取固定归档…</p>
            )}
            <p className="font-mono text-xs text-muted-foreground">
              manifest · {shortHash(audit.data?.manifest_sha256)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <BrainCircuit className="size-4 text-primary" />
              创建独立运行
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="项目名称（可选）"
              aria-label="项目名称"
            />
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value as MemoryStudyKind)}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              aria-label="研究规模"
            >
              {(Object.keys(KIND_LABELS) as MemoryStudyKind[]).map((value) => (
                <option
                  key={value}
                  value={value}
                  disabled={value === 'pilot' ? !canPilot : value === 'formal' ? !canFormal : false}
                >
                  {KIND_LABELS[value]}
                </option>
              ))}
            </select>
            <select
              value={protocolId}
              onChange={(event) =>
                setProtocolId(event.target.value as MemoryStudyProtocol)
              }
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              aria-label="评分协议"
            >
              {(Object.keys(PROTOCOL_LABELS) as MemoryStudyProtocol[]).map((value) => (
                <option key={value} value={value}>
                  {PROTOCOL_LABELS[value]}
                </option>
              ))}
            </select>
            {protocolId === 'saf-memory-framework-v3' ? (
              <p className="text-xs text-muted-foreground">
                V3 原协议仅用于保留历史基线；新项目建议使用 V3-r2。
              </p>
            ) : null}
            <label className="flex items-start gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
                className="mt-0.5"
              />
              我确认仅将受限 SAF 归档用于本研究，测试人工标签不会进入任何模型请求。
            </label>
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">
                Luna 速度绑定：选择使用哪套 Runner 配置
              </p>
              {MODEL_BINDINGS.map(({ model, label }) => (
                <div
                  key={model}
                  className="flex items-center justify-between gap-2"
                >
                  <span className="text-sm">{label}</span>
                  <select
                    value={bindings[model] ?? 'standard'}
                    onChange={(event) =>
                      setBindings((prev) => ({
                        ...prev,
                        [model]: event.target.value as 'standard' | 'fast',
                      }))
                    }
                    className="h-9 w-full max-w-56 rounded-md border bg-background px-3 text-sm"
                    aria-label={`${label} 速度模式`}
                  >
                    <option value="standard">{SPEED_LABELS.standard}</option>
                    <option value="fast">{SPEED_LABELS.fast}</option>
                  </select>
                </div>
              ))}
            </div>
            {kind === 'formal' ? <label className="flex items-start gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={includeNoFeedback} onChange={(event) => setIncludeNoFeedback(event.target.checked)} className="mt-0.5" />同时生成无反馈 wave（追加独立 3 条条件流；会增加调用量与费用）</label> : null}
            <Button
              className="w-full"
              disabled={!confirmed || !audit.data?.ready || !kindAllowed || create.isPending}
              onClick={() => create.mutate({ name: selectedName, kind, protocol_id: protocolId, data_processing_confirmed: true, speed_modes: bindings, feedback_waves: kind === 'formal' && includeNoFeedback ? ['full', 'no_feedback'] : ['full'] }, {
                onSuccess: (project) => navigate(`/memory-study/${project.id}`),
              })}
            >
              创建并生成调用计划 <ArrowRight className="size-4" />
            </Button>
            <p className="text-xs text-muted-foreground">
              创建只生成固定清单、确定性检索快照和待执行调用，不会启动模型或产生费用。
            </p>
            {!kindAllowed ? <p className="text-xs text-destructive">当前规模不可用。</p> : null}
          </CardContent>
        </Card>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">研究项目</h2>
            <p className="text-sm text-muted-foreground">正式结果在完整性审计通过前保持封存。</p>
          </div>
          <Badge variant="outline" className="gap-1"><LockKeyhole className="size-3" /> 默认封存</Badge>
        </div>
        {projects.isLoading ? <p className="text-sm text-muted-foreground">加载项目…</p> : null}
        {!projects.isLoading && (projects.data ?? []).length === 0 ? (
          <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">还没有独立记忆研究项目。</CardContent></Card>
        ) : (
          <div className="grid gap-3">
            {(projects.data ?? []).map((project) => (
              <Card key={project.id} className="transition-shadow hover:shadow-md">
                <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-semibold">{project.name}</p>
                      <Badge variant="outline">{KIND_LABELS[project.kind]}</Badge>
                      <Badge variant="outline">{PROTOCOL_LABELS[project.protocol_id as MemoryStudyProtocol] ?? project.protocol_id}</Badge>
                      <Badge variant={statusVariant(project.status)}>{STATUS_LABELS[project.status] ?? project.status}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {project.progress.succeeded ?? 0}/{project.progress.total ?? 0} 次调用 ·{' '}
                      {project.config.models.map((model) => model.model).join(' / ')} · manifest {shortHash(project.data_manifest_sha256)}
                    </p>
                    {project.status === 'attention_required' && project.error_summary ? (
                      <p className="mt-1 max-w-xl truncate text-xs text-destructive" title={project.error_summary}>
                        {project.error_summary}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex gap-2">
                    <Button asChild variant="outline" size="sm">
                      <Link to={`/memory-study/${project.id}`}>查看运行台 <Play className="size-3.5" /></Link>
                    </Button>
                    {project.protocol_id === 'saf-memory-framework-v2' ? null : (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        disabled={project.status === 'running' || remove.isPending}
                        onClick={() =>
                          window.confirm(
                            `删除研究「${project.name}」？将删除全部调用记录、记忆库与产物目录，且不可恢复。`,
                          ) && remove.mutate(project.id)
                        }
                        aria-label={`删除 ${project.name}`}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
