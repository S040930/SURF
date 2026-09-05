import { Link } from 'react-router-dom';
import { ArrowRight, Database, FlaskConical, Lock } from 'lucide-react';

import {
  type ExperimentProject,
  useExperimentDatasets,
  useExperimentProjects,
  useExperimentRuntime,
  useExperimentTemplates,
} from '@/api/experiments';
import McpConnectionStatus from '@/components/r23/McpConnectionStatus';
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

function projectHref(project: ExperimentProject) {
  if (project.source_system === 'legacy-r23' && project.source_project_id) {
    return `/dress/${project.source_project_id}`;
  }
  return `/experiments/projects/${project.id}`;
}

export default function ExperimentsPage() {
  const datasets = useExperimentDatasets();
  const templates = useExperimentTemplates();
  const projects = useExperimentProjects();
  const runtime = useExperimentRuntime();

  const datasetList = datasets.data ?? [];
  const templateList = templates.data ?? [];
  const projectList = projects.data ?? [];

  return (
    <div className="mx-auto max-w-7xl space-y-7">
      <header className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div>
          <p className="text-sm font-medium text-primary">统一实验平台</p>
          <h1 className="mt-2 text-3xl font-bold">数据集 — 研究模板 — 项目</h1>
          <p className="mt-2 text-muted-foreground">
            统一平台承载注册的研究模板：数据集适配器负责受限数据的审计与抽样，
            模板固定抽样规则与分析方法，项目在冻结的运行快照上执行。
          </p>
        </div>
        <McpConnectionStatus
          runtime={runtime.data}
          isLoading={runtime.isLoading}
          isError={runtime.isError}
        />
      </header>

      <section className="grid gap-4 lg:grid-cols-2">
        {datasetList.map((dataset) => (
          <Card key={dataset.dataset_key}>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Database className="size-4 text-primary" />
                {dataset.name ?? dataset.dataset_key}
                <Badge variant={dataset.ready ? 'default' : 'destructive'}>
                  {dataset.ready ? '审计通过' : '未就绪'}
                </Badge>
                <Badge variant="outline" className="gap-1">
                  <Lock className="size-3" /> 受限
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm text-muted-foreground">
              {dataset.ready && dataset.report ? (
                <>
                  <p>
                    可评分唯一输入 <span className="font-semibold text-foreground">{dataset.report.unique_inputs}</span>；
                    空作文排除 {dataset.report.empty_essay_rows}；
                    标签冲突组 {dataset.report.conflict_groups}；
                    强制纳入尾部 {dataset.report.low_tail_inputs}。
                  </p>
                  <p className="font-mono text-xs">
                    {dataset.report.datasets[0]?.file} ·{' '}
                    {dataset.report.datasets[0]?.sha256.slice(0, 12)}…
                  </p>
                </>
              ) : (
                <p className="text-destructive">{dataset.error ?? '数据审计未通过'}</p>
              )}
              {dataset.license_note && <p className="text-xs">{dataset.license_note}</p>}
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          {templateList.map((template) => (
            <Badge key={template.template_id} variant="secondary" className="gap-1">
              <FlaskConical className="size-3" />
              {template.name}
              {template.require_runner_alignment && ' · 对齐比较'}
            </Badge>
          ))}
        </div>
        <Button asChild>
          <Link to="/experiments/create">
            创建统一实验项目 <ArrowRight className="size-4" />
          </Link>
        </Button>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold">项目</h2>
        {projectList.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              还没有统一实验项目。点击右上角“创建统一实验项目”开始。
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-3">
            {projectList.map((project) => (
              <Card key={project.id} className="transition-shadow hover:shadow-md">
                <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2 font-semibold">
                      {project.name}
                      <Badge variant="outline">
                        {project.kind === 'formal' ? '正式' : '技术试点'}
                      </Badge>
                      {project.read_only && (
                        <Badge variant="outline" className="text-muted-foreground">
                          legacy · {project.source_system}
                        </Badge>
                      )}
                      <Badge
                        variant={
                          project.status === 'completed'
                            ? 'default'
                            : project.status === 'attention_required'
                              ? 'destructive'
                              : 'secondary'
                        }
                      >
                        {statusLabel(project.status)}
                      </Badge>
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {project.template_id} · 模型{' '}
                      {project.runner_bindings.map((binding) => binding.model).join(' / ') || '—'}
                      {Object.keys(project.progress).length > 0 &&
                        ` · ${project.progress.succeeded ?? 0}/${project.progress.total ?? 0} 次调用`}
                    </p>
                  </div>
                  <Button asChild variant="outline" size="sm">
                    <Link to={projectHref(project)}>查看项目</Link>
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
