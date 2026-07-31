import { createHash } from 'node:crypto'
import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
  readdirSync,
  realpathSync,
} from 'node:fs'
import { dirname, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  loadPiModelCandidate,
  parsePiModelCertificationManifest,
  PiModelCertificationError,
  promotePiModelCandidate,
  type CandidateResponseModelAdapterInspector,
  type CandidateScenarioCertificationRunEvidence,
  type PiModelCertificationManifest,
} from './model-certification.js'
import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from '../formal-runtime-limits.js'
import {
  COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_SCHEMA_VERSION,
} from '../report/audit-manifest.js'
import { COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION } from '../report/draft-validator.js'
import {
  COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
  COUNTRY_OUTAGE_LANGUAGE_SLOT_IDS,
} from '../report/model-language-plan.js'
import {
  canonicalJsonSha256,
  canonicalJsonStringify,
} from '../shared/deterministic-json.js'
import {
  computeCountryOutageSkillBundleSha256,
  COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
} from './country-outage-skill-bundle.js'
import {
  A4_CERTIFICATION_SCENARIO_IDS,
  type A4CertificationScenarioId,
} from './model-certification-scenarios.js'
import { FORMAL_PI_NARRATION_MODE } from './formal-run-audit.js'

const EVIDENCE_ID =
  /^evidence:model-certification:[a-f0-9]{64}$/
const MANIFEST_MAX_BYTES = 1024 * 1024
const JSON_ARTIFACT_MAX_BYTES = 4 * 1024 * 1024
const MARKDOWN_MAX_BYTES = 2 * 1024 * 1024
const PDF_MAX_BYTES = 32 * 1024 * 1024
const CERTIFICATION_ONLY_MARKER_MAX_BYTES = 1024
const CERTIFICATION_ONLY_MARKER =
  '认证专用合成场景，不是 Domeye 事件事实，不得作为观测报告对外发布。\n'
const EVIDENCE_PARENT_SEGMENTS = [
  'artifacts',
  'country-outage-agent',
  'a4-model-certification',
] as const
const RUN_FILES = [
  'audit-manifest.json',
  'pi-run-audit.json',
  'report-document.json',
  'report.md',
  'report.pdf',
] as const
const SCENARIO_FILES = [
  'CERTIFICATION-ONLY.txt',
  'audit-manifest.json',
  'pi-run-audit.json',
  'report-document.json',
  'report.md',
  'report.pdf',
] as const

type PersistedCertificationArtifactEvidence =
  | PiModelCertificationManifest['runs'][number]
  | CandidateScenarioCertificationRunEvidence

function scenarioDirectoryName(
  scenarioId: A4CertificationScenarioId,
): `scenario-${A4CertificationScenarioId}` {
  return `scenario-${scenarioId}`
}

function sha256(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex')
}

function normalizedEventReferenceForAudit(reference: string): string {
  return reference.replace(' ', '+')
}

function isWithinRoot(root: string, target: string): boolean {
  const difference = relative(root, target)
  return (
    difference === '' ||
    (difference !== '..' &&
      !difference.startsWith(`..${sep}`) &&
      !difference.startsWith('../') &&
      !difference.startsWith('..\\'))
  )
}

function invalidEvidence(): never {
  throw new PiModelCertificationError(
    'certification_manifest_invalid',
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    value !== null &&
    typeof value === 'object' &&
    !Array.isArray(value)
  )
}

function exactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return JSON.stringify(actual) === JSON.stringify(expected)
}

function nonnegativeSafeInteger(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= 0
  )
}

interface PersistedPiRunIdentity {
  modelVersion: string
  input: {
    eventReferenceSha256: string
    incidentId: string
    publicationId: string
    revision: number
    dataThrough: string | null
    factSetId: string
    collectorId: 'rrc25'
    reportSpecificationVersion: 'country_outage_report_spec_v1'
    projectKnowledgeVersion:
      typeof COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION
    validatorRulesVersion:
      typeof COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION
  }
  skillBundleSha256: string
}

interface PersistedReportIdentity {
  schemaVersion: 'country_outage_report_document_v1'
  artifactId: string
  reportContentSha256: string
  generatedAt: string
  event: Record<string, unknown>
  snapshot: Record<string, unknown>
  factSetId: string
  model: Record<string, unknown>
  validation: Record<string, unknown>
}

function assertPersistedPiRunAudit(
  bytes: Buffer,
  manifest: PiModelCertificationManifest,
  evidence: PersistedCertificationArtifactEvidence,
  expectedSkillBundleSha256: string,
): PersistedPiRunIdentity {
  let value: unknown
  try {
    value = JSON.parse(bytes.toString('utf8')) as unknown
  } catch {
    invalidEvidence()
  }
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'schemaVersion',
      'recordedAt',
      'outcome',
      'runtimeIdentity',
      'candidateId',
      'candidateResourceSha256',
      'profileId',
      'provider',
      'model',
      'modelVersion',
      'expectedResponseModel',
      'piVersion',
      'input',
      'narration',
      'runtimeSecurity',
      'modelAttempt',
      'observed',
      'tools',
      'usage',
    ]) ||
    value.schemaVersion !== 'country_outage_pi_run_audit_v3' ||
    value.recordedAt !== evidence.completedAt ||
    value.outcome !== 'accepted' ||
    value.runtimeIdentity !== 'candidate' ||
    value.candidateId !== manifest.candidateId ||
    value.candidateResourceSha256 !==
      manifest.candidateResourceSha256 ||
    value.provider !== evidence.observed.provider ||
    value.model !== evidence.observed.model ||
    typeof value.modelVersion !== 'string' ||
    value.modelVersion.length === 0 ||
    value.expectedResponseModel !==
      evidence.observed.responseModel ||
    value.piVersion !== manifest.policy.piVersion ||
    !isRecord(value.input) ||
    !exactKeys(value.input, [
      'eventReferenceSha256',
      'incidentId',
      'publicationId',
      'revision',
      'dataThrough',
      'factSetId',
      'collectorId',
      'reportSpecificationVersion',
      'projectKnowledgeVersion',
      'validatorRulesVersion',
    ]) ||
    typeof value.input.eventReferenceSha256 !== 'string' ||
    !/^[a-f0-9]{64}$/.test(value.input.eventReferenceSha256) ||
    typeof value.input.incidentId !== 'string' ||
    value.input.incidentId.length === 0 ||
    typeof value.input.publicationId !== 'string' ||
    value.input.publicationId.length === 0 ||
    !nonnegativeSafeInteger(value.input.revision) ||
    !(
      value.input.dataThrough === null ||
      typeof value.input.dataThrough === 'string'
    ) ||
    value.input.factSetId !== evidence.factSetId ||
    value.input.collectorId !== 'rrc25' ||
    value.input.reportSpecificationVersion !==
      'country_outage_report_spec_v1' ||
    value.input.projectKnowledgeVersion !==
      COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION ||
    value.input.validatorRulesVersion !==
      COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION ||
    !isRecord(value.narration) ||
    !exactKeys(value.narration, [
      'mode',
      'slotContractVersion',
      'requestedSlotCount',
      'acceptedSlotCount',
      'baseV5',
      'mergeInvariant',
      'finalV5',
      'modelOutputApplied',
    ]) ||
    value.narration.mode !== FORMAL_PI_NARRATION_MODE ||
    value.narration.slotContractVersion !==
      COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION ||
    !nonnegativeSafeInteger(
      value.narration.requestedSlotCount,
    ) ||
    value.narration.requestedSlotCount < 2 ||
    value.narration.requestedSlotCount >
      COUNTRY_OUTAGE_LANGUAGE_SLOT_IDS.length ||
    value.narration.acceptedSlotCount !==
      value.narration.requestedSlotCount ||
    value.narration.baseV5 !== 'passed' ||
    value.narration.mergeInvariant !== 'passed' ||
    value.narration.finalV5 !== 'passed' ||
    value.narration.modelOutputApplied !== true ||
    !isRecord(value.runtimeSecurity) ||
    !exactKeys(value.runtimeSecurity, [
      'resourceLoaderId',
      'skillBundleSha256',
      'packageManagerResolutionEnabled',
      'modelResolverEnabled',
      'modelsJsonEnabled',
      'modelCatalogNetworkRefreshEnabled',
      'explicitModel',
      'providerRetryAttempts',
      'forwardedProviderRequestCount',
      'structuredOutput',
      'dependencyRiskException',
    ]) ||
    value.runtimeSecurity.resourceLoaderId !==
      'country-outage-static-resource-loader-v1' ||
    value.runtimeSecurity.skillBundleSha256 !==
      expectedSkillBundleSha256 ||
    value.runtimeSecurity.packageManagerResolutionEnabled !== false ||
    value.runtimeSecurity.modelResolverEnabled !== false ||
    value.runtimeSecurity.modelsJsonEnabled !== false ||
    value.runtimeSecurity.modelCatalogNetworkRefreshEnabled !== false ||
    value.runtimeSecurity.explicitModel !== true ||
    value.runtimeSecurity.providerRetryAttempts !== 0 ||
    value.runtimeSecurity.forwardedProviderRequestCount !==
      evidence.checks.providerRequestCount ||
    !isRecord(value.runtimeSecurity.structuredOutput) ||
    !exactKeys(value.runtimeSecurity.structuredOutput, [
      'applicability',
      'mechanism',
      'payloadPreparedCount',
    ]) ||
    value.runtimeSecurity.structuredOutput.applicability !==
      'required' ||
    value.runtimeSecurity.structuredOutput.mechanism !==
      evidence.checks.structuredOutput.mechanism ||
    value.runtimeSecurity.structuredOutput.payloadPreparedCount !==
      evidence.checks.structuredOutput.payloadPreparedCount ||
    !isRecord(value.runtimeSecurity.dependencyRiskException) ||
    !exactKeys(value.runtimeSecurity.dependencyRiskException, [
      'exceptionId',
      'expiresAt',
      'status',
    ]) ||
    value.runtimeSecurity.dependencyRiskException.status !==
      'active' ||
    !isRecord(value.modelAttempt) ||
    !exactKeys(value.modelAttempt, [
      'timeoutMs',
      'maximumAttempts',
      'executedAttempts',
    ]) ||
    value.modelAttempt.timeoutMs !==
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.modelAttemptTimeoutMs ||
    value.modelAttempt.maximumAttempts !==
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelAttempts ||
    !nonnegativeSafeInteger(value.modelAttempt.executedAttempts) ||
    value.modelAttempt.executedAttempts < 1 ||
    value.modelAttempt.executedAttempts >
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelAttempts ||
    !isRecord(value.observed) ||
    !exactKeys(value.observed, [
      'provider',
      'model',
      'responseModel',
      'stopReason',
    ]) ||
    value.observed.provider !== evidence.observed.provider ||
    value.observed.model !== evidence.observed.model ||
    value.observed.responseModel !==
      evidence.observed.responseModel ||
    value.observed.stopReason !== 'stop' ||
    !isRecord(value.tools) ||
    !exactKeys(value.tools, [
      'executedNames',
      'executionCount',
      'unauthorizedAttemptCount',
    ]) ||
    !Array.isArray(value.tools.executedNames) ||
    !value.tools.executedNames.includes('country_outage_resolve') ||
    !value.tools.executedNames.includes(
      'country_outage_get_observation',
    ) ||
    !nonnegativeSafeInteger(value.tools.executionCount) ||
    value.tools.unauthorizedAttemptCount !== 0 ||
    !isRecord(value.usage) ||
    !exactKeys(value.usage, [
      'assistantMessages',
      'toolCalls',
      'toolResults',
      'totalMessages',
      'tokens',
      'estimatedCostUsd',
    ]) ||
    value.usage.assistantMessages !==
      evidence.checks.providerRequestCount ||
    !isRecord(value.usage.tokens) ||
    !exactKeys(value.usage.tokens, [
      'input',
      'output',
      'cacheRead',
      'cacheWrite',
      'total',
    ]) ||
    value.usage.tokens.input !== evidence.usage.inputTokens ||
    value.usage.tokens.output !== evidence.usage.outputTokens ||
    value.usage.tokens.cacheRead !==
      evidence.usage.cacheReadTokens ||
    value.usage.tokens.cacheWrite !==
      evidence.usage.cacheWriteTokens ||
    value.usage.tokens.total !==
      evidence.usage.inputTokens +
        evidence.usage.outputTokens +
        evidence.usage.cacheReadTokens +
        evidence.usage.cacheWriteTokens
  ) {
    invalidEvidence()
  }
  return {
    modelVersion: value.modelVersion as string,
    input: {
      eventReferenceSha256:
        value.input.eventReferenceSha256 as string,
      incidentId: value.input.incidentId as string,
      publicationId: value.input.publicationId as string,
      revision: value.input.revision as number,
      dataThrough: value.input.dataThrough as string | null,
      factSetId: value.input.factSetId as string,
      collectorId: 'rrc25',
      reportSpecificationVersion:
        'country_outage_report_spec_v1',
      projectKnowledgeVersion:
        COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
      validatorRulesVersion:
        COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
    },
    skillBundleSha256: expectedSkillBundleSha256,
  }
}

function parsePersistedJson(bytes: Buffer): Record<string, unknown> {
  try {
    const value = JSON.parse(bytes.toString('utf8')) as unknown
    if (!isRecord(value)) invalidEvidence()
    return value
  } catch (error) {
    if (error instanceof PiModelCertificationError) throw error
    return invalidEvidence()
  }
}

function assertPersistedReportDocument(
  bytes: Buffer,
  manifest: PiModelCertificationManifest,
  evidence: PersistedCertificationArtifactEvidence,
  piAudit: PersistedPiRunIdentity,
  expectedSkillBundleSha256: string,
): PersistedReportIdentity {
  const value = parsePersistedJson(bytes)
  if (
    value.schemaVersion !== 'country_outage_report_document_v1' ||
    value.artifactId !== evidence.artifactId ||
    value.reportContentSha256 !==
      evidence.reportContentSha256 ||
    value.reportSpecificationVersion !==
      piAudit.input.reportSpecificationVersion ||
    value.projectKnowledgeVersion !==
      COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION ||
    value.projectKnowledgeVersion !==
      piAudit.input.projectKnowledgeVersion ||
    value.validatorRulesVersion !==
      COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION ||
    value.validatorRulesVersion !==
      piAudit.input.validatorRulesVersion ||
    value.skillBundleSha256 !== expectedSkillBundleSha256 ||
    value.skillBundleSha256 !== piAudit.skillBundleSha256 ||
    typeof value.generatedAt !== 'string' ||
    value.generatedAt.length === 0 ||
    value.aiGenerated !== true ||
    value.humanReviewed !== false ||
    !isRecord(value.event) ||
    value.event.event_type !== 'country_outage' ||
    value.event.incident_id !== piAudit.input.incidentId ||
    typeof value.event.legacy_reference !== 'string' ||
    sha256(
      normalizedEventReferenceForAudit(
        value.event.legacy_reference,
      ),
    ) !==
      piAudit.input.eventReferenceSha256 ||
    typeof value.event.country_code !== 'string' ||
    typeof value.event.country_name !== 'string' ||
    !isRecord(value.snapshot) ||
    value.snapshot.incidentId !== piAudit.input.incidentId ||
    value.snapshot.publicationId !== piAudit.input.publicationId ||
    value.snapshot.revision !== piAudit.input.revision ||
    value.snapshot.dataThrough !== piAudit.input.dataThrough ||
    value.snapshot.collectorId !== piAudit.input.collectorId ||
    canonicalJsonSha256(value.snapshot) !==
      evidence.snapshotSha256 ||
    value.factSetId !== evidence.factSetId ||
    value.factSetId !== piAudit.input.factSetId ||
    !isRecord(value.model) ||
    value.model.provider !== evidence.observed.provider ||
    value.model.model !== evidence.observed.model ||
    value.model.modelVersion !== piAudit.modelVersion ||
    value.model.adapter !== 'pi-sdk' ||
    value.model.piVersion !== manifest.policy.piVersion ||
    value.model.runtimeIdentity !== 'candidate' ||
    !isRecord(value.validation) ||
    value.validation.passed !== true
  ) {
    invalidEvidence()
  }
  return {
    schemaVersion: 'country_outage_report_document_v1',
    artifactId: value.artifactId as string,
    reportContentSha256: value.reportContentSha256 as string,
    generatedAt: value.generatedAt,
    event: value.event,
    snapshot: value.snapshot,
    factSetId: value.factSetId as string,
    model: value.model,
    validation: value.validation,
  }
}

function assertPersistedReportAuditManifest(
  bytes: Buffer,
  evidence: PersistedCertificationArtifactEvidence,
  piAudit: PersistedPiRunIdentity,
  report: PersistedReportIdentity,
  expectedSkillBundleSha256: string,
): void {
  const value = parsePersistedJson(bytes)
  if (
    value.schemaVersion !==
      COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_SCHEMA_VERSION ||
    !isRecord(value.reportIdentity) ||
    value.reportIdentity.schemaVersion !== report.schemaVersion ||
    value.reportIdentity.artifactId !== evidence.artifactId ||
    value.reportIdentity.artifactId !== report.artifactId ||
    value.reportIdentity.reportContentSha256 !==
      evidence.reportContentSha256 ||
    value.reportIdentity.reportContentSha256 !==
      report.reportContentSha256 ||
    value.reportIdentity.generatedAt !== report.generatedAt ||
    value.reportIdentity.aiGenerated !== true ||
    value.reportIdentity.humanReviewed !== false ||
    !isRecord(value.eventIdentity) ||
    value.eventIdentity.incidentId !== piAudit.input.incidentId ||
    value.eventIdentity.incidentId !== report.event.incident_id ||
    value.eventIdentity.eventReference !==
      report.event.legacy_reference ||
    value.eventIdentity.eventType !== 'country_outage' ||
    value.eventIdentity.countryCode !== report.event.country_code ||
    value.eventIdentity.countryName !== report.event.country_name ||
    !isRecord(value.snapshotIdentity) ||
    canonicalJsonStringify(value.snapshotIdentity) !==
      canonicalJsonStringify(report.snapshot) ||
    canonicalJsonSha256(value.snapshotIdentity) !==
      evidence.snapshotSha256 ||
    !isRecord(value.factSetIdentity) ||
    value.factSetIdentity.schemaVersion !==
      'country_outage_report_facts_v1' ||
    value.factSetIdentity.factSetId !== evidence.factSetId ||
    value.factSetIdentity.factSetId !== report.factSetId ||
    value.factSetIdentity.factSetId !== piAudit.input.factSetId ||
    !isRecord(value.modelIdentity) ||
    canonicalJsonStringify(value.modelIdentity) !==
      canonicalJsonStringify(report.model) ||
    !isRecord(value.contractIdentity) ||
    value.contractIdentity.reportSpecificationVersion !==
      piAudit.input.reportSpecificationVersion ||
    value.contractIdentity.projectKnowledgeVersion !==
      COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION ||
    value.contractIdentity.projectKnowledgeVersion !==
      piAudit.input.projectKnowledgeVersion ||
    value.contractIdentity.validatorRulesVersion !==
      COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION ||
    value.contractIdentity.validatorRulesVersion !==
      piAudit.input.validatorRulesVersion ||
    value.contractIdentity.skillBundleSha256 !==
      expectedSkillBundleSha256 ||
    value.contractIdentity.skillBundleSha256 !==
      piAudit.skillBundleSha256 ||
    !isRecord(value.validation) ||
    canonicalJsonStringify(value.validation) !==
      canonicalJsonStringify(report.validation)
  ) {
    invalidEvidence()
  }
}

function checkedDirectory(path: string, root?: string): string {
  try {
    const normalized = resolve(path)
    const stats = lstatSync(normalized)
    const real = realpathSync(normalized)
    if (
      !stats.isDirectory() ||
      stats.isSymbolicLink() ||
      real !== normalized ||
      (root !== undefined && !isWithinRoot(root, real))
    ) {
      invalidEvidence()
    }
    if (
      typeof process.getuid === 'function' &&
      stats.uid !== process.getuid()
    ) {
      invalidEvidence()
    }
    // 仓库与只读父目录可以是 0755，但不能由组或其他用户写入。
    if ((stats.mode & 0o022) !== 0) invalidEvidence()
    return real
  } catch (error) {
    if (error instanceof PiModelCertificationError) throw error
    return invalidEvidence()
  }
}

function exactDirectoryEntries(
  directory: string,
  expected: readonly string[],
): void {
  try {
    const actual = readdirSync(directory).sort()
    const wanted = [...expected].sort()
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
      invalidEvidence()
    }
  } catch (error) {
    if (error instanceof PiModelCertificationError) throw error
    invalidEvidence()
  }
}

function readTrustedFile(
  path: string,
  root: string,
  maximumBytes: number,
): Buffer {
  let descriptor: number | undefined
  try {
    const normalized = resolve(path)
    if (!isWithinRoot(root, normalized)) invalidEvidence()
    const before = lstatSync(normalized)
    if (
      !before.isFile() ||
      before.isSymbolicLink() ||
      before.size <= 0 ||
      before.size > maximumBytes ||
      realpathSync(normalized) !== normalized ||
      (typeof process.getuid === 'function' &&
        before.uid !== process.getuid()) ||
      (before.mode & 0o077) !== 0
    ) {
      invalidEvidence()
    }
    descriptor = openSync(
      normalized,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    )
    const opened = fstatSync(descriptor)
    if (
      !opened.isFile() ||
      opened.dev !== before.dev ||
      opened.ino !== before.ino ||
      opened.size !== before.size
    ) {
      invalidEvidence()
    }
    const content = readFileSync(descriptor)
    if (
      content.byteLength !== opened.size ||
      content.byteLength > maximumBytes
    ) {
      invalidEvidence()
    }
    return content
  } catch (error) {
    if (error instanceof PiModelCertificationError) throw error
    return invalidEvidence()
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

function defaultRepositoryRoot(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(moduleDirectory, '../../..'),
    resolve(moduleDirectory, '../../../..'),
  ]
  for (const candidate of candidates) {
    try {
      const root = realpathSync(candidate)
      if (
        lstatSync(resolve(root, 'agent-sidecar/package.json')).isFile()
      ) {
        return root
      }
    } catch {
      // 继续检查编译后目录对应的候选根。
    }
  }
  return invalidEvidence()
}

export interface ReadPersistedA4CertificationEvidenceOptions {
  evidenceId: string
  repositoryRoot?: string
}

export async function readPersistedA4CertificationEvidence(
  options: ReadPersistedA4CertificationEvidenceOptions,
): Promise<PiModelCertificationManifest> {
  if (!EVIDENCE_ID.test(options.evidenceId)) invalidEvidence()
  let canonicalRoot: string
  try {
    canonicalRoot = realpathSync(
      resolve(options.repositoryRoot ?? defaultRepositoryRoot()),
    )
  } catch {
    return invalidEvidence()
  }
  const root = checkedDirectory(canonicalRoot)
  let parent = root
  for (const segment of EVIDENCE_PARENT_SEGMENTS) {
    parent = checkedDirectory(resolve(parent, segment), root)
  }
  const evidenceDirectory = checkedDirectory(
    resolve(parent, options.evidenceId),
    root,
  )
  exactDirectoryEntries(evidenceDirectory, [
    'manifest.json',
    'run-1',
    'run-2',
    ...A4_CERTIFICATION_SCENARIO_IDS.map(
      scenarioDirectoryName,
    ),
  ])

  const loadedCandidate = await loadPiModelCandidate()
  const manifestBytes = readTrustedFile(
    resolve(evidenceDirectory, 'manifest.json'),
    root,
    MANIFEST_MAX_BYTES,
  )
  let rawManifest: unknown
  try {
    rawManifest = JSON.parse(manifestBytes.toString('utf8')) as unknown
  } catch {
    return invalidEvidence()
  }
  const manifest = parsePiModelCertificationManifest(
    rawManifest,
    loadedCandidate,
  )
  if (
    manifest.evidenceId !== options.evidenceId ||
    manifest.provenance.runnerIdentity !==
      'country-outage-full-report-runner-v1' ||
    manifest.provenance.promotable !== true ||
    manifest.scenarioCoverage === undefined ||
    manifest.certificationProfile === undefined ||
    manifest.scenarioCoverage.scenarios.length !==
      A4_CERTIFICATION_SCENARIO_IDS.length ||
    manifest.scenarioCoverage.scenarios.some(
      (scenario, index) =>
        scenario.scenarioId !==
        A4_CERTIFICATION_SCENARIO_IDS[index],
    ) ||
    manifest.certificationProfile.certifiedScenarioSetId !==
      manifest.scenarioCoverage.scenarioSetId ||
    manifest.certificationProfile.certifiedInputScope !==
      manifest.scenarioCoverage.certifiedInputScope
  ) {
    invalidEvidence()
  }
  const canonicalManifest = Buffer.from(
    `${JSON.stringify(manifest, null, 2)}\n`,
    'utf8',
  )
  if (!canonicalManifest.equals(manifestBytes)) invalidEvidence()
  let expectedSkillBundleSha256: string
  try {
    expectedSkillBundleSha256 =
      computeCountryOutageSkillBundleSha256()
  } catch {
    return invalidEvidence()
  }

  for (const runNumber of [1, 2] as const) {
    const runDirectory = checkedDirectory(
      resolve(evidenceDirectory, `run-${runNumber}`),
      root,
    )
    exactDirectoryEntries(runDirectory, RUN_FILES)
    const run = manifest.runs[runNumber - 1]!
    const files = {
      reportDocument: readTrustedFile(
        resolve(runDirectory, 'report-document.json'),
        root,
        JSON_ARTIFACT_MAX_BYTES,
      ),
      reportAudit: readTrustedFile(
        resolve(runDirectory, 'audit-manifest.json'),
        root,
        JSON_ARTIFACT_MAX_BYTES,
      ),
      piRunAudit: readTrustedFile(
        resolve(runDirectory, 'pi-run-audit.json'),
        root,
        JSON_ARTIFACT_MAX_BYTES,
      ),
      markdown: readTrustedFile(
        resolve(runDirectory, 'report.md'),
        root,
        MARKDOWN_MAX_BYTES,
      ),
      pdf: readTrustedFile(
        resolve(runDirectory, 'report.pdf'),
        root,
        PDF_MAX_BYTES,
      ),
    }
    if (
      sha256(files.reportDocument) !==
        run.artifacts.reportDocumentSha256 ||
      sha256(files.reportAudit) !==
        run.artifacts.reportAuditManifestSha256 ||
      sha256(files.piRunAudit) !==
        run.artifacts.piRunAuditSha256 ||
      sha256(files.markdown) !== run.artifacts.markdownSha256 ||
      sha256(files.pdf) !== run.artifacts.pdfSha256 ||
      files.pdf.subarray(0, 5).toString('utf8') !== '%PDF-'
    ) {
      invalidEvidence()
    }
    const piAudit = assertPersistedPiRunAudit(
      files.piRunAudit,
      manifest,
      run,
      expectedSkillBundleSha256,
    )
    const report = assertPersistedReportDocument(
      files.reportDocument,
      manifest,
      run,
      piAudit,
      expectedSkillBundleSha256,
    )
    assertPersistedReportAuditManifest(
      files.reportAudit,
      run,
      piAudit,
      report,
      expectedSkillBundleSha256,
    )
  }

  for (
    let index = 0;
    index < A4_CERTIFICATION_SCENARIO_IDS.length;
    index += 1
  ) {
    const scenarioId = A4_CERTIFICATION_SCENARIO_IDS[index]!
    const scenario =
      manifest.scenarioCoverage.scenarios[index]!
    const scenarioDirectory = checkedDirectory(
      resolve(
        evidenceDirectory,
        scenarioDirectoryName(scenarioId),
      ),
      root,
    )
    exactDirectoryEntries(scenarioDirectory, SCENARIO_FILES)
    const certificationOnlyMarker = readTrustedFile(
      resolve(
        scenarioDirectory,
        'CERTIFICATION-ONLY.txt',
      ),
      root,
      CERTIFICATION_ONLY_MARKER_MAX_BYTES,
    )
    const files = {
      reportDocument: readTrustedFile(
        resolve(
          scenarioDirectory,
          'report-document.json',
        ),
        root,
        JSON_ARTIFACT_MAX_BYTES,
      ),
      reportAudit: readTrustedFile(
        resolve(
          scenarioDirectory,
          'audit-manifest.json',
        ),
        root,
        JSON_ARTIFACT_MAX_BYTES,
      ),
      piRunAudit: readTrustedFile(
        resolve(scenarioDirectory, 'pi-run-audit.json'),
        root,
        JSON_ARTIFACT_MAX_BYTES,
      ),
      markdown: readTrustedFile(
        resolve(scenarioDirectory, 'report.md'),
        root,
        MARKDOWN_MAX_BYTES,
      ),
      pdf: readTrustedFile(
        resolve(scenarioDirectory, 'report.pdf'),
        root,
        PDF_MAX_BYTES,
      ),
    }
    if (
      certificationOnlyMarker.toString('utf8') !==
        CERTIFICATION_ONLY_MARKER ||
      sha256(files.reportDocument) !==
        scenario.artifacts.reportDocumentSha256 ||
      sha256(files.reportAudit) !==
        scenario.artifacts.reportAuditManifestSha256 ||
      sha256(files.piRunAudit) !==
        scenario.artifacts.piRunAuditSha256 ||
      sha256(files.markdown) !==
        scenario.artifacts.markdownSha256 ||
      sha256(files.pdf) !== scenario.artifacts.pdfSha256 ||
      files.pdf.subarray(0, 5).toString('utf8') !== '%PDF-'
    ) {
      invalidEvidence()
    }
    const piAudit = assertPersistedPiRunAudit(
      files.piRunAudit,
      manifest,
      scenario,
      expectedSkillBundleSha256,
    )
    const report = assertPersistedReportDocument(
      files.reportDocument,
      manifest,
      scenario,
      piAudit,
      expectedSkillBundleSha256,
    )
    assertPersistedReportAuditManifest(
      files.reportAudit,
      scenario,
      piAudit,
      report,
      expectedSkillBundleSha256,
    )
  }

  return manifest
}

export interface PromotePersistedA4ModelCandidateOptions {
  evidenceId: string
  newRegistryVersion: string
  repositoryRoot?: string
  registryPath?: string
  responseModelAdapterInspector?: CandidateResponseModelAdapterInspector
  now?: () => Date
}

export async function promotePersistedA4ModelCandidate(
  options: PromotePersistedA4ModelCandidateOptions,
): Promise<{
  registrySha256: string
  certificationEvidenceId: string
  registryVersion: string
}> {
  const manifest = await readPersistedA4CertificationEvidence(options)
  const loadedCandidate = await loadPiModelCandidate()
  const promoted = promotePiModelCandidate({
    loadedCandidate,
    manifest,
    newRegistryVersion: options.newRegistryVersion,
    ...(options.registryPath === undefined
      ? {}
      : { registryPath: options.registryPath }),
    ...(options.responseModelAdapterInspector === undefined
      ? {}
      : {
          responseModelAdapterInspector:
            options.responseModelAdapterInspector,
        }),
    ...(options.now === undefined
      ? {}
      : {
          now: options.now,
        }),
  })
  return Object.freeze({
    registrySha256: promoted.registrySha256,
    certificationEvidenceId: manifest.evidenceId,
    registryVersion: promoted.registry.registryVersion,
  })
}
