interface ShortHashProps {
  value?: string | null;
  length?: number;
}

export default function ShortHash({ value, length = 12 }: ShortHashProps) {
  if (!value) return <span aria-label="尚未生成">—</span>;
  return (
    <code className="font-mono text-xs" title={value}>
      {value.slice(0, length)}…{value.slice(-6)}
    </code>
  );
}
