import { useState } from 'react';
import { Trash2 } from 'lucide-react';

import {
  useCreateR23Rubric,
  useDeleteR23Rubric,
  useR23Rubrics,
} from '@/api/r23';
import ShortHash from '@/components/r23/ShortHash';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

const DEFAULT_RUBRIC = `Score the essay independently on Content, Organization, and Language relative to the writing prompt. Use a best-fit judgment for each dimension. Do not let performance in one dimension determine a score in another dimension, and do not treat length alone as evidence of quality.

Allowed scores: 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, or 5.
Integer-anchor rule: choose the integer descriptor that best represents the essay's overall performance on that dimension.
Half-point rule: use a half-point only when the performance falls substantively between the two adjacent integer descriptors.

CONTENT — relevance, completeness, support, and development of ideas in response to the prompt.
5 = Fully addresses the prompt. Ideas are relevant, specific, and thoroughly developed with strong reasons, details, or examples.
4 = Addresses the prompt well. Main ideas are relevant and sufficiently developed, with only minor gaps, redundancy, or unnecessary information.
3 = Adequately addresses the prompt, but some relevant information is missing or ideas are unevenly or only partly developed.
2 = Shows limited relevance to the prompt. Major gaps, insufficient or inappropriate support, or substantial repetition weaken the response.
1 = Shows minimal relevance or development. The response provides little usable content and few or no supporting details.

ORGANIZATION — logical sequencing, paragraphing, cohesion, transitions, and clarity of progression.
5 = Ideas and paragraphs are purposefully and logically sequenced. Cohesion is smooth, and the structure is consistently easy to follow.
4 = The response is generally well organized and coherent. Sequencing and connections are clear despite minor breaks or incomplete transitions.
3 = An understandable structure is present, but uneven sequencing, weak transitions, or local jumps sometimes interrupt the progression.
2 = Organization is weak. Basic connections are present, but substantial sequencing or cohesion problems make much of the response difficult to follow.
1 = The response is fragmented or seriously disordered. Relationships among ideas are difficult to recover, and effective structure or cohesion is largely absent.

LANGUAGE — grammatical control, sentence formation, vocabulary and collocations, spelling, capitalization, and punctuation.
5 = Language is consistently controlled, varied, and precise. Grammar, word choice, and mechanics are accurate apart from minor slips that do not affect understanding.
4 = Language is generally accurate and appropriately varied. Some errors occur, but they rarely interfere with understanding.
3 = Noticeable errors in grammar, sentence formation, word choice, or mechanics occur, but meaning generally remains clear.
2 = Frequent basic errors and restricted or repetitive language sometimes obscure meaning and make the response difficult to understand.
1 = Pervasive errors and very limited language frequently obscure meaning; control of basic sentence forms, vocabulary, or mechanics is minimal.

Score only the submitted essay against the writing prompt and this rubric. Return no explanation or feedback.`;

export default function R23RubricPage() {
  const rubrics = useR23Rubrics();
  const create = useCreateR23Rubric();
  const remove = useDeleteR23Rubric();
  const [name, setName] = useState('DREsS r23 literature-aligned rubric v2');
  const [rubric, setRubric] = useState(DEFAULT_RUBRIC);

  return (
    <div className="mx-auto max-w-6xl space-y-7">
      <header>
        <p className="text-sm font-medium text-primary">DREsS 实验 / Rubric 配置</p>
        <h1 className="mt-2 text-3xl font-bold">Rubric 配置</h1>
        <p className="mt-2 max-w-3xl text-muted-foreground">
          每个项目使用一个 Rubric。点击“开始实验”时，平台会自动把所选版本写入运行快照。
        </p>
      </header>

      <Card className="elevated-card">
        <CardHeader><CardTitle>添加 Rubric</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <label className="block space-y-2 text-sm font-medium">
            版本名称
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="block space-y-2 text-sm font-medium">
            Rubric 正文
            <Textarea className="min-h-80 font-mono text-sm leading-6" value={rubric} onChange={(event) => setRubric(event.target.value)} />
          </label>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">默认文本是与 DREsS 三维构念对齐的文献改编版；必须明确包含 Content、Organization、Language。已被项目使用的版本会保留用于审计。</p>
            <Button
              disabled={!name.trim() || rubric.trim().length < 80 || create.isPending}
              onClick={() => create.mutate({ name: name.trim(), rubric: rubric.trim() })}
            >保存 Rubric</Button>
          </div>
        </CardContent>
      </Card>

      <section className="space-y-3" aria-labelledby="rubric-list">
        <h2 id="rubric-list" className="text-xl font-semibold">版本记录</h2>
        {(rubrics.data ?? []).map((item) => (
          <Card key={item.id} className="elevated-card">
            <CardContent className="flex flex-wrap items-start justify-between gap-4 p-5">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-semibold">{item.name}</h3>
                  <Badge variant="secondary">可用</Badge>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">SHA-256：<ShortHash value={item.rubric_sha256} /></p>
                <details className="mt-3 text-sm">
                  <summary className="cursor-pointer text-primary">查看只读正文</summary>
                  <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-4 font-sans leading-6">{item.rubric}</pre>
                </details>
              </div>
              <Button variant="ghost" size="icon" aria-label={`删除 ${item.name}`} disabled={remove.isPending} onClick={() => window.confirm('删除此 Rubric？已被项目使用的版本不能删除。') && remove.mutate(item.id)}><Trash2 className="size-4" /></Button>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
  );
}
