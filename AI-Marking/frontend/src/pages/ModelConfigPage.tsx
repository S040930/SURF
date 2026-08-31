import { useState } from 'react';
import { Cog, Copy, LockKeyhole, Pencil, Save, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import {
  useCloneR20ModelConfig,
  useCreateR20ModelConfig,
  useDeleteR20ModelConfig,
  useFreezeR20ModelConfig,
  useR20ModelConfigs,
  useUpdateR20ModelConfig,
} from '@/api/r20';
import type { R20ModelConfig } from '@/api/types';
import { ConfirmActionDialog } from '@/components/ConfirmActionDialog';
import { Field } from '@/components/Field';
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

interface FormState {
  name: string;
  base_url: string;
  requested_model: string;
  expected_returned_model: string;
  api_key_env: string;
  api_key: string;
  response_format: 'json_schema' | 'json_object';
  timeout_seconds: string;
  inputPrice: string;
  outputPrice: string;
}

const emptyForm: FormState = {
  name: '',
  base_url: 'https://',
  requested_model: '',
  expected_returned_model: '',
  api_key_env: '',
  api_key: '',
  response_format: 'json_schema',
  timeout_seconds: '120',
  inputPrice: '0',
  outputPrice: '0',
};

function fromConfig(name: string, config: Record<string, unknown>): FormState {
  return {
    name,
    base_url: String(config.base_url ?? ''),
    requested_model: String(config.requested_model ?? ''),
    expected_returned_model: String(config.expected_returned_model ?? ''),
    api_key_env: String(config.api_key_env ?? ''),
    api_key: '',
    response_format:
      config.response_format === 'json_object' ? 'json_object' : 'json_schema',
    timeout_seconds: String(config.timeout_seconds ?? 120),
    inputPrice: String(
      ((config.input_cost_fen_per_million as number) ?? 0) / 100,
    ),
    outputPrice: String(
      ((config.output_cost_fen_per_million as number) ?? 0) / 100,
    ),
  };
}

function toConfig(
  form: FormState,
  existingApiKeyEnv: string,
): Record<string, unknown> {
  return {
    provider: 'openai_compatible',
    base_url: form.base_url.trim(),
    requested_model: form.requested_model.trim(),
    expected_returned_model: form.expected_returned_model.trim(),
    api_key_env: existingApiKeyEnv,
    temperature: 0,
    response_format: form.response_format,
    timeout_seconds: Number(form.timeout_seconds) || 120,
    input_cost_fen_per_million: Math.max(
      0,
      Math.round((Number(form.inputPrice) || 0) * 100),
    ),
    output_cost_fen_per_million: Math.max(
      0,
      Math.round((Number(form.outputPrice) || 0) * 100),
    ),
  };
}

export default function ModelConfigPage() {
  const configs = useR20ModelConfigs();
  const create = useCreateR20ModelConfig();
  const patch = useUpdateR20ModelConfig();
  const clone = useCloneR20ModelConfig();
  const remove = useDeleteR20ModelConfig();
  const freeze = useFreezeR20ModelConfig();

  const deleteConfig = (config: R20ModelConfig) => {
    if (
      window.confirm(`确定删除模型配置“${config.name}”吗？此操作不可撤销。`)
    ) {
      remove.mutate(config.id);
    }
  };

  const [form, setForm] = useState<FormState>(emptyForm);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingSnapshot, setEditingSnapshot] = useState<FormState | null>(
    null,
  );
  const [discardOpen, setDiscardOpen] = useState(false);
  const list = configs.data ?? [];
  const editing =
    editingId !== null
      ? (list.find((config) => config.id === editingId) ?? null)
      : null;

  const setField = (field: keyof FormState, value: string) =>
    setForm((current) => ({ ...current, [field]: value }));

  const beginNew = () => {
    setForm(emptyForm);
    setEditingId(null);
    setEditingSnapshot(null);
    setDiscardOpen(false);
  };

  const beginEdit = (config: R20ModelConfig) => {
    const nextForm = fromConfig(config.name, config.config_json);
    setForm(nextForm);
    setEditingId(config.id);
    setEditingSnapshot(nextForm);
  };

  const cancelEdit = () => {
    if (
      editingSnapshot &&
      JSON.stringify(form) !== JSON.stringify(editingSnapshot)
    ) {
      setDiscardOpen(true);
      return;
    }
    beginNew();
  };

  const submit = () => {
    if (
      !form.name.trim() ||
      !form.requested_model.trim() ||
      !form.base_url.trim()
    ) {
      toast.error('请填写名称、模型 ID 与模型服务 URL');
      return;
    }
    const existingApiKeyEnv = String(editing?.config_json.api_key_env ?? '');
    const config_json = toConfig(form, existingApiKeyEnv);
    const payload = {
      name: form.name.trim(),
      config_json,
      ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}),
    };
    if (editingId) {
      patch.mutate({ id: editingId, payload }, { onSuccess: beginNew });
    } else {
      create.mutate(payload, { onSuccess: beginNew });
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">r20 两模型服务配置</p>
        <h2 className="text-2xl font-bold tracking-tight">模型配置</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          直接填写 API Key；密钥只保存到后端本地 .env，不进入数据库或网页响应。
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2">
              <span className="flex items-center gap-2">
                <Cog className="size-5" />
                {editing ? '编辑模型配置' : '新建模型配置'}
              </span>
            </CardTitle>
            {editing && (
              <Button size="sm" variant="ghost" onClick={cancelEdit}>
                取消编辑
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Field label="配置名称" htmlFor="mc-name" required>
            <Input
              id="mc-name"
              value={form.name}
              onChange={(event) => setField('name', event.target.value)}
              placeholder="例如：provider-model-id"
            />
          </Field>
          <Field label="模型 ID" htmlFor="mc-model" required>
            <Input
              id="mc-model"
              value={form.requested_model}
              onChange={(event) =>
                setField('requested_model', event.target.value)
              }
              placeholder="例如：ep-xxxxxxxx"
            />
          </Field>
          <Field
            label="预期返回模型 ID"
            htmlFor="mc-expected"
            required
            hint="通常与模型 ID 相同"
          >
            <Input
              id="mc-expected"
              value={form.expected_returned_model}
              onChange={(event) =>
                setField('expected_returned_model', event.target.value)
              }
              placeholder="例如：ep-xxxxxxxx"
            />
          </Field>
          <Field label="模型服务 URL" htmlFor="mc-url" required>
            <Input
              id="mc-url"
              value={form.base_url}
              onChange={(event) => setField('base_url', event.target.value)}
              placeholder="https://api.example.com/v1"
            />
          </Field>
          <Field
            label="API Key"
            htmlFor="mc-key"
            hint={
              editing
                ? '留空表示继续使用当前密钥'
                : '只在本机后端保存，不会写入数据库'
            }
          >
            <Input
              id="mc-key"
              type="password"
              value={form.api_key}
              onChange={(event) => setField('api_key', event.target.value)}
              placeholder="粘贴模型服务 API Key"
            />
          </Field>
          <Field label="超时（秒）" htmlFor="mc-timeout" required>
            <Input
              id="mc-timeout"
              type="number"
              min="1"
              value={form.timeout_seconds}
              onChange={(event) =>
                setField('timeout_seconds', event.target.value)
              }
            />
          </Field>
          <Field
            label="结构化输出格式"
            hint="DeepSeek 官方端点不支持 json_schema，请选择 json_object；火山 Ark 等端点支持 json_schema（严格模式）"
          >
            <Select
              value={form.response_format}
              onValueChange={(value) =>
                setField(
                  'response_format',
                  value === 'json_object' ? 'json_object' : 'json_schema',
                )
              }
            >
              <SelectTrigger aria-label="结构化输出格式">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="json_schema">json_schema（严格，需端点支持）</SelectItem>
                <SelectItem value="json_object">json_object（兼容 DeepSeek 官方）</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="输入价格（元/百万 token）" htmlFor="mc-input" required>
            <Input
              id="mc-input"
              type="number"
              min="0"
              step="0.01"
              value={form.inputPrice}
              onChange={(event) => setField('inputPrice', event.target.value)}
            />
          </Field>
          <Field label="输出价格（元/百万 token）" htmlFor="mc-output" required>
            <Input
              id="mc-output"
              type="number"
              min="0"
              step="0.01"
              value={form.outputPrice}
              onChange={(event) => setField('outputPrice', event.target.value)}
            />
          </Field>
          <div className="flex flex-wrap items-center gap-3 md:col-span-2">
            <Button
              onClick={submit}
              disabled={create.isPending || patch.isPending}
            >
              <Save className="size-4" />
              {editing ? '保存修改' : '创建配置'}
            </Button>
            {!editing && (
              <Button
                variant="outline"
                onClick={beginNew}
                disabled={!form.name && !form.requested_model}
              >
                重置
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <ConfirmActionDialog
        open={discardOpen}
        onOpenChange={setDiscardOpen}
        title="放弃未保存的更改？"
        description="当前模型配置包含未保存的更改。放弃后将恢复为新建配置表单。"
        confirmLabel="放弃更改"
        cancelLabel="继续编辑"
        onConfirm={beginNew}
      />

      <section className="space-y-3">
        <h3 className="text-lg font-semibold tracking-tight">
          配置列表（{list.length}）
        </h3>
        {list.length === 0 ? (
          <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
            暂无配置，请在上方新建
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {list.map((config) => (
              <Card key={config.id}>
                <CardContent className="space-y-3 py-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{config.name}</p>
                      <p
                        className="truncate font-mono text-xs text-muted-foreground"
                        title={String(config.config_json.base_url ?? '')}
                      >
                        {String(config.config_json.requested_model ?? '')}
                      </p>
                    </div>
                    <span className="rounded bg-muted px-2 py-1 text-xs">
                      {config.status}
                    </span>
                  </div>
                  <dl className="space-y-1 text-xs">
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">base_url</dt>
                      <dd
                        className="truncate font-mono"
                        title={String(config.config_json.base_url ?? '')}
                      >
                        {String(config.config_json.base_url ?? '')}
                      </dd>
                    </div>
                    {Boolean(config.config_json.api_key_env) && (
                      <div className="flex justify-between gap-2">
                        <dt className="text-muted-foreground">API Key</dt>
                        <dd className="font-mono">已配置</dd>
                      </div>
                    )}
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">sha256</dt>
                      <dd className="truncate font-mono">
                        {config.config_sha256.slice(0, 16)}
                      </dd>
                    </div>
                  </dl>
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={config.status !== 'draft'}
                      onClick={() => beginEdit(config)}
                    >
                      <Pencil className="size-4" />
                      编辑
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => clone.mutate(config.id)}
                    >
                      <Copy className="size-4" />
                      派生草稿
                    </Button>
                    {config.status === 'draft' && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => freeze.mutate(config.id)}
                      >
                        <LockKeyhole className="size-4" />
                        冻结
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      disabled={remove.isPending}
                      onClick={() => deleteConfig(config)}
                    >
                      <Trash2 className="size-4" />
                      删除
                    </Button>
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
