import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { FlaskConical } from 'lucide-react';

import {
  useCreateR20Project,
  useDeleteR20Project,
  useR20Lifecycle,
  useR20ModelConfigs,
  useR20Projects,
  useR20PromptVersions,
} from '@/api/r20';
import type { R20ProjectKind } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmActionDialog } from '@/components/ConfirmActionDialog';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const labels: Record<R20ProjectKind, string> = {
  pilot_run: '技术试点（2题）',
  formal: '正式实验（8题）',
};
const PROTOCOL_ID = 'r20-saf-official-split-2026-08-v4-8q-60m-15t';

export default function R20ProjectsPage() {
  const navigate = useNavigate();
  const projects = useR20Projects();
  const models = useR20ModelConfigs();
  const prompts = useR20PromptVersions();
  const create = useCreateR20Project();
  const lifecycle = useR20Lifecycle();
  const remove = useDeleteR20Project();
  const [name, setName] = useState('');
  const [kind, setKind] = useState<R20ProjectKind>('pilot_run');
  const [firstModel, setFirstModel] = useState('');
  const [secondModel, setSecondModel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const frozenModels = (models.data ?? []).filter(
    (item) => item.status === 'frozen',
  );
  const activeFrozenPrompts = (prompts.data ?? []).filter(
    (item) => item.status === 'frozen' && item.protocol_id === PROTOCOL_ID,
  );
  const frozenPrompts = activeFrozenPrompts;
  const hasModels = frozenModels.length >= 2;
  const hasPrompt = activeFrozenPrompts.length > 0;
  const promptCompatible = frozenPrompts.some((item) => item.id === prompt);
  const ready = Boolean(
    name.trim() &&
    hasModels &&
    hasPrompt &&
    firstModel &&
    secondModel &&
    firstModel !== secondModel &&
    promptCompatible,
  );

  const creationBlocker =
    models.isLoading || prompts.isLoading
      ? '正在载入可用配置…'
      : !hasModels
        ? `还需要 ${2 - frozenModels.length} 个已冻结的模型配置`
        : !hasPrompt
          ? '还需要一个已冻结的 v4 提示词版本'
          : !name.trim()
              ? '请填写项目名称'
              : !firstModel || !secondModel
                  ? '请选择两个不同的模型配置'
                  : !promptCompatible
                    ? '请选择已冻结的提示词版本'
                  : null;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">{PROTOCOL_ID}</p>
        <h1 className="text-3xl font-bold">研究项目</h1>
        <p className="mt-2 max-w-3xl text-muted-foreground">
          主估计为 h=60 的 ARM−CRM；正式实验固定8题、每题15个测试端点。运行期间结果保持盲态。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FlaskConical className="size-5 text-primary" />
            创建 r20 项目
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          {(creationBlocker || !ready) && (
            <div className="md:col-span-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
              <p>{creationBlocker ?? '请完成以下选择后创建项目。'}</p>
              {(!hasModels || !hasPrompt) && (
                <p className="mt-2 text-xs">
                  项目只会载入已冻结的版本，避免运行时配置被更改。请先到{' '}
                  <Link className="underline" to="/models">
                    模型配置
                  </Link>{' '}
                  冻结两个不同的模型配置，并到{' '}
                  <Link className="underline" to="/prompts">
                    提示词配置
                  </Link>{' '}
                  冻结提示词版本即可创建项目。
                </p>
              )}
            </div>
          )}
          <Input
            aria-label="项目名称"
            placeholder="项目名称"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <Select
            value={kind}
            onValueChange={(value) => setKind(value as R20ProjectKind)}
          >
            <SelectTrigger aria-label="项目类型">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(labels).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={firstModel}
            onValueChange={setFirstModel}
            disabled={!hasModels}
          >
            <SelectTrigger aria-label="模型一">
              <SelectValue
                placeholder={
                  hasModels ? '选择模型配置一' : '尚无两个已冻结模型'
                }
              />
            </SelectTrigger>
            <SelectContent>
              {frozenModels.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={secondModel}
            onValueChange={setSecondModel}
            disabled={!hasModels}
          >
            <SelectTrigger aria-label="模型二">
              <SelectValue
                placeholder={
                  hasModels ? '选择模型配置二' : '尚无两个已冻结模型'
                }
              />
            </SelectTrigger>
            <SelectContent>
              {frozenModels.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={prompt}
            onValueChange={setPrompt}
            disabled={!hasPrompt || !firstModel || !secondModel || frozenPrompts.length === 0}
          >
            <SelectTrigger aria-label="提示词版本">
              <SelectValue
                placeholder={frozenPrompts.length ? '选择已冻结的提示词版本' : '没有已冻结的提示词版本'}
              />
            </SelectTrigger>
            <SelectContent>
              {frozenPrompts.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="md:col-span-2">
            <Button
              disabled={!ready || create.isPending}
              onClick={() =>
                create.mutate(
                  {
                    name: name.trim(),
                    kind,
                    model_config_ids: [firstModel, secondModel],
                    prompt_version_id: prompt,
                  },
                  { onSuccess: (item) => navigate(`/research/${item.id}`) },
                )
              }
            >
              {create.isPending ? '正在创建…' : '创建项目'}
            </Button>
          </div>
        </CardContent>
      </Card>
      <section className="grid gap-3 md:grid-cols-2">
        {(projects.data ?? []).map((item) => (
          <div
            key={item.id}
            className="rounded-xl border bg-card p-4 hover:border-primary/40"
          >
            <Link to={`/research/${item.id}`} className="block">
              <div className="flex justify-between gap-3">
                <strong>{item.name}</strong>
                <span className="rounded bg-muted px-2 py-1 text-xs">
                  {item.status}
                </span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {labels[item.kind]} · {item.prompt_version_name}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                已记录 {item.progress?.recorded ?? 0} /{' '}
                {item.progress?.total ?? 0} 次调用
                {(item.progress?.failed_terminal ?? 0) > 0 && (
                  <> · <span className="text-destructive">失败 {item.progress?.failed_terminal}</span></>
                )}
              </p>
            </Link>
            <div className="mt-3 flex flex-wrap gap-2">
              {item.protocol_id !== PROTOCOL_ID && <span className="text-xs text-muted-foreground">v1 历史只读</span>}
              {item.protocol_id === PROTOCOL_ID && <>
              {['queued', 'running'].includes(item.status) && (
                <Button size="sm" variant="outline" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ projectId: item.id, action: 'pause' })}>
                  暂停
                </Button>
              )}
              {item.status === 'paused' && (
                <Button size="sm" variant="outline" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ projectId: item.id, action: 'resume' })}>
                  恢复
                </Button>
              )}
              <Button size="sm" variant="destructive" disabled={remove.isPending} onClick={() => setDeleteTarget(item.id)}>
                删除
              </Button>
              </>}
            </div>
          </div>
        ))}
      </section>
      <ConfirmActionDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="删除项目"
        description="将永久删除该项目及其全部调用、记忆和报告，且无法恢复。运行中的项目会先被终止。"
        confirmLabel="永久删除"
        cancelLabel="取消"
        dangerous
        pending={remove.isPending}
        pendingLabel="正在删除…"
        onConfirm={() => {
          if (deleteTarget === null) return;
          remove.mutate(deleteTarget, {
            onSuccess: () => setDeleteTarget(null),
          });
        }}
      />
    </div>
  );
}
