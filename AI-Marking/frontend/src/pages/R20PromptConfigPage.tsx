import { useState } from 'react';

import {
  useCreateR20PromptValidationSuite,
  useCreateR20PromptVersion,
  useDeleteR20PromptVersion,
  useFreezeR20PromptVersion,
  useR20ModelConfigs,
  useR20PromptValidationSuites,
  useR20PromptVersions,
} from '@/api/r20';
import type { R20PromptTemplates, R20PromptVersion } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';

const PROTOCOL_ID = 'r20-saf-official-split-2026-08-v4-8q-60m-15t';
const EMPTY: R20PromptTemplates = { scoring: '', crm_update: '', arm_update: '' };
const FIELDS: Array<{ key: keyof R20PromptTemplates; label: string; hint: string }> = [
  { key: 'scoring', label: '共同评分提示词', hint: '反馈不超过 118 o200k_base tokens；逐项核对可观察证据，不把记忆转换成固定分值或自动判定。' },
  { key: 'crm_update', label: 'CRM 更新提示词', hint: '原子化的局部条件规则；不得写入 rubric、criterion、总体质量或任何评分映射。' },
  { key: 'arm_update', label: 'ARM 更新提示词', hint: '稳定题目级名词性维度与三档平行 anchors；criterion 不得包含 IF/when/答案行为；每个 anchor 约 14 token 以内以留出合计预算余量。' },
];

function VersionCard({
  version,
  firstModel,
  secondModel,
}: {
  version: R20PromptVersion;
  firstModel: string;
  secondModel: string;
}) {
  const suites = useR20PromptValidationSuites(version.id);
  const validate = useCreateR20PromptValidationSuite();
  const freeze = useFreezeR20PromptVersion();
  const remove = useDeleteR20PromptVersion();
  const latest = suites.data?.[0];
  const passed = (suites.data ?? []).filter((suite) => suite.status === 'passed');
  const activeProtocol = version.protocol_id === PROTOCOL_ID;
  const canValidate = activeProtocol && version.status === 'draft' && firstModel && secondModel && firstModel !== secondModel && passed.length === 0 && !['queued', 'running'].includes(latest?.status ?? '');
  const percent = latest ? Math.round((latest.completed_calls / latest.expected_calls) * 100) : 0;

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <strong>{version.name}</strong>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{version.templates_sha256}</p>
          </div>
          <span className="rounded bg-muted px-2 py-1 text-xs">{activeProtocol ? version.status : '历史版本只读'}</span>
        </div>
        {activeProtocol && latest && (
          <div className="space-y-2 rounded-lg border p-3 text-sm">
            <div className="flex justify-between gap-3">
              <span>Validation suite：{latest.status}</span>
              <span>{latest.completed_calls} / {latest.expected_calls}（{percent}%）</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary transition-all" style={{ width: `${percent}%` }} />
            </div>
            {latest.failure_reason && <p className="text-destructive">{latest.failure_reason}</p>}
          </div>
        )}
        {activeProtocol && version.status === 'draft' && (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={!canValidate || validate.isPending}
              onClick={() => validate.mutate({ versionId: version.id, model_config_ids: [firstModel, secondModel] })}
            >
              {validate.isPending ? '正在入队…' : '开始 48-call 验证'}
            </Button>
            <Button
              size="sm"
              disabled={freeze.isPending}
              onClick={() => freeze.mutate(version.id)}
            >
              冻结版本
            </Button>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(`确定删除提示词版本“${version.name}”吗？此操作不可撤销。`)) {
                remove.mutate(version.id);
              }
            }}
          >
            删除版本
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function R20PromptConfigPage() {
  const versions = useR20PromptVersions();
  const models = useR20ModelConfigs();
  const create = useCreateR20PromptVersion();
  const [name, setName] = useState('');
  const [templates, setTemplates] = useState<R20PromptTemplates>(EMPTY);
  const [firstModel, setFirstModel] = useState('');
  const [secondModel, setSecondModel] = useState('');
  const frozenModels = (models.data ?? []).filter((item) => item.status === 'frozen');
  const ready = Boolean(name.trim() && templates.scoring.trim() && templates.crm_update.trim() && templates.arm_update.trim());

  return <div className="mx-auto max-w-5xl space-y-6">
    <div><p className="text-sm text-muted-foreground">{PROTOCOL_ID}</p><h1 className="text-3xl font-bold">提示词配置与验证</h1><p className="mt-2 text-muted-foreground">文档中的三段规范正文是唯一基线。系统会忽略复制产生的首尾空行及换行格式差异，再逐段校验正文；若不一致会明确指出对应段落。</p></div>
    <Card><CardHeader><CardTitle>保存规范候选版本</CardTitle></CardHeader><CardContent className="space-y-4">
      <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="版本名称" aria-label="版本名称" />
      {FIELDS.map(({ key, label, hint }) => <label key={key} className="block space-y-2"><span className="font-medium">{label}</span><span className="block text-sm text-muted-foreground">{hint}</span><Textarea value={templates[key]} onChange={(event) => setTemplates((current) => ({ ...current, [key]: event.target.value }))} className="min-h-40 font-mono text-xs" /></label>)}
      <Button disabled={!ready || create.isPending} onClick={() => create.mutate({ name: name.trim(), templates }, { onSuccess: () => { setName(''); setTemplates(EMPTY); } })}>{create.isPending ? '保存中…' : '保存规范版本'}</Button>
    </CardContent></Card>
    <Card><CardHeader><CardTitle>Validation suite 模型对</CardTitle></CardHeader><CardContent className="grid gap-3 md:grid-cols-2">
      <Select value={firstModel} onValueChange={setFirstModel}><SelectTrigger aria-label="验证模型一"><SelectValue placeholder="选择已冻结模型一" /></SelectTrigger><SelectContent>{frozenModels.map((item) => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectContent></Select>
      <Select value={secondModel} onValueChange={setSecondModel}><SelectTrigger aria-label="验证模型二"><SelectValue placeholder="选择已冻结模型二" /></SelectTrigger><SelectContent>{frozenModels.map((item) => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectContent></Select>
      <p className="text-sm text-muted-foreground md:col-span-2">固定流程为两模型 × 两题：CRM/ARM 各按 high→low→middle 连续更新，再对两个留出答案分别运行 NM、CRM、ARM。验证不比较效果，也不显示 ARM−CRM。</p>
    </CardContent></Card>
    <section className="space-y-3"><h2 className="text-lg font-semibold">已保存版本</h2>{(versions.data ?? []).map((version) => <VersionCard key={version.id} version={version} firstModel={firstModel} secondModel={secondModel} />)}</section>
  </div>;
}
