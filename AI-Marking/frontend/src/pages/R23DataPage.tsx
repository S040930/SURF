import { Link } from 'react-router-dom';
import { CheckCircle2, Database, FileLock2, ShieldCheck } from 'lucide-react';

import { useR23DataStatus } from '@/api/r23';
import ShortHash from '@/components/r23/ShortHash';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function R23DataPage() {
  const status = useR23DataStatus();
  const data = status.data;

  return (
    <div className="mx-auto max-w-6xl space-y-7">
      <header>
        <p className="text-sm font-medium text-primary">DREsS 实验 / 数据与审计</p>
        <h1 className="mt-2 text-3xl font-bold">数据与审计</h1>
        <p className="mt-2 max-w-3xl text-muted-foreground">
          这里只显示文件级元数据。作文正文与完整题目不会通过此页面、日志或公开导出返回。
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="elevated-card"><CardContent className="p-5"><Database className="size-5 text-primary" /><p className="mt-4 text-sm text-muted-foreground">数据文件</p><p className="mt-1 text-2xl font-semibold tabular-nums">{data?.datasets.length ?? '—'}</p></CardContent></Card>
        <Card className="elevated-card"><CardContent className="p-5"><ShieldCheck className="size-5 text-emerald-600" /><p className="mt-4 text-sm text-muted-foreground">数据门禁</p><p className="mt-1 text-lg font-semibold">{data?.ready ? '全部通过' : '未通过'}</p></CardContent></Card>
        <Card className="elevated-card"><CardContent className="p-5"><FileLock2 className="size-5 text-amber-600" /><p className="mt-4 text-sm text-muted-foreground">分发状态</p><p className="mt-1 text-lg font-semibold">受限数据</p></CardContent></Card>
      </div>

      <Card className="elevated-card">
        <CardHeader><CardTitle>数据完整性检查</CardTitle></CardHeader>
        <CardContent>
          {status.isLoading ? (
            <p className="text-sm text-muted-foreground">正在校验文件…</p>
          ) : !data?.ready ? (
            <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">{data?.error ?? '无法读取 DREsS_CASE 数据。'}</div>
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="bg-muted/60 text-muted-foreground"><tr><th className="px-4 py-3 font-medium">维度</th><th className="px-4 py-3 font-medium">文件</th><th className="px-4 py-3 font-medium">SHA-256</th><th className="px-4 py-3 font-medium">行数</th><th className="px-4 py-3 font-medium">状态</th></tr></thead>
                <tbody className="divide-y">
                  {data.datasets.map((item) => (
                    <tr key={item.dimension}><td className="px-4 py-3 capitalize">{item.dimension}</td><td className="px-4 py-3 font-medium">{item.filename}</td><td className="px-4 py-3"><ShortHash value={item.sha256} /></td><td className="px-4 py-3 tabular-nums">{item.rows.toLocaleString()}</td><td className="px-4 py-3"><Badge className="gap-1" variant="secondary"><CheckCircle2 className="size-3.5 text-emerald-600" />已验证</Badge></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="elevated-card">
          <CardHeader><CardTitle>Organization 配对重建</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p>使用 prompt 与 NFC 后 token 多重集合生成 derived_base_id，并以九个连续等级块的行序交叉验证。</p>
            <dl className="grid grid-cols-2 gap-3 rounded-lg bg-muted/60 p-4"><div><dt className="text-muted-foreground">Base 数</dt><dd className="mt-1 text-xl font-semibold tabular-nums">1,727</dd></div><div><dt className="text-muted-foreground">每个 Base</dt><dd className="mt-1 text-xl font-semibold">9 级 × 2</dd></div></dl>
            <p className="text-muted-foreground">score=5 的重复原文在抽样时合并；跨等级相同文本标记为 collision/no-op，相邻比较只保留真实文本变化。</p>
          </CardContent>
        </Card>
        <Card className="elevated-card">
          <CardHeader><CardTitle>数据安全边界</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            <ul className="space-y-2 text-muted-foreground">
              <li>• TSV 以标准流式解析读取，支持作文内换行。</li>
              <li>• 仅抽中正文进入本地实验数据库与单次 Runner 输入。</li>
              <li>• Manifest、结果 CSV、报告 JSON 和 SVG 均不含正文或完整 prompt。</li>
              <li>• 每个正式项目要求操作者再次确认数据处理授权。</li>
            </ul>
            <Button asChild variant="outline"><Link to="/research">打开 r22 历史入口</Link></Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
