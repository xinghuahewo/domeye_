import assert from 'node:assert/strict'
import { chmodSync, mkdtempSync, realpathSync, rmSync } from 'node:fs'
import type { Server } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import {
  createFormalP1Sidecar,
  FORMAL_P1_CERTIFIED_INPUT_SCOPE,
  FORMAL_P1_CERTIFIED_SCENARIO_SET_ID,
} from '../src/cli/formal-p1-sidecar.js'
import type { FormalPiModelBinding } from '../src/pi/index.js'

function binding(): FormalPiModelBinding {
  const profile = {
    id: 'p1-model-test',
    status: 'certified' as const,
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    modelVersion: 'deepseek-v4-flash',
    expectedResponseModel: 'deepseek-v4-flash',
    thinkingLevel: 'off' as const,
    piVersion: '0.84.1' as const,
    certificationEvidenceId: 'evidence:p1-semantic-certification:test',
    certifiedAt: '2026-08-11T00:00:00Z',
    modelRevisionKind: 'mutable_alias' as const,
    immutableRevisionAvailable: false as const,
    limitation: '供应方未提供不可变权重 revision；deepseek-v4-flash 是可变别名，可能无痕变化。' as const,
    certificationValidUntil: '2026-08-18T00:00:00Z',
    certifiedScenarioSetId: FORMAL_P1_CERTIFIED_SCENARIO_SET_ID,
    certifiedInputScope: FORMAL_P1_CERTIFIED_INPUT_SCOPE,
  }
  return {
    modelRuntime: {} as FormalPiModelBinding['modelRuntime'],
    model: {
      id: profile.model,
      name: profile.model,
      api: 'openai-completions',
      provider: profile.provider,
      baseUrl: 'https://api.deepseek.com',
      reasoning: true,
      input: ['text'],
      contextWindow: 1_000_000,
      maxTokens: 16_384,
      cost: { input: 0.14, output: 0.28, cacheRead: 0.0028, cacheWrite: 0 },
    } as FormalPiModelBinding['model'],
    certification: {
      registryVersion: 'p1-test-registry-v1',
      profile,
    },
    runSelection: {
      runtimeIdentity: 'formal',
      registryVersion: 'p1-test-registry-v1',
      profile,
    },
    preflight: {
      schemaVersion: 'country_outage_pi_model_preflight_v1',
      registryVersion: 'p1-test-registry-v1',
      profileId: profile.id,
      provider: profile.provider,
      model: profile.model,
      modelVersion: profile.modelVersion,
      expectedResponseModel: profile.expectedResponseModel,
      thinkingLevel: profile.thinkingLevel,
      piVersion: profile.piVersion,
      certificationEvidenceId: profile.certificationEvidenceId,
      modelRevisionKind: profile.modelRevisionKind,
      immutableRevisionAvailable: false,
      limitation: profile.limitation,
      certificationValidUntil: profile.certificationValidUntil,
      certifiedScenarioSetId: profile.certifiedScenarioSetId,
      certifiedInputScope: profile.certifiedInputScope,
      maximumOutputTokens: 16_384,
      auth: { configured: true, source: 'stored' },
      available: true,
    },
  }
}

test('正式 P1 Sidecar 只接线聊天并冻结Registry、资源观测和报告关闭边界', async () => {
  const auditDirectory = realpathSync(
    mkdtempSync(join(tmpdir(), 'domeye-p1-audit-')),
  )
  chmodSync(auditDirectory, 0o700)
  const server = {
    requestTimeout: 0,
    headersTimeout: 0,
    keepAliveTimeout: 0,
  } as Server
  try {
    const sidecar = await createFormalP1Sidecar(
      {
        COUNTRY_OUTAGE_AGENT_HOST: '127.0.0.1',
        COUNTRY_OUTAGE_AGENT_PORT: '28475',
        COUNTRY_OUTAGE_AGENT_SHARED_TOKEN:
          'test-p1-shared-token-abcdefghijklmnopqrstuvwxyz',
        DOMEYE_API_BASE_URL: 'http://127.0.0.1:28473/api/v2/',
        COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY: auditDirectory,
      },
      {
        bindingFactory: async () => binding(),
        httpServerFactory: () => server,
      },
    )
    assert.equal(sidecar.port, 28475)
    assert.equal(sidecar.runtime.collector, 'rrc25')
    assert.equal(sidecar.runtime.maximumProviderRequestCountPerTurn, 1)
    assert.equal(sidecar.runtime.businessCostLimit, null)
    assert.equal(sidecar.runtime.reportCapability, 'disabled')
    assert.deepEqual(sidecar.runtime.eventWindowTrendOperator, {
      executionUnit: 'OP-04',
      capabilityId: 'CAP-TREND-001',
      operatorId: 'event-window-trend',
      operatorVersion: '1.2.0',
      modelDependency: 'none',
    })
    assert.equal(sidecar.runtime.toolOperatorRegistry.activationScope, 'runtime_candidate_shadow_only')
    assert.equal(sidecar.runtime.toolOperatorRegistry.runtimeIntegration, 'implemented_not_deployed')
    assert.equal(sidecar.runtime.toolOperatorRegistry.productionDeployed, false)
    assert.match(sidecar.runtime.toolOperatorRegistry.candidateId, /^p2-s0b-[a-f0-9]{16}$/)
    assert.match(
      sidecar.runtime.toolOperatorRegistry.registrySnapshotId,
      /^registry-snapshot-sha256:[a-f0-9]{64}$/,
    )
    assert.match(sidecar.modelIdentity, /p1-user-goal-plan-v1$/)
    assert.equal(server.requestTimeout, 125_000)
  } finally {
    rmSync(auditDirectory, { recursive: true, force: true })
  }
})
