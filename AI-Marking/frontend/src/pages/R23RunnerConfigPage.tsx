import { useState } from 'react';
import { CheckCircle2, Trash2 } from 'lucide-react';

import {
  useCreateR23Runner,
  useDeleteR23Runner,
  useR23Runners,
} from '@/api/r23';
import ShortHash from '@/components/r23/ShortHash';
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

export default function R23RunnerConfigPage() {
  const runners = useR23Runners();
  const create = useCreateR23Runner();
  const remove = useDeleteR23Runner();
  const [name, setName] = useState('');
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState<'low' | 'medium' | 'high'>('medium');
  const [speedMode, setSpeedMode] = useState<'standard' | 'fast'>('standard');
  const [timeoutSeconds, setTimeoutSeconds] = useState('120');
  const parsedTimeout = Number(timeoutSeconds);
  const timeoutIsValid =
    Number.isInteger(parsedTimeout) &&
    parsedTimeout >= 30 &&
    parsedTimeout <= 1800;

  return (
    <div className="mx-auto max-w-5xl space-y-7">
      <header>
        <p className="text-sm font-medium text-primary">
          DREsS 实验 / Codex Runner
        </p>
        <h1 className="mt-2 text-3xl font-bold">Codex Runner</h1>
        <p className="mt-2 max-w-3xl text-muted-foreground">
          可为同一模型保存多个推理配置。点击“开始实验”时，平台会自动记录当时的
          Codex CLI 指纹和完整运行配置。
        </p>
      </header>

      <Card className="elevated-card">
        <CardHeader>
          <CardTitle>添加 Runner</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-[1fr_1.4fr_0.8fr_0.9fr_0.8fr_auto]">
          <label className="space-y-2 text-sm font-medium">
            显示名称
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：模型 A"
            />
          </label>
          <label className="space-y-2 text-sm font-medium">
            Codex 模型标识
            <Input
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="输入当前可用的真实模型标识"
            />
          </label>
          <label className="space-y-2 text-sm font-medium">
            思考程度
            <Select
              value={effort}
              onValueChange={(value) => setEffort(value as typeof effort)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="low">low</SelectItem>
                <SelectItem value="medium">medium</SelectItem>
                <SelectItem value="high">high</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-2 text-sm font-medium">
            运行速度
            <Select
              value={speedMode}
              onValueChange={(value) =>
                setSpeedMode(value as typeof speedMode)
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="standard">标准（standard）</SelectItem>
                <SelectItem value="fast">快速（fast）</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-2 text-sm font-medium">
            超时（秒）
            <Input
              aria-describedby="r23-timeout-help"
              inputMode="numeric"
              min={30}
              max={1800}
              step={10}
              type="number"
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
            />
          </label>
          <Button
            className="self-end"
            disabled={
              !name.trim() ||
              !model.trim() ||
              !timeoutIsValid ||
              create.isPending
            }
            onClick={() =>
              create.mutate(
                {
                  name: name.trim(),
                  model: model.trim(),
                  reasoning_effort: effort,
                  speed_mode: speedMode,
                  timeout_seconds: parsedTimeout,
                },
                {
                  onSuccess: () => {
                    setName('');
                    setModel('');
                  },
                },
              )
            }
          >
            保存 Runner
          </Button>
          <div
            id="r23-timeout-help"
            className="md:col-span-2 xl:col-span-6 flex flex-wrap gap-2 text-xs text-muted-foreground"
          >
            <Badge variant="secondary">
              reasoning effort: low / medium / high
            </Badge>
            <Badge variant="secondary">speed: standard / fast</Badge>
            <Badge variant="secondary">timeout: 30–1800s，默认 120s</Badge>
            <Badge variant="secondary">temperature: CLI 未暴露</Badge>
            <p className="basis-full">
              Fast 会提高受支持模型的处理速度并消耗更多额度；同一实验条件应固定速度模式。
            </p>
          </div>
        </CardContent>
      </Card>

      <section className="space-y-3" aria-labelledby="runner-list">
        <h2 id="runner-list" className="text-xl font-semibold">
          Runner 配置
        </h2>
        {(runners.data ?? []).length === 0 ? (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
            尚无 r23 Runner。
          </div>
        ) : (
          (runners.data ?? []).map((item) => (
            <Card className="elevated-card" key={item.id}>
              <CardContent className="grid gap-4 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold">{item.name}</h3>
                    <Badge variant="secondary">可用</Badge>
                  </div>
                  <p className="mt-2 break-all text-sm">{item.model}</p>
                  <dl className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-3">
                    <div>
                      <dt>配置 SHA-256</dt>
                      <dd className="mt-1 text-foreground">
                        <ShortHash value={item.config_sha256} />
                      </dd>
                    </div>
                    <div>
                      <dt>思考程度</dt>
                      <dd className="mt-1 text-foreground">
                        {item.reasoning_effort}
                      </dd>
                    </div>
                    <div>
                      <dt>超时</dt>
                      <dd className="mt-1 text-foreground">
                        {item.timeout_seconds} 秒
                      </dd>
                    </div>
                    <div>
                      <dt>运行速度</dt>
                      <dd className="mt-1 text-foreground">
                        {item.speed_mode === 'fast'
                          ? '快速（fast）'
                          : '标准（standard）'}
                      </dd>
                    </div>
                    <div>
                      <dt>运行快照</dt>
                      <dd className="mt-1 inline-flex items-center gap-1 text-foreground">
                        <CheckCircle2 className="size-3.5 text-emerald-600" />
                        开始实验时自动记录
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`删除 ${item.name}`}
                    disabled={remove.isPending}
                    onClick={() =>
                      window.confirm(
                        '删除此 Runner？已被项目使用的配置不能删除。',
                      ) && remove.mutate(item.id)
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </section>
    </div>
  );
}
