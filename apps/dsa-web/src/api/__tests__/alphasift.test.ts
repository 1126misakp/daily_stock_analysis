import { beforeEach, describe, expect, it, vi } from 'vitest';
import { alphasiftApi } from '../alphasift';

const { get, post, getConfig, updateConfig } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  getConfig: vi.fn(),
  updateConfig: vi.fn(),
}));

vi.mock('../index', () => ({
  default: {
    get,
    post,
  },
}));

vi.mock('../systemConfig', () => ({
  systemConfigApi: {
    getConfig: (...args: unknown[]) => getConfig(...args),
    update: (...args: unknown[]) => updateConfig(...args),
  },
}));

describe('alphasiftApi', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    getConfig.mockReset();
    updateConfig.mockReset();
  });

  it('enables the config and checks bundled AlphaSift availability', async () => {
    getConfig.mockResolvedValueOnce({ configVersion: 'v1', maskToken: '******' });
    updateConfig.mockResolvedValueOnce({ success: true });
    get.mockResolvedValueOnce({
      data: {
        enabled: true,
        available: true,
        install_spec_is_default: true,
      },
    });

    await alphasiftApi.enable();

    expect(updateConfig).toHaveBeenCalledWith({
      configVersion: 'v1',
      maskToken: '******',
      reloadNow: true,
      items: [{ key: 'ALPHASIFT_ENABLED', value: 'true' }],
    });
    expect(get).toHaveBeenCalledWith('/api/v1/alphasift/status');
    expect(updateConfig).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
  });

  it('keeps enable behavior when called without object binding', async () => {
    getConfig.mockResolvedValueOnce({ configVersion: 'v1', maskToken: '******' });
    updateConfig.mockResolvedValueOnce({ success: true });
    get.mockResolvedValueOnce({
      data: {
        enabled: true,
        available: true,
        install_spec_is_default: true,
      },
    });

    const enable = alphasiftApi.enable;
    await enable();

    expect(updateConfig).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
  });

  it('rolls back ALPHASIFT_ENABLED when bundled AlphaSift is unavailable after enabling', async () => {
    getConfig
      .mockResolvedValueOnce({ configVersion: 'v1', maskToken: '******' })
      .mockResolvedValueOnce({ configVersion: 'v2', maskToken: '******' });
    updateConfig.mockResolvedValue({ success: true });
    get.mockResolvedValueOnce({
      data: {
        enabled: true,
        available: false,
        install_spec_is_default: true,
      },
    });

    await expect(alphasiftApi.enable()).rejects.toThrow('AlphaSift');

    expect(updateConfig).toHaveBeenNthCalledWith(1, {
      configVersion: 'v1',
      maskToken: '******',
      reloadNow: true,
      items: [{ key: 'ALPHASIFT_ENABLED', value: 'true' }],
    });
    expect(updateConfig).toHaveBeenNthCalledWith(2, {
      configVersion: 'v2',
      maskToken: '******',
      reloadNow: true,
      items: [{ key: 'ALPHASIFT_ENABLED', value: 'false' }],
    });
    expect(post).not.toHaveBeenCalled();
  });

  it('loads strategies from the AlphaSift API', async () => {
    get.mockResolvedValueOnce({
      data: {
        enabled: true,
        strategies: [
          {
            id: 'dual_low',
            name: 'Dual Low',
            description: 'value',
            category: 'value',
            market_scope: ['cn'],
          },
        ],
        strategy_count: 1,
      },
    });

    const result = await alphasiftApi.getStrategies();

    expect(get).toHaveBeenCalledWith('/api/v1/alphasift/strategies');
    expect(result.enabled).toBe(true);
    expect(result.strategyCount).toBe(1);
    expect(result.strategies[0].id).toBe('dual_low');
    expect(result.strategies[0].marketScope).toEqual(['cn']);
  });

  it('uses a long timeout for LLM-backed screening', async () => {
    post.mockResolvedValueOnce({
      data: {
        enabled: true,
        candidates: [],
        candidate_count: 0,
        llm_ranked: true,
      },
    });

    await alphasiftApi.screen({ market: 'cn', strategy: 'dual_low', maxResults: 3 });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/alphasift/screen',
      { market: 'cn', strategy: 'dual_low', max_results: 3 },
      { timeout: 180000 }
    );
  });

  it('submits a screen job and returns camelCased job info', async () => {
    post.mockResolvedValueOnce({ data: { job_id: 'abc123', status: 'pending' } });

    const result = await alphasiftApi.submitScreenJob({ market: 'cn', strategy: 'dual_low', maxResults: 3 });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/alphasift/screen/jobs',
      { market: 'cn', strategy: 'dual_low', max_results: 3 },
      expect.objectContaining({ timeout: expect.any(Number) }),
    );
    expect(result.jobId).toBe('abc123');
    expect(result.status).toBe('pending');
  });

  it('gets a screen job and camelCases the completed payload', async () => {
    get.mockResolvedValueOnce({
      data: {
        job_id: 'abc123',
        status: 'completed',
        candidate_count: 1,
        candidates: [{ code: '600519', name: 'MT', rank: 1, reason: '' }],
        llm_ranked: true,
      },
    });

    const result = await alphasiftApi.getScreenJob('abc123');

    expect(get).toHaveBeenCalledWith('/api/v1/alphasift/screen/jobs/abc123', expect.any(Object));
    expect(result.status).toBe('completed');
    expect(result.candidateCount).toBe(1);
    expect(result.candidates[0].code).toBe('600519');
    expect(result.llmRanked).toBe(true);
  });
});
