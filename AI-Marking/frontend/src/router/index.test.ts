import { describe, expect, it } from 'vitest';

import { navItems, router, type AppRouteHandle } from './index';

describe('router surface', () => {
  it('does not expose archived experiment pages', () => {
    expect(navItems.some((item) => item.path.includes('history'))).toBe(false);
    const childPaths =
      router.routes[0]?.children?.map((route) => route.path ?? '') ?? [];
    expect(childPaths.some((path) => path.includes('history'))).toBe(false);
  });

  it('keeps only the SAF memory study and model config surfaces', () => {
    expect(navItems.map((item) => item.path)).toEqual([
      '/memory-study',
      '/memory-study/config',
    ]);
    const childPaths =
      router.routes[0]?.children?.map((route) => route.path ?? '') ?? [];
    expect(childPaths).toContain('memory-study/:studyId');
    expect(childPaths).toContain('memory-study/config');
    expect(childPaths).not.toContain('models');
    expect(childPaths).not.toContain('research/:projectId');
    expect(childPaths).not.toContain('dress/:projectId');
    expect(
      childPaths.some((path) => path.includes('experiments')),
    ).toBe(false);
    expect(
      childPaths.some((path) => path.includes('research') || path.includes('dress') || path.includes('prompts')),
    ).toBe(false);
  });

  it('declares a fixed parent destination for study detail', () => {
    const detailRoute = router.routes[0]?.children?.find(
      (route) => route.path === 'memory-study/:studyId',
    );
    const handle = detailRoute?.handle as AppRouteHandle | undefined;

    expect(handle).toEqual({
      label: '记忆研究运行台',
      parent: { label: 'SAF 记忆研究', to: '/memory-study' },
    });
  });

  it('declares the model config under the SAF memory study hierarchy', () => {
    const configRoute = router.routes[0]?.children?.find(
      (route) => route.path === 'memory-study/config',
    );
    const handle = configRoute?.handle as AppRouteHandle | undefined;

    expect(handle).toEqual({
      label: '模型配置',
      parent: { label: 'SAF 记忆研究', to: '/memory-study' },
    });
  });
});