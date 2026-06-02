import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StockScreeningPage from '../StockScreeningPage';

const { enableAlphaSift, getAlphaSiftStatus, getStrategies, screenStocks, submitScreenJob, getScreenJob } =
  vi.hoisted(() => ({
    enableAlphaSift: vi.fn(),
    getAlphaSiftStatus: vi.fn(),
    getStrategies: vi.fn(),
    screenStocks: vi.fn(),
    submitScreenJob: vi.fn(),
    getScreenJob: vi.fn(),
  }));

vi.mock('../../api/alphasift', () => ({
  alphasiftApi: {
    enable: (...args: unknown[]) => enableAlphaSift(...args),
    getStatus: (...args: unknown[]) => getAlphaSiftStatus(...args),
    getStrategies: (...args: unknown[]) => getStrategies(...args),
    screen: (...args: unknown[]) => screenStocks(...args),
    submitScreenJob: (...args: unknown[]) => submitScreenJob(...args),
    getScreenJob: (...args: unknown[]) => getScreenJob(...args),
  },
}));

const mockStrategiesResponse = {
  enabled: true,
  strategies: [
    {
      id: 'dual_low',
      name: 'Dual Low',
      title: 'Dual Low',
      description: 'Low valuation strategy',
      category: 'value',
      tag: 'value',
      tags: ['value'],
      marketScope: ['cn'],
    },
  ],
  strategyCount: 1,
};

describe('StockScreeningPage', () => {
  beforeEach(() => {
    enableAlphaSift.mockReset();
    getAlphaSiftStatus.mockReset();
    getStrategies.mockReset();
    screenStocks.mockReset();
    submitScreenJob.mockReset();
    getScreenJob.mockReset();
    getStrategies.mockResolvedValue(mockStrategiesResponse);
  });

  it('re-syncs enabled state when AlphaSift install fails after config is enabled', async () => {
    getAlphaSiftStatus
      .mockResolvedValueOnce({
        enabled: false,
        available: false,
        installSpecIsDefault: true,
      })
      .mockResolvedValueOnce({
        enabled: true,
        available: false,
        installSpecIsDefault: true,
      });
    enableAlphaSift.mockRejectedValueOnce(new Error('安装 AlphaSift 失败'));

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股未开启')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /运行选股/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '开启 AlphaSift' }));

    await waitFor(() => expect(getAlphaSiftStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByText('选股已开启')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /运行选股/ })).not.toBeDisabled();
    expect(screen.getByText('安装 AlphaSift 失败')).toBeInTheDocument();
  });

  it('shows input strategy when strategy is not in preset list', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: false,
      installSpecIsDefault: true,
    });
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });
    getScreenJob.mockResolvedValue({
      jobId: 'job1',
      status: 'completed',
      enabled: true,
      candidates: [],
      candidateCount: 0,
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('策略参数'), {
      target: { value: 'custom_strategy_alpha' },
    });

    expect(screen.getByDisplayValue('custom_strategy_alpha')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
    await waitFor(() => expect(submitScreenJob).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/自定义策略 \(custom_strategy_alpha\)/)).toBeInTheDocument());
  });

  it('uses supported AlphaSift strategy ids and cn market', async () => {
    getStrategies.mockResolvedValueOnce({
      enabled: true,
      strategies: [
        { id: 'balanced_alpha', name: '平衡选股', description: 'desc', category: '框架' },
        { id: 'capital_heat', name: '资金热度', description: 'desc', category: '动量' },
        { id: 'dual_low', name: '双低', description: 'desc', category: '价值' },
        { id: 'oversold_reversal', name: '超跌', description: 'desc', category: '反转' },
        { id: 'shrink_pullback', name: '缩量回踩', description: 'desc', category: '趋势' },
      ],
      strategyCount: 5,
    });
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: false,
      installSpecIsDefault: true,
    });
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });
    getScreenJob.mockResolvedValue({
      jobId: 'job1',
      status: 'completed',
      enabled: true,
      candidates: [],
      candidateCount: 0,
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();

    const marketSelect = screen.getByLabelText('市场') as HTMLSelectElement;
    expect(Array.from(marketSelect.options).map((option) => option.value)).toEqual(['cn']);

    [
      ['平衡选股', 'balanced_alpha'],
      ['资金热度', 'capital_heat'],
      ['超跌', 'oversold_reversal'],
      ['缩量回踩', 'shrink_pullback'],
    ].forEach(([label, id]) => {
      fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }));
      expect(screen.getByDisplayValue(id)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));
    await waitFor(() => expect(submitScreenJob).toHaveBeenCalledTimes(1));
    expect(submitScreenJob).toHaveBeenCalledWith({
      market: 'cn',
      strategy: 'shrink_pullback',
      maxResults: 3,
    });
  });

  it('clears previous screening candidates when strategy changes', async () => {
    getStrategies.mockResolvedValueOnce({
      enabled: true,
      strategies: [
        { id: 'dual_low', name: '双低选股', description: 'desc', category: '价值' },
        { id: 'capital_heat', name: '资金热度', description: 'desc', category: '动量' },
      ],
      strategyCount: 2,
    });
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });
    getScreenJob.mockResolvedValue({
      jobId: 'job1',
      status: 'completed',
      enabled: true,
      candidates: [
        {
          rank: 1,
          code: '000001',
          name: '旧策略股票',
          score: 88.5,
          reason: 'old result',
          raw: {},
        },
      ],
      candidateCount: 1,
    });

    render(<StockScreeningPage />);

    expect(await screen.findByText('选股已开启')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /运行选股/ }));

    expect(await screen.findByText('旧策略股票', undefined, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.getByText('选股完成')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /资金热度/ }));

    expect(screen.queryByText('旧策略股票')).not.toBeInTheDocument();
    expect(screen.getByText('等待运行')).toBeInTheDocument();
    expect(screen.getByText('当前策略：资金热度 · A 股')).toBeInTheDocument();
  });

  it('submits a job and renders candidates after polling completes', async () => {
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
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
    expect(await screen.findByText('选股已开启')).toBeInTheDocument();

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
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });

    // 用 deferred promise 控制轮询结果的解析时机：组件卸载后才解析
    let resolveJob: (value: unknown) => void = () => {};
    const pendingJob = new Promise((resolve) => {
      resolveJob = resolve;
    });
    getScreenJob.mockReturnValue(pendingJob);

    const { unmount } = render(<StockScreeningPage />);
    expect(await screen.findByText('选股已开启')).toBeInTheDocument();

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
    getAlphaSiftStatus.mockResolvedValueOnce({
      enabled: true,
      available: true,
      installSpecIsDefault: true,
    });
    submitScreenJob.mockResolvedValue({ jobId: 'job1', status: 'pending' });
    getScreenJob.mockRejectedValue(
      Object.assign(new Error('not found'), { response: { status: 404 } }),
    );

    render(<StockScreeningPage />);
    expect(await screen.findByText('选股已开启')).toBeInTheDocument();

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
