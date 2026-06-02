import apiClient from './index';
import { toCamelCase } from './utils';

const SCREEN_JOB_API_TIMEOUT_MS = 30000;

export type ScreenCandidate = {
  rank: number;
  code: string;
  name: string;
  score?: number | null;
  screenScore?: number | null;
  reason: string;
  llmThesis?: string;
  llmRisks?: string[];
  llmStyleFit?: string;
  price?: number | null;
  changePct?: number | null;
  amount?: number | null;
  industry?: string;
  raw: Record<string, unknown>;
};

export type ScreenStrategy = {
  id: string;
  name: string;
  description: string;
  category?: string;
  tradingStyle?: string;
};

export type ScreenStrategiesResponse = {
  enabled: boolean;
  strategies: ScreenStrategy[];
  strategyCount: number;
};

export type ScreenResponse = {
  enabled: boolean;
  candidates: ScreenCandidate[];
  candidateCount: number;
  runId?: string;
  strategy?: string | null;
  preference?: string | null;
  snapshotCount?: number;
  afterFilterCount?: number;
  llmRanked?: boolean;
  llmSelectionLogic?: string;
  llmPortfolioRisk?: string;
  warnings?: string[];
  sourceErrors?: string[];
};

export type ScreenJobSubmit = {
  jobId: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
};

export type ScreenJobResult = ScreenResponse & {
  jobId: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  error?: string;
};

export const screenApi = {
  async getStrategies(): Promise<ScreenStrategiesResponse> {
    const r = await apiClient.get<Record<string, unknown>>('/api/v1/screen/strategies');
    return toCamelCase<ScreenStrategiesResponse>(r.data);
  },

  async submitScreenJob(p: { strategy?: string; preference?: string; maxResults: number }): Promise<ScreenJobSubmit> {
    const r = await apiClient.post<Record<string, unknown>>(
      '/api/v1/screen/jobs',
      { strategy: p.strategy || null, preference: p.preference || null, max_results: p.maxResults },
      { timeout: SCREEN_JOB_API_TIMEOUT_MS },
    );
    return toCamelCase<ScreenJobSubmit>(r.data);
  },

  async getScreenJob(jobId: string): Promise<ScreenJobResult> {
    const r = await apiClient.get<Record<string, unknown>>(
      `/api/v1/screen/jobs/${jobId}`,
      { timeout: SCREEN_JOB_API_TIMEOUT_MS },
    );
    return toCamelCase<ScreenJobResult>(r.data);
  },
};
