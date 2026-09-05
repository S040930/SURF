import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2 } from 'lucide-react';

import {
  type ExperimentKind,
  useCreateExperimentProject,
  useDatasetRevision,
  useExperimentProjects,
  useExperimentRubrics,
  useExperimentRunners,
  useExperimentTemplates,
} from '@/api/experiments';
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

export default function ExperimentCreatePage() {
  const navigate = useNavigate();
  const templates = useExperimentTemplates();
  const projects = useExperimentProjects();
  const runners = useExperimentRunners();
  const rubrics = useExperimentRubrics();
  const create = useCreateExperimentProject();

  const [name, setName] = useState('');
  const [kind, setKind] = useState<ExperimentKind>('formal');
  const [templateId, setTemplateId] = useState('');
  const [firstRunnerId, setFirstRunnerId] = useState('');
  const [secondRunnerId, setSecondRunnerId] = useState('');
  const [rubricId, setRubricId] = useState('');
  const [pilotId, setPilotId] = useState('');
  const [confirmed, setConfirmed] = useState(false);

  const templateList = templates.data ?? [];
  const selectedTemplate = templateList.find((item) => item.template_id === templateId);
  const revision = useDatasetRevision(selectedTemplate?.dataset_key ?? '', Boolean(selectedTemplate));
  const runnerList = runners.data ?? [];
  const rubricList = (rubrics.data ?? []).filter(
    (item) => !templateId || item.template_id === templateId,
  );

  const selectedRunners = [firstRunnerId, secondRunnerId]
    .filter(Boolean)
    .map((id) => runnerList.find((item) => item.id === id));
  const aligned =
    selectedRunners.length === (selectedTemplate?.runner_count ?? 2) &&
    selectedRunners.every(Boolean) &&
    new Set(selectedRunners.map((item) => item?.config_sha256)).size === 1 &&
    new Set(selectedRunners.map((item) => item?.model)).size ===
      selectedRunners.length;
  const selectedRubric = rubricList.find((item) => item.id === rubricId);
  const completedPilots = (projects.data ?? []).filter(
    (item) =>
      item.kind === 'pilot_run' &&
      item.status === 'completed' &&
      item.template_id === templateId,
  );

  const callPreview = useMemo(() => {
    if (!selectedTemplate) return null;
    const perBinding =
      selectedTemplate.template_id === 'dress_new_human_agreement_v1'
        ? kind === 'formal'
          ? '720 次正式 + 72 次复测'
          : '60 次（30 篇 × 2 模型）'
        : '按模板固定的抽样计划';
    return `${selectedTemplate.runner_count} 个模型 × ${perBinding}`;
  }, [kind, selectedTemplate]);

  const canCreate = Boolean(
    name.trim() &&
      selectedTemplate &&
      revision.data?.revision.id &&
      selectedRubric &&
      aligned &&
      confirmed,
  );

  const gates = [
    { ok: Boolean(selectedTemplate), label: '研究模板已选择' },
    { ok: Boolean(revision.data?.revision.id), label: '数据集审计已通过并锁定修订' },
    { ok: aligned, label: '两个模型配置已对齐（仅模型标识不同）' },
    { ok: Boolean(selectedRubric), label: '原始 DREsS rubric 已选择' },
    { ok: confirmed, label: '受限数据处理授权已确认' },
  ];

  const submit = () => {
    if (!canCreate || !selectedTemplate || !revision.data) return;
    create.mutate(
      {
        name: name.trim(),
        kind,
        template_id: selectedTemplate.template_id,
        dataset_revision_id: revision.data.revision.id,
        rubric_id: rubricId,
        runner_config_ids: [firstRunnerId, secondRunnerId].filter(Boolean),
        pilot_project_id: pilotId || undefined,
        data_processing_confirmed: true,
      },
      {
        onSuccess: (value) => {
          const created = value as { id?: string };
          if (created.id) navigate(`/experiments/projects/${created.id}`);
        },
      },
    );
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-sm font-medium text-primary">统一实验平台 / 创建项目</p>
        <h1 className="mt-2 text-3xl font-bold">创建统一实验项目</h1>
        <p className="mt-2 text-muted-foreground">
          抽样规模与种子由模板固定，不接受自定义。开始实验时平台自动保存完整运行快照。
        </p>
      </header>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">基本信息</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label htmlFor="exp-name" className="text-sm font-medium">
              实验名称
            </label>
            <Input
              id="exp-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：DREsS_New 人评一致性正式实验"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">项目类型</label>
            <Select value={kind} onValueChange={(value) => setKind(value as ExperimentKind)}>
              <SelectTrigger aria-label="项目类型">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="formal">正式实验</SelectItem>
                <SelectItem value="pilot_run">技术试点</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">研究模板</label>
            <Select value={templateId} onValueChange={setTemplateId}>
              <SelectTrigger aria-label="研究模板">
                <SelectValue placeholder="选择模板" />
              </SelectTrigger>
              <SelectContent>
                {templateList.map((template) => (
                  <SelectItem key={template.template_id} value={template.template_id}>
                    {template.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Rubric（原始 DREsS rubric）</label>
            <Select value={rubricId} onValueChange={setRubricId}>
              <SelectTrigger aria-label="Rubric">
                <SelectValue placeholder="选择 rubric" />
              </SelectTrigger>
              <SelectContent>
                {rubricList.map((rubric) => (
                  <SelectItem key={rubric.id} value={rubric.id}>
                    {rubric.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            模型配置（{selectedTemplate?.runner_count ?? 2} 个，必须对齐）
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">模型 A</label>
            <Select value={firstRunnerId} onValueChange={setFirstRunnerId}>
              <SelectTrigger aria-label="模型 A">
                <SelectValue placeholder="选择 runner" />
              </SelectTrigger>
              <SelectContent>
                {runnerList.map((runner) => (
                  <SelectItem key={runner.id} value={runner.id}>
                    {runner.name}（{runner.model} · {runner.reasoning_effort}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">模型 B</label>
            <Select value={secondRunnerId} onValueChange={setSecondRunnerId}>
              <SelectTrigger aria-label="模型 B">
                <SelectValue placeholder="选择 runner" />
              </SelectTrigger>
              <SelectContent>
                {runnerList.map((runner) => (
                  <SelectItem key={runner.id} value={runner.id}>
                    {runner.name}（{runner.model} · {runner.reasoning_effort}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {selectedRunners.every(Boolean) && (
            <p className="text-xs md:col-span-2">
              {aligned ? (
                <span className="inline-flex items-center gap-1 text-emerald-700">
                  <CheckCircle2 className="size-4" />
                  两个 runner 的推理强度、速度、超时一致，仅模型标识不同。
                </span>
              ) : (
                <span className="text-destructive">
                  两个 runner 参数不一致或模型标识重复，无法通过比较模板校验。
                </span>
              )}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">抽样与隐私预览</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            预估调用量：<span className="font-semibold text-foreground">{callPreview ?? '—'}</span>
          </p>
          {revision.data?.revision.audit && (
            <p>
              数据审计：唯一输入 {revision.data.revision.audit.unique_inputs}；空作文{' '}
              {revision.data.revision.audit.empty_essay_rows}（排除）；冲突组{' '}
              {revision.data.revision.audit.conflict_groups}（排除）；强制纳入{' '}
              {revision.data.revision.audit.low_tail_inputs}。导出文件只包含哈希、标签、预测与审计元数据，
              不包含作文正文。
            </p>
          )}
          {kind === 'formal' && completedPilots.length > 0 && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">匹配的已完成技术试点（可选）</label>
              <Select value={pilotId} onValueChange={setPilotId}>
                <SelectTrigger aria-label="匹配的已完成技术试点">
                  <SelectValue placeholder="不绑定试点" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不绑定试点</SelectItem>
                  {completedPilots.map((pilot) => (
                    <SelectItem key={pilot.id} value={pilot.id}>
                      {pilot.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <label className="flex items-start gap-2 pt-1 text-foreground">
            <input
              type="checkbox"
              className="mt-1"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            <span className="text-sm">
              我已获得使用所选模型服务处理该受限数据的授权，并将遵守研究伦理与保密要求。
            </span>
          </label>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={submit} disabled={!canCreate || create.isPending}>
          创建项目
        </Button>
        <div className="flex flex-wrap gap-2">
          {gates.map((gate) => (
            <Badge
              key={gate.label}
              variant={gate.ok ? 'default' : 'outline'}
              className={gate.ok ? '' : 'text-muted-foreground'}
            >
              {gate.ok ? '✓' : '○'} {gate.label}
            </Badge>
          ))}
        </div>
      </div>
    </div>
  );
}
