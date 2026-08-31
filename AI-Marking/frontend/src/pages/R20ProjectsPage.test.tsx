import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  models: [] as Array<{ id: string; name: string; status: string }>,
  prompts: [] as Array<{ id: string; name: string; status: string }>,
}));

vi.mock('@/api/r20', () => ({
  useR20Projects: () => ({ data: [] }),
  useR20ModelConfigs: () => ({ data: state.models, isLoading: false }),
  useR20PromptVersions: () => ({ data: state.prompts, isLoading: false }),
  useCreateR20Project: () => ({ mutate: vi.fn(), isPending: false }),
  useR20Lifecycle: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteR20Project: () => ({ mutate: vi.fn(), isPending: false }),
}));

import R20ProjectsPage from './R20ProjectsPage';

describe('project creation prerequisites', () => {
  afterEach(() => {
    state.models = [];
    state.prompts = [];
    cleanup();
  });

  it('explains why draft configurations are not available to load', () => {
    state.models = [{ id: 'draft-model', name: '草稿模型', status: 'draft' }];

    render(
      <MemoryRouter>
        <R20ProjectsPage />
      </MemoryRouter>,
    );

    expect(screen.getByText('还需要 2 个已冻结的模型配置')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '模型配置' })).toHaveAttribute(
      'href',
      '/models',
    );
    expect(screen.getByRole('button', { name: '创建项目' })).toBeDisabled();
  });

  it('asks for a frozen prompt after two frozen models are available', () => {
    state.models = [
      { id: 'deepseek', name: 'DeepSeek', status: 'frozen' },
      { id: 'doubao', name: 'Doubao', status: 'frozen' },
    ];

    render(
      <MemoryRouter>
        <R20ProjectsPage />
      </MemoryRouter>,
    );

    expect(
      screen.getByText('还需要一个已冻结的 v4 提示词版本'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '提示词配置' })).toHaveAttribute(
      'href',
      '/prompts',
    );
  });
});
