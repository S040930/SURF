import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  create: vi.fn(),
}));

vi.mock('@/api/r23', () => ({
  useR23DataStatus: () => ({
    data: {
      protocol_id: 'r23',
      ready: true,
      datasets: [
        {
          dimension: 'content',
          filename: 'content.jsonl',
          sha256: 'a'.repeat(64),
          bytes: 100,
          rows: 10,
          fields: [],
          label_distribution_x2: {},
          null_counts: {},
          status: 'ready',
        },
      ],
      operator_confirmation_required: true,
    },
    isLoading: false,
  }),
  useR23Runners: () => ({
    data: [
      {
        id: 'runner-1',
        name: 'Runner A',
        model: 'model-a',
        reasoning_effort: 'high',
        speed_mode: 'fast',
        timeout_seconds: 90,
        config_sha256: 'b'.repeat(64),
        status: 'ready',
      },
    ],
  }),
  useR23Rubrics: () => ({
    data: [
      {
        id: 'rubric-1',
        name: 'Rubric A',
        protocol_id: 'r23',
        rubric: 'rubric',
        rubric_sha256: 'c'.repeat(64),
        status: 'ready',
      },
    ],
  }),
  useR23Projects: () => ({ data: [] }),
  useR23Runtime: () => ({ data: { runner_online: true }, isLoading: false }),
  useCreateR23Project: () => ({ mutate: state.create, isPending: false }),
  useDeleteR23Project: () => ({ mutate: vi.fn(), isPending: false }),
}));

import R23ProjectsPage from './R23ProjectsPage';

describe('R23ProjectsPage project creation', () => {
  afterEach(() => {
    cleanup();
    state.create.mockReset();
  });

  it('allows a formal experiment without selecting a technical pilot', () => {
    render(
      <MemoryRouter>
        <R23ProjectsPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText('实验名称'), {
      target: { value: 'Formal experiment' },
    });
    const selects = screen.getAllByRole('combobox');
    fireEvent.click(selects[0]);
    fireEvent.click(screen.getByRole('option', { name: /Runner A/ }));
    fireEvent.click(selects[1]);
    fireEvent.click(screen.getByRole('option', { name: '正式实验' }));
    fireEvent.click(screen.getAllByRole('combobox')[2]);
    fireEvent.click(screen.getByRole('option', { name: 'Rubric A' }));
    fireEvent.click(
      screen.getByLabelText(
        '我已获得使用所选模型服务处理该受限数据的授权，并将遵守研究伦理与保密要求。',
      ),
    );

    expect(
      screen.getByText('匹配的已完成技术试点（可选）'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建项目' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '创建项目' }));

    expect(state.create).toHaveBeenCalledWith(
      {
        name: 'Formal experiment',
        kind: 'formal',
        runner_config_id: 'runner-1',
        rubric_id: 'rubric-1',
        data_processing_confirmed: true,
      },
      expect.any(Object),
    );
  });
});
