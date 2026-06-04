import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MCPKeyPage from '../MCPKeyPage';
import * as api from '../../api/mcpKeys';

vi.mock('../../api/mcpKeys');

describe('MCPKeyPage', () => {
  beforeEach(() => {
    vi.mocked(api.getMcpKey).mockResolvedValue({
      key: '0bc83118abcdef', endpoint: 'https://x/mcp', configured: true,
    });
    vi.mocked(api.resetMcpKey).mockResolvedValue({
      key: 'newresetkey123', endpoint: 'https://x/mcp', configured: true,
    });
  });

  it('masks key by default and reveals on toggle', async () => {
    render(<MCPKeyPage />);
    await waitFor(() => expect(api.getMcpKey).toHaveBeenCalled());
    // 默认脱敏：不直接出现完整 key
    expect(screen.queryByText('0bc83118abcdef')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /显示|reveal/i }));
    await waitFor(() => expect(screen.getByText('0bc83118abcdef')).toBeInTheDocument());
  });

  it('resets key after confirm', async () => {
    render(<MCPKeyPage />);
    await waitFor(() => expect(api.getMcpKey).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: /重置/i }));
    // 二次确认
    fireEvent.click(screen.getByRole('button', { name: /确认重置/i }));
    await waitFor(() => expect(api.resetMcpKey).toHaveBeenCalled());
  });
});
