import { Badge } from '@/components/ui/badge';

const KIND_META: Record<string, { label: string; className: string }> = {
  development: { label: 'development', className: 'border-info/30 bg-info/10 text-info' },
  pilot: { label: 'pilot', className: 'border-primary/30 bg-primary/10 text-primary' },
  formal: {
    label: 'formal',
    className: 'border-secondary bg-secondary text-secondary-foreground',
  },
  pilot_run: { label: '试运行', className: 'border-primary/30 bg-primary/10 text-primary' },
};

const STATUS_META: Record<string, { label: string; className: string }> = {
  draft: { label: '草稿', className: 'border-border bg-muted text-muted-foreground' },
  frozen: {
    label: '已冻结',
    className: 'border-info/30 bg-info/10 text-info',
  },
  prepared: { label: '待启动', className: 'border-info/30 bg-info/10 text-info' },
  queued: { label: '排队中', className: 'border-info/30 bg-info/10 text-info' },
  running: { label: '运行中', className: 'border-info/30 bg-info/10 text-info' },
  paused: { label: '已暂停', className: 'border-warning/30 bg-warning/10 text-warning' },
  completed: { label: '已完成', className: 'border-success/30 bg-success/10 text-success' },
  completed_with_failures: {
    label: '完成（有失败）',
    className: 'border-warning/30 bg-warning/10 text-warning',
  },
  terminated: {
    label: '已终止',
    className: 'border-destructive/30 bg-destructive/10 text-destructive',
  },
  approved: { label: '已审批', className: 'border-success/30 bg-success/10 text-success' },
};

export function ProjectKindBadge({ kind }: { kind: string }) {
  const meta = KIND_META[kind] ?? { label: kind, className: 'border-border bg-muted text-muted-foreground' };
  return (
    <Badge variant="outline" className={meta.className}>
      {meta.label}
    </Badge>
  );
}

export function ProjectStatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? { label: status, className: 'border-border bg-muted text-muted-foreground' };
  return (
    <Badge variant="outline" className={meta.className}>
      {meta.label}
    </Badge>
  );
}
