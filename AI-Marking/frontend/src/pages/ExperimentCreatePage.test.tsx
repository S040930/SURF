import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  create: vi.fn(),
}));

vi.mock('@/api/experiments', () => ({
  useDatasetRevision: () => ({
    data: {
      dataset: {
        id: 'ds-1',
        key: 'dress_new',
        name: 'DREsS_New',
        access_level: 'restricted',
      },
      revision: {
        id: 'revision-1',
        revision_label: 'dress_new-5af9ab64',
        audit: {
          dataset_key: 'dress_new',
          datasets: [{ file: 'DREsS_New.tsv', sha256: 'a'.repeat(64), bytes: 1, rows: 2279 }],
          columns: [],
          raw_rows: 2279,
          empty_essay_rows: 300,
          conflict_groups: 5,
          conflict_rows: 10,
          merged_duplicate_records: 3,
          unique_inputs: 1966,
          distinct_prompts: 51,
          low_tail_inputs: 96,
          strata_sizes: { '1': 278, '2': 498, '3': 572, '4': 424, '5': 194 },
          total_column_mismatches: {
            count: 2,
            in_scoring_pool: 1,
            detail: [],
            rule: '综合分一律由三维重算',
          },
        },
      },
      contract: {
        id: 'contract-1',
        channels: [
          { key: 'content', label: 'Content' },
          { key: 'organization', label: 'Organization' },
          { key: 'language', label: 'Language' },
        ],
        grid_min_x2: 1,
        grid_max_x2: 10,
        score_values: [],
        schema_sha256: 'd'.repeat(64),
      },
    },
    isLoading: false,
  }),
  useExperimentProjects: () => ({ data: [] }),
  useExperimentRubrics: () => ({
    data: [
      {
        id: 'rubric-1',
        template_id: 'dress_new_human_agreement_v1',
        name: 'DREsS 原始三维 rubric',
        rubric: 'rubric',
        rubric_sha256: 'c'.repeat(64),
        status: 'ready',
      },
    ],
  }),
  useExperimentRunners: () => ({
    data: [
      {
        id: 'runner-a',
        name: 'luna',
        model: 'gpt-5.6-luna',
        reasoning_effort: 'medium',
        speed_mode: 'standard',
        timeout_seconds: 120,
        config_sha256: 'b'.repeat(64),
        status: 'ready',
      },
      {
        id: 'runner-b',
        name: 'terra',
        model: 'gpt-5.6-terra',
        reasoning_effort: 'medium',
        speed_mode: 'standard',
        timeout_seconds: 120,
        config_sha256: 'b'.repeat(64),
        status: 'ready',
      },
    ],
  }),
  useExperimentTemplates: () => ({
    data: [
      {
        template_id: 'dress_new_human_agreement_v1',
        name: 'DREsS_New 人工评分一致性验证',
        dataset_key: 'dress_new',
        runner_count: 2,
        require_runner_alignment: true,
        sampling_seed: '20260905',
        contract: {
          channels: [],
          grid_min_x2: 1,
          grid_max_x2: 10,
          score_values: [],
          schema_sha256: 'd'.repeat(64),
        },
      },
    ],
  }),
  useCreateExperimentProject: () => ({ mutate: state.create, isPending: false }),
}));

import ExperimentCreatePage from './ExperimentCreatePage';

describe('ExperimentCreatePage project creation', () => {
  afterEach(() => {
    cleanup();
    state.create.mockReset();
  });

  it('creates a formal project with both aligned runners and the pinned revision', () => {
    render(
      <MemoryRouter>
        <ExperimentCreatePage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText('实验名称'), {
      target: { value: 'DREsS_New 正式' },
    });
    const selects = screen.getAllByRole('combobox');
    // [项目类型, 研究模板, Rubric, 模型 A, 模型 B] in render order.
    fireEvent.click(selects[1]);
    fireEvent.click(
      screen.getByRole('option', { name: 'DREsS_New 人工评分一致性验证' }),
    );
    fireEvent.click(screen.getAllByRole('combobox')[2]);
    fireEvent.click(
      screen.getByRole('option', { name: 'DREsS 原始三维 rubric' }),
    );
    fireEvent.click(screen.getAllByRole('combobox')[3]);
    fireEvent.click(screen.getByRole('option', { name: /luna/ }));
    fireEvent.click(screen.getAllByRole('combobox')[4]);
    fireEvent.click(screen.getByRole('option', { name: /terra/ }));
    fireEvent.click(
      screen.getByLabelText(
        '我已获得使用所选模型服务处理该受限数据的授权，并将遵守研究伦理与保密要求。',
      ),
    );

    expect(screen.getByText(/720 次正式 \+ 72 次复测/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建项目' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '创建项目' }));

    expect(state.create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'DREsS_New 正式',
        kind: 'formal',
        template_id: 'dress_new_human_agreement_v1',
        dataset_revision_id: 'revision-1',
        rubric_id: 'rubric-1',
        runner_config_ids: ['runner-a', 'runner-b'],
        data_processing_confirmed: true,
      }),
      expect.anything(),
    );
  });
});
