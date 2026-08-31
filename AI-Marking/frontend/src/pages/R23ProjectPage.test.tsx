import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  project: {
    id: 'r23-project',
    protocol_id: 'r23-dress-case-rubric-sensitivity-2026-08-v1',
    name: '正式实验',
    kind: 'formal',
    status: 'running',
    rubric_id: 'rubric',
    runner_bindings: [
      {
        id: 'binding-a',
        position: 1,
        model: 'real-model-a',
        reasoning_effort: 'medium',
        speed_mode: 'standard',
        timeout_seconds: 600,
      },
    ],
    data_processing_confirmed: true,
    data_status: { ready: true, datasets: [] },
    manifest_sha256: 'a'.repeat(64),
    manifest_summary: {},
    results_embargoed: true,
    report_sha256: null,
    progress: { total: 2535, succeeded: 120, attention_required: 1 },
    performance: { p50_latency_ms: 1000, p95_latency_ms: 2000 },
  },
  report: { results_embargoed: true, status: 'running', report: null } as {
    results_embargoed: boolean;
    status: string;
    report: unknown;
  },
  calls: {
    results_embargoed: true,
    items: [
      {
        id: 9,
        model_binding_id: 'binding-a',
        run_group_id: 1,
        run_index: 0,
        status: 'attention_required',
        input_sha256: 'b'.repeat(64),
        attempt_count: 1,
        latency_ms: 0,
        failure_code: 'timeout',
        failure_summary: 'Codex 调用超时；未自动重试。',
      },
    ],
  },
  action: vi.fn(),
  actionPending: false,
  runtime: { runner_online: true, last_heartbeat_at: '2026-08-31T00:00:00' },
  retry: vi.fn(),
  repeat: vi.fn(),
}));

vi.mock('@/api/r23', () => ({
  useR23Project: () => ({ data: state.project }),
  useR23Groups: () => ({ data: [] }),
  useR23Calls: () => ({ data: state.calls }),
  useR23Report: () => ({ data: state.report }),
  useR23Runtime: () => ({
    data: state.runtime,
    isLoading: false,
    isError: false,
  }),
  useR23Action: () => ({
    mutate: state.action,
    isPending: state.actionPending,
  }),
  useRetryR23Call: () => ({ mutate: state.retry, isPending: false }),
  useRepeatR23Project: () => ({ mutate: state.repeat, isPending: false }),
  r23ExportUrl: (id: string, kind: string) =>
    `/api/r23/projects/${id}/exports/${kind}`,
}));

import R23ProjectPage from './R23ProjectPage';

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/dress/r23-project']}>
      <Routes>
        <Route path="/dress/:projectId" element={<R23ProjectPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function analysisReport() {
  const trends = Array.from({ length: 9 }, (_, index) => ({
    case_level: 1 + index / 2,
    mean: 1 + index / 2,
    ci95: [1 + index / 2, 1 + index / 2],
  }));
  return {
    protocol_id: state.project.protocol_id,
    analysis_status: 'locked',
    bootstrap_replicates: 5000,
    cells: ['content', 'organization', 'language'].flatMap((dimension) =>
      ['binding-a'].map((binding) => ({
        model_binding_id: binding,
        dimension,
        evidence_level:
          dimension === 'language' ? 'exploratory' : 'confirmatory',
        n: 540,
        mpa: 0.8,
        mpa_ci95: [0.75, 0.85],
        qwk_intended_label_recovery: 0.7,
        mae: 0.4,
        spearman_rho: 0.8,
        extreme_level_difference: 3,
        trend: trends,
        h1_pass: true,
        h2_pass: true,
      })),
    ),
    organization_selectivity: [
      {
        model_binding_id: 'binding-a',
        si: 0.4,
        ci95: [0.2, 0.6],
        h3_pass: true,
      },
    ],
    stable_sensitivity: { content: true, organization: true, language: true },
    interpretation_guardrail: 'QWK measures intended-label recovery only.',
  };
}

describe('R23ProjectPage blinding and recovery', () => {
  afterEach(() => {
    cleanup();
    state.project.status = 'running';
    state.project.results_embargoed = true;
    state.report = { results_embargoed: true, status: 'running', report: null };
    state.action.mockReset();
    state.actionPending = false;
    state.runtime.runner_online = true;
    state.retry.mockReset();
    state.repeat.mockReset();
    vi.restoreAllMocks();
  });

  it('keeps all score and trend UI sealed while a formal run is active', () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '结果分析' }));
    expect(screen.getByText('结果保持密封')).toBeInTheDocument();
    expect(screen.queryByText('预注册指标')).not.toBeInTheDocument();
    expect(screen.queryByText('0.800')).not.toBeInTheDocument();
  });

  it('starts a draft directly without a manual freeze action', () => {
    state.project.status = 'draft';
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();
    expect(
      screen.queryByRole('button', { name: /冻结/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '开始实验' }));
    expect(state.action).toHaveBeenCalledWith({
      id: 'r23-project',
      action: 'start',
    });
  });

  it('shows snapshot progress while a start request is pending', () => {
    state.project.status = 'draft';
    state.actionPending = true;
    renderPage();
    const button = screen.getByRole('button', { name: '正在生成运行快照…' });
    expect(button).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: '开始实验' }),
    ).not.toBeInTheDocument();
  });

  it('shows MCP state and prevents starting while the worker is offline', () => {
    state.project.status = 'draft';
    state.runtime.runner_online = false;
    renderPage();
    expect(screen.getByText('MCP 未连接')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始实验' })).toBeDisabled();
  });

  it('shows native SVG and metrics only after the locked report is released', () => {
    state.project.status = 'completed';
    state.project.results_embargoed = false;
    state.report = {
      results_embargoed: false,
      status: 'completed',
      report: analysisReport(),
    };
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '结果分析' }));
    expect(
      screen.getByRole('img', { name: /CASE 预设等级与模型平均预测分数/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('预注册指标')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Language' }));
    expect(screen.getByText(/探索性分析/)).toBeInTheDocument();
    expect(screen.getByText(/不是与人工评分的一致性/)).toBeInTheDocument();
  });

  it('offers only explicit manual retry and preserves the blinded audit view', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '查看并重试' }));
    expect(screen.getByText('需要处理的调用（1）')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试此调用' }));
    expect(state.retry).toHaveBeenCalledWith({
      projectId: 'r23-project',
      callId: 9,
    });
    expect(screen.getByText(/未自动重试/)).toBeInTheDocument();
    expect(screen.queryByText(/student text/i)).not.toBeInTheDocument();
  });

  it('creates an independent repeat from a completed project', () => {
    state.project.status = 'completed';
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '再次运行' }));
    expect(state.repeat).toHaveBeenCalledWith(
      'r23-project',
      expect.any(Object),
    );
  });
});
