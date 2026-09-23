import { Suspense, useEffect, useState } from 'react';
import {
  ArrowLeft,
  Beaker,
  ChevronRight,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Sun,
} from 'lucide-react';
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useMatches,
} from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { navItems, type AppRouteHandle } from '@/router';
import { useTheme } from '@/components/ThemeProvider';

const STORAGE_KEY = 'app:sidebar-collapsed';

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到暗色模式'}
      title={theme === 'dark' ? '切换到浅色模式' : '切换到暗色模式'}
      onClick={toggleTheme}
    >
      <span
        key={theme}
        className="animate-icon-swap motion-reduce:animate-none"
      >
        {theme === 'dark' ? (
          <Sun className="size-4" />
        ) : (
          <Moon className="size-4" />
        )}
      </span>
    </Button>
  );
}

export function Breadcrumbs() {
  const matches = useMatches();
  const handle = [...matches]
    .reverse()
    .map((match) => match.handle as AppRouteHandle | undefined)
    .find((candidate) => candidate?.label);

  if (!handle) return null;

  return (
    <nav
      aria-label="面包屑"
      className="flex items-center gap-1 text-sm text-muted-foreground"
    >
      {handle.parent && (
        <>
          <Button asChild variant="ghost" size="sm">
            <Link
              to={handle.parent.to}
              aria-label={`返回${handle.parent.label}`}
            >
              <ArrowLeft data-icon="inline-start" />
              {handle.parent.label}
            </Link>
          </Button>
          <ChevronRight className="size-3.5 shrink-0 opacity-50" />
        </>
      )}
      <span aria-current="page" className="font-medium text-foreground">
        {handle.label}
      </span>
    </nav>
  );
}

export default function MainLayout() {
  const location = useLocation();
  // Longest-match: /config/prompt must not highlight /config.
  const activePath = navItems
    .filter(
      (item) =>
        location.pathname === item.path ||
        location.pathname.startsWith(`${item.path}/`),
    )
    .sort((a, b) => b.path.length - a.path.length)[0]?.path;
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(collapsed));
    } catch {
      // ignore storage errors
    }
  }, [collapsed]);

  return (
    <div className="min-h-svh bg-background">
      <a
        href="#main-content"
        className="sr-only fixed left-4 top-4 z-50 rounded-lg bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only"
      >
        跳转到主内容
      </a>
      <div className="flex min-h-svh">
        <aside
          className={`sticky top-0 hidden h-svh flex-col border-r bg-background transition-all duration-200 md:flex ${
            collapsed ? 'w-16' : 'w-56'
          }`}
        >
          <div className="flex h-16 items-center border-b px-4">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Beaker className="size-5" />
            </span>
            {!collapsed && (
              <span className="ml-3 text-sm font-semibold leading-tight tracking-tight">
                AI Marking Lab
              </span>
            )}
          </div>
          <nav className="flex-1 space-y-1 p-3" aria-label="主导航">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = item.path === activePath;
              return (
                <NavLink
                  key={item.path}
                  to={item.path}
                  title={item.label}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                    collapsed ? 'justify-center px-0' : ''
                  } ${
                    isActive
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  }`}
                >
                  <Icon className="size-4 shrink-0" />
                  {!collapsed && item.label}
                </NavLink>
              );
            })}
          </nav>
          <div className="border-t p-3">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
              title={collapsed ? '展开侧边栏' : '收起侧边栏'}
              className="w-full"
              onClick={() => setCollapsed((current) => !current)}
            >
              {collapsed ? (
                <PanelLeftOpen className="size-4" />
              ) : (
                <PanelLeftClose className="size-4" />
              )}
            </Button>
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <nav className="flex gap-1 overflow-x-auto border-b bg-background p-2 md:hidden" aria-label="移动端主导航">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = item.path === activePath;
              return (
                <NavLink key={item.path} to={item.path} className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium ${isActive ? 'bg-primary/10 text-primary' : 'text-muted-foreground'}`}>
                  <Icon className="size-4" />{item.label}
                </NavLink>
              );
            })}
          </nav>
          <header className="border-b bg-background">
            <div className="flex min-h-16 items-center justify-between gap-4 px-4 py-2 sm:px-6">
              <Breadcrumbs />
              <ThemeToggle />
            </div>
          </header>
          <main id="main-content" className="min-w-0 flex-1 px-4 py-6 sm:px-6 sm:py-8" tabIndex={-1}>
            <Suspense
              fallback={
                <div className="mx-auto max-w-6xl space-y-4">
                  <Skeleton className="h-8 w-48" />
                  <Skeleton className="h-64 w-full" />
                </div>
              }
            >
              <Outlet />
            </Suspense>
          </main>
        </div>
      </div>
    </div>
  );
}
