import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StockScreeningPage from '../StockScreeningPage';

const { getStrategies, submitScreenJob, getScreenJob } = vi.hoisted(() => ({
  getStrategies: vi.fn(),
  submitScreenJob: vi.fn(),
  getScreenJob: vi.fn(),
}));

vi.mock('../../api/screen', () => ({
  screenApi: {
    getStrategies: (...args: unknown[]) => getStrategies(...args),
    submitScreenJob: (...args: unknown[]) => submitScreenJob(...args),
    getScreenJob: (...args: unknown[]) => getScreenJob(...args),
  },
}));

const mockStrategiesResponse = {
  enabled: true,
  strategies: [
    {
      id: 'ma_golden_cross',
      name: '均线金叉',
      description: '检测均线金叉配合量能确认信号',
      category: 'trend',
      tradingStyle: '趋势确认、稳健追涨',
    },
    {
      id: 'bottom_volume',
      name: '底部放量',
      description: '深跌后放量收阳',
      category: 'reversal',
      tradingStyle: '抄底、左侧反转',
    },
  ],
  strategyCount: 2,
};

describe('StockScreeningPage', () => {
  beforeEach(() => {
    getStrategies.mockReset();
    submitScreenJob.mockReset();
    getScreenJob.mockReset();
    getStrategies.mockResolvedValue(mockStrategiesResponse);
  });

  it('renders strategy cards with trading style and no market selector', async () => {
    render(<StockScreeningPage />);

    expect(await screen.findByText('均线金叉')).toBeInTheDocument();
    expect(screen.getByText('适合：趋势确认、稳健追涨')).toBeInTheDocument();
    // 不再有市场下拉
    expect(screen.queryByLabelText('市场')).not.toBeInTheDocument();
    // 偏好输入框存在
    expect(screen.getByPlaceholderText(/喜欢科技股/)).toBeInTheDocument();
  });

  it('rejects submit when neither strategy nor preference is provided', async () => {
    render(<StockScreeningPage />);
    expect(await screen.findByText('均线金叉')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('策略和用户偏好至少填写一个')).toBeInTheDocument();
    expect(submitScreenJob).not.toHaveBeenCalled();
  });

  it('submits with strategy and preference', async () => {
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });
    getScreenJob.mockResolvedValue({
      jobId: 'job1',
      status: 'completed',
      enabled: true,
      candidates: [],
      candidateCount: 0,
    });

    render(<StockScreeningPage />);
    expect(await screen.findByText('均线金叉')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /均线金叉/ }));
    fireEvent.change(screen.getByPlaceholderText(/喜欢科技股/), { target: { value: '喜欢科技' } });
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    await waitFor(() => expect(submitScreenJob).toHaveBeenCalledTimes(1));
    expect(submitScreenJob).toHaveBeenCalledWith({
      strategy: 'ma_golden_cross',
      preference: '喜欢科技',
      maxResults: 20,
    });
  });

  it('clears previous candidates when strategy changes', async () => {
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });
    getScreenJob.mockResolvedValue({
      jobId: 'job1',
      status: 'completed',
      enabled: true,
      candidates: [{ rank: 1, code: '000001', name: '旧策略股票', score: 88.5, reason: 'old result', raw: {} }],
      candidateCount: 1,
    });

    render(<StockScreeningPage />);
    expect(await screen.findByText('均线金叉')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /均线金叉/ }));
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('旧策略股票', undefined, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.getByText('选股完成')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /底部放量/ }));

    expect(screen.queryByText('旧策略股票')).not.toBeInTheDocument();
    expect(screen.getByText('等待运行')).toBeInTheDocument();
  });

  it('submits a job and renders candidates after polling completes', async () => {
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });
    getScreenJob
      .mockResolvedValueOnce({ jobId: 'job1', status: 'running', candidates: [] })
      .mockResolvedValueOnce({
        jobId: 'job1',
        status: 'completed',
        enabled: true,
        candidateCount: 1,
        candidates: [{ code: '600519', name: '贵州茅台', rank: 1, reason: '', raw: {} }],
        llmRanked: true,
      });

    render(<StockScreeningPage />);
    expect(await screen.findByText('均线金叉')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /均线金叉/ }));

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
      // submit 完成后第一次轮询(running)
      await vi.advanceTimersByTimeAsync(4000);
      // 第二次轮询(completed)
      await vi.advanceTimersByTimeAsync(4000);
    } finally {
      vi.useRealTimers();
    }

    expect(submitScreenJob).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('600519')).toBeInTheDocument();
  });

  it('neutralizes an in-flight poll continuation after unmount', async () => {
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });

    // 用 deferred promise 控制轮询结果的解析时机：组件卸载后才解析
    let resolveJob: (value: unknown) => void = () => {};
    const pendingJob = new Promise((resolve) => {
      resolveJob = resolve;
    });
    getScreenJob.mockReturnValue(pendingJob);

    const { unmount } = render(<StockScreeningPage />);
    expect(await screen.findByText('均线金叉')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /均线金叉/ }));

    vi.useFakeTimers();
    try {
      // 启动一次运行：提交 job1，触发首次轮询（停在 pendingJob 上等待）
      fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
      await vi.advanceTimersByTimeAsync(4000);
      expect(getScreenJob).toHaveBeenCalledTimes(1);

      // 卸载组件 —— 此时仍有一笔 getScreenJob 在飞行中无法取消
      unmount();

      // 解析这笔过期的轮询：running 状态会尝试再调度下一次轮询
      resolveJob({ jobId: 'job1', status: 'running', candidates: [] });
      await vi.advanceTimersByTimeAsync(0);

      // epoch 守卫应使过期续体成为 no-op：不得再调度下一次轮询
      await vi.advanceTimersByTimeAsync(4000);
      expect(getScreenJob).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows a re-run hint when polling returns 404', async () => {
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });
    getScreenJob.mockRejectedValue(
      Object.assign(new Error('not found'), { response: { status: 404 } }),
    );

    render(<StockScreeningPage />);
    expect(await screen.findByText('均线金叉')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /均线金叉/ }));

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
      await vi.advanceTimersByTimeAsync(4000);
    } finally {
      vi.useRealTimers();
    }

    expect(await screen.findByText(/结果未保留|重新运行/)).toBeInTheDocument();
  });
});
