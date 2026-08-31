import { isRouteErrorResponse, useRouteError } from 'react-router-dom';
import { AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function RouteErrorPage() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : '页面加载时发生异常，请返回实验中心后重试。';

  return (
    <main className="flex min-h-svh items-center justify-center bg-background p-6">
      <div className="w-full max-w-md rounded-xl border bg-card p-8 text-center shadow-sm">
        <AlertCircle className="mx-auto size-7 text-destructive" />
        <h1 className="mt-4 text-xl font-semibold">页面出现异常</h1>
        <p className="mt-2 text-sm text-muted-foreground">{message}</p>
        <Button className="mt-6" onClick={() => window.location.assign('/experiments')}>返回实验中心</Button>
      </div>
    </main>
  );
}
