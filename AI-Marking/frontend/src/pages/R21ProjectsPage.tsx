import { useState } from 'react';
import { Link } from 'react-router-dom';

import {
  type R21Kind,
  useCreateR21Project,
  useDeleteR21Project,
  useR21Projects,
  useR21Prompts,
  useR21Runners,
  useR21Runtime,
} from '@/api/r21';
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

export default function R21ProjectsPage() {
  const projects = useR21Projects();
  const runners = useR21Runners();
  const prompts = useR21Prompts();
  const runtime = useR21Runtime();
  const create = useCreateR21Project();
  const remove = useDeleteR21Project();
  const [name, setName] = useState('');
  const [kind, setKind] = useState<R21Kind>('pilot_run');
  const [runner, setRunner] = useState('');
  const [prompt, setPrompt] = useState('');
  const [pilot, setPilot] = useState('');

  const frozenRunners = (runners.data ?? []).filter(
    (item) => item.status === 'frozen',
  );
  const frozenPrompts = (prompts.data ?? []).filter(
    (item) => item.status === 'frozen',
  );
  const completedPilots = (projects.data ?? []).filter(
    (item) => item.kind === 'pilot_run' && item.status === 'completed',
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">r22 单模型、MCP 执行</p>
        <h1 className="text-3xl font-bold">研究项目</h1>
        <p className="mt-2 text-muted-foreground">
          Runner：{runtime.data?.runner_online ? '已连接' : '未连接'}
          。网页负责管理， Codex MCP 连接后自动串行执行完整项目。
        </p>
        {!runtime.data?.runner_online && (
          <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
            可以先创建、冻结并启动项目；任务会等待你在 Codex App 中打开并信任
            /Users/mac/Desktop/SURF/AI-Marking。
          </p>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>新建项目</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="项目名称"
          />
          <Select
            value={kind}
            onValueChange={(value) => setKind(value as R21Kind)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="pilot_run">
                技术试点（2 题 / 2,640 主调用）
              </SelectItem>
              <SelectItem value="formal">
                正式实验（r22 试点仅支持）
              </SelectItem>
            </SelectContent>
          </Select>
          <Select value={runner} onValueChange={setRunner}>
            <SelectTrigger>
              <SelectValue placeholder="冻结的 Codex Runner" />
            </SelectTrigger>
            <SelectContent>
              {frozenRunners.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={prompt} onValueChange={setPrompt}>
            <SelectTrigger>
              <SelectValue placeholder="冻结的提示词版本" />
            </SelectTrigger>
            <SelectContent>
              {frozenPrompts.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {kind === 'formal' && (
            <Select value={pilot} onValueChange={setPilot}>
              <SelectTrigger>
                <SelectValue placeholder="完成的技术试点" />
              </SelectTrigger>
              <SelectContent>
                {completedPilots.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button
            disabled={
              !name ||
              !runner ||
              !prompt ||
              (kind === 'formal' && !pilot) ||
              create.isPending
            }
            onClick={() =>
              create.mutate(
                {
                  name,
                  kind,
                  runner_config_id: runner,
                  prompt_version_id: prompt,
                  ...(kind === 'formal' ? { pilot_project_id: pilot } : {}),
                },
                { onSuccess: () => setName('') },
              )
            }
          >
            创建项目
          </Button>
        </CardContent>
      </Card>

      <section className="space-y-3">
        {(projects.data ?? []).map((item) => (
          <Card key={item.id}>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div>
                <Link
                  className="font-semibold hover:underline"
                  to={`/research/${item.id}`}
                >
                  {item.name}
                </Link>
                <p className="text-sm text-muted-foreground">
                  {item.kind} · {item.status} · {item.progress.succeeded ?? 0} /{' '}
                  {item.progress.total ?? 0}
                  {(item.progress.failed_terminal ?? 0) > 0 && (
                    <span className="text-destructive">
                      {' '}
                      · 失败 {item.progress.failed_terminal}
                    </span>
                  )}
                </p>
              </div>
              <Button
                size="sm"
                variant="destructive"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm('删除项目？')) remove.mutate(item.id);
                }}
              >
                删除
              </Button>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
  );
}
