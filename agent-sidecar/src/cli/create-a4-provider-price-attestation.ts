import {
  canonicalProviderPriceDecimal,
  PiModelCertificationError,
  ProviderPriceAttestationError,
  writeA4ProviderPriceAttestation,
} from '../pi/index.js'

const SHA256 = /^[a-f0-9]{64}$/

class PriceAttestationConfigurationError extends Error {
  constructor() {
    super('DeepSeek 当前价格证明运维配置无效')
    this.name = 'PriceAttestationConfigurationError'
  }
}

function requiredEnvironmentValue(name: string): string {
  const value = process.env[name]?.trim() ?? ''
  if (!value) throw new PriceAttestationConfigurationError()
  return value
}

function requiredPriceDecimal(name: string): string {
  const raw = requiredEnvironmentValue(name)
  return canonicalProviderPriceDecimal(raw)
}

async function main(): Promise<void> {
  const evidenceSha256 = requiredEnvironmentValue(
    'COUNTRY_OUTAGE_PI_PRICE_EVIDENCE_SHA256',
  )
  if (!SHA256.test(evidenceSha256)) {
    throw new PriceAttestationConfigurationError()
  }
  const attestation = await writeA4ProviderPriceAttestation({
    observedAt: requiredEnvironmentValue(
      'COUNTRY_OUTAGE_PI_PRICE_OBSERVED_AT',
    ),
    evidenceSha256,
    priceUsdPerMillionTokens: {
      input: requiredPriceDecimal(
        'COUNTRY_OUTAGE_PI_PRICE_INPUT_USD_PER_MILLION',
      ),
      output: requiredPriceDecimal(
        'COUNTRY_OUTAGE_PI_PRICE_OUTPUT_USD_PER_MILLION',
      ),
      cacheRead: requiredPriceDecimal(
        'COUNTRY_OUTAGE_PI_PRICE_CACHE_READ_USD_PER_MILLION',
      ),
      cacheWrite: requiredPriceDecimal(
        'COUNTRY_OUTAGE_PI_PRICE_CACHE_WRITE_USD_PER_MILLION',
      ),
    },
  })
  // 只输出可公开核验的资源身份与有效期；不读取或输出 auth、密钥、证据内容。
  process.stdout.write(
    `${JSON.stringify({
      event: 'country_outage_a4_provider_price_attestation_written',
      attestationId: attestation.attestationId,
      resourceSha256: attestation.resourceSha256,
      observedAt: attestation.observedAt,
      expiresAt: attestation.expiresAt,
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const code =
    error instanceof ProviderPriceAttestationError
      ? error.code
      : error instanceof PiModelCertificationError
        ? error.code
        : 'price_attestation_configuration_invalid'
  const message =
    error instanceof ProviderPriceAttestationError ||
    error instanceof PiModelCertificationError ||
    error instanceof PriceAttestationConfigurationError
      ? error.message
      : 'DeepSeek 当前价格证明写入失败'
  process.stderr.write(
    `${JSON.stringify({
      event: 'country_outage_a4_provider_price_attestation_failed',
      code,
      message,
    })}\n`,
  )
  process.exitCode = 1
})
