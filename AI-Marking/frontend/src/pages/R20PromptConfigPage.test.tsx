import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  suites: [] as Array<{
    id: string;
    status: 'queued' | 'running' | 'passed' | 'failed';
    completed_calls: number;
    expected_calls: number;
    failure_reason: string | null;
  }>,
  remove: vi.fn(),
}));

vi.mock('@/api/r20', () => ({
  useR20PromptVersions: () => ({
    data: [{
      id: 'prompt-v2',
      protocol_id: 'r20-saf-official-split-2026-08-v4-8q-60m-15t',
      name: '规范 v4',
      templates_sha256: 'a'.repeat(64),
      status: 'draft',
    }, {
      id: 'prompt-v1',
      protocol_id: 'r20-saf-official-split-2026-08-v1',
      name: '历史 v1',
      templates_sha256: 'b'.repeat(64),
      status: 'frozen',
    }],
  }),
  useR20ModelConfigs: () => ({ data: [] }),
  useCreateR20PromptVersion: () => ({ mutate: vi.fn(), isPending: false }),
  useR20PromptValidationSuites: () => ({ data: state.suites }),
  useCreateR20PromptValidationSuite: () => ({ mutate: vi.fn(), isPending: false }),
  useFreezeR20PromptVersion: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteR20PromptVersion: () => ({ mutate: state.remove, isPending: false }),
}));

import R20PromptConfigPage from './R20PromptConfigPage';

describe('r20 v2 prompt validation controls', () => {
  afterEach(() => {
    state.suites = [];
    state.remove.mockClear();
    cleanup();
  });

  it('uses one 48-call action instead of three manual trial buttons', () => {
    render(<R20PromptConfigPage />);
    expect(screen.getByText(/逐项核对可观察证据/)).toBeInTheDocument();
    expect(screen.getByText(/不得写入 rubric、criterion、总体质量/)).toBeInTheDocument();
    expect(screen.getByText(/criterion 不得包含 IF\/when\/答案行为/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始 48-call 验证' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /试评 scoring/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '冻结版本' })).toBeEnabled();
  });

  it('enables freeze without any passed validation suite', () => {
    render(<R20PromptConfigPage />);
    expect(screen.getByRole('button', { name: '冻结版本' })).toBeEnabled();
  });

  it('shows persistent progress and enables freeze after one pass', () => {
    state.suites = [{
      id: 'suite-1',
      status: 'passed',
      completed_calls: 48,
      expected_calls: 48,
      failure_reason: null,
    }];
    render(<R20PromptConfigPage />);
    expect(screen.getByText('48 / 48（100%）')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '冻结版本' })).toBeEnabled();
  });

  it('deletes a draft version after confirmation', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<R20PromptConfigPage />);
    screen.getAllByRole('button', { name: '删除版本' })[0].click();
    expect(state.remove).toHaveBeenCalledWith('prompt-v2');
    confirmSpy.mockRestore();
  });

  it('does not delete when confirmation is cancelled', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<R20PromptConfigPage />);
    screen.getAllByRole('button', { name: '删除版本' })[0].click();
    expect(state.remove).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('allows deleting a read-only v1 version without validation controls', () => {
    render(<R20PromptConfigPage />);
    const v1Card = screen.getByText('历史 v1').closest('div')!.parentElement!;
    expect(screen.getAllByRole('button', { name: '删除版本' })).toHaveLength(2);
    expect(v1Card).not.toHaveTextContent('开始 48-call 验证');
    expect(v1Card).not.toHaveTextContent('冻结版本');
  });
});
