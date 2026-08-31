import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  create: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('@/api/r23', () => ({
  useR23Rubrics: () => ({ data: [] }),
  useCreateR23Rubric: () => ({ mutate: state.create, isPending: false }),
  useDeleteR23Rubric: () => ({ mutate: state.remove, isPending: false }),
}));

import R23RubricPage from './R23RubricPage';

describe('R23RubricPage literature-aligned default', () => {
  afterEach(() => {
    cleanup();
    state.create.mockReset();
    state.remove.mockReset();
  });

  it('provides five integer anchors, a half-point rule, and no condition leakage', () => {
    render(<R23RubricPage />);

    const rubric = screen.getByLabelText('Rubric 正文') as HTMLTextAreaElement;
    expect(rubric.value).toContain('Half-point rule');
    expect(rubric.value).toContain('CONTENT');
    expect(rubric.value).toContain('ORGANIZATION');
    expect(rubric.value).toContain('LANGUAGE');
    expect(rubric.value.match(/^5 =/gm)).toHaveLength(3);
    expect(rubric.value.match(/^1 =/gm)).toHaveLength(3);
    expect(rubric.value).not.toMatch(/CASE|corrupt/i);
  });

  it('saves the documented rubric as a new version', () => {
    render(<R23RubricPage />);

    fireEvent.click(screen.getByRole('button', { name: '保存 Rubric' }));

    expect(state.create).toHaveBeenCalledWith({
      name: 'DREsS r23 literature-aligned rubric v2',
      rubric: expect.stringContaining('Integer-anchor rule'),
    });
  });
});
