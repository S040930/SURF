import { cleanup, render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { Breadcrumbs } from './MainLayout';
import type { AppRouteHandle } from '@/router';

afterEach(cleanup);

const detailHandle = {
  label: '项目详情',
  parent: { label: '研究项目', to: '/research' },
} satisfies AppRouteHandle;

function renderDetail(
  initialEntries: string[],
  initialIndex = initialEntries.length - 1,
) {
  const router = createMemoryRouter(
    [
      {
        path: '/research',
        element: <div>项目列表目标页</div>,
        handle: { label: '研究项目' },
      },
      {
        path: '/prompts',
        element: <div>提示词页面</div>,
        handle: { label: '提示词配置' },
      },
      {
        path: '/research/:projectId',
        element: <Breadcrumbs />,
        handle: detailHandle,
      },
    ],
    { initialEntries, initialIndex },
  );

  render(<RouterProvider router={router} />);
}

describe('project detail hierarchy navigation', () => {
  it.each([
    ['从项目列表进入', ['/research', '/research/project-1']],
    ['直接打开详情', ['/research/project-1']],
    ['上一页来自其他模块', ['/prompts', '/research/project-1']],
  ])('%s 时都固定返回项目列表', (_label, entries) => {
    renderDetail(entries);

    expect(screen.getByText('项目详情')).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.getByRole('link', { name: '返回研究项目' })).toHaveAttribute(
      'href',
      '/research',
    );
  });
});
