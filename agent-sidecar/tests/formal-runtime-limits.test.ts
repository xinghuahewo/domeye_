import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from '../src/formal-runtime-limits.js'

interface AcceptanceConfiguration {
  timeouts: {
    model_attempt_ms: number
  }
  capacity: {
    maximum_model_attempts: number
    maximum_provider_requests_per_report: number
    maximum_provider_context_utf8_bytes: number
    maximum_fact_records: number
    maximum_context_tokens: number
  }
}

test('正式运行容量常量与冻结验收配置的六项合同保持一致', () => {
  const configuration = JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        '../config/country-outage-agent-core-acceptance-v3.json',
      ),
      'utf8',
    ),
  ) as AcceptanceConfiguration

  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.modelAttemptTimeoutMs,
    configuration.timeouts.model_attempt_ms,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelAttempts,
    configuration.capacity.maximum_model_attempts,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
      .maximumProviderRequestsPerReport,
    configuration.capacity.maximum_provider_requests_per_report,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
      .maximumProviderContextBytes,
    configuration.capacity.maximum_provider_context_utf8_bytes,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumEvidenceRecords,
    configuration.capacity.maximum_fact_records,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumContextInputTokens,
    configuration.capacity.maximum_context_tokens,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.minimumModelContextWindowTokens,
    configuration.capacity.maximum_context_tokens,
  )
  assert.equal(Object.isFrozen(FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS), true)
  assert.equal(
    Object.isFrozen(
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
        .maximumToolExecutionsByName,
    ),
    true,
  )
})

test('工具次数、单结果与累计结果限制作为附加安全上限冻结', () => {
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumToolExecutions,
    4,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumToolResultBytes,
    24_576,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
      .maximumCumulativeToolResultBytes,
    36_864,
  )
  assert.deepEqual(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
      .maximumToolExecutionsByName,
    {
      country_outage_resolve: 1,
      country_outage_get_observation: 1,
      country_outage_get_asns: 1,
    },
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderPayloadBytes,
    59_904,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.providerFramingTokenReserve,
    4_096,
  )
  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderPayloadBytes
      + FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
        .providerFramingTokenReserve,
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumContextInputTokens,
  )
})
