import type React from 'react';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, CircleAlert, Play, PlusCircle, Search, SlidersHorizontal } from 'lucide-react';
import {
  screenApi,
  type ScreenCandidate,
  type ScreenResponse,
  type ScreenStrategy,
} from '../api/screen';
import { getParsedApiError } from '../api/error';
import { AppPage, Button, InlineAlert } from '../components/common';

const formatScore = (score: ScreenCandidate['score']) => {
  if (score == null || Number.isNaN(Number(score))) {
    return '-';
  }
  return Number(score).toFixed(2);
};

const formatNumber = (value: unknown, digits = 2) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return Number(value).toFixed(digits);
};

const formatAmount = (value: unknown) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  const amount = Number(value);
  if (Math.abs(amount) >= 100_000_000) {
    return `${(amount / 100_000_000).toFixed(2)} 亿`;
  }
  if (Math.abs(amount) >= 10_000) {
    return `${(amount / 10_000).toFixed(2)} 万`;
  }
  return amount.toFixed(2);
};

const getCandidateReason = (item: ScreenCandidate) => {
  if (item.reason) {
    return item.reason;
  }
  return '选股返回候选，但没有给出文字摘要。请查看下方逻辑、风险和原始字段。';
};

const StockScreeningPage: React.FC = () => {
  const [strategy, setStrategy] = useState('');
  const [preference, setPreference] = useState('');
  const [strategies, setStrategies] = useState<ScreenStrategy[]>([]);
  const [maxResults, setMaxResults] = useState(20);
  const [candidates, setCandidates] = useState<ScreenCandidate[]>([]);
  const [screenMeta, setScreenMeta] = useState<ScreenResponse | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingStrategies, setLoadingStrategies] = useState(false);
  const [error, setError] = useState('');
  const [strategyLoadError, setStrategyLoadError] = useState('');

  const POLL_INTERVAL_MS = 4000;
  const MAX_POLL_MS = 15 * 60 * 1000;
  const MAX_TRANSIENT_ERRORS = 3;
  const pollTimerRef = useRef<number | null>(null);
  const pollStartRef = useRef<number>(0);
  const transientErrorsRef = useRef<number>(0);
  const runEpochRef = useRef(0);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current != null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(
    () => () => {
      // 卸载时 bump epoch，使任何在飞行中的轮询续体成为 no-op
      runEpochRef.current += 1;
      stopPolling();
    },
    [stopPolling],
  );

  const selectedStrategy = useMemo(() => strategies.find((item) => item.id === strategy), [strategies, strategy]);
  const displayedStrategy = selectedStrategy?.name || (strategy ? strategy : '仅按用户偏好');

  const clearScreeningResults = () => {
    setCandidates([]);
    setScreenMeta(null);
    setExpandedCode(null);
  };

  const loadStrategies = useCallback(async () => {
    setLoadingStrategies(true);
    try {
      setStrategyLoadError('');
      const result = await screenApi.getStrategies();
      const loadedStrategies = result.strategies || [];
      setStrategies(loadedStrategies);
    } catch (err) {
      setStrategies([]);
      setStrategyLoadError(err instanceof Error ? err.message : '策略列表加载失败');
    } finally {
      setLoadingStrategies(false);
    }
  }, []);

  useEffect(() => {
    void loadStrategies();
  }, [loadStrategies]);

  const handleStrategyChange = (nextStrategy: string) => {
    if (nextStrategy !== strategy) {
      clearScreeningResults();
    }
    // 再次点击已选策略则取消选择（允许仅按偏好选股）
    setStrategy((current) => (current === nextStrategy ? '' : nextStrategy));
  };

  const handlePreferenceChange = (next: string) => {
    if (next !== preference) {
      clearScreeningResults();
    }
    setPreference(next);
  };

  const handleMaxResultsChange = (nextMaxResults: number) => {
    if (nextMaxResults !== maxResults) {
      clearScreeningResults();
    }
    setMaxResults(nextMaxResults);
  };

  const pollJob = useCallback(async (jobId: string, epoch: number) => {
    if (Date.now() - pollStartRef.current > MAX_POLL_MS) {
      setError('选股超时，请稍后重试');
      setLoading(false);
      return;
    }
    try {
      const job = await screenApi.getScreenJob(jobId);
      // 已被新一次运行（或卸载）取代的过期续体直接忽略，避免覆盖新状态/泄漏定时器
      if (epoch !== runEpochRef.current) {
        return;
      }
      transientErrorsRef.current = 0;
      if (job.status === 'completed') {
        setScreenMeta(job);
        setCandidates(job.candidates ?? []);
        setExpandedCode(job.candidates?.[0]?.code ?? null);
        setLoading(false);
        return;
      }
      if (job.status === 'failed') {
        setCandidates([]);
        setError(job.error || '选股失败');
        setLoading(false);
        return;
      }
      pollTimerRef.current = window.setTimeout(() => void pollJob(jobId, epoch), POLL_INTERVAL_MS);
    } catch (err) {
      // 过期续体的错误分支同样直接忽略
      if (epoch !== runEpochRef.current) {
        return;
      }
      const parsed = getParsedApiError(err);
      if (parsed.status === 404) {
        setCandidates([]);
        setError('任务已结束或服务重启，结果未保留，请重新运行');
        setLoading(false);
        return;
      }
      transientErrorsRef.current += 1;
      if (transientErrorsRef.current >= MAX_TRANSIENT_ERRORS) {
        setError(err instanceof Error ? err.message : '选股失败');
        setLoading(false);
        return;
      }
      pollTimerRef.current = window.setTimeout(() => void pollJob(jobId, epoch), POLL_INTERVAL_MS);
    }
    // 仅依赖稳定的 ref/setter 与稳定的 screenApi，无需补充其他依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = async () => {
    if (!strategy && !preference.trim()) {
      setError('策略和用户偏好至少填写一个');
      return;
    }
    stopPolling();
    const epoch = ++runEpochRef.current;
    setLoading(true);
    setError('');
    setScreenMeta(null);
    transientErrorsRef.current = 0;
    pollStartRef.current = Date.now();
    try {
      const submitted = await screenApi.submitScreenJob({
        strategy: strategy || undefined,
        preference: preference.trim() || undefined,
        maxResults,
      });
      if (epoch !== runEpochRef.current) {
        return;
      }
      pollTimerRef.current = window.setTimeout(() => void pollJob(submitted.jobId, epoch), POLL_INTERVAL_MS);
    } catch (err) {
      if (epoch !== runEpochRef.current) {
        return;
      }
      setCandidates([]);
      setError(err instanceof Error ? err.message : '选股失败');
      setLoading(false);
    }
  };

  return (
    <AppPage className="max-w-6xl space-y-6 pb-12 pt-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-center gap-3">
          <span className="grid h-7 w-7 place-items-center rounded-full border-2 border-cyan text-cyan shadow-[0_0_24px_hsl(var(--primary)/0.18)]">
            <PlusCircle className="h-4 w-4" />
          </span>
          <div>
            <h1 className="text-2xl font-bold tracking-normal text-foreground">自研选股</h1>
            <p className="mt-1 text-sm text-secondary-text">全市场量化初筛 + LLM 轻量重排，按策略与偏好生成候选股票</p>
          </div>
        </div>
      </div>

      <InlineAlert
        variant="warning"
        title="风险提示"
        message="选股结果仅用于研究和辅助判断，不构成投资建议；市场有风险，交易决策和损益由使用者自行承担。"
      />

      {error ? <InlineAlert variant="danger" title="调用失败" message={error} /> : null}

      <section className="rounded-2xl border border-cyan/35 bg-card/95 p-4 shadow-soft-card">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-foreground">选择策略（可选）</h2>
            <p className="mt-1 text-xs text-secondary-text">选择一个策略进行量化初筛；也可留空，仅按下方用户偏好选股。</p>
          </div>
          {selectedStrategy?.tradingStyle ? (
            <span className="inline-block rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
              适合：{selectedStrategy.tradingStyle}
            </span>
          ) : null}
        </div>

        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {loadingStrategies ? (
            <div className="rounded-xl border border-dashed border-border bg-surface/70 p-4 text-sm text-secondary-text">
              正在读取可用策略...
            </div>
          ) : strategies.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-surface/70 p-4 text-sm text-secondary-text">
              {strategyLoadError || '策略列表暂未载入，可仅填写用户偏好后运行选股。'}
            </div>
          ) : (
            strategies.map((item) => {
              const selected = item.id === strategy;
              return (
                <button
                  key={item.id}
                  className={`min-h-28 rounded-xl border p-4 text-left transition-all ${
                    selected
                      ? 'border-cyan bg-cyan/10 shadow-[0_0_0_1px_hsl(var(--primary)/0.15),0_16px_36px_hsl(var(--primary)/0.12)]'
                      : 'border-border/80 bg-surface/70 hover:border-cyan/45 hover:bg-hover/70'
                  }`}
                  type="button"
                  onClick={() => handleStrategyChange(item.id)}
                >
                  <span className="text-base font-semibold text-foreground">{item.name || item.id}</span>
                  <span className="mt-2 block text-sm leading-6 text-secondary-text">{item.description || item.id}</span>
                  {item.tradingStyle ? (
                    <span className="mt-3 inline-flex rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
                      适合：{item.tradingStyle}
                    </span>
                  ) : null}
                </button>
              );
            })
          )}
        </div>
      </section>

      <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground">
          <SlidersHorizontal className="h-4 w-4 text-cyan" />
          参数设置
        </div>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-secondary-text">用户偏好（可选）</label>
            <textarea
              value={preference}
              onChange={(event) => handlePreferenceChange(event.target.value)}
              maxLength={500}
              placeholder="例如：喜欢科技股、偏好抄底、规避高估值。留空则只按所选策略选股。"
              className="w-full rounded-xl border border-border bg-surface px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-cyan"
              rows={2}
            />
            <p className="mt-1 text-xs text-secondary-text">偏好与策略冲突时，将在候选范围内优先满足你的偏好。</p>
          </div>

          <div className="grid gap-4 lg:grid-cols-[180px_auto] lg:items-end">
            <label className="space-y-2 text-xs font-medium text-secondary-text">
              返回数量
              <input
                className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
                type="number"
                min={1}
                max={100}
                value={maxResults}
                onChange={(event) => handleMaxResultsChange(Number(event.target.value))}
              />
            </label>

            <Button
              className="h-11 min-w-40"
              isLoading={loading}
              loadingText="选股中(约几分钟)..."
              disabled={loading}
              onClick={() => void handleSubmit()}
            >
              <Play className="h-4 w-4" />
              运行选股
            </Button>
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <span
              className={`grid h-7 w-7 place-items-center rounded-full ${
                candidates.length > 0 ? 'text-success' : 'text-cyan'
              }`}
            >
              {candidates.length > 0 ? <CheckCircle2 className="h-5 w-5" /> : <CircleAlert className="h-5 w-5" />}
            </span>
            <div>
              <h2 className="text-sm font-semibold text-foreground">
                {candidates.length > 0 ? '选股完成' : '等待运行'}
              </h2>
              <p className="mt-1 text-xs text-secondary-text">
                当前策略：{displayedStrategy}
                {screenMeta?.preference ? ` · 按偏好：${screenMeta.preference}` : ''}
              </p>
            </div>
          </div>
          <div className="grid gap-1 text-xs text-secondary-text sm:text-right">
            <span>Run ID：{screenMeta?.runId || '-'}</span>
            <span>
              快照 {screenMeta?.snapshotCount ?? '-'} · 过滤后 {screenMeta?.afterFilterCount ?? '-'} · 候选{' '}
              {screenMeta?.candidateCount ?? candidates.length}
            </span>
            <span>LLM：{screenMeta?.llmRanked ? '已重排' : screenMeta ? '未重排（降级按量化打分）' : '-'}</span>
          </div>
        </div>
        {screenMeta?.llmSelectionLogic ? (
          <p className="mt-3 text-xs leading-6 text-secondary-text">选股逻辑：{screenMeta.llmSelectionLogic}</p>
        ) : null}
        {screenMeta?.llmPortfolioRisk ? (
          <p className="mt-1 text-xs leading-6 text-secondary-text">组合风险：{screenMeta.llmPortfolioRisk}</p>
        ) : null}
        {screenMeta?.warnings?.length ? (
          <p className="mt-1 text-xs leading-6 text-warning">{screenMeta.warnings.join('；')}</p>
        ) : null}
      </section>

      <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
        <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-foreground">选股结果</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-secondary-text">
              候选会在这里展示，展开后可查看选股逻辑、风险、与偏好的契合度和原始字段。
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-2 text-xs text-secondary-text">
            <Search className="h-4 w-4 text-cyan" />
            {candidates.length} 条候选
          </div>
        </div>

        {loading ? (
          <p className="mb-4 text-sm text-secondary-text">选股需扫描全市场，预计需几分钟，请勿关闭页面。</p>
        ) : null}

        {candidates.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface/70 px-5 py-10 text-center">
            <p className="text-sm font-medium text-foreground">暂无结果</p>
            <p className="mt-2 text-sm text-secondary-text">选择策略或填写用户偏好后点击“运行选股”生成候选列表。</p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border">
            <table className="w-full min-w-[760px] border-collapse text-sm">
              <thead className="bg-surface text-left text-xs text-secondary-text">
                <tr>
                  <th className="w-14 px-4 py-3 font-semibold">#</th>
                  <th className="px-4 py-3 font-semibold">代码</th>
                  <th className="px-4 py-3 font-semibold">名称</th>
                  <th className="px-4 py-3 font-semibold">行业</th>
                  <th className="px-4 py-3 font-semibold">价格</th>
                  <th className="px-4 py-3 font-semibold">涨跌幅</th>
                  <th className="px-4 py-3 font-semibold">评分</th>
                  <th className="px-4 py-3 font-semibold">详情</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((item) => {
                  const expanded = expandedCode === item.code;
                  return (
                    <Fragment key={`${item.rank}-${item.code}`}>
                      <tr className="border-t border-border align-top transition-colors hover:bg-hover/50">
                        <td className="px-4 py-3 text-secondary-text">{item.rank}</td>
                        <td className="px-4 py-3 font-mono font-semibold text-foreground">{item.code}</td>
                        <td className="px-4 py-3 font-semibold text-foreground">{item.name || '-'}</td>
                        <td className="px-4 py-3 text-secondary-text">{item.industry || '-'}</td>
                        <td className="px-4 py-3 text-secondary-text">{formatNumber(item.price)}</td>
                        <td className="px-4 py-3 text-secondary-text">
                          {item.changePct == null ? '-' : `${(Number(item.changePct) * 100).toFixed(2)}%`}
                        </td>
                        <td className="px-4 py-3 font-bold text-cyan">{formatScore(item.score)}</td>
                        <td className="px-4 py-3">
                          <button
                            className="text-sm font-semibold text-cyan transition-colors hover:text-foreground"
                            type="button"
                            onClick={() => setExpandedCode(expanded ? null : item.code)}
                          >
                            {expanded ? '收起' : '展开查看'}
                          </button>
                        </td>
                      </tr>
                      {expanded ? (
                        <tr className="border-t border-border bg-surface/45">
                          <td colSpan={8} className="px-4 py-4">
                            <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
                              <div className="space-y-3">
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">摘要</p>
                                  <p className="mt-1 text-sm leading-6 text-foreground">{getCandidateReason(item)}</p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">LLM 判断</p>
                                  <p className="mt-1 text-sm leading-6 text-foreground">
                                    {item.llmThesis || item.reason || '暂无 LLM 判断'}
                                  </p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">风险标签</p>
                                  <p className="mt-1 text-sm text-foreground">
                                    {item.llmRisks?.length ? item.llmRisks.join('，') : '无'}
                                  </p>
                                </div>
                              </div>
                              <div className="space-y-3">
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">与偏好/风格契合度</p>
                                  <p className="mt-1 text-sm text-foreground">{item.llmStyleFit || '-'}</p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">成交额</p>
                                  <p className="mt-1 text-sm text-foreground">{formatAmount(item.amount)}</p>
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </AppPage>
  );
};

export default StockScreeningPage;
