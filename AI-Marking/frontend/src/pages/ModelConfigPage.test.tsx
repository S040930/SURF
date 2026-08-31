import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  patch: vi.fn(),
  clone: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('@/api/r20', () => ({
  useR20ModelConfigs: () => ({
    data: [
      {
        id: 'model-1',
        name: '现有模型',
        config_json: {
          base_url: 'https://api.example.com/v1',
          requested_model: 'model-a',
          expected_returned_model: 'model-a',
          api_key_env: 'MODEL_KEY',
          timeout_seconds: 120,
          input_cost_fen_per_million: 100,
          output_cost_fen_per_million: 200,
        },
        config_sha256: '1234567890abcdef1234567890abcdef',
        status: 'draft',
      },
    ],
  }),
  useCreateR20ModelConfig: () => ({ mutate: mocks.create, isPending: false }),
  useUpdateR20ModelConfig: () => ({ mutate: mocks.patch, isPending: false }),
  useCloneR20ModelConfig: () => ({ mutate: mocks.clone, isPending: false }),
  useDeleteR20ModelConfig: () => ({ mutate: mocks.remove, isPending: false }),
  useFreezeR20ModelConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

import ModelConfigPage from './ModelConfigPage';

function beginEditing() {
  fireEvent.click(screen.getByRole('button', { name: '编辑' }));
  expect(screen.getByText('编辑模型配置')).toBeInTheDocument();
}

describe('model configuration cancellation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  it('exits immediately when the form has not changed', () => {
    render(<ModelConfigPage />);
    beginEditing();

    fireEvent.click(screen.getByRole('button', { name: '取消编辑' }));

    expect(screen.getByText('新建模型配置')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('keeps dirty values or discards them only after confirmation', () => {
    render(<ModelConfigPage />);
    beginEditing();
    const nameInput = screen.getByRole('textbox', { name: /配置名称/ });
    fireEvent.change(nameInput, { target: { value: '未保存的新名称' } });

    fireEvent.click(screen.getByRole('button', { name: '取消编辑' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
    expect(nameInput).toHaveValue('未保存的新名称');

    fireEvent.click(screen.getByRole('button', { name: '取消编辑' }));
    fireEvent.click(screen.getByRole('button', { name: '放弃更改' }));
    expect(screen.getByText('新建模型配置')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /配置名称/ })).toHaveValue('');
  });

  it('clears edit and dirty state after a successful save', () => {
    mocks.patch.mockImplementation((_variables, options) =>
      options?.onSuccess?.(),
    );
    render(<ModelConfigPage />);
    beginEditing();
    fireEvent.change(screen.getByRole('textbox', { name: /配置名称/ }), {
      target: { value: '已保存的新名称' },
    });

    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));

    expect(mocks.patch).toHaveBeenCalledOnce();
    expect(screen.getByText('新建模型配置')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '取消编辑' }),
    ).not.toBeInTheDocument();
  });

  it('submits the selected response format with the config', () => {
    let captured: unknown;
    mocks.create.mockImplementation((variables) => {
      captured = variables;
    });
    render(<ModelConfigPage />);
    fireEvent.change(screen.getByRole('textbox', { name: /配置名称/ }), {
      target: { value: 'DeepSeek 官方' },
    });
    fireEvent.change(screen.getAllByRole('textbox', { name: /模型 ID/ })[0], {
      target: { value: 'deepseek-v4-flash' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: /模型服务 URL/ }), {
      target: { value: 'https://api.deepseek.com' },
    });

    fireEvent.click(screen.getByRole('combobox', { name: /结构化输出格式/ }));
    fireEvent.click(screen.getByRole('option', { name: /json_object/ }));

    fireEvent.click(screen.getByRole('button', { name: '创建配置' }));
    expect(captured).toMatchObject({
      config_json: { response_format: 'json_object' },
    });
  });
});
