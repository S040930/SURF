import { cleanup, render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { Breadcrumbs } from './MainLayout';
import type { AppRouteHandle } from '@/router';

afterEach(cleanup);

const detailHandle = {
  label: '记忆研究运行台',
  parent: { label: 'SAF 记忆研究', to: '/memory-study' },
} satisfies AppRouteHandle;

function renderDetail(
  initialEntries: string[],
  initialIndex = initialEntries.length - 1,
) {
  const router = createMemoryRouter(
    [
      {
        path: '/memory-study',
        element: <div>记忆研究首页</div>,
        handle: { label: 'SAF 记忆研究' },
      },
      {
        path: '/memory-study/config',
        element: <div>模型配置页</div>,
        handle: {
          label: '模型配置',
          parent: { label: 'SAF 记忆研究', to: '/memory-study' },
        },
      },
      {
        path: '/memory-study/:studyId',
        element: <Breadcrumbs />,
        handle: detailHandle,
      },
    ],
    { initialEntries, initialIndex },
  );

  render(<RouterProvider router={router} />);
}

describe('study detail hierarchy navigation', () => {
  it.each([
    ['从研究首页进入', ['/memory-study', '/memory-study/study-1']],
    ['直接打开详情', ['/memory-study/study-1']],
    ['上一页来自其他模块', ['/memory-study/config', '/memory-study/study-1']],
  ])('%s 时都固定返回研究首页', (_label, entries) => {
    renderDetail(entries);

    expect(screen.getByText('记忆研究运行台')).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.getByRole('link', { name: '返回SAF 记忆研究' })).toHaveAttribute(
      'href',
      '/memory-study',
    );
  });
});

describe('model config breadcrumb', () => {
  it('shows a label with a parent link back to the SAF memory study', () => {
    const router = createMemoryRouter(
      [
        {
          path: '/memory-study/config',
          element: <Breadcrumbs />,
          handle: {
            label: '模型配置',
            parent: { label: 'SAF 记忆研究', to: '/memory-study' },
          },
        },
      ],
      { initialEntries: ['/memory-study/config'], initialIndex: 0 },
    );

    render(<RouterProvider router={router} />);

    expect(screen.getByText('模型配置')).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.getByRole('link', { name: '返回SAF 记忆研究' })).toHaveAttribute(
      'href',
      '/memory-study',
    );
  });
});