import apiClient from './index';
import { toCamelCase } from './utils';

export type McpKeyInfo = {
  key: string | null;
  endpoint: string;
  configured: boolean;
};

export async function getMcpKey(): Promise<McpKeyInfo> {
  const r = await apiClient.get<Record<string, unknown>>('/api/v1/mcp-keys');
  return toCamelCase<McpKeyInfo>(r.data);
}

export async function resetMcpKey(): Promise<McpKeyInfo> {
  const r = await apiClient.post<Record<string, unknown>>('/api/v1/mcp-keys/reset');
  return toCamelCase<McpKeyInfo>(r.data);
}
