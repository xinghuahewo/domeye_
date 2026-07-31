import {
  PiModelCertificationError,
  reconcileA4PreLedgerHistoricalUsage,
  type CandidateActivityUsage,
} from '../pi/index.js'

const SHA256 = /^[a-f0-9]{64}$/

class HistoricalUsageConfigurationError extends Error {
  constructor() {
    super('DeepSeek 候选认证历史用量结清配置无效')
    this.name = 'HistoricalUsageConfigurationError'
  }
}

function requiredSafeInteger(name: string): number {
  const raw = process.env[name]?.trim()
  if (!raw || !/^(?:0|[1-9]\d*)$/.test(raw)) {
    throw new HistoricalUsageConfigurationError()
  }
  const value = Number(raw)
  if (!Number.isSafeInteger(value)) {
    throw new HistoricalUsageConfigurationError()
  }
  return value
}

async function main(): Promise<void> {
  const evidenceSha256 =
    process.env.COUNTRY_OUTAGE_PI_PRE_LEDGER_EVIDENCE_SHA256?.trim()
  if (!evidenceSha256 || !SHA256.test(evidenceSha256)) {
    throw new HistoricalUsageConfigurationError()
  }
  const usage: CandidateActivityUsage = {
    providerRequestCount: requiredSafeInteger(
      'COUNTRY_OUTAGE_PI_PRE_LEDGER_PROVIDER_REQUEST_COUNT',
    ),
    inputTokens: requiredSafeInteger(
      'COUNTRY_OUTAGE_PI_PRE_LEDGER_INPUT_TOKENS',
    ),
    outputTokens: requiredSafeInteger(
      'COUNTRY_OUTAGE_PI_PRE_LEDGER_OUTPUT_TOKENS',
    ),
    cacheReadTokens: requiredSafeInteger(
      'COUNTRY_OUTAGE_PI_PRE_LEDGER_CACHE_READ_TOKENS',
    ),
    cacheWriteTokens: requiredSafeInteger(
      'COUNTRY_OUTAGE_PI_PRE_LEDGER_CACHE_WRITE_TOKENS',
    ),
  }
  if (usage.providerRequestCount < 1) {
    throw new HistoricalUsageConfigurationError()
  }
  await reconcileA4PreLedgerHistoricalUsage({
    evidenceSha256,
    usage,
  })
  process.stdout.write(
    'DeepSeek 候选认证首次历史调用用量已结清。\n',
  )
}

void main().catch((error: unknown) => {
  const message =
    error instanceof PiModelCertificationError ||
    error instanceof HistoricalUsageConfigurationError
      ? error.message
      : 'DeepSeek 候选认证首次历史调用用量结清失败'
  process.stderr.write(`${message}\n`)
  process.exitCode = 1
})
