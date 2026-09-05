import { describe, expect, it } from 'vitest';

import { navItems, router, type AppRouteHandle } from './index';

describe('router surface', () => {
  it('does not expose archived experiment pages', () => {
    expect(navItems.some((item) => item.path.includes('history'))).toBe(false);
    const childPaths =
      router.routes[0]?.children?.map((route) => route.path ?? '') ?? [];
    expect(childPaths.some((path) => path.includes('history'))).toBe(false);
  });

  it('declares a fixed parent destination for project details', () => {
    const detailRoute = router.routes[0]?.children?.find(
      (route) => route.path === 'research/:projectId',
    );
    const handle = detailRoute?.handle as AppRouteHandle | undefined;

    expect(handle).toEqual({
      label: '项目详情',
      parent: { label: '研究项目', to: '/research' },
    });
  });

  it('leads with the unified platform while keeping the r23 legacy surface', () => {
    expect(navItems.map((item) => item.path)).toEqual([
      '/experiments',
      '/dress',
      '/dress/rubric',
      '/dress/runners',
      '/dress/data',
    ]);
    const childPaths =
      router.routes[0]?.children?.map((route) => route.path ?? '') ?? [];
    expect(childPaths).toContain('experiments/projects/:projectId');
    expect(childPaths).toContain('dress/:projectId');
    expect(childPaths).toContain('research/:projectId');
  });
});
