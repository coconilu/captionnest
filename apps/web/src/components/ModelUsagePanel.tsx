import { Bot } from 'lucide-react'

import { formatTokenCount } from '../lib/format'
import type { ModelUsageSummary } from '../types/api'

const PROVIDER_LABELS: Record<string, string> = {
  codex_spark: 'Codex Spark',
  lmstudio: 'LM Studio',
  deepseek: 'DeepSeek',
  multiple: '多个 Provider',
  openai_compatible: 'OpenAI-compatible',
}

interface ModelUsagePanelProps {
  usage: ModelUsageSummary
  title?: string
  compact?: boolean
}

export function ModelUsagePanel({
  usage,
  title = '累计模型用量',
  compact = false,
}: ModelUsagePanelProps) {
  const provider = PROVIDER_LABELS[usage.provider] ?? usage.provider
  const hasReportedTokens = [
    usage.input_tokens,
    usage.output_tokens,
    usage.total_tokens,
    usage.cached_input_tokens,
    usage.reasoning_tokens,
  ].some((value) => value !== null)

  return (
    <section className={`pipeline-usage-panel ${compact ? 'is-compact' : ''}`}>
      <header>
        <span>
          <Bot size={14} aria-hidden="true" />
          {title}
        </span>
        <small className={usage.complete ? 'is-complete' : 'is-partial'}>
          {usage.complete ? '完整报告' : '部分报告'}
        </small>
      </header>
      <p>
        {provider}{usage.model ? ` · ${usage.model}` : ''} · 请求 {usage.request_count} 次
      </p>
      {hasReportedTokens ? (
        <dl>
          <div><dt>输入</dt><dd>{formatTokenCount(usage.input_tokens)}</dd></div>
          <div><dt>输出</dt><dd>{formatTokenCount(usage.output_tokens)}</dd></div>
          <div><dt>总 Token</dt><dd>{formatTokenCount(usage.total_tokens)}</dd></div>
          {usage.cached_input_tokens !== null ? (
            <div><dt>缓存输入</dt><dd>{formatTokenCount(usage.cached_input_tokens)}</dd></div>
          ) : null}
          {usage.reasoning_tokens !== null ? (
            <div><dt>推理</dt><dd>{formatTokenCount(usage.reasoning_tokens)}</dd></div>
          ) : null}
        </dl>
      ) : (
        <strong className="pipeline-usage-unavailable">
          Provider 未报告 Token 用量
        </strong>
      )}
    </section>
  )
}
