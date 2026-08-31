import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  project: {
    id: 'p1',
    protocol_id: 'r21-test',
    name: '技术试点',
    kind: 'pilot_run',
    status: 'frozen',
    runner_config_id: 'runner',
    runner_config_json: {},
    prompt_version_id: 'prompt',
    prompt_version_name: 'prompt',
    pilot_project_id: null,
    manifest_json: {},
    progress: { total: 510, succeeded: 0, failed_terminal: 0 },
    performance: {
      successful_attempts: 0,
      throughput_calls_per_hour: null,
      p50_latency_ms: null,
      p95_latency_ms: null,
      estimated_remaining_seconds: null,
    },
    runtime: {
      runner_online: false,
      queued_groups: 0,
      runtime: {},
      active_group: null,
    },
  },
  groups: [
    {
      id: 1,
      project_id: 'p1',
      question_id: 'q-1',
      condition: 'nm',
      status: 'pending',
      order_rank: 0,
      expected_calls: 85,
      progress: {},
    },
    {
      id: 2,
      project_id: 'p1',
      question_id: 'q-1',
      condition: 'crm',
      status: 'pending',
      order_rank: 1,
      expected_calls: 85,
      progress: {},
    },
  ],
  failures: {
    items: [
      {
        id: 12,
        kind: 'test_score',
        status: 'failed_terminal',
        question_id: 'q-1',
        condition: 'nm',
        trajectory: 1,
        history_count: 20,
        repeat: 1,
        failure_reason: 'codex failed',
      },
    ],
    total: 1,
  },
  action: vi.fn(),
  start: vi.fn(),
  retry: vi.fn(),
}));

vi.mock('@/api/r21', () => ({
  useR21Project: () => ({ data: state.project }),
  useR21Groups: () => ({ data: state.groups }),
  useR21Failures: () => ({ data: state.failures }),
  useR21Report: () => ({ data: { report: null } }),
  useR21Action: () => ({ mutate: state.action, isPending: false }),
  useStartR21Project: () => ({ mutate: state.start, isPending: false }),
  useRetryR21Call: () => ({ mutate: state.retry, isPending: false }),
}));

import R21ProjectPage from './R21ProjectPage';

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/research/p1']}>
      <Routes>
        <Route path="/research/:projectId" element={<R21ProjectPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('R21ProjectPage automatic project execution', () => {
  afterEach(() => {
    cleanup();
    state.action.mockReset();
    state.start.mockReset();
    state.retry.mockReset();
    state.project.status = 'frozen';
    state.project.progress.failed_terminal = 0;
    vi.restoreAllMocks();
  });

  it('starts the complete project with one confirmation', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: '运行完整项目' }));

    expect(state.start).toHaveBeenCalledWith('p1');
    expect(screen.queryByText(/启动此组/)).not.toBeInTheDocument();
    expect(screen.getByText('实验组（冻结顺序，只读）')).toBeInTheDocument();
  });

  it('shows the Codex connection instruction while waiting', () => {
    state.project.status = 'waiting_for_runner';
    renderPage();

    expect(screen.getByText(/不需要发送任何 MCP 命令/)).toBeInTheDocument();
    expect(
      screen.getByText(/\/Users\/mac\/Desktop\/SURF\/AI-Marking/),
    ).toBeInTheDocument();
  });

  it('retries a terminal failure and continues automatically', () => {
    state.project.status = 'attention_required';
    state.project.progress.failed_terminal = 1;
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: '重试并继续' }));

    expect(state.retry).toHaveBeenCalledWith({ projectId: 'p1', callId: 12 });
    expect(screen.getByText('codex failed')).toBeInTheDocument();
  });
});
