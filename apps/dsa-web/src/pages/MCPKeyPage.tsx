import React, { useEffect, useState } from 'react';
import { getMcpKey, resetMcpKey, type McpKeyInfo } from '../api/mcpKeys';
import { ConfirmDialog } from '../components/common/ConfirmDialog';

function mask(key: string): string {
  if (key.length <= 8) return '••••••';
  return `${key.slice(0, 4)}${'•'.repeat(12)}${key.slice(-4)}`;
}

const btnClass =
  'inline-flex items-center rounded-md border border-border bg-background px-3 py-1.5 text-sm hover:bg-accent';
const dangerBtnClass =
  'inline-flex items-center rounded-md bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700';

const MCPKeyPage: React.FC = () => {
  const [info, setInfo] = useState<McpKeyInfo | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getMcpKey().then(setInfo).catch(() => setInfo(null));
  }, []);

  const copy = (text: string) => {
    void navigator.clipboard?.writeText(text);
  };

  const doReset = async () => {
    setBusy(true);
    try {
      const next = await resetMcpKey();
      setInfo(next);
      setRevealed(true);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const key = info?.key ?? null;
  const endpoint = info?.endpoint ?? '';
  const snippet = key
    ? `{\n  "mcpServers": {\n    "a-stock": {\n      "type": "streamable-http",\n      "url": "${endpoint}",\n      "headers": { "Authorization": "Bearer ${key}" }\n    }\n  }\n}`
    : '';

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">MCP 密钥</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          智能体经此 Key 连接 MCP 中转站（/mcp）。重置后旧 Key 立即失效、新 Key 立即生效。
        </p>
      </div>

      <section className="space-y-2">
        <div className="text-sm text-muted-foreground">端点地址</div>
        <div className="flex items-center gap-2">
          <code className="rounded bg-muted px-2 py-1 text-sm">{endpoint || '—'}</code>
          {endpoint && (
            <button type="button" className={btnClass} onClick={() => copy(endpoint)}>
              复制
            </button>
          )}
        </div>
      </section>

      <section className="space-y-2">
        <div className="text-sm text-muted-foreground">当前生效 Key</div>
        {key ? (
          <div className="flex flex-wrap items-center gap-2">
            <code className="rounded bg-muted px-2 py-1 text-sm">{revealed ? key : mask(key)}</code>
            <button type="button" className={btnClass} onClick={() => setRevealed((v) => !v)}>
              {revealed ? '隐藏' : '显示'}
            </button>
            <button type="button" className={btnClass} onClick={() => copy(key)}>
              复制
            </button>
          </div>
        ) : (
          <div className="text-sm text-foreground">尚未生成</div>
        )}
      </section>

      {key && (
        <section className="space-y-2">
          <div className="text-sm text-muted-foreground">客户端连接配置</div>
          <pre className="overflow-auto rounded bg-muted p-3 text-xs">{snippet}</pre>
          <button type="button" className={btnClass} onClick={() => copy(snippet)}>
            复制配置
          </button>
        </section>
      )}

      <section>
        <button
          type="button"
          className={dangerBtnClass}
          disabled={busy}
          onClick={() => setConfirming(true)}
        >
          {key ? '重置 Key' : '生成 Key'}
        </button>
      </section>

      <ConfirmDialog
        isOpen={confirming}
        isDanger
        title={key ? '重置 MCP Key' : '生成 MCP Key'}
        message={
          key
            ? '重置后旧 Key 立即失效，正在使用该 Key 的智能体须更新为新 Key。确认重置？'
            : '将为 MCP 中转站生成一把新 Key。确认生成？'
        }
        confirmText="确认重置"
        cancelText="取消"
        onConfirm={doReset}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
};

export default MCPKeyPage;
