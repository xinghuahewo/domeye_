import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import {
  chmodSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'

import {
  computeCountryOutageSkillBundleSha256,
  COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
  loadPiModelCandidate,
  MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  PiModelCertificationError,
  promotePersistedA4ModelCandidate,
  readPersistedA4CertificationEvidence,
  writeCurrentProviderPriceAttestation,
  type CandidateScenarioCertificationRunEvidence,
  type PiModelCertificationManifest,
} from '../src/pi/index.js'
import { COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION } from '../src/report/index.js'

const TEST_ROOT = mkdtempSync(
  join(tmpdir(), 'domeye-persisted-promotion-'),
)

after(() => {
  rmSync(TEST_ROOT, { recursive: true, force: true })
})

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]),
    )
  }
  return value
}

function sha256(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex')
}

function canonicalSha256(value: unknown): string {
  return sha256(JSON.stringify(canonicalize(value)))
}

const CURRENT_SKILL_BUNDLE_SHA256 =
  computeCountryOutageSkillBundleSha256()
const FIXTURE_EVENT_REFERENCE =
  'country_outage/2026-02-27 09:12:32/IR/1/r'
const FIXTURE_INCIDENT_ID =
  'incident_go_v1_a1de26f854831330c616a72af21597eb'
const FIXTURE_PUBLICATION_ID =
  'publication_v1_38bddead083db3f49023c2e1'
const FIXTURE_DATA_THROUGH = '2026-02-28T15:00:00Z'
const FIXTURE_FACT_SET_ID = 'facts-fixed-a4'
const FIXTURE_SNAPSHOT = {
  incidentId: FIXTURE_INCIDENT_ID,
  publicationId: FIXTURE_PUBLICATION_ID,
  revision: 1,
  dataThrough: FIXTURE_DATA_THROUGH,
  isFinal: true,
  cohortId: 'cohort-fixed-a4',
  collectorId: 'rrc25',
  windowStartUtc: '2026-02-28T10:05:00Z',
  windowEndUtc: FIXTURE_DATA_THROUGH,
} as const
const FIXTURE_SNAPSHOT_SHA256 =
  canonicalSha256(FIXTURE_SNAPSHOT)
const CERTIFICATION_ONLY_MARKER =
  '认证专用合成场景，不是 Domeye 事件事实，不得作为观测报告对外发布。\n'
const FIXED_PROMOTION_NOW = () =>
  new Date('2026-07-30T00:00:00.000Z')
const SCENARIOS = [
  {
    id: 'capability-degraded-final',
    purpose: 'capability_degradation',
    isFinal: true,
  },
  {
    id: 'direction-end-above-start-final',
    purpose: 'direction_change',
    isFinal: true,
  },
  {
    id: 'non-final-snapshot',
    purpose: 'non_final_snapshot',
    isFinal: false,
  },
] as const

function certificationEvidenceBody(
  manifest: PiModelCertificationManifest,
): Record<string, unknown> {
  const scenarioCoverage = manifest.scenarioCoverage
  const certificationProfile = manifest.certificationProfile
  return {
    candidateId: manifest.candidateId,
    candidateResourceSha256: manifest.candidateResourceSha256,
    certificationStartedAt: manifest.certificationStartedAt,
    completedAt: manifest.completedAt,
    registrySha256Before: manifest.targetRegistry.sha256Before,
    responseModelAdapterSourceSha256:
      manifest.policy.responseModelAdapterSourceSha256,
    priceAttestationId:
      manifest.policy.priceAttestation?.attestationId ?? null,
    priceAttestationResourceSha256:
      manifest.policy.priceAttestation?.resourceSha256 ?? null,
    priceEvidenceSha256:
      manifest.policy.priceAttestation?.evidenceSha256 ?? null,
    runnerIdentity: manifest.provenance.runnerIdentity,
    certificationFixtureId:
      manifest.provenance.certificationFixtureId,
    runs: manifest.runs.map((run) => run.runEvidenceId),
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
}

function secureDirectory(path: string): void {
  mkdirSync(path, { recursive: true, mode: 0o700 })
  chmodSync(path, 0o700)
}

function secureFile(path: string, content: string | Buffer): Buffer {
  const bytes =
    typeof content === 'string' ? Buffer.from(content, 'utf8') : content
  writeFileSync(path, bytes, { mode: 0o600 })
  chmodSync(path, 0o600)
  return bytes
}

interface PersistedArtifactFixture {
  completedAt: string
  observed: {
    provider: 'deepseek'
    model: 'deepseek-v4-flash'
    responseModel: 'deepseek-v4-flash'
  }
  artifactId: string
  reportContentSha256: string
  factSetId: string
  snapshotSha256: string
  evidenceInputSha256: string
  checks: PiModelCertificationManifest['runs'][number]['checks']
  artifacts: PiModelCertificationManifest['runs'][number]['artifacts']
  usage: PiModelCertificationManifest['runs'][number]['usage']
  files: {
    reportDocument: Buffer
    reportAudit: Buffer
    piRunAudit: Buffer
    markdown: Buffer
    pdf: Buffer
  }
}

async function persistedFixture(
  label: string,
  options: { includeScenarios?: boolean } = {},
): Promise<{
  root: string
  registryPath: string
  evidenceId: string
  evidenceDirectory: string
}> {
  const includeScenarios = options.includeScenarios ?? true
  const root = join(TEST_ROOT, label)
  secureDirectory(root)
  const registryPath = join(root, 'registry.json')
  const registryText = `${JSON.stringify(
    {
      schemaVersion: 'country_outage_pi_certified_models_v1',
      registryVersion: `${label}-registry-v1`,
      status: 'frozen',
      profiles: [],
    },
    null,
    2,
  )}\n`
  writeFileSync(registryPath, registryText, 'utf8')

  const loadedCandidate = await loadPiModelCandidate()
  const priceAttestation = writeCurrentProviderPriceAttestation({
    repositoryRoot: root,
    candidate: loadedCandidate,
    observedAt: '2026-07-29T04:00:00.000Z',
    evidenceSha256: 'e'.repeat(64),
    priceUsdPerMillionTokens: {
      input: '0.14',
      output: '0.28',
      cacheRead: '0.0028',
      cacheWrite: '0',
    },
    now: new Date('2026-07-29T04:00:00.000Z'),
  })

  const createArtifact = (input: {
    completedAt: string
    artifactId: string
    reportContentSha256: string
    factSetId: string
    snapshot: Record<string, unknown>
    evidenceInputSha256: string
    inputTokens: number
    outputTokens: number
    label: string
  }): PersistedArtifactFixture => {
    const reportModel = {
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      modelVersion: 'deepseek-v4-flash',
      adapter: 'pi-sdk',
      piVersion: '0.82.1',
      runtimeIdentity: 'candidate',
    }
    const reportValidation = {
      passed: true,
      errors: [],
      warnings: [],
      checkedEvidenceRefs: [],
    }
    const reportEvent = {
      incident_id: FIXTURE_INCIDENT_ID,
      legacy_reference: FIXTURE_EVENT_REFERENCE,
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗',
    }
    const piRunAudit = {
      schemaVersion: 'country_outage_pi_run_audit_v3',
      recordedAt: input.completedAt,
      outcome: 'accepted',
      runtimeIdentity: 'candidate',
      candidateId: 'deepseek-v4-flash-pi-0.82.1-v1',
      candidateResourceSha256: loadedCandidate.resourceSha256,
      profileId: 'deepseek-v4-flash-pi-0.82.1-v1',
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      modelVersion: 'deepseek-v4-flash',
      expectedResponseModel: 'deepseek-v4-flash',
      piVersion: '0.82.1',
      input: {
        eventReferenceSha256: sha256(
          FIXTURE_EVENT_REFERENCE.replace(' ', '+'),
        ),
        incidentId: FIXTURE_INCIDENT_ID,
        publicationId: FIXTURE_PUBLICATION_ID,
        revision: 1,
        dataThrough: FIXTURE_DATA_THROUGH,
        factSetId: input.factSetId,
        collectorId: 'rrc25',
        reportSpecificationVersion:
          'country_outage_report_spec_v1',
        projectKnowledgeVersion:
          COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
        validatorRulesVersion:
          COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
      },
      narration: {
        mode: 'deterministic-base-with-language-slots-v1',
        slotContractVersion: 'country_outage_language_slots_v1',
        requestedSlotCount: 5,
        acceptedSlotCount: 5,
        baseV5: 'passed',
        mergeInvariant: 'passed',
        finalV5: 'passed',
        modelOutputApplied: true,
      },
      runtimeSecurity: {
        resourceLoaderId:
          'country-outage-static-resource-loader-v1',
        skillBundleSha256: CURRENT_SKILL_BUNDLE_SHA256,
        packageManagerResolutionEnabled: false,
        modelResolverEnabled: false,
        modelsJsonEnabled: false,
        modelCatalogNetworkRefreshEnabled: false,
        explicitModel: true,
        providerRetryAttempts: 0,
        forwardedProviderRequestCount: 2,
        structuredOutput: {
          applicability: 'required',
          mechanism:
            'deepseek-json-object-after-required-tools-v1',
          payloadPreparedCount: 1,
        },
        dependencyRiskException: {
          exceptionId:
            'country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2',
          expiresAt: '2026-08-12T16:00:00Z',
          status: 'active',
        },
      },
      modelAttempt: {
        timeoutMs: 75_000,
        maximumAttempts: 2,
        executedAttempts: 1,
      },
      observed: {
        provider: 'deepseek',
        model: 'deepseek-v4-flash',
        responseModel: 'deepseek-v4-flash',
        stopReason: 'stop',
      },
      tools: {
        executedNames: [
          'country_outage_resolve',
          'country_outage_get_observation',
        ],
        executionCount: 2,
        unauthorizedAttemptCount: 0,
      },
      usage: {
        assistantMessages: 2,
        toolCalls: 2,
        toolResults: 2,
        totalMessages: 5,
        tokens: {
          input: input.inputTokens,
          output: input.outputTokens,
          cacheRead: 0,
          cacheWrite: 0,
          total: input.inputTokens + input.outputTokens,
        },
        estimatedCostUsd: 0,
      },
    }
    const reportDocument = {
      schemaVersion: 'country_outage_report_document_v1',
      artifactId: input.artifactId,
      reportContentSha256: input.reportContentSha256,
      reportSpecificationVersion:
        'country_outage_report_spec_v1',
      projectKnowledgeVersion:
        COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
      validatorRulesVersion:
        COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
      skillBundleSha256: CURRENT_SKILL_BUNDLE_SHA256,
      generatedAt: input.completedAt,
      aiGenerated: true,
      humanReviewed: false,
      event: reportEvent,
      snapshot: input.snapshot,
      factSetId: input.factSetId,
      model: reportModel,
      validation: reportValidation,
      draft: {},
    }
    const reportAudit = {
      schemaVersion:
        'country_outage_report_audit_manifest_v1',
      reportIdentity: {
        schemaVersion: reportDocument.schemaVersion,
        artifactId: input.artifactId,
        reportContentSha256: input.reportContentSha256,
        generatedAt: input.completedAt,
        aiGenerated: true,
        humanReviewed: false,
      },
      eventIdentity: {
        incidentId: FIXTURE_INCIDENT_ID,
        eventReference: FIXTURE_EVENT_REFERENCE,
        eventType: 'country_outage',
        countryCode: 'IR',
        countryName: '伊朗',
      },
      snapshotIdentity: input.snapshot,
      factSetIdentity: {
        schemaVersion: 'country_outage_report_facts_v1',
        factSetId: input.factSetId,
      },
      modelIdentity: reportModel,
      contractIdentity: {
        reportSpecificationVersion:
          'country_outage_report_spec_v1',
        projectKnowledgeVersion:
          COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
        validatorRulesVersion:
          COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
        skillBundleSha256: CURRENT_SKILL_BUNDLE_SHA256,
      },
      validation: reportValidation,
    }
    const files = {
      reportDocument: Buffer.from(
        `${JSON.stringify(reportDocument)}\n`,
        'utf8',
      ),
      reportAudit: Buffer.from(
        `${JSON.stringify(reportAudit)}\n`,
        'utf8',
      ),
      piRunAudit: Buffer.from(
        `${JSON.stringify(piRunAudit)}\n`,
        'utf8',
      ),
      markdown: Buffer.from(`# ${input.label}\n`, 'utf8'),
      pdf: Buffer.from(
        `%PDF-1.4\n${input.label}\n%%EOF\n`,
        'utf8',
      ),
    }
    const conservativeCostUsd =
      (input.inputTokens * 0.14 +
        input.outputTokens * 0.28) /
      1_000_000
    return {
      completedAt: input.completedAt,
      observed: {
        provider: 'deepseek',
        model: 'deepseek-v4-flash',
        responseModel: 'deepseek-v4-flash',
      },
      artifactId: input.artifactId,
      reportContentSha256: input.reportContentSha256,
      factSetId: input.factSetId,
      snapshotSha256: canonicalSha256(input.snapshot),
      evidenceInputSha256: input.evidenceInputSha256,
      checks: {
        reportComplete: true,
        validator: true,
        markdown: true,
        pdf: true,
        providerRequestCount: 2,
        providerRetryAttempts: 0,
        structuredOutput: {
          mechanism:
            'deepseek-json-object-after-required-tools-v1',
          payloadPreparedCount: 1,
        },
      },
      artifacts: {
        reportDocumentSha256: sha256(files.reportDocument),
        reportAuditManifestSha256: sha256(files.reportAudit),
        piRunAuditSha256: sha256(files.piRunAudit),
        markdownSha256: sha256(files.markdown),
        pdfSha256: sha256(files.pdf),
      },
      usage: {
        inputTokens: input.inputTokens,
        outputTokens: input.outputTokens,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        conservativeCostUsd,
        conservativeCostCny: conservativeCostUsd * 8,
      },
      files,
    }
  }

  const runArtifacts = [1, 2].map((runNumber) =>
    createArtifact({
      completedAt:
        `2026-07-29T10:0${runNumber}:00.000Z`,
      artifactId: `report_fixed_run_${runNumber}`,
      reportContentSha256: String(runNumber).repeat(64),
      factSetId: FIXTURE_FACT_SET_ID,
      snapshot: { ...FIXTURE_SNAPSHOT },
      evidenceInputSha256: 'c'.repeat(64),
      inputTokens: 1_000 + runNumber,
      outputTokens: 100 + runNumber,
      label: `固定报告 ${runNumber}`,
    }),
  )
  const runs = runArtifacts.map((artifact, index) => {
    const body = {
      runtimeIdentity: 'candidate' as const,
      runNumber: (index + 1) as 1 | 2,
      completedAt: artifact.completedAt,
      observed: artifact.observed,
      artifactId: artifact.artifactId,
      reportContentSha256: artifact.reportContentSha256,
      factSetId: artifact.factSetId,
      snapshotSha256: artifact.snapshotSha256,
      evidenceInputSha256: artifact.evidenceInputSha256,
      checks: artifact.checks,
      artifacts: artifact.artifacts,
      usage: artifact.usage,
    }
    return {
      ...body,
      runEvidenceId:
        `candidate-run:${canonicalSha256(body)}`,
    }
  }) as unknown as PiModelCertificationManifest['runs']

  const scenarioArtifacts = includeScenarios
    ? SCENARIOS.map((scenario, index) => {
        const snapshot = {
          ...FIXTURE_SNAPSHOT,
          isFinal: scenario.isFinal,
          cohortId: `cohort-${scenario.id}`,
        }
        return {
          definition: scenario,
          artifact: createArtifact({
            completedAt:
              `2026-07-29T10:02:${10 + index}.000Z`,
            artifactId:
              `report_scenario_${scenario.id.replaceAll('-', '_')}`,
            reportContentSha256: sha256(
              `scenario-report-content:${scenario.id}`,
            ),
            factSetId: `facts-scenario-${index + 1}`,
            snapshot,
            evidenceInputSha256: sha256(
              `scenario-evidence-input:${scenario.id}`,
            ),
            inputTokens: 1_100 + index,
            outputTokens: 120 + index,
            label: `认证场景 ${scenario.id}`,
          }),
        }
      })
    : []
  const scenarioEvidence = scenarioArtifacts.map(
    ({ definition, artifact }) => {
      const body = {
        scenarioId: definition.id,
        purpose: definition.purpose,
        certificationOnly: true as const,
        synthetic: true as const,
        completedAt: artifact.completedAt,
        observed: artifact.observed,
        artifactId: artifact.artifactId,
        reportContentSha256: artifact.reportContentSha256,
        factSetId: artifact.factSetId,
        snapshotSha256: artifact.snapshotSha256,
        evidenceInputSha256: artifact.evidenceInputSha256,
        checks: artifact.checks,
        artifacts: artifact.artifacts,
        usage: artifact.usage,
      }
      return {
        ...body,
        scenarioEvidenceId:
          `candidate-scenario:${canonicalSha256(body)}`,
      }
    },
  ) as unknown as readonly CandidateScenarioCertificationRunEvidence[]

  const certificationStartedAt = '2026-07-29T10:00:00.000Z'
  const completedAt = '2026-07-29T10:03:00.000Z'
  const scenarioCoverage = includeScenarios
    ? {
        scenarioSetId:
          'country-outage-rrc25-legal-scenarios-v2' as const,
        certifiedInputScope:
          'legal_country_outage_rrc25_v1' as const,
        representativeRepeatRunEvidenceIds: [
          runs[0].runEvidenceId,
          runs[1].runEvidenceId,
        ] as const,
        boundaryQuestionEngine:
          'deterministic-country-outage-question-engine-v1' as const,
        scenarios: scenarioEvidence,
      }
    : undefined
  const certificationProfile = includeScenarios
    ? {
        modelRevisionKind: 'mutable_alias' as const,
        immutableRevisionAvailable: false as const,
        limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
        certificationValidUntil:
          '2026-08-05T10:03:00.000Z',
        certifiedScenarioSetId:
          'country-outage-rrc25-legal-scenarios-v2' as const,
        certifiedInputScope:
          'legal_country_outage_rrc25_v1' as const,
      }
    : undefined
  const manifest: PiModelCertificationManifest = {
    schemaVersion:
      'country_outage_pi_model_certification_manifest_v1',
    status: 'passed',
    runtimeIdentity: 'candidate',
    candidateId: 'deepseek-v4-flash-pi-0.82.1-v1',
    candidateResourceSha256: loadedCandidate.resourceSha256,
    evidenceId:
      `evidence:model-certification:${'0'.repeat(64)}`,
    certificationStartedAt,
    completedAt,
    provenance: {
      runnerIdentity: 'country-outage-full-report-runner-v1',
      promotable: true,
      certificationFixtureId:
        'a4-iran-country-outage-rrc25-v1',
    },
    targetRegistry: {
      registryVersionBefore: `${label}-registry-v1`,
      sha256Before: sha256(registryText),
    },
    policy: {
      piVersion: '0.82.1',
      providerRetryAttempts: 0,
      maximumProviderRequestCount: 5,
      maximumOutputTokens: 16_384,
      requiredIndependentReportRuns: 2,
      responseModelAdapterSourceSha256: 'a'.repeat(64),
      priceAttestation,
    },
    budget: {
      limitCny: 20,
      conservativeCnyPerUsd: 8,
      maximumCertificationCostCny: includeScenarios
        ? 2.709504
        : 1.0838016,
      actualCertificationCostCny: [
        ...runs,
        ...scenarioEvidence,
      ].reduce(
        (sum, run) => sum + run.usage.conservativeCostCny,
        0,
      ),
    },
    factEquivalence: {
      passed: true,
      factSetId: FIXTURE_FACT_SET_ID,
      snapshotSha256: FIXTURE_SNAPSHOT_SHA256,
      evidenceInputSha256: 'c'.repeat(64),
    },
    runs,
    ...(scenarioCoverage && certificationProfile
      ? { scenarioCoverage, certificationProfile }
      : {}),
  }
  manifest.evidenceId =
    `evidence:model-certification:${canonicalSha256(
      certificationEvidenceBody(manifest),
    )}`

  const evidenceParent = join(
    root,
    'artifacts',
    'country-outage-agent',
    'a4-model-certification',
  )
  secureDirectory(evidenceParent)
  const evidenceDirectory = join(
    evidenceParent,
    manifest.evidenceId,
  )
  secureDirectory(evidenceDirectory)
  for (const [index, artifact] of runArtifacts.entries()) {
    const runDirectory = join(
      evidenceDirectory,
      `run-${index + 1}`,
    )
    secureDirectory(runDirectory)
    secureFile(
      join(runDirectory, 'report-document.json'),
      artifact.files.reportDocument,
    )
    secureFile(
      join(runDirectory, 'audit-manifest.json'),
      artifact.files.reportAudit,
    )
    secureFile(
      join(runDirectory, 'pi-run-audit.json'),
      artifact.files.piRunAudit,
    )
    secureFile(
      join(runDirectory, 'report.md'),
      artifact.files.markdown,
    )
    secureFile(
      join(runDirectory, 'report.pdf'),
      artifact.files.pdf,
    )
  }
  for (const { definition, artifact } of scenarioArtifacts) {
    const scenarioDirectory = join(
      evidenceDirectory,
      `scenario-${definition.id}`,
    )
    secureDirectory(scenarioDirectory)
    secureFile(
      join(scenarioDirectory, 'CERTIFICATION-ONLY.txt'),
      CERTIFICATION_ONLY_MARKER,
    )
    secureFile(
      join(scenarioDirectory, 'report-document.json'),
      artifact.files.reportDocument,
    )
    secureFile(
      join(scenarioDirectory, 'audit-manifest.json'),
      artifact.files.reportAudit,
    )
    secureFile(
      join(scenarioDirectory, 'pi-run-audit.json'),
      artifact.files.piRunAudit,
    )
    secureFile(
      join(scenarioDirectory, 'report.md'),
      artifact.files.markdown,
    )
    secureFile(
      join(scenarioDirectory, 'report.pdf'),
      artifact.files.pdf,
    )
  }
  secureFile(
    join(evidenceDirectory, 'manifest.json'),
    `${JSON.stringify(manifest, null, 2)}\n`,
  )
  return {
    root,
    registryPath,
    evidenceId: manifest.evidenceId,
    evidenceDirectory,
  }
}

function rebindTamperedRunJson(
  fixture: Awaited<ReturnType<typeof persistedFixture>>,
  filename:
    | 'pi-run-audit.json'
    | 'report-document.json'
    | 'audit-manifest.json',
  mutate: (artifact: Record<string, unknown>) => void,
): Awaited<ReturnType<typeof persistedFixture>> {
  const manifestPath = join(fixture.evidenceDirectory, 'manifest.json')
  const manifest = JSON.parse(
    readFileSync(manifestPath, 'utf8'),
  ) as PiModelCertificationManifest
  const run = manifest.runs[0]
  const artifactPath = join(
    fixture.evidenceDirectory,
    'run-1',
    filename,
  )
  const artifact = JSON.parse(
    readFileSync(artifactPath, 'utf8'),
  ) as Record<string, unknown>
  mutate(artifact)
  const artifactBytes = secureFile(
    artifactPath,
    `${JSON.stringify(artifact)}\n`,
  )
  if (filename === 'pi-run-audit.json') {
    run.artifacts.piRunAuditSha256 = sha256(artifactBytes)
  } else if (filename === 'report-document.json') {
    run.artifacts.reportDocumentSha256 = sha256(artifactBytes)
  } else {
    run.artifacts.reportAuditManifestSha256 =
      sha256(artifactBytes)
  }
  const { runEvidenceId: _previousRunEvidenceId, ...runBody } = run
  run.runEvidenceId = `candidate-run:${canonicalSha256(runBody)}`
  if (manifest.scenarioCoverage !== undefined) {
    const representativeIds =
      manifest.scenarioCoverage
        .representativeRepeatRunEvidenceIds as [string, string]
    representativeIds[0] = run.runEvidenceId
  }
  const evidenceId = `evidence:model-certification:${canonicalSha256(
    certificationEvidenceBody(manifest),
  )}`
  manifest.evidenceId = evidenceId
  secureFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)

  const evidenceDirectory = join(
    fixture.root,
    'artifacts',
    'country-outage-agent',
    'a4-model-certification',
    evidenceId,
  )
  renameSync(fixture.evidenceDirectory, evidenceDirectory)
  return {
    ...fixture,
    evidenceId,
    evidenceDirectory,
  }
}

function rebindTamperedScenarioJson(
  fixture: Awaited<ReturnType<typeof persistedFixture>>,
  filename:
    | 'pi-run-audit.json'
    | 'report-document.json'
    | 'audit-manifest.json',
  mutate: (artifact: Record<string, unknown>) => void,
  scenarioIndex = 0,
): Awaited<ReturnType<typeof persistedFixture>> {
  const manifestPath = join(fixture.evidenceDirectory, 'manifest.json')
  const manifest = JSON.parse(
    readFileSync(manifestPath, 'utf8'),
  ) as PiModelCertificationManifest
  assert.ok(manifest.scenarioCoverage)
  const scenario = manifest.scenarioCoverage.scenarios[scenarioIndex]
  assert.ok(scenario)
  const artifactPath = join(
    fixture.evidenceDirectory,
    `scenario-${scenario.scenarioId}`,
    filename,
  )
  const artifact = JSON.parse(
    readFileSync(artifactPath, 'utf8'),
  ) as Record<string, unknown>
  mutate(artifact)
  const artifactBytes = secureFile(
    artifactPath,
    `${JSON.stringify(artifact)}\n`,
  )
  if (filename === 'pi-run-audit.json') {
    scenario.artifacts.piRunAuditSha256 = sha256(artifactBytes)
  } else if (filename === 'report-document.json') {
    scenario.artifacts.reportDocumentSha256 = sha256(artifactBytes)
  } else {
    scenario.artifacts.reportAuditManifestSha256 =
      sha256(artifactBytes)
  }
  const {
    scenarioEvidenceId: _previousScenarioEvidenceId,
    ...scenarioBody
  } = scenario
  scenario.scenarioEvidenceId =
    `candidate-scenario:${canonicalSha256(scenarioBody)}`
  manifest.evidenceId =
    `evidence:model-certification:${canonicalSha256(
      certificationEvidenceBody(manifest),
    )}`
  secureFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)

  const evidenceDirectory = join(
    fixture.root,
    'artifacts',
    'country-outage-agent',
    'a4-model-certification',
    manifest.evidenceId,
  )
  renameSync(fixture.evidenceDirectory, evidenceDirectory)
  return {
    ...fixture,
    evidenceId: manifest.evidenceId,
    evidenceDirectory,
  }
}

function rebindTamperedPiRunAudit(
  fixture: Awaited<ReturnType<typeof persistedFixture>>,
  mutate: (audit: {
    runtimeSecurity: {
      structuredOutput: {
        applicability: string
        mechanism: string
        payloadPreparedCount: number
      }
    }
  }) => void,
): Awaited<ReturnType<typeof persistedFixture>> {
  return rebindTamperedRunJson(
    fixture,
    'pi-run-audit.json',
    (artifact) => {
      mutate(
        artifact as unknown as Parameters<typeof mutate>[0],
      )
    },
  )
}

function setNestedField(
  artifact: Record<string, unknown>,
  objectKey: string,
  field: string,
  value: unknown,
): void {
  const nested = artifact[objectKey]
  assert.ok(
    nested !== null &&
      typeof nested === 'object' &&
      !Array.isArray(nested),
  )
  const record = nested as Record<string, unknown>
  record[field] = value
}

async function assertPromotionRejectedWithoutRegistryWrite(
  fixture: Awaited<ReturnType<typeof persistedFixture>>,
): Promise<void> {
  const before = readFileSync(fixture.registryPath, 'utf8')
  await assert.rejects(
    promotePersistedA4ModelCandidate({
      evidenceId: fixture.evidenceId,
      newRegistryVersion: 'must-not-write',
      repositoryRoot: fixture.root,
      registryPath: fixture.registryPath,
      responseModelAdapterInspector: () => ({
        sameNamePreserved: true,
        sourceSha256: 'a'.repeat(64),
      }),
      now: FIXED_PROMOTION_NOW,
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_manifest_invalid',
  )
  assert.equal(readFileSync(fixture.registryPath, 'utf8'), before)
}

test('晋级门使用当前项目知识、校验规则与现场 Skill 包摘要', () => {
  assert.match(
    COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
    /^country_outage_report_skill_v[1-9]\d*$/,
  )
  assert.equal(
    COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
    'country_outage_report_validator_rules_v5',
  )
  assert.match(CURRENT_SKILL_BUNDLE_SHA256, /^[a-f0-9]{64}$/)
})

test('机械晋级按真实 Pi 规范化带空格 reference 并只从固定 evidence 目录原子更新注册表', async () => {
  const fixture = await persistedFixture('success')
  const manifest = JSON.parse(
    readFileSync(
      join(fixture.evidenceDirectory, 'manifest.json'),
      'utf8',
    ),
  ) as PiModelCertificationManifest
  assert.ok(manifest.scenarioCoverage)
  assert.ok(manifest.certificationProfile)
  assert.deepEqual(
    manifest.scenarioCoverage.scenarios.map((scenario) => ({
      scenarioId: scenario.scenarioId,
      certificationOnly: scenario.certificationOnly,
      synthetic: scenario.synthetic,
      differsFromRepresentativeFacts:
        scenario.factSetId !== manifest.runs[0].factSetId &&
        scenario.snapshotSha256 !==
          manifest.runs[0].snapshotSha256 &&
        scenario.reportContentSha256 !==
          manifest.runs[0].reportContentSha256,
    })),
    SCENARIOS.map((scenario) => ({
      scenarioId: scenario.id,
      certificationOnly: true,
      synthetic: true,
      differsFromRepresentativeFacts: true,
    })),
  )
  for (const scenario of SCENARIOS) {
    assert.equal(
      readFileSync(
        join(
          fixture.evidenceDirectory,
          `scenario-${scenario.id}`,
          'CERTIFICATION-ONLY.txt',
        ),
        'utf8',
      ),
      CERTIFICATION_ONLY_MARKER,
    )
  }
  const report = JSON.parse(
    readFileSync(
      join(
        fixture.evidenceDirectory,
        'run-1',
        'report-document.json',
      ),
      'utf8',
    ),
  ) as { event: { legacy_reference: string } }
  const piAudit = JSON.parse(
    readFileSync(
      join(fixture.evidenceDirectory, 'run-1', 'pi-run-audit.json'),
      'utf8',
    ),
  ) as { input: { eventReferenceSha256: string } }
  assert.equal(
    report.event.legacy_reference,
    FIXTURE_EVENT_REFERENCE,
  )
  assert.equal(
    piAudit.input.eventReferenceSha256,
    sha256(FIXTURE_EVENT_REFERENCE.replace(' ', '+')),
  )
  assert.notEqual(
    piAudit.input.eventReferenceSha256,
    sha256(FIXTURE_EVENT_REFERENCE),
  )
  const result = await promotePersistedA4ModelCandidate({
    evidenceId: fixture.evidenceId,
    newRegistryVersion: 'deepseek-v4-flash-certified-v1',
    repositoryRoot: fixture.root,
    registryPath: fixture.registryPath,
    responseModelAdapterInspector: () => ({
      sameNamePreserved: true,
      sourceSha256: 'a'.repeat(64),
    }),
    now: FIXED_PROMOTION_NOW,
  })
  assert.equal(result.certificationEvidenceId, fixture.evidenceId)
  assert.equal(
    result.registryVersion,
    'deepseek-v4-flash-certified-v1',
  )
  const registry = JSON.parse(
    readFileSync(fixture.registryPath, 'utf8'),
  ) as { profiles: Array<Record<string, unknown>> }
  assert.equal(registry.profiles.length, 1)
  assert.equal(
    registry.profiles[0]?.certificationEvidenceId,
    fixture.evidenceId,
  )
  assert.deepEqual(
    {
      modelRevisionKind:
        registry.profiles[0]?.modelRevisionKind,
      immutableRevisionAvailable:
        registry.profiles[0]?.immutableRevisionAvailable,
      limitation: registry.profiles[0]?.limitation,
      certificationValidUntil:
        registry.profiles[0]?.certificationValidUntil,
      certifiedScenarioSetId:
        registry.profiles[0]?.certifiedScenarioSetId,
      certifiedInputScope:
        registry.profiles[0]?.certifiedInputScope,
    },
    {
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
    },
  )
})

test('旧无场景证书与场景目录结构异常均禁止晋级且注册表零写入', async (context) => {
  const cases: readonly {
    label: string
    createFixture: () => Promise<
      Awaited<ReturnType<typeof persistedFixture>>
    >
    mutate?: (
      fixture: Awaited<ReturnType<typeof persistedFixture>>,
    ) => void
  }[] = [
    {
      label: '旧 formal runner 无场景证书',
      createFixture: () =>
        persistedFixture('reject-old-no-scenarios', {
          includeScenarios: false,
        }),
    },
    {
      label: '缺少固定场景目录',
      createFixture: () =>
        persistedFixture('reject-missing-scenario-directory'),
      mutate(fixture) {
        rmSync(
          join(
            fixture.evidenceDirectory,
            'scenario-capability-degraded-final',
          ),
          { recursive: true },
        )
      },
    },
    {
      label: '场景目录缺少必需制品',
      createFixture: () =>
        persistedFixture('reject-missing-scenario-artifact'),
      mutate(fixture) {
        rmSync(
          join(
            fixture.evidenceDirectory,
            'scenario-capability-degraded-final',
            'audit-manifest.json',
          ),
        )
      },
    },
    {
      label: '场景目录含额外文件',
      createFixture: () =>
        persistedFixture('reject-extra-scenario-file'),
      mutate(fixture) {
        secureFile(
          join(
            fixture.evidenceDirectory,
            'scenario-capability-degraded-final',
            'unexpected.txt',
          ),
          'unexpected',
        )
      },
    },
    {
      label: '认证专用标记被篡改',
      createFixture: () =>
        persistedFixture('reject-tampered-scenario-marker'),
      mutate(fixture) {
        secureFile(
          join(
            fixture.evidenceDirectory,
            'scenario-capability-degraded-final',
            'CERTIFICATION-ONLY.txt',
          ),
          '可对外发布\n',
        )
      },
    },
  ]

  for (const item of cases) {
    await context.test(item.label, async () => {
      const fixture = await item.createFixture()
      item.mutate?.(fixture)
      await assertPromotionRejectedWithoutRegistryWrite(fixture)
    })
  }
})

test('场景制品篡改即使重算摘要和两级证据身份也禁止晋级且注册表零写入', async (context) => {
  const cases: readonly {
    label: string
    filename:
      | 'pi-run-audit.json'
      | 'report-document.json'
      | 'audit-manifest.json'
    mutate: (artifact: Record<string, unknown>) => void
  }[] = [
    {
      label: 'artifactId 漂移',
      filename: 'report-document.json',
      mutate(artifact) {
        artifact.artifactId = 'scenario_artifact_cross_file_drift'
      },
    },
    {
      label: 'reportContentSha256 漂移',
      filename: 'report-document.json',
      mutate(artifact) {
        artifact.reportContentSha256 = 'f'.repeat(64)
      },
    },
    {
      label: 'factSetId 漂移',
      filename: 'report-document.json',
      mutate(artifact) {
        artifact.factSetId = 'scenario_fact_set_cross_file_drift'
      },
    },
    {
      label: 'snapshot 身份漂移',
      filename: 'audit-manifest.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'snapshotIdentity',
          'publicationId',
          'scenario_publication_cross_file_drift',
        )
      },
    },
    {
      label: '项目知识版本漂移',
      filename: 'report-document.json',
      mutate(artifact) {
        artifact.projectKnowledgeVersion =
          `${COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION}-tampered`
      },
    },
    {
      label: 'Skill 摘要漂移',
      filename: 'pi-run-audit.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'runtimeSecurity',
          'skillBundleSha256',
          '0'.repeat(64),
        )
      },
    },
    {
      label: '模型身份漂移',
      filename: 'report-document.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'model',
          'modelVersion',
          'deepseek-v4-flash-cross-file-drift',
        )
      },
    },
  ]

  for (const [index, item] of cases.entries()) {
    await context.test(item.label, async () => {
      const originalFixture = await persistedFixture(
        `reject-scenario-identity-${index + 1}`,
      )
      const fixture = rebindTamperedScenarioJson(
        originalFixture,
        item.filename,
        item.mutate,
      )
      await assertPromotionRejectedWithoutRegistryWrite(fixture)
    })
  }
})

test('落盘证据被删改、替换为符号链接或包含额外文件时晋级零写入', async (context) => {
  for (const scenario of [
    {
      label: '摘要不匹配',
      mutate(fixture: Awaited<ReturnType<typeof persistedFixture>>) {
        secureFile(
          join(fixture.evidenceDirectory, 'run-1', 'report.md'),
          '# 已篡改\n',
        )
      },
    },
    {
      label: '清单符号链接',
      mutate(fixture: Awaited<ReturnType<typeof persistedFixture>>) {
        const manifestPath = join(
          fixture.evidenceDirectory,
          'manifest.json',
        )
        const copyPath = join(fixture.root, 'manifest-copy.json')
        secureFile(copyPath, readFileSync(manifestPath))
        rmSync(manifestPath)
        symlinkSync(copyPath, manifestPath)
      },
    },
    {
      label: '额外未声明文件',
      mutate(fixture: Awaited<ReturnType<typeof persistedFixture>>) {
        secureFile(
          join(fixture.evidenceDirectory, 'unexpected.txt'),
          'unexpected',
        )
      },
    },
  ] as const) {
    await context.test(scenario.label, async () => {
      const fixture = await persistedFixture(
        `reject-${scenario.label}`,
      )
      scenario.mutate(fixture)
      const before = readFileSync(fixture.registryPath, 'utf8')
      await assert.rejects(
        promotePersistedA4ModelCandidate({
          evidenceId: fixture.evidenceId,
          newRegistryVersion: 'must-not-write',
          repositoryRoot: fixture.root,
          registryPath: fixture.registryPath,
          responseModelAdapterInspector: () => ({
            sameNamePreserved: true,
            sourceSha256: 'a'.repeat(64),
          }),
          now: FIXED_PROMOTION_NOW,
        }),
        (error: unknown) =>
          error instanceof PiModelCertificationError &&
          error.code === 'certification_manifest_invalid',
      )
      assert.equal(readFileSync(fixture.registryPath, 'utf8'), before)
    })
  }
})

test('语义篡改即使重算全部摘要与证据身份也禁止晋级', async () => {
  const originalFixture = await persistedFixture(
    'reject-semantic-tampering',
  )
  const fixture = rebindTamperedPiRunAudit(
    originalFixture,
    (audit) => {
      audit.runtimeSecurity.structuredOutput.payloadPreparedCount = 2
    },
  )
  const manifest = JSON.parse(
    readFileSync(
      join(fixture.evidenceDirectory, 'manifest.json'),
      'utf8',
    ),
  ) as PiModelCertificationManifest
  const run = manifest.runs[0]
  const auditBytes = readFileSync(
    join(
      fixture.evidenceDirectory,
      'run-1',
      'pi-run-audit.json',
    ),
  )
  assert.equal(
    sha256(auditBytes),
    run.artifacts.piRunAuditSha256,
  )
  const { runEvidenceId, ...runBody } = run
  assert.equal(
    runEvidenceId,
    `candidate-run:${canonicalSha256(runBody)}`,
  )
  assert.equal(manifest.evidenceId, fixture.evidenceId)
  assert.equal(run.checks.structuredOutput.payloadPreparedCount, 1)
  assert.equal(
    (
      JSON.parse(auditBytes.toString('utf8')) as {
        runtimeSecurity: {
          structuredOutput: { payloadPreparedCount: number }
        }
      }
    ).runtimeSecurity.structuredOutput.payloadPreparedCount,
    2,
  )

  const before = readFileSync(fixture.registryPath, 'utf8')
  await assert.rejects(
    promotePersistedA4ModelCandidate({
      evidenceId: fixture.evidenceId,
      newRegistryVersion: 'must-not-write',
      repositoryRoot: fixture.root,
      registryPath: fixture.registryPath,
      responseModelAdapterInspector: () => ({
        sameNamePreserved: true,
        sourceSha256: 'a'.repeat(64),
      }),
      now: FIXED_PROMOTION_NOW,
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_manifest_invalid',
  )
  assert.equal(readFileSync(fixture.registryPath, 'utf8'), before)
})

test('旧身份或跨文件身份漂移即使重算全部摘要与证据身份也禁止晋级', async (context) => {
  const scenarios: readonly {
    label: string
    filename:
      | 'pi-run-audit.json'
      | 'report-document.json'
      | 'audit-manifest.json'
    mutate: (artifact: Record<string, unknown>) => void
  }[] = [
    {
      label: 'Pi audit 项目知识旧版本',
      filename: 'pi-run-audit.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'input',
          'projectKnowledgeVersion',
          'country_outage_report_skill_v3',
        )
      },
    },
    {
      label: 'Pi audit 校验规则旧版本',
      filename: 'pi-run-audit.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'input',
          'validatorRulesVersion',
          'country_outage_report_validator_rules_v4',
        )
      },
    },
    {
      label: 'Pi audit Skill 摘要过期',
      filename: 'pi-run-audit.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'runtimeSecurity',
          'skillBundleSha256',
          '0'.repeat(64),
        )
      },
    },
    {
      label: 'Pi audit 语言槽终检状态漂移',
      filename: 'pi-run-audit.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'narration',
          'finalV5',
          'failed',
        )
      },
    },
    {
      label: '报告项目知识旧版本',
      filename: 'report-document.json',
      mutate(artifact) {
        artifact.projectKnowledgeVersion =
          'country_outage_report_skill_v3'
      },
    },
    {
      label: '报告校验规则旧版本',
      filename: 'report-document.json',
      mutate(artifact) {
        artifact.validatorRulesVersion =
          'country_outage_report_validator_rules_v4'
      },
    },
    {
      label: '报告 Skill 摘要过期',
      filename: 'report-document.json',
      mutate(artifact) {
        artifact.skillBundleSha256 = '0'.repeat(64)
      },
    },
    {
      label: '报告与 manifest run 的 artifactId 漂移',
      filename: 'report-document.json',
      mutate(artifact) {
        artifact.artifactId = 'report_cross_file_drift'
      },
    },
    {
      label: '审计清单项目知识旧版本',
      filename: 'audit-manifest.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'contractIdentity',
          'projectKnowledgeVersion',
          'country_outage_report_skill_v3',
        )
      },
    },
    {
      label: '审计清单校验规则旧版本',
      filename: 'audit-manifest.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'contractIdentity',
          'validatorRulesVersion',
          'country_outage_report_validator_rules_v4',
        )
      },
    },
    {
      label: '审计清单 Skill 摘要过期',
      filename: 'audit-manifest.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'contractIdentity',
          'skillBundleSha256',
          '0'.repeat(64),
        )
      },
    },
    {
      label: '审计清单与报告的 snapshot 漂移',
      filename: 'audit-manifest.json',
      mutate(artifact) {
        setNestedField(
          artifact,
          'snapshotIdentity',
          'publicationId',
          'publication_v1_cross_file_drift',
        )
      },
    },
  ]

  for (const [index, scenario] of scenarios.entries()) {
    await context.test(scenario.label, async () => {
      const originalFixture = await persistedFixture(
        `reject-identity-${index + 1}`,
      )
      const fixture = rebindTamperedRunJson(
        originalFixture,
        scenario.filename,
        scenario.mutate,
      )
      const before = readFileSync(fixture.registryPath, 'utf8')
      await assert.rejects(
        promotePersistedA4ModelCandidate({
          evidenceId: fixture.evidenceId,
          newRegistryVersion: 'must-not-write',
          repositoryRoot: fixture.root,
          registryPath: fixture.registryPath,
          responseModelAdapterInspector: () => ({
            sameNamePreserved: true,
            sourceSha256: 'a'.repeat(64),
          }),
          now: FIXED_PROMOTION_NOW,
        }),
        (error: unknown) =>
          error instanceof PiModelCertificationError &&
          error.code === 'certification_manifest_invalid',
      )
      assert.equal(readFileSync(fixture.registryPath, 'utf8'), before)
    })
  }
})

test('evidenceId 不能成为任意路径输入', async () => {
  await assert.rejects(
    readPersistedA4CertificationEvidence({
      evidenceId: '../../outside',
      repositoryRoot: TEST_ROOT,
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_manifest_invalid',
  )
})
