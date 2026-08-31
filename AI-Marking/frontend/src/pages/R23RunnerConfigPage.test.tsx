import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  create: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('@/api/r23', () => ({
  useR23Runners: () => ({
    data: [
      {
        id: 'runner-existing',
        name: 'Existing runner',
        model: 'model-a',
        reasoning_effort: 'medium',
        speed_mode: 'fast',
        timeout_seconds: 75,
        config_sha256: 'a'.repeat(64),
        status: 'ready',
      },
    ],
  }),
  useCreateR23Runner: () => ({ mutate: state.create, isPending: false }),
  useDeleteR23Runner: () => ({ mutate: state.remove, isPending: false }),
}));

import R23RunnerConfigPage from './R23RunnerConfigPage';

describe('R23RunnerConfigPage runtime configuration', () => {
  afterEach(() => {
    cleanup();
    state.create.mockReset();
    state.remove.mockReset();
  });

  it('submits the operator-selected timeout and displays saved values', () => {
    render(<R23RunnerConfigPage />);

    fireEvent.change(screen.getByLabelText('显示名称'), {
      target: { value: 'Short timeout' },
    });
    fireEvent.change(screen.getByLabelText('Codex 模型标识'), {
      target: { value: 'model-b' },
    });
    fireEvent.change(screen.getByLabelText('超时（秒）'), {
      target: { value: '90' },
    });
    fireEvent.click(screen.getByLabelText('运行速度'));
    fireEvent.click(screen.getByRole('option', { name: '快速（fast）' }));
    fireEvent.click(screen.getByRole('button', { name: '保存 Runner' }));

    expect(state.create).toHaveBeenCalledWith(
      {
        name: 'Short timeout',
        model: 'model-b',
        reasoning_effort: 'medium',
        speed_mode: 'fast',
        timeout_seconds: 90,
      },
      expect.any(Object),
    );
    expect(screen.getByText('75 秒')).toBeInTheDocument();
    expect(screen.getAllByText('快速（fast）')).toHaveLength(2);
  });

  it('rejects timeout values outside 30 to 1800 seconds', () => {
    render(<R23RunnerConfigPage />);

    fireEvent.change(screen.getByLabelText('显示名称'), {
      target: { value: 'Invalid timeout' },
    });
    fireEvent.change(screen.getByLabelText('Codex 模型标识'), {
      target: { value: 'model-b' },
    });
    fireEvent.change(screen.getByLabelText('超时（秒）'), {
      target: { value: '29' },
    });

    expect(screen.getByRole('button', { name: '保存 Runner' })).toBeDisabled();
  });
});
