import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import MasterDataPanel from '../MasterDataPanel';

describe('MasterDataPanel', () => {
  it('renders 4 sub-tabs', () => {
    render(<MasterDataPanel />);
    expect(screen.getByText(/股票主数据/)).toBeInTheDocument();
    expect(screen.getByText(/基金主数据/)).toBeInTheDocument();
    expect(screen.getByText(/指数主数据/)).toBeInTheDocument();
    expect(screen.getByText(/分类维度管理/)).toBeInTheDocument();
  });
});
