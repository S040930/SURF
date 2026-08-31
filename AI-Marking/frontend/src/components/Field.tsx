import { type ReactNode } from 'react';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

interface FieldProps {
  label: string;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
  /** 校验错误信息，存在时显示红色文案并设置 aria-invalid */
  error?: string;
  /** 辅助说明，灰色小字 */
  hint?: string;
  /** 必填标记，在 label 前加红色 * */
  required?: boolean;
  /** 信息提示，hover 显示（原生 title） */
  tooltip?: string;
}

/** 统一字段封装：Label + 内容 + 校验/提示反馈 */
export function Field({
  label,
  htmlFor,
  children,
  className,
  error,
  hint,
  required,
  tooltip,
}: FieldProps) {
  const descriptionId = htmlFor ? `${htmlFor}-description` : undefined;
  const errorId = htmlFor ? `${htmlFor}-error` : undefined;
  return (
    <div className={cn('space-y-2', className)} aria-invalid={error ? 'true' : undefined}>
      <Label htmlFor={htmlFor} title={tooltip}>
        {required && <span className="text-destructive">*</span>}
        {label}
      </Label>
      {children}
      {hint && !error && (
        <p id={descriptionId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
