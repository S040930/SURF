import { useState } from 'react';
import { Cog, Database, Pencil, Plus, ShieldAlert, Trash2 } from 'lucide-react';

import {
  type MemoryStudyRunnerConfig,
  type MemoryStudySpeedMode,
  useCreateMemoryStudyRunner,
  useDeleteMemoryStudyRunner,
  useMemoryStudyProtocolConfig,
  useMemoryStudyRunners,
  useMemoryStudyRuntime,
  useMemoryStudySiteConfig,
  useSaveMemoryStudySiteConfig,
  useUpdateMemoryStudyRunner,
} from '@/api/memoryStudy';
import MemoryStudyMcpStatus from '@/components/MemoryStudyMcpStatus';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

/** 模型槽位与 Codex 运行模式由协议冻结；与后端 MODEL_IDS 保持一致。 */
const MODEL_OPTIONS = ['gpt-6-luna'];
const SPEED_MODES: MemoryStudySpeedMode[] = ['standard', 'fast'];

const SPEED_LABELS: Record<MemoryStudySpeedMode, string> = {
  standard: 'standard（标准）',
  fast: 'fast（快速）',
};

function RunnerFormFields({
  model,
  onModelChange,
  effort,
  onEffortChange,
  speed,
  onSpeedChange,
  timeout,
  onTimeoutChange,
  modelDisabled,
  speedDisabled,
}: {
  model: string;
  onModelChange: (value: string) => void;
  effort: string;
  onEffortChange: (value: string) => void;
  speed: MemoryStudySpeedMode;
  onSpeedChange: (value: MemoryStudySpeedMode) => void;
  timeout: string;
  onTimeoutChange: (value: string) => void;
  modelDisabled?: boolean;
  speedDisabled?: boolean;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-4">
      <div className="space-y-2">
        <Label htmlFor="runner-model">模型</Label>
        <Select
          value={model}
          onValueChange={onModelChange}
          disabled={modelDisabled}
        >
          <SelectTrigger id="runner-model" aria-label="模型">
            <SelectValue placeholder="选择模型" />
          </SelectTrigger>
          <SelectContent>
            {MODEL_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="runner-effort">reasoning_effort</Label>
        <Input
          id="runner-effort"
          value={effort}
          onChange={(event) => onEffortChange(event.target.value)}
          placeholder="medium"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="runner-speed">speed_mode</Label>
        <Select
          value={speed}
          onValueChange={onSpeedChange}
          disabled={speedDisabled}
        >
          <SelectTrigger id="runner-speed" aria-label="运行模式">
            <SelectValue placeholder="选择运行模式" />
          </SelectTrigger>
          <SelectContent>
            {SPEED_MODES.map((option) => (
              <SelectItem key={option} value={option}>
                {SPEED_LABELS[option]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="runner-timeout">timeout（秒）</Label>
        <Input
          id="runner-timeout"
          value={timeout}
          onChange={(event) => onTimeoutChange(event.target.value)}
          type="number"
          min={1}
          max={3600}
        />
      </div>
    </div>
  );
}

function RunnerList({
  runners,
}: {
  runners: MemoryStudyRunnerConfig[];
}) {
  const remove = useDeleteMemoryStudyRunner();
  const update = useUpdateMemoryStudyRunner();
  const [editing, setEditing] = useState<MemoryStudyRunnerConfig | null>(null);
  const [effort, setEffort] = useState('');
  const [timeout, setTimeoutValue] = useState('120');

  const openEdit = (runner: MemoryStudyRunnerConfig) => {
    setEditing(runner);
    setEffort(runner.reasoning_effort);
    setTimeoutValue(String(runner.timeout_seconds));
  };

  return (
    <div className="space-y-3">
      {runners.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无配置，使用协议冻结默认值。</p>
      ) : null}
      {(runners ?? []).map((item) => (
        <Card key={`${item.model}:${item.speed_mode}`}>
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
            <div className="min-w-0">
              <p className="font-semibold">
                {item.model}
                <Badge
                  variant={item.speed_mode === 'fast' ? 'default' : 'secondary'}
                  className="ml-2"
                >
                  {SPEED_LABELS[item.speed_mode]}
                </Badge>
              </p>
              <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                {item.reasoning_effort} · {item.timeout_seconds}s
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => openEdit(item)}
                aria-label={`编辑 ${item.model} ${item.speed_mode}`}
              >
                <Pencil className="size-3.5" />
                编辑
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() =>
                  window.confirm(
                    `删除 ${item.model}（${item.speed_mode}）配置？将回退协议冻结默认值。`,
                  ) &&
                  remove.mutate({ model: item.model, speed_mode: item.speed_mode })
                }
                aria-label={`删除 ${item.model} ${item.speed_mode}`}
              >
                <Trash2 className="size-3.5" />
                删除
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
      <Dialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              编辑模型配置 · {editing?.model}（{editing ? SPEED_LABELS[editing.speed_mode] : ''}）
            </DialogTitle>
          </DialogHeader>
          {editing ? (
            <div className="space-y-3">
              <RunnerFormFields
                model={editing.model}
                onModelChange={() => undefined}
                modelDisabled
                speed={editing.speed_mode}
                onSpeedChange={() => undefined}
                speedDisabled
                effort={effort}
                onEffortChange={setEffort}
                timeout={timeout}
                onTimeoutChange={setTimeoutValue}
              />
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)} disabled={update.isPending}>
              取消
            </Button>
            <Button
              disabled={!editing || update.isPending || Number(timeout) < 1}
              onClick={() =>
                editing &&
                update.mutate(
                  {
                    model: editing.model,
                    reasoning_effort: effort,
                    speed_mode: editing.speed_mode,
                    timeout_seconds: Number(timeout),
                  },
                  { onSuccess: () => setEditing(null) },
                )
              }
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function EnvironmentCard() {
  const siteConfig = useMemoryStudySiteConfig();
  const protocol = useMemoryStudyProtocolConfig();
  const save = useSaveMemoryStudySiteConfig();
  const [model, setModel] = useState('');
  const [revision, setRevision] = useState('');
  const [apiBase, setApiBase] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [dims, setDims] = useState('');

  const current = siteConfig.data;
  const editingModel = model !== '' ? model : current?.embedding_model ?? '';
  const editingRevision =
    revision !== '' ? revision : current?.embedding_revision ?? '';
  const editingApiBase =
    apiBase !== '' ? apiBase : current?.embedding_api_base ?? '';
  const cloudMode = true;

  const apply = () => {
    if (!current) return;
    setModel(current.embedding_model);
    setRevision(current.embedding_revision);
    setApiBase(current.embedding_api_base);
    setApiKey('');
    setDims(current.embedding_dims != null ? String(current.embedding_dims) : '');
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Database className="size-4 text-primary" />
          运行环境
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="embedding-backend">embedding backend</Label>
            <Input id="embedding-backend" value="云端 API（OpenAI 兼容）" disabled />
          </div>
          <div className="space-y-2">
            <Label htmlFor="embedding-model">embedding model</Label>
            <Input
              id="embedding-model"
              value={editingModel}
              onChange={(event) => setModel(event.target.value)}
              disabled={current == null}
              placeholder={
                'doubao-embedding-vision'
              }
            />
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor="embedding-revision">embedding revision</Label>
              {current && !current.revision_pinned ? (
                <Badge
                  variant="destructive"
                  className="gap-1 text-[11px]"
                >
                  <ShieldAlert className="size-3" />
                  revision 未固定
                </Badge>
              ) : null}
            </div>
            <Input
              id="embedding-revision"
              value={editingRevision}
              onChange={(event) => setRevision(event.target.value)}
              disabled={current == null}
              placeholder="云端模型快照版本，如 doubao-embedding-vision-250615"
            />
          </div>
          {cloudMode ? (
            <div className="space-y-2">
              <Label htmlFor="embedding-dims">embedding dims（可选）</Label>
              <Input
                id="embedding-dims"
                value={dims}
                onChange={(event) => setDims(event.target.value)}
                disabled={current == null}
                placeholder="留空自动；如 2048"
                inputMode="numeric"
              />
            </div>
          ) : null}
        </div>
        {cloudMode ? (
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="embedding-api-base">API base URL</Label>
              <Input
                id="embedding-api-base"
                value={editingApiBase}
                onChange={(event) => setApiBase(event.target.value)}
                disabled={current == null}
                placeholder="https://ark.cn-beijing.volces.com/api/v3"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="embedding-api-key">API key</Label>
              <Input
                id="embedding-api-key"
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                disabled={current == null}
                placeholder={
                  current?.embedding_api_key_set
                    ? '已配置，留空保持不变'
                    : 'sk-...'
                }
              />
            </div>
          </div>
        ) : null}
        {current && !current.revision_pinned ? (
          <p className="text-xs text-destructive">
            revision 未固定；冻结前必须配置不可变 revision（非 &quot;&quot;/main/latest/unresolved）。
          </p>
        ) : null}
        <div className="flex items-center justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={apply}
            disabled={!current || save.isPending}
          >
            重置
          </Button>
          <Button
            size="sm"
            onClick={() =>
              current != null &&
              save.mutate({
                embedding_backend: 'openai',
                embedding_model: model.trim() || current.embedding_model,
                embedding_revision:
                  revision.trim() || current.embedding_revision,
                embedding_api_base: cloudMode
                  ? apiBase.trim() || current.embedding_api_base
                  : '',
                embedding_api_key: apiKey.trim(),
                embedding_dims: cloudMode && dims.trim() ? Number(dims) : null,
              })
            }
            disabled={!current || save.isPending}
          >
            保存 embedding 配置
          </Button>
        </div>

        <div className="rounded-lg bg-muted/60 p-4">
          <p className="text-xs font-semibold text-muted-foreground">协议环境（只读）</p>
          <dl className="mt-2 grid gap-1.5 text-sm">
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">protocol</dt>
              <dd className="font-mono text-xs">{protocol.data?.protocol ?? '…'}</dd>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">tokenizer</dt>
              <dd className="font-mono text-xs">{protocol.data?.tokenizer ?? '…'}</dd>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">codex_subprocess_limit</dt>
              <dd className="font-mono text-xs">{protocol.data?.codex_subprocess_limit ?? '…'}</dd>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">official_frameworks</dt>
              <dd className="font-mono text-xs">
                {protocol.data
                  ? Object.entries(protocol.data.official_frameworks)
                      .map(([name, info]) => `${name}@${info.revision}`)
                      .join(' · ')
                  : '…'}
              </dd>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">artifact_root</dt>
              <dd className="font-mono text-xs">{protocol.data?.artifact_root ?? '…'}</dd>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">framework_contract</dt>
              <dd className="font-mono text-xs">
                {protocol.data?.framework_contract.join(' · ') ?? '…'}
              </dd>
            </div>
          </dl>
        </div>
      </CardContent>
    </Card>
  );
}

export default function MemoryStudyConfigPage() {
  const runtime = useMemoryStudyRuntime();
  const runners = useMemoryStudyRunners();
  const create = useCreateMemoryStudyRunner();
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('medium');
  const [speed, setSpeed] = useState<MemoryStudySpeedMode>('standard');
  const [timeout, setTimeoutValue] = useState('120');

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-sm font-medium text-primary">独立 SAF 2.0 研究平台</p>
        <h1 className="mt-2 text-3xl font-bold">模型配置</h1>
        <p className="mt-2 max-w-3xl text-muted-foreground">
          绑定 SAF 记忆研究：模型槽位冻结为 Luna，可编辑运行参数与
          embedding 配置；保存后用于新建研究，已冻结或运行中的研究不会被覆盖。
        </p>
      </header>

      <MemoryStudyMcpStatus
        runtime={runtime.data}
        isLoading={runtime.isLoading}
        isError={runtime.isError}
      />

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Cog className="size-4 text-primary" />
            模型配置
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3 rounded-lg border p-4">
            <p className="text-sm font-semibold">新建配置</p>
            <RunnerFormFields
              model={model}
              onModelChange={setModel}
              effort={effort}
              onEffortChange={setEffort}
              speed={speed}
              onSpeedChange={setSpeed}
              timeout={timeout}
              onTimeoutChange={setTimeoutValue}
            />
            <Button
              size="sm"
              disabled={!model || create.isPending}
              onClick={() =>
                create.mutate(
                  {
                    model,
                    reasoning_effort: effort || 'medium',
                    speed_mode: speed,
                    timeout_seconds: Number(timeout) || 120,
                  },
                  {
                    onSuccess: () => {
                      setModel('');
                      setEffort('medium');
                      setSpeed('standard');
                      setTimeoutValue('120');
                    },
                  },
                )
              }
            >
              <Plus className="size-3.5" />
              创建
            </Button>
            <p className="text-xs text-muted-foreground">
              speed_mode 对应 Codex 运行模式，仅支持 standard / fast 两个选项。
            </p>
          </div>

          {runners.isLoading ? (
            <p className="text-sm text-muted-foreground">加载模型配置…</p>
          ) : (
            <RunnerList runners={runners.data ?? []} />
          )}
        </CardContent>
      </Card>

      <EnvironmentCard />
    </div>
  );
}
