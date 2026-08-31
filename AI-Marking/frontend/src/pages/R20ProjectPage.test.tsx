import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  project: {
    id: 'p1',
    protocol_id: 'r20-saf-official-split-2026-08-v4-8q-60m-15t',
    name: '试点一',
    kind: 'pilot_run',
    status: 'running',
    prompt_version_name: 'v2',
    progress: {
      total: 100,
      recorded: 60,
      succeeded: 55,
      failed_terminal: 3,
    },
  },
  failures: {
    items: [
      {
        id: 11,
        kind: 'test_score',
        status: 'failed_terminal',
        model_id: 'm',
        question_id: 'q',
        condition: 'nm',
        trajectory: 1,
        history_count: 40,
        repeat: 1,
        failure_reason: 'network error',
      },
    ],
    total: 1,
    summary: {},
  },
  retry: vi.fn(),
  lifecycle: vi.fn(),
  remove: vi.fn(),
  startGroup: vi.fn(),
  groups: [
    {
      id: 1,
      project_id: 'p1',
      question_id: 'q-1',
      condition: 'nm',
      status: 'running',
      order_rank: 0,
      expected_calls: 100,
      progress: { recorded: 40, succeeded: 40 },
      started_at: null,
      completed_at: null,
    },
    {
      id: 2,
      project_id: 'p1',
      question_id: 'q-1',
      condition: 'crm',
      status: 'pending',
      order_rank: 1,
      expected_calls: 200,
      progress: {},
      started_at: null,
      completed_at: null,
    },
  ],
}));

vi.mock('@/api/r20', () => ({
  useR20Project: () => ({ data: state.project }),
  useR20Report: () => ({ data: { report: null } }),
  useR20Lifecycle: () => ({ mutate: state.lifecycle, isPending: false }),
  useR20Failures: () => ({ data: state.failures }),
  useRetryR20Call: () => ({ mutate: state.retry, isPending: false }),
  useDeleteR20Project: () => ({ mutate: state.remove, isPending: false }),
  useR20Groups: () => ({ data: state.groups }),
  useStartR20Group: () => ({ mutate: state.startGroup, isPending: false }),
}));

import R20ProjectPage from './R20ProjectPage';

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/research/p1']}>
      <Routes>
        <Route path="/research/:projectId" element={<R20ProjectPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('R20ProjectPage execution progress and retry', () => {
  afterEach(() => {
    state.retry.mockReset();
    state.lifecycle.mockReset();
    state.remove.mockReset();
    state.startGroup.mockReset();
    cleanup();
  });

  it('shows live progress with failure counts', () => {
    renderPage();

    expect(screen.getByText(/已成功 55 \/ 100 次调用/)).toBeInTheDocument();
    expect(screen.getByText('55%')).toBeInTheDocument();
    expect(screen.getByText(/失败 3/)).toBeInTheDocument();
    expect(screen.getByText('network error')).toBeInTheDocument();
  });

  it('shows failed calls with a retry button', () => {
    renderPage();

    const retryButtons = screen.getAllByRole('button', { name: '重试' });
    expect(retryButtons).toHaveLength(1);
  });

  it('retries a single failed call after confirmation', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    fireEvent.click(screen.getByRole('button', { name: '重新执行' }));

    await waitFor(() => {
      expect(state.retry).toHaveBeenCalledWith(
        { projectId: 'p1', callId: 11 },
        expect.any(Object),
      );
    });
  });

  it('hides retry buttons once the project is completed without failures', () => {
    state.project = { ...state.project, status: 'completed', progress: { ...state.project.progress, failed_terminal: 0 } };
    renderPage();
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
    cleanup();
    state.project.status = 'running';
    state.project.progress.failed_terminal = 3;
  });

  it('pauses and resumes the project', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '暂停项目' }));
    expect(state.lifecycle).toHaveBeenCalledWith(
      { projectId: 'p1', action: 'pause' },
    );
    cleanup();

    state.project.status = 'paused';
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '恢复运行' }));
    expect(state.lifecycle).toHaveBeenCalledWith(
      { projectId: 'p1', action: 'resume' },
    );
    cleanup();
    state.project.status = 'running';
  });

  it('deletes the project after confirmation', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '删除项目' }));
    fireEvent.click(screen.getByRole('button', { name: '永久删除' }));
    expect(state.remove).toHaveBeenCalledWith('p1', expect.any(Object));
  });

  it('shows the question × condition grid with one cell running', () => {
    renderPage();
    expect(screen.getByText('题目 × 实验组')).toBeInTheDocument();
    expect(screen.getByText('运行中')).toBeInTheDocument();
    expect(screen.getByText('40/100')).toBeInTheDocument();
  });

  it('runs a single pending group from the grid', () => {
    state.project.status = 'frozen';
    state.groups = state.groups.map((group) => ({ ...group, status: 'pending' }));
    renderPage();

    const runButton = screen.getByRole('button', { name: '开始 CRM 实验' });
    fireEvent.click(runButton);

    expect(state.startGroup).toHaveBeenCalledWith(
      { questionId: 'q-1', condition: 'crm' },
    );
    cleanup();
    state.project.status = 'running';
    state.groups[0].status = 'running';
  });

  it('renders NM, CRM, and ARM as three separately startable groups', () => {
    state.project.status = 'frozen';
    const originalGroups = state.groups;
    state.groups = ['nm', 'crm', 'arm'].map((condition, index) => ({
      ...originalGroups[0],
      id: index + 10,
      condition,
      status: 'pending',
      order_rank: index,
      progress: {},
    }));
    renderPage();

    for (const condition of ['NM', 'CRM', 'ARM']) {
      expect(screen.getByRole('button', { name: `开始 ${condition} 实验` })).toBeEnabled();
    }
    cleanup();
    state.project.status = 'running';
    state.groups = originalGroups;
  });
});
