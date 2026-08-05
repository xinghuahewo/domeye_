import { createHash, randomUUID } from 'node:crypto'
import {
  chmodSync,
  closeSync,
  constants,
  existsSync,
  fchmodSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { readFile } from 'node:fs/promises'
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  ModelRuntime,
  type CreateAgentSessionOptions,
  type CreateModelRuntimeOptions,
} from '@earendil-works/pi-coding-agent'

import type {
  CountryOutageAsnPage,
  ObservationBatch,
  SnapshotIdentity,
} from '../domain/contracts.js'
import {
  assertAsnPageIdentity,
  assertBatchIdentity,
  DomeyeCountryOutageClient,
  type AsnQuery,
} from '../domain/domeye-client.js'
import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from '../formal-runtime-limits.js'
import { compareUnicodeCodePoints } from '../shared/deterministic-json.js'
import {
  CountryOutageArtifactBuilder,
  type PdfDocumentRenderer,
} from '../report/artifact-builder.js'
import {
  createCountryOutageReportAuditManifestArtifact,
  type CountryOutageAuditManifestArtifact,
} from '../report/audit-manifest.js'
import type {
  CountryOutageReportDocument,
  ReportArtifact,
  ReportEvidenceBundle,
} from '../report/contracts.js'
import { CountryOutagePdfRenderer } from '../report/pdf-renderer.js'
import {
  CountryOutageReportCompiler,
  ReportValidationError,
  type CompiledCountryOutageReport,
  type CountryOutageReportDataSource,
} from '../report/report-compiler.js'
import {
  CandidateActivityLedgerError,
  initializeCleanCandidateActivityLedger,
  initializeCandidateActivityLedgerWithPreLedgerFailure,
  isCandidateActivityRejectionCode,
  inspectCandidateActivityLedger,
  openCandidateActivityLedger,
  reconcileCandidateActivityLedgerHistoricalBilledAmount,
  reconcileCandidateActivityLedgerHistoricalUsage,
  type CandidateActivityHistoricalBilledAmount,
  type CandidateActivityRunNumber,
  type CandidateActivityRejectionCode,
  type CandidateActivityBudgetPolicy,
  type CandidateActivityBudgetSnapshot,
  type CandidateActivityLedger,
  type CandidateActivityUsage,
} from './candidate-activity-ledger.js'
import {
  loadCountryOutageDependencyRiskException,
  type ActiveCountryOutageDependencyRiskException,
} from './dependency-risk-exception.js'
import {
  FORMAL_PI_NARRATION_MODE,
  isFormalPiRunRejectionCode,
  type FormalPiRunRejectionCode,
  type FormalPiRunAuditRecord,
} from './formal-run-audit.js'
import {
  COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
  COUNTRY_OUTAGE_LANGUAGE_SLOT_IDS,
} from '../report/model-language-plan.js'
import {
  assertFormalPiInstalledVersion,
  capCountryOutageModelOutput,
  createFrozenFormalCredentialStore,
  FORMAL_PI_VERSION,
  MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  parseCertifiedPiModelRegistry,
  type CertifiedPiModelRegistry,
  type FormalPiModelRuntimeFactory,
  type PiModelRunSelection,
} from './formal-model-runtime.js'
import {
  PiReportNarrator,
  type PiReportNarratorOptions,
  type PiSessionFactory,
} from './pi-report-narrator.js'
import {
  A4_CERTIFICATION_SCENARIOS,
  A4_CERTIFIED_INPUT_SCOPE,
  A4_CERTIFIED_SCENARIO_SET_ID,
  createA4CertificationScenarioClient,
  type A4CertificationScenarioDefinition,
  type A4CertificationScenarioId,
} from './model-certification-scenarios.js'
import {
  assertProviderPriceAttestationRunway,
  assertVerifiedProviderPriceAttestation,
  loadCurrentProviderPriceAttestation,
  ProviderPriceAttestationError,
  writeCurrentProviderPriceAttestation,
  type ProviderPriceAttestationErrorCode,
  type VerifiedProviderPriceAttestation,
} from './provider-price-attestation.js'

export const COUNTRY_OUTAGE_PI_MODEL_CANDIDATE_SCHEMA =
  'country_outage_pi_model_candidate_v1' as const
export const COUNTRY_OUTAGE_PI_MODEL_CERTIFICATION_MANIFEST_SCHEMA =
  'country_outage_pi_model_certification_manifest_v1' as const
export const DEEPSEEK_V4_FLASH_CANDIDATE_ID =
  'deepseek-v4-flash-pi-0.82.1-v1' as const
export const A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID =
  'a4-iran-country-outage-rrc25-v1' as const

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/
const SHA256 = /^[a-f0-9]{64}$/
const MAXIMUM_CERTIFICATION_RUNS = 2
const A4_TOTAL_REPORT_RUNS =
  MAXIMUM_CERTIFICATION_RUNS + A4_CERTIFICATION_SCENARIOS.length
const A4_MODEL_ALIAS_CERTIFICATION_VALIDITY_MS =
  7 * 24 * 60 * 60 * 1_000
const MILLION = 1_000_000
const APPROVED_RESPONSE_MODEL_PATCH_SHA256 =
  '5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b' as const

export interface PiModelCandidate {
  schemaVersion: typeof COUNTRY_OUTAGE_PI_MODEL_CANDIDATE_SCHEMA
  candidateId: typeof DEEPSEEK_V4_FLASH_CANDIDATE_ID
  status: 'candidate'
  provider: 'deepseek'
  model: 'deepseek-v4-flash'
  modelVersion: 'deepseek-v4-flash'
  expectedResponseModel: 'deepseek-v4-flash'
  thinkingLevel: 'off'
  piVersion: typeof FORMAL_PI_VERSION
  catalog: {
    api: 'openai-completions'
    baseUrl: 'https://api.deepseek.com'
    input: readonly ['text']
    reasoning: true
    contextWindowTokens: 1_000_000
    catalogMaximumOutputTokens: 384_000
    priceUsdPerMillionTokens: {
      input: 0.14
      output: 0.28
      cacheRead: 0.0028
      cacheWrite: 0
    }
  }
  execution: {
    maximumInputTokens: 64_000
    maximumOutputTokens: 16_384
    maximumProviderRequestCount: 2
    providerRetryAttempts: 0
  }
  adapterRequirement: {
    api: 'openai-completions'
    sameNameResponseModelRequired: true
    pinnedUnpatchedSourceSha256: '0d50250fe2931e66e2078279a397814202e1ecddee58faf4b8bc04c278da177a'
    approvedSameNameSourceSha256: readonly [
      typeof APPROVED_RESPONSE_MODEL_PATCH_SHA256,
    ]
  }
  certification: {
    maximumIndependentReportRuns: 2
    budgetLimitCny: 20
    conservativeCnyPerUsd: 8
  }
}

export interface LoadedPiModelCandidate {
  candidate: PiModelCandidate
  resourceSha256: string
}

export interface A4IranModelCertificationFixture {
  schemaVersion: 'country_outage_model_certification_fixture_v1'
  fixtureId: typeof A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID
  eventReference: 'country_outage/2026-02-27 09:12:32/IR/1/r'
  eventType: 'country_outage'
  countryCode: 'IR'
  collectorId: 'rrc25'
  incidentId: 'incident_go_v1_a1de26f854831330c616a72af21597eb'
  publicationId: 'publication_v1_38bddead083db3f49023c2e1'
  revision: 1
  dataThrough: '2026-02-28T15:00:00Z'
  isFinal: true
}

export interface CandidatePiModelPreflight {
  schemaVersion: 'country_outage_pi_model_candidate_preflight_v1'
  runtimeIdentity: 'candidate'
  candidateId: typeof DEEPSEEK_V4_FLASH_CANDIDATE_ID
  candidateResourceSha256: string
  provider: 'deepseek'
  model: 'deepseek-v4-flash'
  expectedResponseModel: 'deepseek-v4-flash'
  thinkingLevel: 'off'
  piVersion: typeof FORMAL_PI_VERSION
  modelCatalogNetworkRefreshEnabled: false
  modelsJsonEnabled: false
  providerRetryAttempts: 0
  maximumProviderRequestCount: 2
  maximumInputTokens: 64_000
  maximumOutputTokens: 16_384
  maximumIndependentReportRuns: 2
  maximumCertificationCostCny: number
  budgetLimitCny: 20
  auth: {
    configured: true
    source: 'stored'
  }
  available: true
  responseModelAdapter: {
    sameNamePreserved: true
    sourceSha256: string
  }
  priceAttestation: VerifiedProviderPriceAttestation | null
}

export interface CandidatePiModelBinding {
  modelRuntime: ModelRuntime
  model: NonNullable<CreateAgentSessionOptions['model']>
  candidate: PiModelCandidate
  candidateResourceSha256: string
  runSelection: PiModelRunSelection
  preflight: CandidatePiModelPreflight
  priceAttestation: VerifiedProviderPriceAttestation | null
}

export type PiModelCertificationErrorCode =
  | 'candidate_invalid'
  | 'candidate_auth_required'
  | 'candidate_runtime_initialization_failed'
  | 'candidate_runtime_metadata_invalid'
  | 'candidate_model_not_found'
  | 'candidate_model_catalog_mismatch'
  | 'candidate_model_not_available'
  | 'candidate_response_model_adapter_unsupported'
  | 'candidate_budget_preflight_failed'
  | 'candidate_runner_failed'
  | 'candidate_report_validation_failed'
  | 'candidate_response_model_missing'
  | 'candidate_response_model_mismatch'
  | 'candidate_run_evidence_invalid'
  | 'candidate_fact_equivalence_failed'
  | 'candidate_budget_exceeded'
  | 'candidate_audit_sink_failed'
  | 'candidate_activity_audit_failed'
  | 'candidate_historical_usage_unresolved'
  | 'candidate_price_attestation_missing'
  | 'candidate_price_attestation_invalid'
  | 'candidate_price_attestation_expired'
  | 'candidate_price_attestation_future_observation'
  | 'candidate_price_attestation_insufficient_runway'
  | 'candidate_price_attestation_candidate_drift'
  | 'candidate_price_rebudget_required'
  | 'candidate_fixture_mismatch'
  | 'candidate_internal_audit_invalid'
  | 'candidate_artifact_write_failed'
  | 'certification_manifest_invalid'
  | 'certification_provenance_untrusted'
  | 'certification_registry_changed'
  | 'certification_promotion_conflict'
  | 'certification_promotion_failed'

const SAFE_ERROR_MESSAGES: Record<
  PiModelCertificationErrorCode,
  string
> = {
  candidate_invalid: 'DeepSeek 候选模型资源无效',
  candidate_auth_required: 'DeepSeek 候选认证缺少安全的专用认证文件',
  candidate_runtime_initialization_failed:
    'DeepSeek 候选模型运行时初始化失败',
  candidate_runtime_metadata_invalid:
    'DeepSeek 候选模型运行时元数据不可核验',
  candidate_model_not_found: 'DeepSeek 候选模型不在固定 Pi 模型目录中',
  candidate_model_catalog_mismatch:
    'DeepSeek 候选模型目录与冻结资源不一致',
  candidate_model_not_available: 'DeepSeek 候选模型当前不可用',
  candidate_response_model_adapter_unsupported:
    'Pi 0.82.1 的 openai-completions 适配器不能保留同名 responseModel；未批准并应用修复前禁止计费认证',
  candidate_budget_preflight_failed: 'DeepSeek 候选认证预算预检失败',
  candidate_runner_failed: 'DeepSeek 候选完整报告运行失败',
  candidate_report_validation_failed:
    'DeepSeek 候选报告未通过发布前机器校验',
  candidate_response_model_missing:
    'DeepSeek 候选响应缺少独立 responseModel 元数据',
  candidate_response_model_mismatch:
    'DeepSeek 候选实际 responseModel 与冻结值不一致',
  candidate_run_evidence_invalid:
    'DeepSeek 候选完整报告证据未通过',
  candidate_fact_equivalence_failed:
    'DeepSeek 候选两次完整报告的事实快照不等价',
  candidate_budget_exceeded: 'DeepSeek 候选认证实际成本超过预算',
  candidate_audit_sink_failed: 'DeepSeek 候选认证安全审计写入失败',
  candidate_activity_audit_failed:
    'DeepSeek 候选认证活动账本写入或核验失败',
  candidate_historical_usage_unresolved:
    'DeepSeek 候选认证首次历史调用用量尚未显式结清',
  candidate_price_attestation_missing:
    'DeepSeek 候选认证缺少当前供应商价格证明',
  candidate_price_attestation_invalid:
    'DeepSeek 候选认证的供应商价格证明无效',
  candidate_price_attestation_expired:
    'DeepSeek 候选认证的供应商价格证明已过期',
  candidate_price_attestation_future_observation:
    'DeepSeek 候选认证的供应商价格证明观测时间在未来',
  candidate_price_attestation_insufficient_runway:
    'DeepSeek 候选认证的供应商价格证明剩余有效期不足',
  candidate_price_attestation_candidate_drift:
    'DeepSeek 候选认证的供应商价格证明与冻结候选不一致',
  candidate_price_rebudget_required:
    'DeepSeek 当前供应商价格高于冻结候选值，必须重新预算',
  candidate_fixture_mismatch:
    'DeepSeek 候选认证读取到的国家中断快照与固定 A4 样本不一致',
  candidate_internal_audit_invalid:
    'DeepSeek 候选认证无法从内部受验证审计形成运行证据',
  candidate_artifact_write_failed:
    'DeepSeek 候选认证证据制品原子写入失败',
  certification_manifest_invalid: 'DeepSeek 模型认证清单无效',
  certification_provenance_untrusted:
    'DeepSeek 模型认证清单不是固定真实完整报告 runner 产生的可晋级证据',
  certification_registry_changed:
    '正式模型注册表已变化，禁止基于旧清单晋级',
  certification_promotion_conflict:
    '正式模型注册表已存在同名模型组合',
  certification_promotion_failed: '正式模型注册表原子晋级失败',
}

export class PiModelCertificationError extends Error {
  constructor(readonly code: PiModelCertificationErrorCode) {
    super(SAFE_ERROR_MESSAGES[code])
    this.name = 'PiModelCertificationError'
  }
}

function candidatePriceAttestationError(
  error: unknown,
): PiModelCertificationError {
  const mappings: Record<
    ProviderPriceAttestationErrorCode,
    PiModelCertificationErrorCode
  > = {
    price_attestation_missing:
      'candidate_price_attestation_missing',
    price_attestation_invalid:
      'candidate_price_attestation_invalid',
    price_attestation_expired:
      'candidate_price_attestation_expired',
    price_attestation_future_observation:
      'candidate_price_attestation_future_observation',
    price_attestation_insufficient_runway:
      'candidate_price_attestation_insufficient_runway',
    price_attestation_candidate_drift:
      'candidate_price_attestation_candidate_drift',
    price_attestation_rebudget_required:
      'candidate_price_rebudget_required',
    price_attestation_busy:
      'candidate_price_attestation_invalid',
  }
  return error instanceof ProviderPriceAttestationError
    ? new PiModelCertificationError(mappings[error.code])
    : new PiModelCertificationError(
        'candidate_price_attestation_invalid',
      )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function sha256(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex')
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) =>
          compareUnicodeCodePoints(left, right),
        )
        .map(([key, item]) => [key, canonicalize(item)]),
    )
  }
  return value
}

function canonicalSha256(value: unknown): string {
  return sha256(JSON.stringify(canonicalize(value)))
}

function isIsoTimestamp(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value) &&
    Number.isFinite(Date.parse(value))
  )
}

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const keys = Object.keys(value).sort(compareUnicodeCodePoints)
  return (
    keys.length === expected.length &&
    [...expected]
      .sort(compareUnicodeCodePoints)
      .every((key, index) => keys[index] === key)
  )
}

function exactPrice(value: unknown): value is PiModelCandidate['catalog']['priceUsdPerMillionTokens'] {
  return (
    isRecord(value) &&
    exactKeys(value, ['input', 'output', 'cacheRead', 'cacheWrite']) &&
    value.input === 0.14 &&
    value.output === 0.28 &&
    value.cacheRead === 0.0028 &&
    value.cacheWrite === 0
  )
}

export function parsePiModelCandidate(
  value: unknown,
): PiModelCandidate {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'schemaVersion',
      'candidateId',
      'status',
      'provider',
      'model',
      'modelVersion',
      'expectedResponseModel',
      'thinkingLevel',
      'piVersion',
      'catalog',
      'execution',
      'adapterRequirement',
      'certification',
    ]) ||
    value.schemaVersion !== COUNTRY_OUTAGE_PI_MODEL_CANDIDATE_SCHEMA ||
    value.candidateId !== DEEPSEEK_V4_FLASH_CANDIDATE_ID ||
    value.status !== 'candidate' ||
    value.provider !== 'deepseek' ||
    value.model !== 'deepseek-v4-flash' ||
    value.modelVersion !== 'deepseek-v4-flash' ||
    value.expectedResponseModel !== 'deepseek-v4-flash' ||
    value.thinkingLevel !== 'off' ||
    value.piVersion !== FORMAL_PI_VERSION ||
    !isRecord(value.catalog) ||
    !exactKeys(value.catalog, [
      'api',
      'baseUrl',
      'input',
      'reasoning',
      'contextWindowTokens',
      'catalogMaximumOutputTokens',
      'priceUsdPerMillionTokens',
    ]) ||
    value.catalog.api !== 'openai-completions' ||
    value.catalog.baseUrl !== 'https://api.deepseek.com' ||
    !Array.isArray(value.catalog.input) ||
    value.catalog.input.length !== 1 ||
    value.catalog.input[0] !== 'text' ||
    value.catalog.reasoning !== true ||
    value.catalog.contextWindowTokens !== 1_000_000 ||
    value.catalog.catalogMaximumOutputTokens !== 384_000 ||
    !exactPrice(value.catalog.priceUsdPerMillionTokens) ||
    !isRecord(value.execution) ||
    !exactKeys(value.execution, [
      'maximumInputTokens',
      'maximumOutputTokens',
      'maximumProviderRequestCount',
      'providerRetryAttempts',
    ]) ||
    value.execution.maximumInputTokens !== 64_000 ||
    value.execution.maximumOutputTokens !== 16_384 ||
    value.execution.maximumProviderRequestCount !==
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
        .maximumProviderRequestsPerReport ||
    value.execution.providerRetryAttempts !== 0 ||
    !isRecord(value.adapterRequirement) ||
    !exactKeys(value.adapterRequirement, [
      'api',
      'sameNameResponseModelRequired',
      'pinnedUnpatchedSourceSha256',
      'approvedSameNameSourceSha256',
    ]) ||
    value.adapterRequirement.api !== 'openai-completions' ||
    value.adapterRequirement.sameNameResponseModelRequired !== true ||
    value.adapterRequirement.pinnedUnpatchedSourceSha256 !==
      '0d50250fe2931e66e2078279a397814202e1ecddee58faf4b8bc04c278da177a' ||
    !Array.isArray(
      value.adapterRequirement.approvedSameNameSourceSha256,
    ) ||
    value.adapterRequirement.approvedSameNameSourceSha256.length !== 1 ||
    value.adapterRequirement.approvedSameNameSourceSha256[0] !==
      APPROVED_RESPONSE_MODEL_PATCH_SHA256 ||
    !isRecord(value.certification) ||
    !exactKeys(value.certification, [
      'maximumIndependentReportRuns',
      'budgetLimitCny',
      'conservativeCnyPerUsd',
    ]) ||
    value.certification.maximumIndependentReportRuns !== 2 ||
    value.certification.budgetLimitCny !== 20 ||
    value.certification.conservativeCnyPerUsd !== 8
  ) {
    throw new PiModelCertificationError('candidate_invalid')
  }

  return Object.freeze({
    schemaVersion: COUNTRY_OUTAGE_PI_MODEL_CANDIDATE_SCHEMA,
    candidateId: DEEPSEEK_V4_FLASH_CANDIDATE_ID,
    status: 'candidate',
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    modelVersion: 'deepseek-v4-flash',
    expectedResponseModel: 'deepseek-v4-flash',
    thinkingLevel: 'off',
    piVersion: FORMAL_PI_VERSION,
    catalog: Object.freeze({
      api: 'openai-completions',
      baseUrl: 'https://api.deepseek.com',
      input: Object.freeze(['text'] as const),
      reasoning: true,
      contextWindowTokens: 1_000_000,
      catalogMaximumOutputTokens: 384_000,
      priceUsdPerMillionTokens: Object.freeze({
        input: 0.14,
        output: 0.28,
        cacheRead: 0.0028,
        cacheWrite: 0,
      }),
    }),
    execution: Object.freeze({
      maximumInputTokens: 64_000,
      maximumOutputTokens: 16_384,
      maximumProviderRequestCount: 2,
      providerRetryAttempts: 0,
    }),
    adapterRequirement: Object.freeze({
      api: 'openai-completions',
      sameNameResponseModelRequired: true,
      pinnedUnpatchedSourceSha256:
        '0d50250fe2931e66e2078279a397814202e1ecddee58faf4b8bc04c278da177a',
      approvedSameNameSourceSha256: Object.freeze([
        APPROVED_RESPONSE_MODEL_PATCH_SHA256,
      ] as const),
    }),
    certification: Object.freeze({
      maximumIndependentReportRuns: 2,
      budgetLimitCny: 20,
      conservativeCnyPerUsd: 8,
    }),
  })
}

function defaultCandidatePath(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(
      moduleDirectory,
      '../../resources/model-candidates/deepseek-v4-flash-pi-0.82.1-v1.json',
    ),
    resolve(
      moduleDirectory,
      '../../../resources/model-candidates/deepseek-v4-flash-pi-0.82.1-v1.json',
    ),
  ]
  return (
    candidates.find((candidate) => existsSync(candidate)) ?? candidates[0]!
  )
}

function defaultRegistryPath(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(
      moduleDirectory,
      '../../resources/certified-models/country-outage-pi-models-v1.json',
    ),
    resolve(
      moduleDirectory,
      '../../../resources/certified-models/country-outage-pi-models-v1.json',
    ),
  ]
  return (
    candidates.find((candidate) => existsSync(candidate)) ?? candidates[0]!
  )
}

export async function loadPiModelCandidate(
  path: string = defaultCandidatePath(),
): Promise<LoadedPiModelCandidate> {
  try {
    const text = await readFile(resolve(path), 'utf8')
    return Object.freeze({
      candidate: parsePiModelCandidate(JSON.parse(text) as unknown),
      resourceSha256: sha256(text),
    })
  } catch (error) {
    if (error instanceof PiModelCertificationError) throw error
    throw new PiModelCertificationError('candidate_invalid')
  }
}

function defaultA4CertificationFixturePath(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(
      moduleDirectory,
      '../../resources/model-certification/a4-iran-country-outage-v1.json',
    ),
    resolve(
      moduleDirectory,
      '../../../resources/model-certification/a4-iran-country-outage-v1.json',
    ),
  ]
  return (
    candidates.find((candidate) => existsSync(candidate)) ?? candidates[0]!
  )
}

export function parseA4IranModelCertificationFixture(
  value: unknown,
): A4IranModelCertificationFixture {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'schemaVersion',
      'fixtureId',
      'eventReference',
      'eventType',
      'countryCode',
      'collectorId',
      'incidentId',
      'publicationId',
      'revision',
      'dataThrough',
      'isFinal',
    ]) ||
    value.schemaVersion !==
      'country_outage_model_certification_fixture_v1' ||
    value.fixtureId !== A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID ||
    value.eventReference !==
      'country_outage/2026-02-27 09:12:32/IR/1/r' ||
    value.eventType !== 'country_outage' ||
    value.countryCode !== 'IR' ||
    value.collectorId !== 'rrc25' ||
    value.incidentId !==
      'incident_go_v1_a1de26f854831330c616a72af21597eb' ||
    value.publicationId !==
      'publication_v1_38bddead083db3f49023c2e1' ||
    value.revision !== 1 ||
    value.dataThrough !== '2026-02-28T15:00:00Z' ||
    value.isFinal !== true
  ) {
    throw new PiModelCertificationError('candidate_invalid')
  }
  return Object.freeze({
    schemaVersion:
      'country_outage_model_certification_fixture_v1',
    fixtureId: A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID,
    eventReference:
      'country_outage/2026-02-27 09:12:32/IR/1/r',
    eventType: 'country_outage',
    countryCode: 'IR',
    collectorId: 'rrc25',
    incidentId:
      'incident_go_v1_a1de26f854831330c616a72af21597eb',
    publicationId:
      'publication_v1_38bddead083db3f49023c2e1',
    revision: 1,
    dataThrough: '2026-02-28T15:00:00Z',
    isFinal: true,
  })
}

export function loadA4IranModelCertificationFixture(): A4IranModelCertificationFixture {
  try {
    const text = readFileSync(
      defaultA4CertificationFixturePath(),
      'utf8',
    )
    return parseA4IranModelCertificationFixture(
      JSON.parse(text) as unknown,
    )
  } catch (error) {
    if (error instanceof PiModelCertificationError) throw error
    throw new PiModelCertificationError('candidate_invalid')
  }
}

function maximumCertificationCostCny(
  candidate: PiModelCandidate,
): number {
  const maximumInputLikePriceUsdPerMillionTokens = Math.max(
    candidate.catalog.priceUsdPerMillionTokens.input,
    candidate.catalog.priceUsdPerMillionTokens.cacheRead,
    candidate.catalog.priceUsdPerMillionTokens.cacheWrite,
  )
  const perProviderRequestUsd =
    (candidate.execution.maximumInputTokens *
      maximumInputLikePriceUsdPerMillionTokens +
      candidate.execution.maximumOutputTokens *
        candidate.catalog.priceUsdPerMillionTokens.output) /
    MILLION
  const unroundedCny =
    perProviderRequestUsd *
    candidate.execution.maximumProviderRequestCount *
    candidate.certification.maximumIndependentReportRuns *
    candidate.certification.conservativeCnyPerUsd
  // 预算账本以 CNY E8 为最小单位。先归一到同一单位，避免乘法顺序
  // 产生 0.5419008000000001 这类并非真实费用差异的浮点尾数。
  return (
    Math.round(unroundedCny * 100_000_000) /
    100_000_000
  )
}

function candidateActivityBudgetPolicy(
  loadedCandidate: LoadedPiModelCandidate,
): CandidateActivityBudgetPolicy {
  const candidate = loadedCandidate.candidate
  const maximumCostCny = maximumCertificationCostCny(candidate)
  return Object.freeze({
    candidateId: candidate.candidateId,
    candidateResourceSha256: loadedCandidate.resourceSha256,
    provider: candidate.provider,
    model: candidate.model,
    budgetLimitCny: candidate.certification.budgetLimitCny,
    maximumSingleReportCostCny:
      maximumCostCny /
      candidate.certification.maximumIndependentReportRuns,
    maximumCertificationCostCny: maximumCostCny,
    conservativeCnyPerUsd:
      candidate.certification.conservativeCnyPerUsd,
    priceUsdPerMillionTokens: Object.freeze({
      input:
        candidate.catalog.priceUsdPerMillionTokens.input,
      output:
        candidate.catalog.priceUsdPerMillionTokens.output,
      cacheRead:
        candidate.catalog.priceUsdPerMillionTokens.cacheRead,
      cacheWrite:
        candidate.catalog.priceUsdPerMillionTokens.cacheWrite,
    }),
  })
}

function maximumA4ScenarioSuiteCertificationCostCny(
  loadedCandidate: LoadedPiModelCandidate,
): number {
  const policy = candidateActivityBudgetPolicy(loadedCandidate)
  return (
    Math.round(
      policy.maximumSingleReportCostCny *
        A4_TOTAL_REPORT_RUNS *
        100_000_000,
    ) / 100_000_000
  )
}

function finalizeA4ScenarioSuiteManifest(
  base: PiModelCertificationManifest,
  loadedCandidate: LoadedPiModelCandidate,
  scenarios: readonly CandidateScenarioCertificationRunEvidence[],
): PiModelCertificationManifest {
  if (
    scenarios.length !== A4_CERTIFICATION_SCENARIOS.length ||
    scenarios.some(
      (scenario, index) =>
        scenario.scenarioId !==
        A4_CERTIFICATION_SCENARIOS[index]?.id,
    )
  ) {
    throw new PiModelCertificationError(
      'candidate_run_evidence_invalid',
    )
  }
  const validUntil = new Date(
    Date.parse(base.completedAt) +
      A4_MODEL_ALIAS_CERTIFICATION_VALIDITY_MS,
  ).toISOString()
  const scenarioCoverage: A4ModelScenarioCoverage = Object.freeze({
    scenarioSetId: A4_CERTIFIED_SCENARIO_SET_ID,
    certifiedInputScope: A4_CERTIFIED_INPUT_SCOPE,
    representativeRepeatRunEvidenceIds: Object.freeze([
      base.runs[0].runEvidenceId,
      base.runs[1].runEvidenceId,
    ]) as readonly [string, string],
    boundaryQuestionEngine:
      'deterministic-country-outage-question-engine-v1',
    scenarios: Object.freeze([...scenarios]),
  })
  const certificationProfile: A4ModelAliasCertificationProfile =
    Object.freeze({
      modelRevisionKind: 'mutable_alias',
      immutableRevisionAvailable: false,
      limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
      certificationValidUntil: validUntil,
      certifiedScenarioSetId: A4_CERTIFIED_SCENARIO_SET_ID,
      certifiedInputScope: A4_CERTIFIED_INPUT_SCOPE,
    })
  const actualCertificationCostCny =
    base.budget.actualCertificationCostCny +
    scenarios.reduce(
      (sum, scenario) =>
        sum + scenario.usage.conservativeCostCny,
      0,
    )
  if (
    !Number.isFinite(actualCertificationCostCny) ||
    actualCertificationCostCny >
      loadedCandidate.candidate.certification.budgetLimitCny
  ) {
    throw new PiModelCertificationError('candidate_budget_exceeded')
  }
  const evidenceBody = {
    candidateId: base.candidateId,
    candidateResourceSha256: base.candidateResourceSha256,
    certificationStartedAt: base.certificationStartedAt,
    completedAt: base.completedAt,
    registrySha256Before: base.targetRegistry.sha256Before,
    responseModelAdapterSourceSha256:
      base.policy.responseModelAdapterSourceSha256,
    priceAttestationId:
      base.policy.priceAttestation?.attestationId ?? null,
    priceAttestationResourceSha256:
      base.policy.priceAttestation?.resourceSha256 ?? null,
    priceEvidenceSha256:
      base.policy.priceAttestation?.evidenceSha256 ?? null,
    runnerIdentity: base.provenance.runnerIdentity,
    certificationFixtureId:
      base.provenance.certificationFixtureId,
    runs: base.runs.map((run) => run.runEvidenceId),
    scenarioSetId: scenarioCoverage.scenarioSetId,
    certifiedInputScope: scenarioCoverage.certifiedInputScope,
    boundaryQuestionEngine:
      scenarioCoverage.boundaryQuestionEngine,
    scenarioRuns: scenarioCoverage.scenarios.map(
      (scenario) => scenario.scenarioEvidenceId,
    ),
    modelRevisionKind:
      certificationProfile.modelRevisionKind,
    certificationValidUntil:
      certificationProfile.certificationValidUntil,
  }
  return Object.freeze({
    ...base,
    evidenceId:
      `evidence:model-certification:${canonicalSha256(evidenceBody)}`,
    budget: Object.freeze({
      ...base.budget,
      maximumCertificationCostCny:
        maximumA4ScenarioSuiteCertificationCostCny(
          loadedCandidate,
        ),
      actualCertificationCostCny,
    }),
    scenarioCoverage,
    certificationProfile,
  })
}

function catalogMatches(
  model: NonNullable<CreateAgentSessionOptions['model']>,
  candidate: PiModelCandidate,
): boolean {
  const price = model.cost
  return (
    model.provider === candidate.provider &&
    model.id === candidate.model &&
    model.api === candidate.catalog.api &&
    model.baseUrl === candidate.catalog.baseUrl &&
    model.reasoning === candidate.catalog.reasoning &&
    Array.isArray(model.input) &&
    model.input.length === 1 &&
    model.input[0] === 'text' &&
    model.contextWindow === candidate.catalog.contextWindowTokens &&
    model.maxTokens ===
      candidate.catalog.catalogMaximumOutputTokens &&
    price.input ===
      candidate.catalog.priceUsdPerMillionTokens.input &&
    price.output ===
      candidate.catalog.priceUsdPerMillionTokens.output &&
    price.cacheRead ===
      candidate.catalog.priceUsdPerMillionTokens.cacheRead &&
    price.cacheWrite ===
      candidate.catalog.priceUsdPerMillionTokens.cacheWrite
  )
}

export interface CreateCandidatePiModelBindingOptions {
  loadedCandidate: LoadedPiModelCandidate
  authPath: string
  priceAttestation?: VerifiedProviderPriceAttestation
  priceAttestationCheckedAt?: Date
  runtimeFactory?: FormalPiModelRuntimeFactory
  responseModelAdapterInspector?: CandidateResponseModelAdapterInspector
}

const defaultRuntimeFactory: FormalPiModelRuntimeFactory = async (
  options: CreateModelRuntimeOptions,
) => await ModelRuntime.create(options)

export interface CandidateResponseModelAdapterInspection {
  sameNamePreserved: boolean
  sourceSha256: string
}

export type CandidateResponseModelAdapterInspector =
  () => CandidateResponseModelAdapterInspection

function defaultResponseModelAdapterInspector(): CandidateResponseModelAdapterInspection {
  try {
    const codingAgentEntry = fileURLToPath(
      import.meta.resolve('@earendil-works/pi-coding-agent'),
    )
    const codingAgentRoot = resolve(dirname(codingAgentEntry), '..')
    const candidates = [
      resolve(
        codingAgentRoot,
        'node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js',
      ),
      resolve(
        codingAgentRoot,
        '../pi-ai/dist/api/openai-completions.js',
      ),
    ]
    const adapterPath = candidates.find((path) => existsSync(path))
    if (!adapterPath) {
      throw new PiModelCertificationError(
        'candidate_response_model_adapter_unsupported',
      )
    }
    const source = readFileSync(adapterPath, 'utf8')
    const assignment =
      /output\.responseModel\s*\|\|=\s*chunk\.model/u.test(source)
    const dropsSameName =
      /chunk\.model\s*!==\s*model\.id/u.test(source)
    return {
      sameNamePreserved: assignment && !dropsSameName,
      sourceSha256: sha256(source),
    }
  } catch (error) {
    if (error instanceof PiModelCertificationError) throw error
    throw new PiModelCertificationError(
      'candidate_response_model_adapter_unsupported',
    )
  }
}

export async function createCandidatePiModelBinding(
  options: CreateCandidatePiModelBindingOptions,
): Promise<CandidatePiModelBinding> {
  assertFormalPiInstalledVersion()
  const candidate = parsePiModelCandidate(
    options.loadedCandidate.candidate,
  )
  if (!SHA256.test(options.loadedCandidate.resourceSha256)) {
    throw new PiModelCertificationError('candidate_invalid')
  }
  let priceAttestation: VerifiedProviderPriceAttestation | null = null
  if (options.priceAttestation !== undefined) {
    try {
      priceAttestation = assertVerifiedProviderPriceAttestation(
        options.priceAttestation,
        options.loadedCandidate,
      )
      assertProviderPriceAttestationRunway(
        priceAttestation,
        options.priceAttestationCheckedAt ?? new Date(),
      )
    } catch (error) {
      throw candidatePriceAttestationError(error)
    }
  }
  const authPath = options.authPath.trim()
  if (!authPath || !isAbsolute(authPath)) {
    throw new PiModelCertificationError('candidate_auth_required')
  }

  const adapterInspectorInjected =
    options.responseModelAdapterInspector !== undefined
  const adapterInspection = (
    options.responseModelAdapterInspector ??
    defaultResponseModelAdapterInspector
  )()
  const approvedAdapterSources =
    candidate.adapterRequirement
      .approvedSameNameSourceSha256 as readonly string[]
  if (
    adapterInspection.sameNamePreserved !== true ||
    !SHA256.test(adapterInspection.sourceSha256) ||
    (!adapterInspectorInjected &&
      !approvedAdapterSources.includes(
        adapterInspection.sourceSha256,
      ))
  ) {
    throw new PiModelCertificationError(
      'candidate_response_model_adapter_unsupported',
    )
  }

  let credentials: ReturnType<
    typeof createFrozenFormalCredentialStore
  >
  try {
    credentials = createFrozenFormalCredentialStore(
      authPath,
      candidate.provider,
    )
    const entries = await credentials.list()
    if (
      entries.length !== 1 ||
      entries[0]?.providerId !== candidate.provider ||
      entries[0]?.type !== 'api_key'
    ) {
      throw new PiModelCertificationError('candidate_auth_required')
    }
  } catch (error) {
    if (error instanceof PiModelCertificationError) throw error
    throw new PiModelCertificationError('candidate_auth_required')
  }

  const maximumCostCny = maximumCertificationCostCny(candidate)
  if (
    !Number.isFinite(maximumCostCny) ||
    maximumCostCny <= 0 ||
    maximumCostCny > candidate.certification.budgetLimitCny
  ) {
    throw new PiModelCertificationError(
      'candidate_budget_preflight_failed',
    )
  }

  let modelRuntime: ModelRuntime
  try {
    modelRuntime = await (options.runtimeFactory ?? defaultRuntimeFactory)({
      credentials,
      modelsPath: null,
      allowModelNetwork: false,
    })
  } catch {
    throw new PiModelCertificationError(
      'candidate_runtime_initialization_failed',
    )
  }
  if (modelRuntime.getError()) {
    throw new PiModelCertificationError(
      'candidate_runtime_metadata_invalid',
    )
  }

  const catalogModel = modelRuntime.getModel(
    candidate.provider,
    candidate.model,
  )
  if (!catalogModel) {
    throw new PiModelCertificationError('candidate_model_not_found')
  }
  if (!catalogMatches(catalogModel, candidate)) {
    throw new PiModelCertificationError(
      'candidate_model_catalog_mismatch',
    )
  }
  const authStatus = modelRuntime.getProviderAuthStatus(
    candidate.provider,
  )
  if (!authStatus.configured || authStatus.source !== 'stored') {
    throw new PiModelCertificationError('candidate_auth_required')
  }
  let available: readonly NonNullable<
    CreateAgentSessionOptions['model']
  >[]
  try {
    available = await modelRuntime.getAvailable(candidate.provider)
  } catch {
    throw new PiModelCertificationError(
      'candidate_runtime_metadata_invalid',
    )
  }
  if (
    !available.some(
      (model) =>
        model.provider === candidate.provider &&
        model.id === candidate.model,
    )
  ) {
    throw new PiModelCertificationError(
      'candidate_model_not_available',
    )
  }
  if (modelRuntime.getError()) {
    throw new PiModelCertificationError(
      'candidate_runtime_metadata_invalid',
    )
  }

  const model = capCountryOutageModelOutput(catalogModel)
  if (
    model.maxTokens !== candidate.execution.maximumOutputTokens ||
    model.contextWindow <
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.minimumModelContextWindowTokens
  ) {
    throw new PiModelCertificationError(
      'candidate_model_catalog_mismatch',
    )
  }
  return Object.freeze({
    modelRuntime,
    model,
    candidate,
    candidateResourceSha256: options.loadedCandidate.resourceSha256,
    priceAttestation,
    runSelection: Object.freeze({
      runtimeIdentity: 'candidate',
      candidateId: candidate.candidateId,
      candidateResourceSha256:
        options.loadedCandidate.resourceSha256,
      profile: Object.freeze({
        id: candidate.candidateId,
        status: 'candidate',
        provider: candidate.provider,
        model: candidate.model,
        modelVersion: candidate.modelVersion,
        expectedResponseModel: candidate.expectedResponseModel,
        thinkingLevel: candidate.thinkingLevel,
        piVersion: candidate.piVersion,
      }),
    }),
    preflight: Object.freeze({
      schemaVersion:
        'country_outage_pi_model_candidate_preflight_v1',
      runtimeIdentity: 'candidate',
      candidateId: candidate.candidateId,
      candidateResourceSha256:
        options.loadedCandidate.resourceSha256,
      provider: candidate.provider,
      model: candidate.model,
      expectedResponseModel: candidate.expectedResponseModel,
      thinkingLevel: candidate.thinkingLevel,
      piVersion: candidate.piVersion,
      modelCatalogNetworkRefreshEnabled: false,
      modelsJsonEnabled: false,
      providerRetryAttempts:
        candidate.execution.providerRetryAttempts,
      maximumProviderRequestCount:
        candidate.execution.maximumProviderRequestCount,
      maximumInputTokens: candidate.execution.maximumInputTokens,
      maximumOutputTokens:
        candidate.execution.maximumOutputTokens,
      maximumIndependentReportRuns:
        candidate.certification.maximumIndependentReportRuns,
      maximumCertificationCostCny: maximumCostCny,
      budgetLimitCny: candidate.certification.budgetLimitCny,
      auth: Object.freeze({
        configured: true,
        source: 'stored',
      }),
      available: true,
      responseModelAdapter: Object.freeze({
        sameNamePreserved: true,
        sourceSha256: adapterInspection.sourceSha256,
      }),
      priceAttestation,
    }),
  })
}

export interface CandidateCertificationRunnerResult {
  completedAt: string
  observedProvider: string
  observedModel: string
  responseModel?: string
  providerRequestCount: number
  providerRetryAttempts: number
  structuredOutput: {
    mechanism:
      'deepseek-json-object-no-tools-v2'
    payloadPreparedCount: number
  }
  artifactId: string
  reportContentSha256: string
  reportDocumentSha256: string
  reportAuditManifestSha256: string
  piRunAuditSha256: string
  factSetId: string
  snapshotSha256: string
  evidenceInputSha256: string
  validatorPassed: boolean
  reportComplete: boolean
  markdown: {
    ready: boolean
    sha256: string
  }
  pdf: {
    ready: boolean
    sha256: string
  }
  usage: {
    inputTokens: number
    outputTokens: number
    cacheReadTokens: number
    cacheWriteTokens: number
  }
}

export type CandidateCertificationRunner = (options: {
  runNumber: 1 | 2
  binding: CandidatePiModelBinding
}) => Promise<CandidateCertificationRunnerResult>

export interface CandidateCertificationRunEvidence {
  runtimeIdentity: 'candidate'
  runNumber: 1 | 2
  runEvidenceId: string
  completedAt: string
  observed: {
    provider: string
    model: string
    responseModel: string
  }
  artifactId: string
  reportContentSha256: string
  factSetId: string
  snapshotSha256: string
  evidenceInputSha256: string
  checks: {
    reportComplete: true
    validator: true
    markdown: true
    pdf: true
    providerRequestCount: number
    providerRetryAttempts: 0
    structuredOutput: {
      mechanism:
        'deepseek-json-object-no-tools-v2'
      payloadPreparedCount: number
    }
  }
  artifacts: {
    reportDocumentSha256: string
    reportAuditManifestSha256: string
    piRunAuditSha256: string
    markdownSha256: string
    pdfSha256: string
  }
  usage: {
    inputTokens: number
    outputTokens: number
    cacheReadTokens: number
    cacheWriteTokens: number
    conservativeCostUsd: number
    conservativeCostCny: number
  }
}

export interface CandidateScenarioCertificationRunEvidence {
  scenarioId: A4CertificationScenarioId
  purpose: A4CertificationScenarioDefinition['purpose']
  certificationOnly: true
  synthetic: true
  scenarioEvidenceId: string
  completedAt: string
  observed: CandidateCertificationRunEvidence['observed']
  artifactId: string
  reportContentSha256: string
  factSetId: string
  snapshotSha256: string
  evidenceInputSha256: string
  checks: CandidateCertificationRunEvidence['checks']
  artifacts: CandidateCertificationRunEvidence['artifacts']
  usage: CandidateCertificationRunEvidence['usage']
}

export interface A4ModelScenarioCoverage {
  scenarioSetId: typeof A4_CERTIFIED_SCENARIO_SET_ID
  certifiedInputScope: typeof A4_CERTIFIED_INPUT_SCOPE
  representativeRepeatRunEvidenceIds: readonly [string, string]
  boundaryQuestionEngine:
    'deterministic-country-outage-question-engine-v1'
  scenarios: readonly CandidateScenarioCertificationRunEvidence[]
}

export interface A4ModelAliasCertificationProfile {
  modelRevisionKind: 'mutable_alias'
  immutableRevisionAvailable: false
  limitation: typeof MUTABLE_MODEL_ALIAS_LIMITATION_ZH
  certificationValidUntil: string
  certifiedScenarioSetId: typeof A4_CERTIFIED_SCENARIO_SET_ID
  certifiedInputScope: typeof A4_CERTIFIED_INPUT_SCOPE
}

export interface CandidateCertificationRunAudit {
  schemaVersion: 'country_outage_pi_model_candidate_run_audit_v1'
  recordedAt: string
  runtimeIdentity: 'candidate'
  candidateId: typeof DEEPSEEK_V4_FLASH_CANDIDATE_ID
  candidateResourceSha256: string
  provider: 'deepseek'
  model: 'deepseek-v4-flash'
  runNumber: 1 | 2
  outcome: 'accepted' | 'rejected'
  runEvidenceId?: string
  rejectionCode?: PiModelCertificationErrorCode
}

export type CandidateCertificationAuditSink = (
  audit: CandidateCertificationRunAudit,
) => void | Promise<void>

export interface PiModelCertificationManifest {
  schemaVersion: typeof COUNTRY_OUTAGE_PI_MODEL_CERTIFICATION_MANIFEST_SCHEMA
  status: 'passed'
  runtimeIdentity: 'candidate'
  candidateId: typeof DEEPSEEK_V4_FLASH_CANDIDATE_ID
  candidateResourceSha256: string
  evidenceId: string
  certificationStartedAt: string
  completedAt: string
  provenance: {
    runnerIdentity:
      | 'candidate-framework-test-runner-v1'
      | 'country-outage-full-report-integration-test-v1'
      | 'country-outage-full-report-runner-v1'
    promotable: boolean
    certificationFixtureId:
      | typeof A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID
      | null
  }
  targetRegistry: {
    registryVersionBefore: string
    sha256Before: string
  }
  policy: {
    piVersion: typeof FORMAL_PI_VERSION
    providerRetryAttempts: 0
    maximumProviderRequestCount: 2
    maximumOutputTokens: 16_384
    requiredIndependentReportRuns: 2
    responseModelAdapterSourceSha256: string
    priceAttestation: VerifiedProviderPriceAttestation | null
  }
  budget: {
    limitCny: 20
    conservativeCnyPerUsd: 8
    maximumCertificationCostCny: number
    actualCertificationCostCny: number
  }
  factEquivalence: {
    passed: true
    factSetId: string
    snapshotSha256: string
    evidenceInputSha256: string
  }
  runs: readonly [
    CandidateCertificationRunEvidence,
    CandidateCertificationRunEvidence,
  ]
  scenarioCoverage?: A4ModelScenarioCoverage
  certificationProfile?: A4ModelAliasCertificationProfile
}

function finiteNonnegativeInteger(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= 0
  )
}

function conservativeRunCost(
  candidate: PiModelCandidate,
  usage: CandidateCertificationRunnerResult['usage'],
): { usd: number; cny: number } {
  // 认证预算不享受缓存折扣：所有输入类 token 都按 input 单价计。
  const inputLike =
    usage.inputTokens +
    usage.cacheReadTokens +
    usage.cacheWriteTokens
  const usd =
    (inputLike *
      candidate.catalog.priceUsdPerMillionTokens.input +
      usage.outputTokens *
        candidate.catalog.priceUsdPerMillionTokens.output) /
    MILLION
  return {
    usd,
    cny: usd * candidate.certification.conservativeCnyPerUsd,
  }
}

function validateRunnerResult(
  candidate: PiModelCandidate,
  runNumber: 1 | 2,
  result: CandidateCertificationRunnerResult,
): CandidateCertificationRunEvidence {
  const aggregateInputTokens =
    result.usage.inputTokens +
    result.usage.cacheReadTokens +
    result.usage.cacheWriteTokens
  if (
    result.responseModel === undefined ||
    !result.responseModel.trim()
  ) {
    // 不允许用 observedModel 或消息中的 model 字段补齐。
    throw new PiModelCertificationError(
      'candidate_response_model_missing',
    )
  }
  if (result.responseModel !== candidate.expectedResponseModel) {
    throw new PiModelCertificationError(
      'candidate_response_model_mismatch',
    )
  }
  if (
    result.observedProvider !== candidate.provider ||
    result.observedModel !== candidate.model ||
    !finiteNonnegativeInteger(result.providerRequestCount) ||
    result.providerRequestCount < 1 ||
    result.providerRequestCount >
      candidate.execution.maximumProviderRequestCount ||
    result.providerRetryAttempts !==
      candidate.execution.providerRetryAttempts ||
    !isRecord(result.structuredOutput) ||
    !exactKeys(result.structuredOutput, [
      'mechanism',
      'payloadPreparedCount',
    ]) ||
    result.structuredOutput.mechanism !==
      'deepseek-json-object-no-tools-v2' ||
    !finiteNonnegativeInteger(
      result.structuredOutput.payloadPreparedCount,
    ) ||
    result.structuredOutput.payloadPreparedCount !==
      result.providerRequestCount ||
    !isIsoTimestamp(result.completedAt) ||
    !SAFE_ID.test(result.artifactId) ||
    !SHA256.test(result.reportContentSha256) ||
    !SHA256.test(result.reportDocumentSha256) ||
    !SHA256.test(result.reportAuditManifestSha256) ||
    !SHA256.test(result.piRunAuditSha256) ||
    !SAFE_ID.test(result.factSetId) ||
    !SHA256.test(result.snapshotSha256) ||
    !SHA256.test(result.evidenceInputSha256) ||
    result.reportComplete !== true ||
    result.validatorPassed !== true ||
    result.markdown.ready !== true ||
    !SHA256.test(result.markdown.sha256) ||
    result.pdf.ready !== true ||
    !SHA256.test(result.pdf.sha256) ||
    !finiteNonnegativeInteger(result.usage.inputTokens) ||
    !finiteNonnegativeInteger(result.usage.outputTokens) ||
    !finiteNonnegativeInteger(result.usage.cacheReadTokens) ||
    !finiteNonnegativeInteger(result.usage.cacheWriteTokens) ||
    aggregateInputTokens >
      candidate.execution.maximumInputTokens *
        result.providerRequestCount ||
    result.usage.outputTokens >
      candidate.execution.maximumOutputTokens *
        result.providerRequestCount
  ) {
    throw new PiModelCertificationError(
      'candidate_run_evidence_invalid',
    )
  }
  const cost = conservativeRunCost(candidate, result.usage)
  const safeEvidence = {
    runtimeIdentity: 'candidate' as const,
    runNumber,
    completedAt: result.completedAt,
    observed: {
      provider: result.observedProvider,
      model: result.observedModel,
      responseModel: result.responseModel,
    },
    artifactId: result.artifactId,
    reportContentSha256: result.reportContentSha256,
    factSetId: result.factSetId,
    snapshotSha256: result.snapshotSha256,
    evidenceInputSha256: result.evidenceInputSha256,
    checks: {
      reportComplete: true as const,
      validator: true as const,
      markdown: true as const,
      pdf: true as const,
      providerRequestCount: result.providerRequestCount,
      providerRetryAttempts: 0 as const,
      structuredOutput: {
        mechanism:
          'deepseek-json-object-no-tools-v2' as const,
        payloadPreparedCount:
          result.structuredOutput.payloadPreparedCount,
      },
    },
    artifacts: {
      reportDocumentSha256: result.reportDocumentSha256,
      reportAuditManifestSha256:
        result.reportAuditManifestSha256,
      piRunAuditSha256: result.piRunAuditSha256,
      markdownSha256: result.markdown.sha256,
      pdfSha256: result.pdf.sha256,
    },
    usage: {
      inputTokens: result.usage.inputTokens,
      outputTokens: result.usage.outputTokens,
      cacheReadTokens: result.usage.cacheReadTokens,
      cacheWriteTokens: result.usage.cacheWriteTokens,
      conservativeCostUsd: cost.usd,
      conservativeCostCny: cost.cny,
    },
  }
  return Object.freeze({
    ...safeEvidence,
    runEvidenceId: `candidate-run:${canonicalSha256(safeEvidence)}`,
  })
}

function validateScenarioRunnerResult(
  candidate: PiModelCandidate,
  scenario: A4CertificationScenarioDefinition,
  result: CandidateCertificationRunnerResult,
): CandidateScenarioCertificationRunEvidence {
  const validated = validateRunnerResult(candidate, 2, result)
  const safeEvidence = {
    scenarioId: scenario.id,
    purpose: scenario.purpose,
    certificationOnly: true as const,
    synthetic: true as const,
    completedAt: validated.completedAt,
    observed: validated.observed,
    artifactId: validated.artifactId,
    reportContentSha256: validated.reportContentSha256,
    factSetId: validated.factSetId,
    snapshotSha256: validated.snapshotSha256,
    evidenceInputSha256: validated.evidenceInputSha256,
    checks: validated.checks,
    artifacts: validated.artifacts,
    usage: validated.usage,
  }
  return Object.freeze({
    ...safeEvidence,
    scenarioEvidenceId:
      `candidate-scenario:${canonicalSha256(safeEvidence)}`,
  })
}

function registrySnapshot(path: string): {
  registry: CertifiedPiModelRegistry
  sha256: string
  text: string
} {
  try {
    const normalized = resolve(path)
    if (lstatSync(normalized).isSymbolicLink()) {
      throw new PiModelCertificationError(
        'certification_manifest_invalid',
      )
    }
    const text = readFileSync(normalized, 'utf8')
    return {
      registry: parseCertifiedPiModelRegistry(
        JSON.parse(text) as unknown,
      ),
      sha256: sha256(text),
      text,
    }
  } catch (error) {
    if (
      error instanceof PiModelCertificationError ||
      error instanceof Error &&
        error.name === 'FormalPiRuntimeError'
    ) {
      throw error
    }
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
}

async function safeAuditWrite(
  sink: CandidateCertificationAuditSink | undefined,
  audit: CandidateCertificationRunAudit,
): Promise<void> {
  if (!sink) return
  try {
    await sink(Object.freeze({ ...audit }))
  } catch {
    throw new PiModelCertificationError(
      'candidate_audit_sink_failed',
    )
  }
}

export interface RunPiModelCertificationOptions {
  loadedCandidate: LoadedPiModelCandidate
  authPath: string
  runner: CandidateCertificationRunner
  priceAttestation?: VerifiedProviderPriceAttestation
  certificationStartedAt?: string
  registryPath?: string
  runtimeFactory?: FormalPiModelRuntimeFactory
  responseModelAdapterInspector?: CandidateResponseModelAdapterInspector
  auditSink?: CandidateCertificationAuditSink
  now?: () => Date
}

type CandidateCertificationRunnerIdentity =
  PiModelCertificationManifest['provenance']['runnerIdentity']

async function runPiModelCandidateCertificationInternal(
  options: RunPiModelCertificationOptions,
  runnerIdentity: CandidateCertificationRunnerIdentity,
  certificationFixtureId:
    | typeof A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID
    | null,
): Promise<PiModelCertificationManifest> {
  const now = options.now ?? (() => new Date())
  const certificationStartedAt =
    options.certificationStartedAt ?? now().toISOString()
  if (!isIsoTimestamp(certificationStartedAt)) {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  const requiresPriceAttestation =
    runnerIdentity !== 'candidate-framework-test-runner-v1'
  if (
    requiresPriceAttestation &&
    options.priceAttestation === undefined
  ) {
    throw new PiModelCertificationError(
      'candidate_price_attestation_missing',
    )
  }
  let priceAttestation: VerifiedProviderPriceAttestation | null = null
  if (options.priceAttestation !== undefined) {
    try {
      priceAttestation = assertVerifiedProviderPriceAttestation(
        options.priceAttestation,
        options.loadedCandidate,
      )
    } catch (error) {
      throw candidatePriceAttestationError(error)
    }
  }
  if (
    priceAttestation !== null &&
    (Date.parse(priceAttestation.observedAt) >
      Date.parse(certificationStartedAt) ||
      Date.parse(certificationStartedAt) >=
        Date.parse(priceAttestation.expiresAt))
  ) {
    throw new PiModelCertificationError(
      Date.parse(priceAttestation.observedAt) >
        Date.parse(certificationStartedAt)
        ? 'candidate_price_attestation_future_observation'
        : 'candidate_price_attestation_expired',
    )
  }
  const registryPath = options.registryPath ?? defaultRegistryPath()
  const before = registrySnapshot(registryPath)
  const binding = await createCandidatePiModelBinding({
    loadedCandidate: options.loadedCandidate,
    authPath: options.authPath,
    ...(priceAttestation === null
      ? {}
      : {
          priceAttestation,
          priceAttestationCheckedAt: now(),
        }),
    ...(options.runtimeFactory
      ? { runtimeFactory: options.runtimeFactory }
      : {}),
    ...(options.responseModelAdapterInspector
      ? {
          responseModelAdapterInspector:
            options.responseModelAdapterInspector,
        }
      : {}),
  })
  const accepted: CandidateCertificationRunEvidence[] = []

  for (const runNumber of [1, 2] as const) {
    let evidence: CandidateCertificationRunEvidence
    try {
      const runStartedAt = now()
      if (
        !Number.isFinite(runStartedAt.valueOf()) ||
        runStartedAt.valueOf() <
          Date.parse(certificationStartedAt) ||
        (priceAttestation !== null &&
          runStartedAt.valueOf() >=
            Date.parse(priceAttestation.expiresAt))
      ) {
        throw new PiModelCertificationError(
          'candidate_price_attestation_expired',
        )
      }
      const result = await options.runner({ runNumber, binding })
      evidence = validateRunnerResult(
        binding.candidate,
        runNumber,
        result,
      )
      if (
        priceAttestation !== null &&
        (Date.parse(result.completedAt) <
          Date.parse(certificationStartedAt) ||
          Date.parse(result.completedAt) >=
            Date.parse(priceAttestation.expiresAt))
      ) {
        throw new PiModelCertificationError(
          'candidate_price_attestation_expired',
        )
      }
      const nextTotalCostCny =
        accepted.reduce(
          (sum, run) => sum + run.usage.conservativeCostCny,
          0,
        ) + evidence.usage.conservativeCostCny
      if (
        !Number.isFinite(nextTotalCostCny) ||
        nextTotalCostCny >
          binding.candidate.certification.budgetLimitCny
      ) {
        throw new PiModelCertificationError(
          'candidate_budget_exceeded',
        )
      }
      accepted.push(evidence)
      await safeAuditWrite(options.auditSink, {
        schemaVersion:
          'country_outage_pi_model_candidate_run_audit_v1',
        recordedAt: now().toISOString(),
        runtimeIdentity: 'candidate',
        candidateId: binding.candidate.candidateId,
        candidateResourceSha256:
          binding.candidateResourceSha256,
        provider: binding.candidate.provider,
        model: binding.candidate.model,
        runNumber,
        outcome: 'accepted',
        runEvidenceId: evidence.runEvidenceId,
      })
    } catch (error) {
      const code =
        error instanceof PiModelCertificationError
          ? error.code
          : 'candidate_runner_failed'
      await safeAuditWrite(options.auditSink, {
        schemaVersion:
          'country_outage_pi_model_candidate_run_audit_v1',
        recordedAt: now().toISOString(),
        runtimeIdentity: 'candidate',
        candidateId: binding.candidate.candidateId,
        candidateResourceSha256:
          binding.candidateResourceSha256,
        provider: binding.candidate.provider,
        model: binding.candidate.model,
        runNumber,
        outcome: 'rejected',
        rejectionCode: code,
      })
      if (error instanceof PiModelCertificationError) throw error
      throw new PiModelCertificationError('candidate_runner_failed')
    }
  }

  if (
    accepted.length !== MAXIMUM_CERTIFICATION_RUNS ||
    accepted[0]?.factSetId !== accepted[1]?.factSetId ||
    accepted[0]?.snapshotSha256 !== accepted[1]?.snapshotSha256 ||
    accepted[0]?.evidenceInputSha256 !==
      accepted[1]?.evidenceInputSha256
  ) {
    throw new PiModelCertificationError(
      'candidate_fact_equivalence_failed',
    )
  }
  const firstRun = accepted[0]!
  const secondRun = accepted[1]!
  const actualCostCny = accepted.reduce(
    (sum, run) => sum + run.usage.conservativeCostCny,
    0,
  )
  if (
    !Number.isFinite(actualCostCny) ||
    actualCostCny > binding.candidate.certification.budgetLimitCny
  ) {
    throw new PiModelCertificationError('candidate_budget_exceeded')
  }

  const after = registrySnapshot(registryPath)
  if (after.sha256 !== before.sha256) {
    throw new PiModelCertificationError(
      'certification_registry_changed',
    )
  }
  const completedAt = now().toISOString()
  if (!isIsoTimestamp(completedAt)) {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  if (
    Date.parse(completedAt) <
      Date.parse(certificationStartedAt) ||
    (priceAttestation !== null &&
      Date.parse(completedAt) >=
        Date.parse(priceAttestation.expiresAt))
  ) {
    throw new PiModelCertificationError(
      'candidate_price_attestation_expired',
    )
  }
  const evidenceBody = {
    candidateId: binding.candidate.candidateId,
    candidateResourceSha256: binding.candidateResourceSha256,
    certificationStartedAt,
    completedAt,
    registrySha256Before: before.sha256,
    responseModelAdapterSourceSha256:
      binding.preflight.responseModelAdapter.sourceSha256,
    priceAttestationId:
      priceAttestation?.attestationId ?? null,
    priceAttestationResourceSha256:
      priceAttestation?.resourceSha256 ?? null,
    priceEvidenceSha256:
      priceAttestation?.evidenceSha256 ?? null,
    runnerIdentity,
    certificationFixtureId,
    runs: accepted.map((run) => run.runEvidenceId),
  }
  return Object.freeze({
    schemaVersion:
      COUNTRY_OUTAGE_PI_MODEL_CERTIFICATION_MANIFEST_SCHEMA,
    status: 'passed',
    runtimeIdentity: 'candidate',
    candidateId: binding.candidate.candidateId,
    candidateResourceSha256: binding.candidateResourceSha256,
    evidenceId: `evidence:model-certification:${canonicalSha256(evidenceBody)}`,
    certificationStartedAt,
    completedAt,
    provenance: Object.freeze({
      runnerIdentity,
      promotable:
        runnerIdentity ===
        'country-outage-full-report-runner-v1',
      certificationFixtureId,
    }),
    targetRegistry: Object.freeze({
      registryVersionBefore: before.registry.registryVersion,
      sha256Before: before.sha256,
    }),
    policy: Object.freeze({
      piVersion: FORMAL_PI_VERSION,
      providerRetryAttempts: 0,
      maximumProviderRequestCount:
        binding.candidate.execution.maximumProviderRequestCount,
      maximumOutputTokens: 16_384,
      requiredIndependentReportRuns: 2,
      responseModelAdapterSourceSha256:
        binding.preflight.responseModelAdapter.sourceSha256,
      priceAttestation,
    }),
    budget: Object.freeze({
      limitCny: 20,
      conservativeCnyPerUsd: 8,
      maximumCertificationCostCny:
        binding.preflight.maximumCertificationCostCny,
      actualCertificationCostCny: actualCostCny,
    }),
    factEquivalence: Object.freeze({
      passed: true,
      factSetId: firstRun.factSetId,
      snapshotSha256: firstRun.snapshotSha256,
      evidenceInputSha256: firstRun.evidenceInputSha256,
    }),
    runs: Object.freeze([
      firstRun,
      secondRun,
    ]) as PiModelCertificationManifest['runs'],
  })
}

export async function runPiModelCandidateCertification(
  options: RunPiModelCertificationOptions,
): Promise<PiModelCertificationManifest> {
  return await runPiModelCandidateCertificationInternal(
    options,
    'candidate-framework-test-runner-v1',
    null,
  )
}

export interface A4ModelCertificationDependencies {
  /**
   * 这些依赖只用于集成测试。只要显式传入 dependencies，清单就会固定标记为
   * non-promotable，避免 mock Domeye、Pi 会话或 PDF 结果伪装成真实认证。
   */
  runtimeFactory?: FormalPiModelRuntimeFactory
  responseModelAdapterInspector?: CandidateResponseModelAdapterInspector
  clientFactory?: (options: {
    runNumber: CandidateActivityRunNumber
    scenarioId?: A4CertificationScenarioId
    fixture: A4IranModelCertificationFixture
  }) => CountryOutageReportDataSource
  pdfRendererFactory?: (options: {
    runNumber: CandidateActivityRunNumber
    scenarioId?: A4CertificationScenarioId
    fixture: A4IranModelCertificationFixture
  }) => PdfDocumentRenderer
  executeScenarioSuite?: boolean
  sessionFactory?: PiSessionFactory
  dependencyRiskException?: ActiveCountryOutageDependencyRiskException
  now?: () => Date
  repositoryRoot?: string
  registryPath?: string
}

export interface RunA4ModelCandidateCertificationOptions {
  authPath: string
  domeyeApiBaseUrl: string
  domeyeApiTimeoutMs?: number
  pythonExecutable: string
  fontPath: string
  pdfTimeoutMs?: number
  dependencies?: A4ModelCertificationDependencies
}

export interface A4ModelCandidateCertificationResult {
  evidenceId: string
  artifactDirectory: string
  manifest: PiModelCertificationManifest
}

interface A4FullReportRunArtifacts {
  runNumber: 1 | 2
  document: CountryOutageReportDocument
  evidenceInputSha256: string
  reportAuditManifest: CountryOutageAuditManifestArtifact
  piRunAudit: {
    filename: 'pi-run-audit.json'
    mediaType: 'application/json; charset=utf-8'
    sha256: string
    content: Buffer
  }
  markdown: ReportArtifact
  pdf: ReportArtifact
}

interface A4ScenarioReportRunArtifacts
  extends Omit<A4FullReportRunArtifacts, 'runNumber'> {
  scenarioId: A4CertificationScenarioId
}

type A4ExpectedCertificationFixture = Omit<
  A4IranModelCertificationFixture,
  'isFinal'
> & {
  isFinal: boolean
}

function assertA4Snapshot(
  snapshot: SnapshotIdentity,
  fixture: A4ExpectedCertificationFixture,
): void {
  if (
    snapshot.incidentId !== fixture.incidentId ||
    snapshot.publicationId !== fixture.publicationId ||
    snapshot.revision !== fixture.revision ||
    snapshot.dataThrough !== fixture.dataThrough ||
    snapshot.isFinal !== fixture.isFinal ||
    snapshot.collectorId !== fixture.collectorId
  ) {
    throw new PiModelCertificationError(
      'candidate_fixture_mismatch',
    )
  }
}

function assertA4ObservationBatch(
  batch: ObservationBatch,
  fixture: A4ExpectedCertificationFixture,
): void {
  try {
    assertBatchIdentity(batch)
  } catch {
    throw new PiModelCertificationError(
      'candidate_fixture_mismatch',
    )
  }
  const resolution = batch.resolution
  const event = batch.overview.event_identity
  const scope = batch.overview.observation_scope
  if (
    resolution.legacy_reference !== fixture.eventReference ||
    resolution.event_type !== fixture.eventType ||
    resolution.incident_id !== fixture.incidentId ||
    resolution.publication_id !== fixture.publicationId ||
    resolution.latest_revision !== fixture.revision ||
    resolution.data_through !== fixture.dataThrough ||
    resolution.is_final !== fixture.isFinal ||
    event.legacy_reference !== fixture.eventReference ||
    event.event_type !== fixture.eventType ||
    event.incident_id !== fixture.incidentId ||
    event.country_code !== fixture.countryCode ||
    scope.collector_id !== fixture.collectorId ||
    scope.collector_count !== 1 ||
    scope.collector_ids.length !== 1 ||
    scope.collector_ids[0] !== fixture.collectorId
  ) {
    throw new PiModelCertificationError(
      'candidate_fixture_mismatch',
    )
  }
}

function createA4PinnedClient(
  client: CountryOutageReportDataSource,
  fixture: A4ExpectedCertificationFixture,
): CountryOutageReportDataSource {
  return Object.freeze({
    async getObservationBatch(
      reference: string,
    ): Promise<ObservationBatch> {
      if (reference.trim() !== fixture.eventReference) {
        throw new PiModelCertificationError(
          'candidate_fixture_mismatch',
        )
      }
      const batch = await client.getObservationBatch(reference)
      assertA4ObservationBatch(batch, fixture)
      return batch
    },
    async getAsns(
      snapshot: SnapshotIdentity,
      query?: AsnQuery,
    ): Promise<CountryOutageAsnPage> {
      assertA4Snapshot(snapshot, fixture)
      const page = await client.getAsns(snapshot, query)
      try {
        assertAsnPageIdentity(page, snapshot)
      } catch {
        throw new PiModelCertificationError(
          'candidate_fixture_mismatch',
        )
      }
      if (
        page.incident_id !== fixture.incidentId ||
        page.publication_id !== fixture.publicationId ||
        page.revision !== fixture.revision ||
        page.data_through !== fixture.dataThrough ||
        page.is_final !== fixture.isFinal
      ) {
        throw new PiModelCertificationError(
          'candidate_fixture_mismatch',
        )
      }
      return page
    },
  })
}

function assertA4Document(
  document: CountryOutageReportDocument,
  fixture: A4ExpectedCertificationFixture,
): void {
  assertA4Snapshot(document.snapshot, fixture)
  if (
    document.schemaVersion !==
      'country_outage_report_document_v1' ||
    document.event.event_type !== fixture.eventType ||
    document.event.legacy_reference !== fixture.eventReference ||
    document.event.incident_id !== fixture.incidentId ||
    document.event.country_code !== fixture.countryCode ||
    document.model.runtimeIdentity !== 'candidate' ||
    document.model.provider !== 'deepseek' ||
    document.model.model !== 'deepseek-v4-flash' ||
    document.validation.passed !== true
  ) {
    throw new PiModelCertificationError(
      'candidate_fixture_mismatch',
    )
  }
}

function expectedA4ScenarioFixture(
  fixture: A4IranModelCertificationFixture,
  scenarioId: A4CertificationScenarioId,
): A4ExpectedCertificationFixture {
  return Object.freeze({
    ...fixture,
    isFinal: scenarioId === 'non-final-snapshot' ? false : true,
  })
}

function assertA4ScenarioDocument(
  document: CountryOutageReportDocument,
  fixture: A4IranModelCertificationFixture,
  scenarioId: A4CertificationScenarioId,
): void {
  assertA4Document(
    document,
    expectedA4ScenarioFixture(fixture, scenarioId),
  )
  if (
    scenarioId === 'capability-degraded-final' &&
    document.draft.sections.some((section) =>
      [
        'asn_scope',
        'address_families',
        'updates',
        'resources',
      ].includes(section.id),
    )
  ) {
    throw new PiModelCertificationError(
      'candidate_run_evidence_invalid',
    )
  }
  if (
    scenarioId === 'non-final-snapshot' &&
    (document.snapshot.isFinal !== false ||
      document.snapshot.dataThrough !== fixture.dataThrough)
  ) {
    throw new PiModelCertificationError(
      'candidate_run_evidence_invalid',
    )
  }
}

function acceptedCandidateAudit(
  audits: readonly FormalPiRunAuditRecord[],
  binding: CandidatePiModelBinding,
  document: CountryOutageReportDocument,
  fixture: A4ExpectedCertificationFixture,
): FormalPiRunAuditRecord & {
  outcome: 'accepted'
  observed: NonNullable<FormalPiRunAuditRecord['observed']>
  usage: NonNullable<FormalPiRunAuditRecord['usage']>
} {
  if (audits.length !== 1) {
    throw new PiModelCertificationError(
      'candidate_internal_audit_invalid',
    )
  }
  const audit = audits[0]!
  const modelAttemptAuditIsValid =
    audit.modelAttempt.timeoutMs ===
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.modelAttemptTimeoutMs &&
    audit.modelAttempt.maximumAttempts ===
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelAttempts &&
    Number.isSafeInteger(audit.modelAttempt.executedAttempts) &&
    audit.modelAttempt.executedAttempts >= 1 &&
    audit.modelAttempt.executedAttempts <=
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelAttempts
  if (
    audit.schemaVersion !== 'country_outage_pi_run_audit_v3' ||
    audit.outcome !== 'accepted' ||
    audit.runtimeIdentity !== 'candidate' ||
    audit.candidateId !== binding.candidate.candidateId ||
    audit.candidateResourceSha256 !==
      binding.candidateResourceSha256 ||
    audit.registryVersion !== undefined ||
    audit.certificationEvidenceId !== undefined ||
    audit.profileId !== binding.candidate.candidateId ||
    audit.provider !== binding.candidate.provider ||
    audit.model !== binding.candidate.model ||
    audit.modelVersion !== binding.candidate.modelVersion ||
    audit.expectedResponseModel !==
      binding.candidate.expectedResponseModel ||
    audit.piVersion !== binding.candidate.piVersion ||
    audit.input.incidentId !== fixture.incidentId ||
    audit.input.publicationId !== fixture.publicationId ||
    audit.input.revision !== fixture.revision ||
    audit.input.dataThrough !== fixture.dataThrough ||
    audit.input.factSetId !== document.factSetId ||
    audit.input.collectorId !== fixture.collectorId ||
    audit.narration.mode !== FORMAL_PI_NARRATION_MODE ||
    audit.narration.slotContractVersion !==
      COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION ||
    !Number.isSafeInteger(audit.narration.requestedSlotCount) ||
    audit.narration.requestedSlotCount < 2 ||
    audit.narration.requestedSlotCount >
      COUNTRY_OUTAGE_LANGUAGE_SLOT_IDS.length ||
    audit.narration.acceptedSlotCount !==
      audit.narration.requestedSlotCount ||
    audit.narration.baseV5 !== 'passed' ||
    audit.narration.mergeInvariant !== 'passed' ||
    audit.narration.finalV5 !== 'passed' ||
    audit.narration.modelOutputApplied !== true ||
    audit.runtimeSecurity.providerRetryAttempts !== 0 ||
    audit.runtimeSecurity.forwardedProviderRequestCount !==
      audit.usage?.assistantMessages ||
    audit.runtimeSecurity.structuredOutput?.applicability !==
      'required' ||
    audit.runtimeSecurity.structuredOutput?.mechanism !==
      'deepseek-json-object-no-tools-v2' ||
    !Number.isSafeInteger(
      audit.runtimeSecurity.structuredOutput
        ?.payloadPreparedCount,
    ) ||
    audit.runtimeSecurity.structuredOutput
      ?.payloadPreparedCount !==
      audit.runtimeSecurity.forwardedProviderRequestCount ||
    audit.runtimeSecurity.explicitModel !== true ||
    audit.runtimeSecurity.packageManagerResolutionEnabled !== false ||
    audit.runtimeSecurity.modelResolverEnabled !== false ||
    audit.runtimeSecurity.modelsJsonEnabled !== false ||
    audit.runtimeSecurity.modelCatalogNetworkRefreshEnabled !== false ||
    !modelAttemptAuditIsValid ||
    audit.observed === undefined ||
    audit.usage === undefined
  ) {
    throw new PiModelCertificationError(
      'candidate_internal_audit_invalid',
    )
  }
  return audit as FormalPiRunAuditRecord & {
    outcome: 'accepted'
    observed: NonNullable<FormalPiRunAuditRecord['observed']>
    usage: NonNullable<FormalPiRunAuditRecord['usage']>
  }
}

interface SafeCandidateActivityAudit {
  formalRejectionCode?: FormalPiRunRejectionCode
  usage?: CandidateActivityUsage
}

function safeCandidateActivityAudit(
  audits: readonly FormalPiRunAuditRecord[],
  binding: CandidatePiModelBinding,
): SafeCandidateActivityAudit {
  if (audits.length !== 1) return {}
  const audit = audits[0]
  if (
    audit === undefined ||
    audit.runtimeIdentity !== 'candidate' ||
    audit.candidateId !== binding.candidate.candidateId ||
    audit.candidateResourceSha256 !==
      binding.candidateResourceSha256 ||
    audit.registryVersion !== undefined ||
    audit.certificationEvidenceId !== undefined ||
    audit.profileId !== binding.candidate.candidateId ||
    audit.provider !== binding.candidate.provider ||
    audit.model !== binding.candidate.model ||
    audit.modelVersion !== binding.candidate.modelVersion ||
    audit.expectedResponseModel !==
      binding.candidate.expectedResponseModel ||
    audit.piVersion !== binding.candidate.piVersion
  ) {
    return {}
  }

  const formalRejectionCode =
    audit.outcome === 'rejected' &&
    isFormalPiRunRejectionCode(audit.rejectionCode)
      ? audit.rejectionCode
      : undefined
  if (audit.outcome === 'rejected') {
    // Pi 审计拒绝可能发生在 provider 已接受请求、但完整 usage 回执尚未
    // 到达的窗口。部分 SessionStats 不能证明完整账单，因此不返回 usage，
    // 由账本按整份未来预留保守结算。
    return formalRejectionCode === undefined
      ? {}
      : { formalRejectionCode }
  }
  const source = audit.usage
  if (source === undefined) {
    return formalRejectionCode === undefined
      ? {}
      : { formalRejectionCode }
  }
  const integerValues = [
    source.assistantMessages,
    source.tokens.input,
    source.tokens.output,
    source.tokens.cacheRead,
    source.tokens.cacheWrite,
  ]
  const aggregateInput =
    source.tokens.input +
    source.tokens.cacheRead +
    source.tokens.cacheWrite
  const totalBillableTokens =
    aggregateInput + source.tokens.output
  if (
    integerValues.some(
      (value) =>
        !Number.isSafeInteger(value) ||
        value < 0,
    ) ||
    audit.runtimeSecurity.forwardedProviderRequestCount !==
      source.assistantMessages ||
    source.assistantMessages < 1 ||
    !Number.isSafeInteger(aggregateInput) ||
    !Number.isSafeInteger(totalBillableTokens) ||
    totalBillableTokens <= 0
  ) {
    return formalRejectionCode === undefined
      ? {}
      : { formalRejectionCode }
  }
  return {
    ...(formalRejectionCode === undefined
      ? {}
      : { formalRejectionCode }),
    usage: {
      providerRequestCount: source.assistantMessages,
      inputTokens: source.tokens.input,
      outputTokens: source.tokens.output,
      cacheReadTokens: source.tokens.cacheRead,
      cacheWriteTokens: source.tokens.cacheWrite,
    },
  }
}

function safeCandidateActivityRejectionCode(
  error: unknown,
): CandidateActivityRejectionCode {
  if (
    error instanceof PiModelCertificationError &&
    isCandidateActivityRejectionCode(error.code)
  ) {
    return error.code
  }
  return 'candidate_runner_failed'
}

function readyArtifact(
  outcome:
    | { status: 'ready'; artifact: ReportArtifact }
    | { status: 'failed'; error: unknown },
): ReportArtifact {
  if (outcome.status !== 'ready') {
    throw new PiModelCertificationError(
      'candidate_run_evidence_invalid',
    )
  }
  return outcome.artifact
}

function serializedReportDocument(
  document: CountryOutageReportDocument,
): Buffer {
  return Buffer.from(`${JSON.stringify(document, null, 2)}\n`, 'utf8')
}

function evidenceInputSha256(
  evidence: ReportEvidenceBundle,
): string {
  // 绑定真正交给叙述器和校验器的完整证据输入，包括所有已分页 ASN 证据。
  return canonicalSha256(evidence)
}

function createSafeCandidatePiRunAuditArtifact(
  audit: FormalPiRunAuditRecord & {
    outcome: 'accepted'
    observed: NonNullable<FormalPiRunAuditRecord['observed']>
    usage: NonNullable<FormalPiRunAuditRecord['usage']>
  },
): A4FullReportRunArtifacts['piRunAudit'] {
  // 逐字段复制白名单，拒绝未来运行时附加的正文、参数、结果或认证材料。
  const safeRecord = {
    schemaVersion: audit.schemaVersion,
    recordedAt: audit.recordedAt,
    outcome: audit.outcome,
    runtimeIdentity: audit.runtimeIdentity,
    candidateId: audit.candidateId,
    candidateResourceSha256: audit.candidateResourceSha256,
    profileId: audit.profileId,
    provider: audit.provider,
    model: audit.model,
    modelVersion: audit.modelVersion,
    expectedResponseModel: audit.expectedResponseModel,
    piVersion: audit.piVersion,
    input: {
      eventReferenceSha256: audit.input.eventReferenceSha256,
      incidentId: audit.input.incidentId,
      publicationId: audit.input.publicationId,
      revision: audit.input.revision,
      dataThrough: audit.input.dataThrough,
      factSetId: audit.input.factSetId,
      collectorId: audit.input.collectorId,
      reportSpecificationVersion:
        audit.input.reportSpecificationVersion,
      projectKnowledgeVersion: audit.input.projectKnowledgeVersion,
      validatorRulesVersion: audit.input.validatorRulesVersion,
    },
    narration: {
      mode: audit.narration.mode,
      slotContractVersion: audit.narration.slotContractVersion,
      requestedSlotCount: audit.narration.requestedSlotCount,
      acceptedSlotCount: audit.narration.acceptedSlotCount,
      baseV5: audit.narration.baseV5,
      mergeInvariant: audit.narration.mergeInvariant,
      finalV5: audit.narration.finalV5,
      modelOutputApplied: audit.narration.modelOutputApplied,
    },
    runtimeSecurity: {
      resourceLoaderId: audit.runtimeSecurity.resourceLoaderId,
      skillBundleSha256:
        audit.runtimeSecurity.skillBundleSha256,
      packageManagerResolutionEnabled:
        audit.runtimeSecurity.packageManagerResolutionEnabled,
      modelResolverEnabled:
        audit.runtimeSecurity.modelResolverEnabled,
      modelsJsonEnabled: audit.runtimeSecurity.modelsJsonEnabled,
      modelCatalogNetworkRefreshEnabled:
        audit.runtimeSecurity.modelCatalogNetworkRefreshEnabled,
      explicitModel: audit.runtimeSecurity.explicitModel,
      providerRetryAttempts:
        audit.runtimeSecurity.providerRetryAttempts,
      forwardedProviderRequestCount:
        audit.runtimeSecurity.forwardedProviderRequestCount,
      structuredOutput: {
        applicability:
          audit.runtimeSecurity.structuredOutput.applicability,
        mechanism:
          audit.runtimeSecurity.structuredOutput.mechanism,
        payloadPreparedCount:
          audit.runtimeSecurity.structuredOutput
            .payloadPreparedCount,
      },
      dependencyRiskException: {
        exceptionId:
          audit.runtimeSecurity.dependencyRiskException.exceptionId,
        expiresAt:
          audit.runtimeSecurity.dependencyRiskException.expiresAt,
        status: audit.runtimeSecurity.dependencyRiskException.status,
      },
    },
    modelAttempt: {
      timeoutMs: audit.modelAttempt.timeoutMs,
      maximumAttempts: audit.modelAttempt.maximumAttempts,
      executedAttempts: audit.modelAttempt.executedAttempts,
    },
    observed: {
      provider: audit.observed.provider,
      model: audit.observed.model,
      responseModel: audit.observed.responseModel,
      stopReason: audit.observed.stopReason,
    },
    tools: {
      executedNames: [...audit.tools.executedNames],
      executionCount: audit.tools.executionCount,
      unauthorizedAttemptCount: audit.tools.unauthorizedAttemptCount,
    },
    usage: {
      assistantMessages: audit.usage.assistantMessages,
      toolCalls: audit.usage.toolCalls,
      toolResults: audit.usage.toolResults,
      totalMessages: audit.usage.totalMessages,
      tokens: {
        input: audit.usage.tokens.input,
        output: audit.usage.tokens.output,
        cacheRead: audit.usage.tokens.cacheRead,
        cacheWrite: audit.usage.tokens.cacheWrite,
        total: audit.usage.tokens.total,
      },
      estimatedCostUsd: audit.usage.estimatedCostUsd,
    },
  }
  const content = Buffer.from(
    `${JSON.stringify(canonicalize(safeRecord), null, 2)}\n`,
    'utf8',
  )
  return {
    filename: 'pi-run-audit.json',
    mediaType: 'application/json; charset=utf-8',
    sha256: sha256(content),
    content,
  }
}

function buildA4RunnerResult(
  binding: CandidatePiModelBinding,
  fixture: A4ExpectedCertificationFixture,
  compiled: CompiledCountryOutageReport,
  reportAuditManifest: CountryOutageAuditManifestArtifact,
  artifacts: {
    artifactId: string
    markdown:
      | { status: 'ready'; artifact: ReportArtifact }
      | { status: 'failed'; error: unknown }
    pdf:
      | { status: 'ready'; artifact: ReportArtifact }
      | { status: 'failed'; error: unknown }
  },
  audits: readonly FormalPiRunAuditRecord[],
): {
  result: CandidateCertificationRunnerResult
  runArtifacts: Omit<A4FullReportRunArtifacts, 'runNumber'>
} {
  const { document, evidence } = compiled
  assertA4Document(document, fixture)
  const markdown = readyArtifact(artifacts.markdown)
  const pdf = readyArtifact(artifacts.pdf)
  if (
    artifacts.artifactId !== document.artifactId ||
    markdown.format !== 'markdown' ||
    pdf.format !== 'pdf' ||
    !pdf.content.subarray(0, 5).equals(Buffer.from('%PDF-')) ||
    sha256(markdown.content) !== markdown.sha256 ||
    sha256(pdf.content) !== pdf.sha256 ||
    reportAuditManifest.filename !== 'audit-manifest.json' ||
    sha256(reportAuditManifest.content) !==
      reportAuditManifest.sha256
  ) {
    throw new PiModelCertificationError(
      'candidate_run_evidence_invalid',
    )
  }
  const audit = acceptedCandidateAudit(
    audits,
    binding,
    document,
    fixture,
  )
  const piRunAudit = createSafeCandidatePiRunAuditArtifact(audit)
  const inputSha256 = evidenceInputSha256(evidence)
  return {
    result: {
      completedAt: audit.recordedAt,
      observedProvider: audit.observed.provider,
      observedModel: audit.observed.model,
      responseModel: audit.observed.responseModel,
      // 只从 PiReportNarrator 已验证的 SessionStats 取请求轮次。
      providerRequestCount: audit.usage.assistantMessages,
      // retry 值来自 PiReportNarrator 冻结 Settings 对应的内部审计。
      providerRetryAttempts:
        audit.runtimeSecurity.providerRetryAttempts,
      structuredOutput: {
        mechanism:
          'deepseek-json-object-no-tools-v2',
        payloadPreparedCount:
          audit.runtimeSecurity.structuredOutput
            .payloadPreparedCount,
      },
      artifactId: document.artifactId,
      reportContentSha256: document.reportContentSha256,
      reportDocumentSha256: sha256(
        serializedReportDocument(document),
      ),
      reportAuditManifestSha256: reportAuditManifest.sha256,
      piRunAuditSha256: piRunAudit.sha256,
      factSetId: document.factSetId,
      snapshotSha256: canonicalSha256(document.snapshot),
      evidenceInputSha256: inputSha256,
      validatorPassed: document.validation.passed,
      reportComplete: true,
      markdown: {
        ready: true,
        sha256: markdown.sha256,
      },
      pdf: {
        ready: true,
        sha256: pdf.sha256,
      },
      usage: {
        inputTokens: audit.usage.tokens.input,
        outputTokens: audit.usage.tokens.output,
        cacheReadTokens: audit.usage.tokens.cacheRead,
        cacheWriteTokens: audit.usage.tokens.cacheWrite,
      },
    },
    runArtifacts: {
      document,
      evidenceInputSha256: inputSha256,
      reportAuditManifest,
      piRunAudit,
      markdown,
      pdf,
    },
  }
}

function defaultA4RepositoryRoot(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(moduleDirectory, '../../..'),
    resolve(moduleDirectory, '../../../..'),
  ]
  const selected = candidates.find((candidate) =>
    existsSync(resolve(candidate, 'agent-sidecar/package.json')),
  )
  if (!selected) {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
  return selected
}

function candidateActivityError(
  error: unknown,
): PiModelCertificationError {
  if (
    error instanceof CandidateActivityLedgerError &&
    error.code === 'activity_historical_usage_unresolved'
  ) {
    return new PiModelCertificationError(
      'candidate_historical_usage_unresolved',
    )
  }
  if (
    error instanceof CandidateActivityLedgerError &&
    error.code === 'activity_budget_preflight_failed'
  ) {
    return new PiModelCertificationError(
      'candidate_budget_preflight_failed',
    )
  }
  return new PiModelCertificationError(
    'candidate_activity_audit_failed',
  )
}

function withCandidateActivityError<T>(operation: () => T): T {
  try {
    return operation()
  } catch (error) {
    throw candidateActivityError(error)
  }
}

export type A4CandidateReadinessBlocker =
  | ProviderPriceAttestationErrorCode
  | 'historical_usage_unresolved'
  | 'activity_budget_preflight_failed'
  | 'activity_ledger_invalid'

export interface A4CandidateReadinessStatus {
  schemaVersion: 'country_outage_a4_candidate_readiness_v1'
  checkedAt: string
  candidateId: typeof DEEPSEEK_V4_FLASH_CANDIDATE_ID
  candidateResourceSha256: string
  ready: boolean
  blockers: readonly A4CandidateReadinessBlocker[]
  priceAttestation: {
    status:
      | 'valid'
      | ProviderPriceAttestationErrorCode
    identity: VerifiedProviderPriceAttestation | null
  }
  activity: {
    status:
      | 'ready'
      | 'historical_usage_unresolved'
      | 'activity_budget_preflight_failed'
      | 'activity_ledger_invalid'
    snapshot: CandidateActivityBudgetSnapshot | null
  }
  safety: {
    readOnly: true
    credentialsRead: false
    networkAccessed: false
  }
}

/**
 * 纯只读 A4 readiness：不读取 auth，不创建 ModelRuntime，不访问 Domeye/供应商，
 * 不创建 ledger lock，也不写入价格证明、账本、anchor 或认证制品。
 */
export async function readA4CandidateReadinessStatus(options: {
  repositoryRoot?: string
  now?: () => Date
} = {}): Promise<A4CandidateReadinessStatus> {
  const loadedCandidate = await loadPiModelCandidate()
  const repositoryRoot =
    options.repositoryRoot ?? defaultA4RepositoryRoot()
  const checkedAtDate = (options.now ?? (() => new Date()))()
  if (!Number.isFinite(checkedAtDate.valueOf())) {
    throw new PiModelCertificationError(
      'candidate_price_attestation_invalid',
    )
  }
  let priceStatus:
    A4CandidateReadinessStatus['priceAttestation']['status'] =
    'valid'
  let priceIdentity: VerifiedProviderPriceAttestation | null = null
  try {
    priceIdentity = loadCurrentProviderPriceAttestation({
      repositoryRoot,
      candidate: loadedCandidate,
      now: checkedAtDate,
    })
    assertProviderPriceAttestationRunway(
      priceIdentity,
      checkedAtDate,
    )
  } catch (error) {
    priceStatus =
      error instanceof ProviderPriceAttestationError
        ? error.code
        : 'price_attestation_invalid'
  }

  let activityStatus:
    A4CandidateReadinessStatus['activity']['status'] = 'ready'
  let activitySnapshot: CandidateActivityBudgetSnapshot | null = null
  const budgetPolicy = candidateActivityBudgetPolicy(loadedCandidate)
  try {
    activitySnapshot = inspectCandidateActivityLedger({
      repositoryRoot,
      policy: budgetPolicy,
    })
    if (activitySnapshot.historicalUsageStatus !== 'resolved') {
      activityStatus = 'historical_usage_unresolved'
    } else if (
      !Number.isFinite(activitySnapshot.committedCostCny) ||
      activitySnapshot.committedCostCny +
        maximumA4ScenarioSuiteCertificationCostCny(
          loadedCandidate,
        ) >
        budgetPolicy.budgetLimitCny
    ) {
      activityStatus = 'activity_budget_preflight_failed'
    }
  } catch {
    activityStatus = 'activity_ledger_invalid'
  }
  const blockers: A4CandidateReadinessBlocker[] = []
  if (priceStatus !== 'valid') blockers.push(priceStatus)
  if (activityStatus !== 'ready') blockers.push(activityStatus)
  return Object.freeze({
    schemaVersion: 'country_outage_a4_candidate_readiness_v1',
    checkedAt: checkedAtDate.toISOString(),
    candidateId: loadedCandidate.candidate.candidateId,
    candidateResourceSha256: loadedCandidate.resourceSha256,
    ready: blockers.length === 0,
    blockers: Object.freeze([...blockers]),
    priceAttestation: Object.freeze({
      status: priceStatus,
      identity: priceIdentity,
    }),
    activity: Object.freeze({
      status: activityStatus,
      snapshot: activitySnapshot,
    }),
    safety: Object.freeze({
      readOnly: true,
      credentialsRead: false,
      networkAccessed: false,
    }),
  })
}

export async function writeA4ProviderPriceAttestation(options: {
  observedAt: string
  evidenceSha256: string
  priceUsdPerMillionTokens: {
    input: string
    output: string
    cacheRead: string
    cacheWrite: string
  }
  repositoryRoot?: string
  now?: () => Date
}): Promise<VerifiedProviderPriceAttestation> {
  const loadedCandidate = await loadPiModelCandidate()
  return writeCurrentProviderPriceAttestation({
    repositoryRoot:
      options.repositoryRoot ?? defaultA4RepositoryRoot(),
    candidate: loadedCandidate,
    observedAt: options.observedAt,
    evidenceSha256: options.evidenceSha256,
    priceUsdPerMillionTokens:
      options.priceUsdPerMillionTokens,
    now: (options.now ?? (() => new Date()))(),
  })
}

export function loadA4ProviderPriceAttestation(options: {
  loadedCandidate: LoadedPiModelCandidate
  repositoryRoot?: string
  now?: () => Date
}): VerifiedProviderPriceAttestation {
  try {
    return loadCurrentProviderPriceAttestation({
      repositoryRoot:
        options.repositoryRoot ?? defaultA4RepositoryRoot(),
      candidate: options.loadedCandidate,
      now: (options.now ?? (() => new Date()))(),
    })
  } catch (error) {
    throw candidatePriceAttestationError(error)
  }
}

export async function reconcileA4PreLedgerFailure(options: {
  repositoryRoot?: string
  formalRejectionCode?: FormalPiRunRejectionCode
  now?: () => Date
} = {}): Promise<CandidateActivityBudgetSnapshot> {
  const loadedCandidate = await loadPiModelCandidate()
  const repositoryRoot =
    options.repositoryRoot ?? defaultA4RepositoryRoot()
  let ledger: CandidateActivityLedger | undefined
  try {
    ledger =
      initializeCandidateActivityLedgerWithPreLedgerFailure({
        repositoryRoot,
        policy: candidateActivityBudgetPolicy(loadedCandidate),
        recordedAt: (options.now ?? (() => new Date()))(),
        ...(options.formalRejectionCode === undefined
          ? {}
          : {
              formalRejectionCode: options.formalRejectionCode,
            }),
      })
    return ledger.snapshot()
  } catch (error) {
    throw candidateActivityError(error)
  } finally {
    if (ledger) {
      try {
        ledger.close()
      } catch (error) {
        throw candidateActivityError(error)
      }
    }
  }
}

export async function initializeA4CandidateActivityLedger(options: {
  repositoryRoot?: string
  now?: () => Date
} = {}): Promise<CandidateActivityBudgetSnapshot> {
  const loadedCandidate = await loadPiModelCandidate()
  const repositoryRoot =
    options.repositoryRoot ?? defaultA4RepositoryRoot()
  let ledger: CandidateActivityLedger | undefined
  try {
    ledger = initializeCleanCandidateActivityLedger({
      repositoryRoot,
      policy: candidateActivityBudgetPolicy(loadedCandidate),
      recordedAt: (options.now ?? (() => new Date()))(),
    })
    return ledger.snapshot()
  } catch (error) {
    throw candidateActivityError(error)
  } finally {
    if (ledger) {
      try {
        ledger.close()
      } catch (error) {
        throw candidateActivityError(error)
      }
    }
  }
}

export async function reconcileA4PreLedgerHistoricalUsage(options: {
  evidenceSha256: string
  usage: CandidateActivityUsage
  repositoryRoot?: string
  now?: () => Date
}): Promise<CandidateActivityBudgetSnapshot> {
  const loadedCandidate = await loadPiModelCandidate()
  const repositoryRoot =
    options.repositoryRoot ?? defaultA4RepositoryRoot()
  let ledger: CandidateActivityLedger | undefined
  try {
    ledger = reconcileCandidateActivityLedgerHistoricalUsage({
      repositoryRoot,
      policy: candidateActivityBudgetPolicy(loadedCandidate),
      recordedAt: (options.now ?? (() => new Date()))(),
      evidenceSha256: options.evidenceSha256,
      usage: options.usage,
    })
    return ledger.snapshot()
  } catch (error) {
    throw candidateActivityError(error)
  } finally {
    if (ledger) {
      try {
        ledger.close()
      } catch (error) {
        throw candidateActivityError(error)
      }
    }
  }
}

export async function reconcileA4PreLedgerHistoricalBilledAmount(
  options: {
    billedAmount: CandidateActivityHistoricalBilledAmount
    repositoryRoot?: string
    now?: () => Date
  },
): Promise<CandidateActivityBudgetSnapshot> {
  const loadedCandidate = await loadPiModelCandidate()
  const repositoryRoot =
    options.repositoryRoot ?? defaultA4RepositoryRoot()
  let ledger: CandidateActivityLedger | undefined
  try {
    ledger =
      reconcileCandidateActivityLedgerHistoricalBilledAmount({
        repositoryRoot,
        policy: candidateActivityBudgetPolicy(loadedCandidate),
        recordedAt: (options.now ?? (() => new Date()))(),
        billedAmount: options.billedAmount,
      })
    return ledger.snapshot()
  } catch (error) {
    throw candidateActivityError(error)
  } finally {
    if (ledger) {
      try {
        ledger.close()
      } catch (error) {
        throw candidateActivityError(error)
      }
    }
  }
}

function isWithinRoot(root: string, target: string): boolean {
  const difference = relative(root, target)
  return (
    difference === '' ||
    (difference !== '..' &&
      !difference.startsWith(`..${sep}`) &&
      !isAbsolute(difference))
  )
}

function checkedRealDirectory(path: string): string {
  try {
    const normalized = resolve(path)
    const stats = lstatSync(normalized)
    if (!stats.isDirectory() || stats.isSymbolicLink()) {
      throw new Error('invalid directory')
    }
    return realpathSync(normalized)
  } catch {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
}

function ensureEvidenceParent(
  repositoryRoot: string,
): { root: string; parent: string } {
  const root = checkedRealDirectory(repositoryRoot)
  let current = root
  for (const segment of [
    'artifacts',
    'country-outage-agent',
    'a4-model-certification',
  ]) {
    const next = resolve(current, segment)
    if (!isWithinRoot(root, next)) {
      throw new PiModelCertificationError(
        'candidate_artifact_write_failed',
      )
    }
    try {
      if (!existsSync(next)) {
        mkdirSync(next, { mode: 0o700 })
      }
      const stats = lstatSync(next)
      if (
        !stats.isDirectory() ||
        stats.isSymbolicLink() ||
        realpathSync(next) !== next
      ) {
        throw new Error('invalid evidence parent')
      }
    } catch {
      throw new PiModelCertificationError(
        'candidate_artifact_write_failed',
      )
    }
    current = next
  }
  return { root, parent: current }
}

function fsyncDirectory(path: string): void {
  const descriptor = openSync(
    path,
    constants.O_RDONLY | (constants.O_DIRECTORY ?? 0),
  )
  try {
    fsyncSync(descriptor)
  } finally {
    closeSync(descriptor)
  }
}

function writeExclusiveFile(
  path: string,
  content: string | Buffer,
): void {
  let descriptor: number | undefined
  try {
    descriptor = openSync(
      path,
      constants.O_CREAT |
        constants.O_EXCL |
        constants.O_WRONLY |
        (constants.O_NOFOLLOW ?? 0),
      0o600,
    )
    fchmodSync(descriptor, 0o600)
    writeFileSync(descriptor, content)
    fsyncSync(descriptor)
    closeSync(descriptor)
    descriptor = undefined
  } catch {
    if (descriptor !== undefined) closeSync(descriptor)
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
}

function verifyRunArtifactAgainstManifest(
  run: A4FullReportRunArtifacts,
  evidence: CandidateCertificationRunEvidence,
): void {
  if (
    run.runNumber !== evidence.runNumber ||
    run.document.artifactId !== evidence.artifactId ||
    run.document.reportContentSha256 !==
      evidence.reportContentSha256 ||
    sha256(serializedReportDocument(run.document)) !==
      evidence.artifacts.reportDocumentSha256 ||
    run.document.factSetId !== evidence.factSetId ||
    canonicalSha256(run.document.snapshot) !==
      evidence.snapshotSha256 ||
    run.evidenceInputSha256 !== evidence.evidenceInputSha256 ||
    run.reportAuditManifest.sha256 !==
      evidence.artifacts.reportAuditManifestSha256 ||
    sha256(run.reportAuditManifest.content) !==
      evidence.artifacts.reportAuditManifestSha256 ||
    run.piRunAudit.sha256 !==
      evidence.artifacts.piRunAuditSha256 ||
    sha256(run.piRunAudit.content) !==
      evidence.artifacts.piRunAuditSha256 ||
    run.markdown.sha256 !== evidence.artifacts.markdownSha256 ||
    run.pdf.sha256 !== evidence.artifacts.pdfSha256 ||
    sha256(run.markdown.content) !==
      evidence.artifacts.markdownSha256 ||
    sha256(run.pdf.content) !== evidence.artifacts.pdfSha256
  ) {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
}

function verifyScenarioArtifactAgainstManifest(
  run: A4ScenarioReportRunArtifacts,
  evidence: CandidateScenarioCertificationRunEvidence,
): void {
  if (
    run.scenarioId !== evidence.scenarioId ||
    run.document.artifactId !== evidence.artifactId ||
    run.document.reportContentSha256 !==
      evidence.reportContentSha256 ||
    sha256(serializedReportDocument(run.document)) !==
      evidence.artifacts.reportDocumentSha256 ||
    run.document.factSetId !== evidence.factSetId ||
    canonicalSha256(run.document.snapshot) !==
      evidence.snapshotSha256 ||
    run.evidenceInputSha256 !== evidence.evidenceInputSha256 ||
    run.reportAuditManifest.sha256 !==
      evidence.artifacts.reportAuditManifestSha256 ||
    sha256(run.reportAuditManifest.content) !==
      evidence.artifacts.reportAuditManifestSha256 ||
    run.piRunAudit.sha256 !==
      evidence.artifacts.piRunAuditSha256 ||
    sha256(run.piRunAudit.content) !==
      evidence.artifacts.piRunAuditSha256 ||
    run.markdown.sha256 !== evidence.artifacts.markdownSha256 ||
    run.pdf.sha256 !== evidence.artifacts.pdfSha256 ||
    sha256(run.markdown.content) !==
      evidence.artifacts.markdownSha256 ||
    sha256(run.pdf.content) !== evidence.artifacts.pdfSha256
  ) {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
}

function verifyPersistedRunFiles(
  runDirectory: string,
  run:
    | A4FullReportRunArtifacts
    | A4ScenarioReportRunArtifacts,
  evidence:
    | CandidateCertificationRunEvidence
    | CandidateScenarioCertificationRunEvidence,
): void {
  try {
    const actual = {
      reportDocumentSha256: sha256(
        readFileSync(
          resolve(runDirectory, 'report-document.json'),
        ),
      ),
      reportAuditManifestSha256: sha256(
        readFileSync(
          resolve(
            runDirectory,
            run.reportAuditManifest.filename,
          ),
        ),
      ),
      piRunAuditSha256: sha256(
        readFileSync(
          resolve(runDirectory, run.piRunAudit.filename),
        ),
      ),
      markdownSha256: sha256(
        readFileSync(resolve(runDirectory, 'report.md')),
      ),
      pdfSha256: sha256(
        readFileSync(resolve(runDirectory, 'report.pdf')),
      ),
    }
    if (
      actual.reportDocumentSha256 !==
        evidence.artifacts.reportDocumentSha256 ||
      actual.reportAuditManifestSha256 !==
        evidence.artifacts.reportAuditManifestSha256 ||
      actual.piRunAuditSha256 !==
        evidence.artifacts.piRunAuditSha256 ||
      actual.markdownSha256 !==
        evidence.artifacts.markdownSha256 ||
      actual.pdfSha256 !== evidence.artifacts.pdfSha256
    ) {
      throw new Error('persisted hash mismatch')
    }
  } catch {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
}

function persistA4CertificationArtifacts(
  manifest: PiModelCertificationManifest,
  runArtifacts: readonly [
    A4FullReportRunArtifacts,
    A4FullReportRunArtifacts,
  ],
  scenarioArtifacts: readonly A4ScenarioReportRunArtifacts[],
  repositoryRoot: string,
): string {
  if (
    !/^evidence:model-certification:[a-f0-9]{64}$/.test(
      manifest.evidenceId,
    ) ||
    manifest.provenance.certificationFixtureId !==
      A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID
  ) {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
  verifyRunArtifactAgainstManifest(runArtifacts[0], manifest.runs[0])
  verifyRunArtifactAgainstManifest(runArtifacts[1], manifest.runs[1])
  if (
    manifest.scenarioCoverage === undefined
      ? scenarioArtifacts.length !== 0
      : scenarioArtifacts.length !==
          manifest.scenarioCoverage.scenarios.length
  ) {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
  for (let index = 0; index < scenarioArtifacts.length; index += 1) {
    verifyScenarioArtifactAgainstManifest(
      scenarioArtifacts[index]!,
      manifest.scenarioCoverage!.scenarios[index]!,
    )
  }

  const { root, parent } = ensureEvidenceParent(repositoryRoot)
  const finalDirectory = resolve(parent, manifest.evidenceId)
  const tempDirectory = resolve(
    parent,
    `.tmp-${manifest.evidenceId.slice(-16)}-${process.pid}-${randomUUID()}`,
  )
  const lockPath = resolve(
    parent,
    `.${manifest.evidenceId}.lock`,
  )
  if (
    !isWithinRoot(root, finalDirectory) ||
    !isWithinRoot(root, tempDirectory) ||
    !isWithinRoot(root, lockPath) ||
    existsSync(finalDirectory)
  ) {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }

  let lockDescriptor: number | undefined
  let lockOwned = false
  let tempCreated = false
  let published = false
  try {
    lockDescriptor = openSync(
      lockPath,
      constants.O_CREAT |
        constants.O_EXCL |
        constants.O_WRONLY |
        (constants.O_NOFOLLOW ?? 0),
      0o600,
    )
    lockOwned = true
    fchmodSync(lockDescriptor, 0o600)
    fsyncSync(lockDescriptor)
    if (existsSync(finalDirectory)) {
      throw new PiModelCertificationError(
        'candidate_artifact_write_failed',
      )
    }

    mkdirSync(tempDirectory, { mode: 0o700 })
    chmodSync(tempDirectory, 0o700)
    tempCreated = true
    for (const run of runArtifacts) {
      const runDirectory = resolve(
        tempDirectory,
        `run-${run.runNumber}`,
      )
      mkdirSync(runDirectory, { mode: 0o700 })
      chmodSync(runDirectory, 0o700)
      writeExclusiveFile(
        resolve(runDirectory, 'report-document.json'),
        serializedReportDocument(run.document),
      )
      writeExclusiveFile(
        resolve(
          runDirectory,
          run.reportAuditManifest.filename,
        ),
        run.reportAuditManifest.content,
      )
      writeExclusiveFile(
        resolve(runDirectory, run.piRunAudit.filename),
        run.piRunAudit.content,
      )
      writeExclusiveFile(
        resolve(runDirectory, 'report.md'),
        run.markdown.content,
      )
      writeExclusiveFile(
        resolve(runDirectory, 'report.pdf'),
        run.pdf.content,
      )
      verifyPersistedRunFiles(
        runDirectory,
        run,
        manifest.runs[run.runNumber - 1]!,
      )
      fsyncDirectory(runDirectory)
    }
    for (let index = 0; index < scenarioArtifacts.length; index += 1) {
      const run = scenarioArtifacts[index]!
      const evidence = manifest.scenarioCoverage!.scenarios[index]!
      const runDirectory = resolve(
        tempDirectory,
        `scenario-${run.scenarioId}`,
      )
      mkdirSync(runDirectory, { mode: 0o700 })
      chmodSync(runDirectory, 0o700)
      writeExclusiveFile(
        resolve(runDirectory, 'CERTIFICATION-ONLY.txt'),
        '认证专用合成场景，不是 Domeye 事件事实，不得作为观测报告对外发布。\n',
      )
      writeExclusiveFile(
        resolve(runDirectory, 'report-document.json'),
        serializedReportDocument(run.document),
      )
      writeExclusiveFile(
        resolve(
          runDirectory,
          run.reportAuditManifest.filename,
        ),
        run.reportAuditManifest.content,
      )
      writeExclusiveFile(
        resolve(runDirectory, run.piRunAudit.filename),
        run.piRunAudit.content,
      )
      writeExclusiveFile(
        resolve(runDirectory, 'report.md'),
        run.markdown.content,
      )
      writeExclusiveFile(
        resolve(runDirectory, 'report.pdf'),
        run.pdf.content,
      )
      verifyPersistedRunFiles(
        runDirectory,
        run,
        evidence,
      )
      fsyncDirectory(runDirectory)
    }
    writeExclusiveFile(
      resolve(tempDirectory, 'manifest.json'),
      `${JSON.stringify(manifest, null, 2)}\n`,
    )
    fsyncDirectory(tempDirectory)
    if (existsSync(finalDirectory)) {
      throw new PiModelCertificationError(
        'candidate_artifact_write_failed',
      )
    }
    renameSync(tempDirectory, finalDirectory)
    tempCreated = false
    published = true
    fsyncDirectory(parent)
  } catch (error) {
    if (
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_artifact_write_failed'
    ) {
      throw error
    }
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  } finally {
    if (lockDescriptor !== undefined) closeSync(lockDescriptor)
    if (tempCreated) {
      rmSync(tempDirectory, { recursive: true, force: true })
    }
    // 只有本进程成功以 O_EXCL 创建锁后才能删除；并发方持有的锁绝不能被误删。
    if (lockOwned) rmSync(lockPath, { force: true })
    if (published) fsyncDirectory(parent)
  }

  const relativeDirectory = [
    'artifacts',
    'country-outage-agent',
    'a4-model-certification',
    manifest.evidenceId,
  ].join('/')
  if (
    realpathSync(finalDirectory) !== finalDirectory ||
    !isWithinRoot(root, realpathSync(finalDirectory))
  ) {
    throw new PiModelCertificationError(
      'candidate_artifact_write_failed',
    )
  }
  return relativeDirectory
}

export async function runA4ModelCandidateCertification(
  options: RunA4ModelCandidateCertificationOptions,
): Promise<A4ModelCandidateCertificationResult> {
  const dependencies = options.dependencies
  const executeScenarioSuite =
    dependencies === undefined ||
    dependencies.executeScenarioSuite === true
  const runnerIdentity: CandidateCertificationRunnerIdentity =
    dependencies === undefined
      ? 'country-outage-full-report-runner-v1'
      : 'country-outage-full-report-integration-test-v1'
  const loadedCandidate = await loadPiModelCandidate()
  const repositoryRoot =
    dependencies?.repositoryRoot ?? defaultA4RepositoryRoot()
  const certificationStartedAtDate = (
    dependencies?.now ?? (() => new Date())
  )()
  if (!Number.isFinite(certificationStartedAtDate.valueOf())) {
    throw new PiModelCertificationError(
      'candidate_price_attestation_invalid',
    )
  }
  let priceAttestation: VerifiedProviderPriceAttestation
  try {
    priceAttestation = loadCurrentProviderPriceAttestation({
      repositoryRoot,
      candidate: loadedCandidate,
      now: certificationStartedAtDate,
    })
    assertProviderPriceAttestationRunway(
      priceAttestation,
      certificationStartedAtDate,
    )
  } catch (error) {
    throw candidatePriceAttestationError(error)
  }
  const capturedRuns: A4FullReportRunArtifacts[] = []
  const capturedScenarioRuns: A4ScenarioReportRunArtifacts[] = []
  const scenarioEvidence: CandidateScenarioCertificationRunEvidence[] =
    []
  let fixture: A4IranModelCertificationFixture | undefined
  let activityLedger: CandidateActivityLedger | undefined
  try {
    activityLedger = openCandidateActivityLedger({
      repositoryRoot,
      policy: candidateActivityBudgetPolicy(loadedCandidate),
    })
    activityLedger.assertCertificationBudgetAvailable()
    if (executeScenarioSuite) {
      const snapshot = activityLedger.snapshot()
      if (
        snapshot.historicalUsageStatus !== 'resolved' ||
        !Number.isFinite(snapshot.committedCostCny) ||
        snapshot.committedCostCny +
          maximumA4ScenarioSuiteCertificationCostCny(
            loadedCandidate,
          ) >
          loadedCandidate.candidate.certification
            .budgetLimitCny
      ) {
        throw new CandidateActivityLedgerError(
          'activity_budget_preflight_failed',
        )
      }
    }
  } catch (error) {
    if (activityLedger) {
      try {
        activityLedger.close()
      } catch (closeError) {
        throw candidateActivityError(closeError)
      }
    }
    throw candidateActivityError(error)
  }
  const verifiedActivityLedger = activityLedger

  try {
    const baseManifest =
      await runPiModelCandidateCertificationInternal(
      {
        loadedCandidate,
        authPath: options.authPath,
        priceAttestation,
        certificationStartedAt:
          certificationStartedAtDate.toISOString(),
        ...(dependencies?.registryPath
          ? { registryPath: dependencies.registryPath }
          : {}),
        ...(dependencies?.runtimeFactory
          ? { runtimeFactory: dependencies.runtimeFactory }
          : {}),
        ...(dependencies?.responseModelAdapterInspector
          ? {
              responseModelAdapterInspector:
                dependencies.responseModelAdapterInspector,
            }
          : {}),
        ...(dependencies?.now ? { now: dependencies.now } : {}),
        runner: async ({ runNumber, binding }) => {
          fixture ??= loadA4IranModelCertificationFixture()
          const runFixture = fixture
          const now = dependencies?.now ?? (() => new Date())
          const rawClient =
            dependencies?.clientFactory?.({
              runNumber,
              fixture: runFixture,
            }) ??
            new DomeyeCountryOutageClient({
              baseUrl: options.domeyeApiBaseUrl,
              ...(options.domeyeApiTimeoutMs === undefined
                ? {}
                : { timeoutMs: options.domeyeApiTimeoutMs }),
            })
          const client = createA4PinnedClient(rawClient, runFixture)
          const dependencyRiskException =
            dependencies?.dependencyRiskException ??
            loadCountryOutageDependencyRiskException({
              now: now(),
            })
          const executeSingleReport = async (single: {
            activityRunNumber: CandidateActivityRunNumber
            client: CountryOutageReportDataSource
            expectedFixture: A4ExpectedCertificationFixture
            scenario?: A4CertificationScenarioDefinition
          }) => {
            const audits: FormalPiRunAuditRecord[] = []
            const narratorOptions: PiReportNarratorOptions = {
              client: single.client,
              model: binding.model,
              modelRuntime: binding.modelRuntime,
              modelSelection: binding.runSelection,
              dependencyRiskException,
              auditSink(record) {
                audits.push(structuredClone(record))
              },
              now,
              ...(dependencies?.sessionFactory
                ? { sessionFactory: dependencies.sessionFactory }
                : {}),
            }
            // 每份代表报告和每个认证场景都创建独立 narrator；其内部继续只使用
            // 新的内存 SessionManager，不共享自然语言历史。
            const narrator = new PiReportNarrator(narratorOptions)
            const compiler = new CountryOutageReportCompiler({
              client: single.client,
              narrator,
              now,
            })
            const reservation = withCandidateActivityError(() =>
              verifiedActivityLedger.reserve(
                single.activityRunNumber,
                now(),
              ),
            )
            let settled = false
            try {
              let compiled: CompiledCountryOutageReport
              try {
                compiled = await compiler.compileWithEvidence(
                  runFixture.eventReference,
                )
              } catch (error) {
                if (error instanceof ReportValidationError) {
                  // 只暴露固定阶段码；不得把校验详情、模型正文或 cause 写入
                  // CLI、活动账本或候选认证审计。
                  throw new PiModelCertificationError(
                    'candidate_report_validation_failed',
                  )
                }
                throw error
              }
              const { document } = compiled
              if (single.scenario === undefined) {
                assertA4Document(document, single.expectedFixture)
              } else {
                assertA4ScenarioDocument(
                  document,
                  runFixture,
                  single.scenario.id,
                )
              }
              const reportAuditManifest =
                createCountryOutageReportAuditManifestArtifact(
                  compiled,
                ).artifact
              const pdfRenderer =
                dependencies?.pdfRendererFactory?.({
                  runNumber: single.activityRunNumber,
                  ...(single.scenario === undefined
                    ? {}
                    : { scenarioId: single.scenario.id }),
                  fixture: runFixture,
                }) ??
                new CountryOutagePdfRenderer({
                  pythonExecutable: options.pythonExecutable,
                  fontPath: options.fontPath,
                  ...(options.pdfTimeoutMs === undefined
                    ? {}
                    : { timeoutMs: options.pdfTimeoutMs }),
                })
              const artifacts = await new CountryOutageArtifactBuilder(
                pdfRenderer,
              ).build(document)
              const built = buildA4RunnerResult(
                binding,
                single.expectedFixture,
                compiled,
                reportAuditManifest,
                artifacts,
                audits,
              )
              let validatedScenario:
                | CandidateScenarioCertificationRunEvidence
                | undefined
              if (single.scenario !== undefined) {
                validatedScenario = validateScenarioRunnerResult(
                  binding.candidate,
                  single.scenario,
                  built.result,
                )
              }
              const safeAudit = safeCandidateActivityAudit(
                audits,
                binding,
              )
              if (safeAudit.usage === undefined) {
                throw new PiModelCertificationError(
                  'candidate_internal_audit_invalid',
                )
              }
              withCandidateActivityError(() =>
                verifiedActivityLedger.settle(reservation, {
                  outcome: 'completed',
                  recordedAt: now(),
                  usage: safeAudit.usage!,
                }),
              )
              settled = true
              return { built, validatedScenario }
            } catch (error) {
              if (
                !settled &&
                !(
                  error instanceof PiModelCertificationError &&
                  error.code === 'candidate_activity_audit_failed'
                )
              ) {
                const safeAudit = safeCandidateActivityAudit(
                  audits,
                  binding,
                )
                withCandidateActivityError(() =>
                  verifiedActivityLedger.settle(reservation, {
                    outcome: 'rejected',
                    recordedAt: now(),
                    ...(safeAudit.formalRejectionCode === undefined
                      ? {
                          candidateRejectionCode:
                            safeCandidateActivityRejectionCode(
                              error,
                            ),
                        }
                      : {
                          formalRejectionCode:
                            safeAudit.formalRejectionCode,
                        }),
                    ...(safeAudit.usage === undefined
                      ? {}
                      : { usage: safeAudit.usage }),
                  }),
                )
              }
              throw error
            }
          }

          const representative = await executeSingleReport({
            activityRunNumber: runNumber,
            client,
            expectedFixture: runFixture,
          })
          capturedRuns.push({
            runNumber,
            ...representative.built.runArtifacts,
          })

          if (runNumber === 2 && executeScenarioSuite) {
            for (
              let index = 0;
              index < A4_CERTIFICATION_SCENARIOS.length;
              index += 1
            ) {
              const scenario = A4_CERTIFICATION_SCENARIOS[index]!
              const activityRunNumber = (index +
                3) as CandidateActivityRunNumber
              const scenarioRawClient =
                dependencies?.clientFactory?.({
                  runNumber: activityRunNumber,
                  scenarioId: scenario.id,
                  fixture: runFixture,
                }) ??
                new DomeyeCountryOutageClient({
                  baseUrl: options.domeyeApiBaseUrl,
                  ...(options.domeyeApiTimeoutMs === undefined
                    ? {}
                    : { timeoutMs: options.domeyeApiTimeoutMs }),
                })
              const pinnedScenarioBase = createA4PinnedClient(
                scenarioRawClient,
                runFixture,
              )
              const scenarioClient =
                createA4CertificationScenarioClient(
                  pinnedScenarioBase,
                  scenario.id,
                )
              const scenarioResult = await executeSingleReport({
                activityRunNumber,
                client: scenarioClient,
                expectedFixture: expectedA4ScenarioFixture(
                  runFixture,
                  scenario.id,
                ),
                scenario,
              })
              if (scenarioResult.validatedScenario === undefined) {
                throw new PiModelCertificationError(
                  'candidate_run_evidence_invalid',
                )
              }
              scenarioEvidence.push(
                scenarioResult.validatedScenario,
              )
              capturedScenarioRuns.push({
                scenarioId: scenario.id,
                ...scenarioResult.built.runArtifacts,
              })
            }
          }
          return representative.built.result
        },
      },
      runnerIdentity,
      A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID,
    )

    const manifest = executeScenarioSuite
      ? finalizeA4ScenarioSuiteManifest(
          baseManifest,
          loadedCandidate,
          scenarioEvidence,
        )
      : baseManifest

    if (
      capturedRuns.length !== 2 ||
      capturedRuns[0]?.runNumber !== 1 ||
      capturedRuns[1]?.runNumber !== 2 ||
      (executeScenarioSuite &&
        capturedScenarioRuns.length !==
          A4_CERTIFICATION_SCENARIOS.length)
    ) {
      throw new PiModelCertificationError(
        'candidate_run_evidence_invalid',
      )
    }
    const artifactDirectory = persistA4CertificationArtifacts(
      manifest,
      capturedRuns as [
        A4FullReportRunArtifacts,
        A4FullReportRunArtifacts,
      ],
      capturedScenarioRuns,
      repositoryRoot,
    )
    return Object.freeze({
      evidenceId: manifest.evidenceId,
      artifactDirectory,
      manifest,
    })
  } finally {
    withCandidateActivityError(() =>
      verifiedActivityLedger.close(),
    )
  }
}

function validRunEvidence(
  value: unknown,
  candidate: PiModelCandidate,
  runNumber: 1 | 2,
): value is CandidateCertificationRunEvidence {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'runtimeIdentity',
      'runNumber',
      'runEvidenceId',
      'completedAt',
      'observed',
      'artifactId',
      'reportContentSha256',
      'factSetId',
      'snapshotSha256',
      'evidenceInputSha256',
      'checks',
      'artifacts',
      'usage',
    ]) ||
    value.runtimeIdentity !== 'candidate' ||
    value.runNumber !== runNumber ||
    typeof value.runEvidenceId !== 'string' ||
    !/^candidate-run:[a-f0-9]{64}$/.test(value.runEvidenceId) ||
    !isIsoTimestamp(value.completedAt) ||
    !isRecord(value.observed) ||
    !exactKeys(value.observed, [
      'provider',
      'model',
      'responseModel',
    ]) ||
    value.observed.provider !== candidate.provider ||
    value.observed.model !== candidate.model ||
    value.observed.responseModel !==
      candidate.expectedResponseModel ||
    typeof value.artifactId !== 'string' ||
    !SAFE_ID.test(value.artifactId) ||
    typeof value.reportContentSha256 !== 'string' ||
    !SHA256.test(value.reportContentSha256) ||
    typeof value.factSetId !== 'string' ||
    !SAFE_ID.test(value.factSetId) ||
    typeof value.snapshotSha256 !== 'string' ||
    !SHA256.test(value.snapshotSha256) ||
    typeof value.evidenceInputSha256 !== 'string' ||
    !SHA256.test(value.evidenceInputSha256) ||
    !isRecord(value.checks) ||
    !exactKeys(value.checks, [
      'reportComplete',
      'validator',
      'markdown',
      'pdf',
      'providerRequestCount',
      'providerRetryAttempts',
      'structuredOutput',
    ]) ||
    value.checks.reportComplete !== true ||
    value.checks.validator !== true ||
    value.checks.markdown !== true ||
    value.checks.pdf !== true ||
    !finiteNonnegativeInteger(value.checks.providerRequestCount) ||
    value.checks.providerRequestCount < 1 ||
    value.checks.providerRequestCount >
      candidate.execution.maximumProviderRequestCount ||
    value.checks.providerRetryAttempts !== 0 ||
    !isRecord(value.checks.structuredOutput) ||
    !exactKeys(value.checks.structuredOutput, [
      'mechanism',
      'payloadPreparedCount',
    ]) ||
    value.checks.structuredOutput.mechanism !==
      'deepseek-json-object-no-tools-v2' ||
    !finiteNonnegativeInteger(
      value.checks.structuredOutput.payloadPreparedCount,
    ) ||
    value.checks.structuredOutput.payloadPreparedCount !==
      value.checks.providerRequestCount ||
    !isRecord(value.artifacts) ||
    !exactKeys(value.artifacts, [
      'reportDocumentSha256',
      'reportAuditManifestSha256',
      'piRunAuditSha256',
      'markdownSha256',
      'pdfSha256',
    ]) ||
    typeof value.artifacts.reportDocumentSha256 !== 'string' ||
    !SHA256.test(value.artifacts.reportDocumentSha256) ||
    typeof value.artifacts.reportAuditManifestSha256 !== 'string' ||
    !SHA256.test(value.artifacts.reportAuditManifestSha256) ||
    typeof value.artifacts.piRunAuditSha256 !== 'string' ||
    !SHA256.test(value.artifacts.piRunAuditSha256) ||
    typeof value.artifacts.markdownSha256 !== 'string' ||
    !SHA256.test(value.artifacts.markdownSha256) ||
    typeof value.artifacts.pdfSha256 !== 'string' ||
    !SHA256.test(value.artifacts.pdfSha256) ||
    !isRecord(value.usage) ||
    !exactKeys(value.usage, [
      'inputTokens',
      'outputTokens',
      'cacheReadTokens',
      'cacheWriteTokens',
      'conservativeCostUsd',
      'conservativeCostCny',
    ]) ||
    !finiteNonnegativeInteger(value.usage.inputTokens) ||
    !finiteNonnegativeInteger(value.usage.outputTokens) ||
    !finiteNonnegativeInteger(value.usage.cacheReadTokens) ||
    !finiteNonnegativeInteger(value.usage.cacheWriteTokens) ||
    typeof value.usage.conservativeCostUsd !== 'number' ||
    !Number.isFinite(value.usage.conservativeCostUsd) ||
    value.usage.conservativeCostUsd < 0 ||
    typeof value.usage.conservativeCostCny !== 'number' ||
    !Number.isFinite(value.usage.conservativeCostCny) ||
    value.usage.conservativeCostCny < 0
  ) {
    return false
  }
  const usage = value.usage as unknown as
    CandidateCertificationRunnerResult['usage']
  const aggregateInputTokens =
    usage.inputTokens +
    usage.cacheReadTokens +
    usage.cacheWriteTokens
  if (
    aggregateInputTokens >
      candidate.execution.maximumInputTokens *
        value.checks.providerRequestCount ||
    usage.outputTokens >
      candidate.execution.maximumOutputTokens *
        value.checks.providerRequestCount
  ) {
    return false
  }
  const expectedCost = conservativeRunCost(candidate, usage)
  if (
    Math.abs(
      expectedCost.usd - value.usage.conservativeCostUsd,
    ) >
      Number.EPSILON * 16 ||
    Math.abs(
      expectedCost.cny - value.usage.conservativeCostCny,
    ) >
      Number.EPSILON * 16
  ) {
    return false
  }
  const { runEvidenceId, ...body } = value
  return runEvidenceId === `candidate-run:${canonicalSha256(body)}`
}

function validScenarioEvidence(
  value: unknown,
  candidate: PiModelCandidate,
  expected: A4CertificationScenarioDefinition,
): value is CandidateScenarioCertificationRunEvidence {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'scenarioId',
      'purpose',
      'certificationOnly',
      'synthetic',
      'scenarioEvidenceId',
      'completedAt',
      'observed',
      'artifactId',
      'reportContentSha256',
      'factSetId',
      'snapshotSha256',
      'evidenceInputSha256',
      'checks',
      'artifacts',
      'usage',
    ]) ||
    value.scenarioId !== expected.id ||
    value.purpose !== expected.purpose ||
    value.certificationOnly !== true ||
    value.synthetic !== true ||
    typeof value.scenarioEvidenceId !== 'string' ||
    !/^candidate-scenario:[a-f0-9]{64}$/.test(
      value.scenarioEvidenceId,
    )
  ) {
    return false
  }
  // 复用代表报告的所有身份、调用次数、结构化输出、制品哈希、token
  // 上限与保守费用核验。合成场景没有 runNumber，因此只在这个临时
  // 结构中使用 2，生成出的临时 runEvidenceId 不进入持久化证据。
  const runBody = {
    runtimeIdentity: 'candidate' as const,
    runNumber: 2 as const,
    completedAt: value.completedAt,
    observed: value.observed,
    artifactId: value.artifactId,
    reportContentSha256: value.reportContentSha256,
    factSetId: value.factSetId,
    snapshotSha256: value.snapshotSha256,
    evidenceInputSha256: value.evidenceInputSha256,
    checks: value.checks,
    artifacts: value.artifacts,
    usage: value.usage,
  }
  if (
    !validRunEvidence(
      {
        ...runBody,
        runEvidenceId:
          `candidate-run:${canonicalSha256(runBody)}`,
      },
      candidate,
      2,
    )
  ) {
    return false
  }
  const { scenarioEvidenceId, ...body } = value
  return (
    scenarioEvidenceId ===
    `candidate-scenario:${canonicalSha256(body)}`
  )
}

function validScenarioCoverage(
  value: unknown,
  candidate: PiModelCandidate,
  runs: readonly [
    CandidateCertificationRunEvidence,
    CandidateCertificationRunEvidence,
  ],
): value is A4ModelScenarioCoverage {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'scenarioSetId',
      'certifiedInputScope',
      'representativeRepeatRunEvidenceIds',
      'boundaryQuestionEngine',
      'scenarios',
    ]) ||
    value.scenarioSetId !== A4_CERTIFIED_SCENARIO_SET_ID ||
    value.certifiedInputScope !== A4_CERTIFIED_INPUT_SCOPE ||
    value.boundaryQuestionEngine !==
      'deterministic-country-outage-question-engine-v1' ||
    !Array.isArray(value.representativeRepeatRunEvidenceIds) ||
    value.representativeRepeatRunEvidenceIds.length !== 2 ||
    value.representativeRepeatRunEvidenceIds[0] !==
      runs[0].runEvidenceId ||
    value.representativeRepeatRunEvidenceIds[1] !==
      runs[1].runEvidenceId ||
    !Array.isArray(value.scenarios) ||
    value.scenarios.length !== A4_CERTIFICATION_SCENARIOS.length ||
    value.scenarios.some(
      (scenario, index) =>
        !validScenarioEvidence(
          scenario,
          candidate,
          A4_CERTIFICATION_SCENARIOS[index]!,
        ),
    )
  ) {
    return false
  }
  const scenarios =
    value.scenarios as readonly CandidateScenarioCertificationRunEvidence[]
  // 三个场景必须真的形成不同报告与事实集合，不能只把同一份伊朗报告
  // 换一个 scenarioId 后重复登记。snapshotSha256 可以相同，因为场景
  // 都从同一固定窗口派生，差异由 factSet/reportContent 证明。
  const reportHashes = [
    runs[0].reportContentSha256,
    ...scenarios.map((scenario) => scenario.reportContentSha256),
  ]
  const factSetIds = [
    runs[0].factSetId,
    ...scenarios.map((scenario) => scenario.factSetId),
  ]
  return (
    new Set(reportHashes).size === reportHashes.length &&
    new Set(factSetIds).size === factSetIds.length
  )
}

function validAliasCertificationProfile(
  value: unknown,
  completedAt: string,
  scenarioCoverage: A4ModelScenarioCoverage,
): value is A4ModelAliasCertificationProfile {
  const expectedValidUntil = new Date(
    Date.parse(completedAt) +
      A4_MODEL_ALIAS_CERTIFICATION_VALIDITY_MS,
  ).toISOString()
  return (
    isRecord(value) &&
    exactKeys(value, [
      'modelRevisionKind',
      'immutableRevisionAvailable',
      'limitation',
      'certificationValidUntil',
      'certifiedScenarioSetId',
      'certifiedInputScope',
    ]) &&
    value.modelRevisionKind === 'mutable_alias' &&
    value.immutableRevisionAvailable === false &&
    value.limitation === MUTABLE_MODEL_ALIAS_LIMITATION_ZH &&
    value.certificationValidUntil === expectedValidUntil &&
    value.certifiedScenarioSetId ===
      scenarioCoverage.scenarioSetId &&
    value.certifiedInputScope ===
      scenarioCoverage.certifiedInputScope
  )
}

export function parsePiModelCertificationManifest(
  value: unknown,
  loadedCandidate: LoadedPiModelCandidate,
): PiModelCertificationManifest {
  const candidate = parsePiModelCandidate(
    loadedCandidate.candidate,
  )
  const hasScenarioCoverage =
    isRecord(value) &&
    Object.prototype.hasOwnProperty.call(
      value,
      'scenarioCoverage',
    )
  const hasCertificationProfile =
    isRecord(value) &&
    Object.prototype.hasOwnProperty.call(
      value,
      'certificationProfile',
    )
  if (
    !isRecord(value) ||
    hasScenarioCoverage !== hasCertificationProfile ||
    !exactKeys(value, [
      'schemaVersion',
      'status',
      'runtimeIdentity',
      'candidateId',
      'candidateResourceSha256',
      'evidenceId',
      'certificationStartedAt',
      'completedAt',
      'provenance',
      'targetRegistry',
      'policy',
      'budget',
      'factEquivalence',
      'runs',
      ...(hasScenarioCoverage
        ? ['scenarioCoverage', 'certificationProfile']
        : []),
    ]) ||
    value.schemaVersion !==
      COUNTRY_OUTAGE_PI_MODEL_CERTIFICATION_MANIFEST_SCHEMA ||
    value.status !== 'passed' ||
    value.runtimeIdentity !== 'candidate' ||
    value.candidateId !== candidate.candidateId ||
    value.candidateResourceSha256 !==
      loadedCandidate.resourceSha256 ||
    typeof value.evidenceId !== 'string' ||
    !/^evidence:model-certification:[a-f0-9]{64}$/.test(
      value.evidenceId,
    ) ||
    !isIsoTimestamp(value.certificationStartedAt) ||
    !isIsoTimestamp(value.completedAt) ||
    Date.parse(value.completedAt) <
      Date.parse(value.certificationStartedAt) ||
    !isRecord(value.provenance) ||
    !exactKeys(value.provenance, [
      'runnerIdentity',
      'promotable',
      'certificationFixtureId',
    ]) ||
    ![
      'candidate-framework-test-runner-v1',
      'country-outage-full-report-integration-test-v1',
      'country-outage-full-report-runner-v1',
    ].includes(value.provenance.runnerIdentity as string) ||
    typeof value.provenance.promotable !== 'boolean' ||
    value.provenance.promotable !==
      (value.provenance.runnerIdentity ===
        'country-outage-full-report-runner-v1') ||
    value.provenance.certificationFixtureId !==
      (value.provenance.runnerIdentity ===
      'candidate-framework-test-runner-v1'
        ? null
        : A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID) ||
    !isRecord(value.targetRegistry) ||
    !exactKeys(value.targetRegistry, [
      'registryVersionBefore',
      'sha256Before',
    ]) ||
    typeof value.targetRegistry.registryVersionBefore !== 'string' ||
    !SAFE_ID.test(value.targetRegistry.registryVersionBefore) ||
    typeof value.targetRegistry.sha256Before !== 'string' ||
    !SHA256.test(value.targetRegistry.sha256Before) ||
    !isRecord(value.policy) ||
    !exactKeys(value.policy, [
      'piVersion',
      'providerRetryAttempts',
      'maximumProviderRequestCount',
      'maximumOutputTokens',
      'requiredIndependentReportRuns',
      'responseModelAdapterSourceSha256',
      'priceAttestation',
    ]) ||
    value.policy.piVersion !== FORMAL_PI_VERSION ||
    value.policy.providerRetryAttempts !== 0 ||
    value.policy.maximumProviderRequestCount !==
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderRequestsPerReport ||
    value.policy.maximumOutputTokens !== 16_384 ||
    value.policy.requiredIndependentReportRuns !== 2 ||
    typeof value.policy.responseModelAdapterSourceSha256 !== 'string' ||
    !SHA256.test(
      value.policy.responseModelAdapterSourceSha256,
    ) ||
    (value.policy.priceAttestation !== null &&
      !isRecord(value.policy.priceAttestation)) ||
    !isRecord(value.budget) ||
    !exactKeys(value.budget, [
      'limitCny',
      'conservativeCnyPerUsd',
      'maximumCertificationCostCny',
      'actualCertificationCostCny',
    ]) ||
    value.budget.limitCny !== 20 ||
    value.budget.conservativeCnyPerUsd !== 8 ||
    typeof value.budget.maximumCertificationCostCny !== 'number' ||
    value.budget.maximumCertificationCostCny !==
      (hasScenarioCoverage
        ? maximumA4ScenarioSuiteCertificationCostCny(
            loadedCandidate,
          )
        : maximumCertificationCostCny(candidate)) ||
    typeof value.budget.actualCertificationCostCny !== 'number' ||
    !Number.isFinite(value.budget.actualCertificationCostCny) ||
    value.budget.actualCertificationCostCny < 0 ||
    value.budget.actualCertificationCostCny >
      candidate.certification.budgetLimitCny ||
    !isRecord(value.factEquivalence) ||
    !exactKeys(value.factEquivalence, [
      'passed',
      'factSetId',
      'snapshotSha256',
      'evidenceInputSha256',
    ]) ||
    value.factEquivalence.passed !== true ||
    typeof value.factEquivalence.factSetId !== 'string' ||
    !SAFE_ID.test(value.factEquivalence.factSetId) ||
    typeof value.factEquivalence.snapshotSha256 !== 'string' ||
    !SHA256.test(value.factEquivalence.snapshotSha256) ||
    typeof value.factEquivalence.evidenceInputSha256 !== 'string' ||
    !SHA256.test(value.factEquivalence.evidenceInputSha256) ||
    !Array.isArray(value.runs) ||
    value.runs.length !== 2 ||
    !validRunEvidence(value.runs[0], candidate, 1) ||
    !validRunEvidence(value.runs[1], candidate, 2) ||
    value.runs[0].factSetId !==
      value.factEquivalence.factSetId ||
    value.runs[1].factSetId !==
      value.factEquivalence.factSetId ||
    value.runs[0].snapshotSha256 !==
      value.factEquivalence.snapshotSha256 ||
    value.runs[1].snapshotSha256 !==
      value.factEquivalence.snapshotSha256 ||
    value.runs[0].evidenceInputSha256 !==
      value.factEquivalence.evidenceInputSha256 ||
    value.runs[1].evidenceInputSha256 !==
      value.factEquivalence.evidenceInputSha256
  ) {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  const runs = value.runs as unknown as readonly [
    CandidateCertificationRunEvidence,
    CandidateCertificationRunEvidence,
  ]
  let scenarioCoverage: A4ModelScenarioCoverage | undefined
  let certificationProfile:
    | A4ModelAliasCertificationProfile
    | undefined
  if (
    value.provenance.runnerIdentity ===
      'country-outage-full-report-runner-v1' &&
    !hasScenarioCoverage
  ) {
    // 正式可晋级证书必须覆盖代表事件复跑、能力缺失、相反方向和
    // 非终态；旧的双报告证书只能保留为历史证据，不能晋级。
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  if (hasScenarioCoverage) {
    if (
      value.provenance.runnerIdentity ===
        'candidate-framework-test-runner-v1' ||
      !validScenarioCoverage(
        value.scenarioCoverage,
        candidate,
        runs,
      )
    ) {
      throw new PiModelCertificationError(
        'certification_manifest_invalid',
      )
    }
    scenarioCoverage = value.scenarioCoverage
    if (
      !validAliasCertificationProfile(
        value.certificationProfile,
        value.completedAt,
        scenarioCoverage,
      )
    ) {
      throw new PiModelCertificationError(
        'certification_manifest_invalid',
      )
    }
    certificationProfile = value.certificationProfile
  }
  let priceAttestation: VerifiedProviderPriceAttestation | null
  try {
    priceAttestation =
      value.policy.priceAttestation === null
        ? null
        : assertVerifiedProviderPriceAttestation(
            value.policy.priceAttestation,
            loadedCandidate,
          )
  } catch {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  const frameworkManifest =
    value.provenance.runnerIdentity ===
    'candidate-framework-test-runner-v1'
  const certificationStartedAt =
    value.certificationStartedAt as string
  const completedAt = value.completedAt as string
  if (
    (frameworkManifest && priceAttestation !== null) ||
    (!frameworkManifest && priceAttestation === null)
  ) {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  if (
    priceAttestation !== null &&
    (Date.parse(priceAttestation.observedAt) >
      Date.parse(certificationStartedAt) ||
      Date.parse(certificationStartedAt) >=
        Date.parse(priceAttestation.expiresAt) ||
      Date.parse(completedAt) >=
        Date.parse(priceAttestation.expiresAt) ||
      value.runs.some(
        (run) =>
          Date.parse(run.completedAt) <
            Date.parse(certificationStartedAt) ||
          Date.parse(run.completedAt) >
            Date.parse(completedAt) ||
          Date.parse(run.completedAt) >=
            Date.parse(priceAttestation!.expiresAt),
      ) ||
      (scenarioCoverage?.scenarios.some(
        (scenario) =>
          Date.parse(scenario.completedAt) <
            Date.parse(certificationStartedAt) ||
          Date.parse(scenario.completedAt) >
            Date.parse(completedAt) ||
          Date.parse(scenario.completedAt) >=
            Date.parse(priceAttestation!.expiresAt),
      ) ??
        false))
  ) {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  const totalCostCny = value.runs.reduce(
    (sum, run) => sum + run.usage.conservativeCostCny,
    0,
  ) +
    (scenarioCoverage?.scenarios.reduce(
      (sum, scenario) =>
        sum + scenario.usage.conservativeCostCny,
      0,
    ) ?? 0)
  if (
    Math.abs(
      totalCostCny - value.budget.actualCertificationCostCny,
    ) > Number.EPSILON * 16
  ) {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  const evidenceBody = {
    candidateId: candidate.candidateId,
    candidateResourceSha256: loadedCandidate.resourceSha256,
    certificationStartedAt: value.certificationStartedAt,
    completedAt: value.completedAt,
    registrySha256Before: value.targetRegistry.sha256Before,
    responseModelAdapterSourceSha256:
      value.policy.responseModelAdapterSourceSha256,
    priceAttestationId:
      priceAttestation?.attestationId ?? null,
    priceAttestationResourceSha256:
      priceAttestation?.resourceSha256 ?? null,
    priceEvidenceSha256:
      priceAttestation?.evidenceSha256 ?? null,
    runnerIdentity: value.provenance.runnerIdentity,
    certificationFixtureId:
      value.provenance.certificationFixtureId,
    runs: value.runs.map((run) => run.runEvidenceId),
    ...(scenarioCoverage && certificationProfile
      ? {
          scenarioSetId: scenarioCoverage.scenarioSetId,
          certifiedInputScope:
            scenarioCoverage.certifiedInputScope,
          boundaryQuestionEngine:
            scenarioCoverage.boundaryQuestionEngine,
          scenarioRuns: scenarioCoverage.scenarios.map(
            (scenario) => scenario.scenarioEvidenceId,
          ),
          modelRevisionKind:
            certificationProfile.modelRevisionKind,
          certificationValidUntil:
            certificationProfile.certificationValidUntil,
        }
      : {}),
  }
  if (
    value.evidenceId !==
    `evidence:model-certification:${canonicalSha256(evidenceBody)}`
  ) {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  return value as unknown as PiModelCertificationManifest
}

function safeRegistryVersion(value: string): string {
  const normalized = value.trim()
  if (!SAFE_ID.test(normalized)) {
    throw new PiModelCertificationError(
      'certification_manifest_invalid',
    )
  }
  return normalized
}

export interface PromotePiModelCandidateOptions {
  loadedCandidate: LoadedPiModelCandidate
  manifest: unknown
  newRegistryVersion: string
  registryPath?: string
  responseModelAdapterInspector?: CandidateResponseModelAdapterInspector
  now?: () => Date
}

export function promotePiModelCandidate(
  options: PromotePiModelCandidateOptions,
): {
  registry: CertifiedPiModelRegistry
  registrySha256: string
} {
  const registryPath = resolve(
    options.registryPath ?? defaultRegistryPath(),
  )
  const manifest = parsePiModelCertificationManifest(
    options.manifest,
    options.loadedCandidate,
  )
  if (
    manifest.provenance.runnerIdentity !==
      'country-outage-full-report-runner-v1' ||
    manifest.provenance.promotable !== true ||
    manifest.provenance.certificationFixtureId !==
      A4_IRAN_MODEL_CERTIFICATION_FIXTURE_ID ||
    manifest.scenarioCoverage === undefined ||
    manifest.certificationProfile === undefined
  ) {
    throw new PiModelCertificationError(
      'certification_provenance_untrusted',
    )
  }
  const promotionTime = (options.now ?? (() => new Date()))()
  if (
    !Number.isFinite(promotionTime.valueOf()) ||
    promotionTime.valueOf() >=
      Date.parse(
        manifest.certificationProfile.certificationValidUntil,
      )
  ) {
    throw new PiModelCertificationError(
      'certification_provenance_untrusted',
    )
  }
  const before = registrySnapshot(registryPath)
  const adapterInspectorInjected =
    options.responseModelAdapterInspector !== undefined
  const adapterInspection = (
    options.responseModelAdapterInspector ??
    defaultResponseModelAdapterInspector
  )()
  const approvedAdapterSources =
    options.loadedCandidate.candidate.adapterRequirement
      .approvedSameNameSourceSha256 as readonly string[]
  if (
    adapterInspection.sameNamePreserved !== true ||
    adapterInspection.sourceSha256 !==
      manifest.policy.responseModelAdapterSourceSha256 ||
    (!adapterInspectorInjected &&
      !approvedAdapterSources.includes(
        adapterInspection.sourceSha256,
      ))
  ) {
    throw new PiModelCertificationError(
      'candidate_response_model_adapter_unsupported',
    )
  }
  if (
    before.sha256 !== manifest.targetRegistry.sha256Before ||
    before.registry.registryVersion !==
      manifest.targetRegistry.registryVersionBefore
  ) {
    throw new PiModelCertificationError(
      'certification_registry_changed',
    )
  }
  const candidate = parsePiModelCandidate(
    options.loadedCandidate.candidate,
  )
  const existingProfile = before.registry.profiles.find(
    (profile) => profile.id === candidate.candidateId,
  )
  const nextRegistryVersion = safeRegistryVersion(
    options.newRegistryVersion,
  )
  if (
    existingProfile !== undefined &&
    (nextRegistryVersion === before.registry.registryVersion ||
      existingProfile.status !== 'certified' ||
      existingProfile.provider !== candidate.provider ||
      existingProfile.model !== candidate.model ||
      existingProfile.modelVersion !== candidate.modelVersion ||
      existingProfile.expectedResponseModel !==
        candidate.expectedResponseModel ||
      existingProfile.thinkingLevel !== candidate.thinkingLevel ||
      existingProfile.piVersion !== candidate.piVersion ||
      existingProfile.modelRevisionKind !== 'mutable_alias' ||
      existingProfile.immutableRevisionAvailable !== false ||
      existingProfile.certifiedScenarioSetId !==
        manifest.certificationProfile.certifiedScenarioSetId ||
      existingProfile.certifiedInputScope !==
        manifest.certificationProfile.certifiedInputScope ||
      existingProfile.certificationEvidenceId === manifest.evidenceId ||
      Date.parse(manifest.completedAt) <=
        Date.parse(existingProfile.certifiedAt) ||
      Date.parse(
        manifest.certificationProfile.certificationValidUntil,
      ) <= Date.parse(existingProfile.certificationValidUntil))
  ) {
    throw new PiModelCertificationError(
      'certification_promotion_conflict',
    )
  }
  const certifiedProfile = {
    id: candidate.candidateId,
    status: 'certified' as const,
    provider: candidate.provider,
    model: candidate.model,
    modelVersion: candidate.modelVersion,
    expectedResponseModel: candidate.expectedResponseModel,
    thinkingLevel: candidate.thinkingLevel,
    piVersion: candidate.piVersion,
    certificationEvidenceId: manifest.evidenceId,
    certifiedAt: manifest.completedAt,
    modelRevisionKind:
      manifest.certificationProfile.modelRevisionKind,
    immutableRevisionAvailable:
      manifest.certificationProfile.immutableRevisionAvailable,
    limitation: manifest.certificationProfile.limitation,
    certificationValidUntil:
      manifest.certificationProfile.certificationValidUntil,
    certifiedScenarioSetId:
      manifest.certificationProfile.certifiedScenarioSetId,
    certifiedInputScope:
      manifest.certificationProfile.certifiedInputScope,
  }
  const nextValue = {
    schemaVersion: before.registry.schemaVersion,
    registryVersion: nextRegistryVersion,
    status: 'frozen',
    profiles:
      existingProfile === undefined
        ? [...before.registry.profiles, certifiedProfile]
        : before.registry.profiles.map((profile) =>
            profile.id === candidate.candidateId
              ? certifiedProfile
              : profile,
          ),
  }
  const nextRegistry = parseCertifiedPiModelRegistry(nextValue)
  const text = `${JSON.stringify(nextValue, null, 2)}\n`
  const tempPath = resolve(
    dirname(registryPath),
    `.${basename(registryPath)}.${process.pid}.${randomUUID()}.tmp`,
  )
  let descriptor: number | undefined
  try {
    descriptor = openSync(
      tempPath,
      constants.O_CREAT |
        constants.O_EXCL |
        constants.O_WRONLY |
        (constants.O_NOFOLLOW ?? 0),
      0o644,
    )
    writeFileSync(descriptor, text, 'utf8')
    fsyncSync(descriptor)
    closeSync(descriptor)
    descriptor = undefined
    // 在最终 rename 前重新核验，避免运行间隙覆盖并发修改。
    if (registrySnapshot(registryPath).sha256 !== before.sha256) {
      throw new PiModelCertificationError(
        'certification_registry_changed',
      )
    }
    renameSync(tempPath, registryPath)
    const directoryDescriptor = openSync(
      dirname(registryPath),
      constants.O_RDONLY,
    )
    try {
      fsyncSync(directoryDescriptor)
    } finally {
      closeSync(directoryDescriptor)
    }
  } catch (error) {
    if (descriptor !== undefined) closeSync(descriptor)
    rmSync(tempPath, { force: true })
    if (error instanceof PiModelCertificationError) throw error
    throw new PiModelCertificationError(
      'certification_promotion_failed',
    )
  }
  return {
    registry: nextRegistry,
    registrySha256: sha256(text),
  }
}
