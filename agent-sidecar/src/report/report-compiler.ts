import type {
  AsnQuery,
  DomeyeCountryOutageClient,
} from '../domain/domeye-client.js'
import type {
  CountryOutageAsnPage,
  ObservationBatch,
  SnapshotIdentity,
} from '../domain/contracts.js'
import { assembleCountryOutageFacts } from '../domain/observation-assembler.js'
import { assertCountryOutageEvidenceCapacity } from '../formal-runtime-limits.js'
import {
  computeCountryOutageSkillBundleSha256,
  COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
} from '../pi/country-outage-skill-bundle.js'
import { canonicalJsonSha256 } from '../shared/deterministic-json.js'
import type {
  CountryOutageReportDocument,
  ReportEvidenceBundle,
  ReportNarrator,
} from './contracts.js'
import {
  COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
  validateReportDraft,
} from './draft-validator.js'

export class ReportValidationError extends Error {
  constructor(readonly errors: string[]) {
    super(`正式报告机器校验失败：${errors.join('；')}`)
    this.name = 'ReportValidationError'
  }
}

export interface ReportCompilerOptions {
  client: CountryOutageReportDataSource
  narrator: ReportNarrator
  now?: () => Date
  asnPageSize?: number
}

export interface CompiledCountryOutageReport {
  document: CountryOutageReportDocument
  evidence: ReportEvidenceBundle
}

export type CountryOutageReportDataSource = Pick<
  DomeyeCountryOutageClient,
  'getObservationBatch' | 'getAsns'
> & {
  getObservationBatch(
    reference: string,
    signal?: AbortSignal,
  ): Promise<ObservationBatch>
  getAsns(
    snapshot: SnapshotIdentity,
    query?: AsnQuery,
    signal?: AbortSignal,
  ): Promise<CountryOutageAsnPage>
}

export class CountryOutageReportCompiler {
  readonly #client: CountryOutageReportDataSource
  readonly #narrator: ReportNarrator
  readonly #now: () => Date
  readonly #asnPageSize: number
  readonly #startupSkillBundleSha256: string

  constructor(options: ReportCompilerOptions) {
    this.#client = options.client
    this.#narrator = options.narrator
    this.#now = options.now ?? (() => new Date())
    this.#asnPageSize = Math.min(60, Math.max(1, options.asnPageSize ?? 10))
    this.#startupSkillBundleSha256 =
      computeCountryOutageSkillBundleSha256()
    this.#assertNarratorIdentity()
  }

  #assertNarratorIdentity(): void {
    if (
      this.#narrator.validatorRulesVersion !==
        COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION ||
      this.#narrator.skillBundleSha256 !==
        this.#startupSkillBundleSha256
    ) {
      throw new ReportValidationError([
        '叙述器声明的报告校验规则版本或 Skill 摘要与启动时固定资源不一致',
      ])
    }
  }

  async compile(
    reference: string,
    signal?: AbortSignal,
  ): Promise<CountryOutageReportDocument> {
    return (await this.compileWithEvidence(reference, signal)).document
  }

  async compileWithEvidence(
    reference: string,
    signal?: AbortSignal,
  ): Promise<CompiledCountryOutageReport> {
    signal?.throwIfAborted()
    // 在任何数据读取或叙述器调用之前重新核对，阻断启动后的可变 getter
    // 或运行时对象篡改；后续身份只使用启动时实际加载资源的摘要。
    this.#assertNarratorIdentity()
    const batch = await this.#client.getObservationBatch(reference, signal)
    signal?.throwIfAborted()
    const facts = assembleCountryOutageFacts(batch)
    const asnPages =
      facts.capabilities.asn_matrix?.state === 'available'
        ? [
            await this.#client.getAsns(
              facts.snapshot,
              {
                page: 1,
                pageSize: this.#asnPageSize,
                sort: 'longest_fully_invisible_desc',
              },
              signal,
            ),
          ]
        : []
    signal?.throwIfAborted()
    const evidence: ReportEvidenceBundle = { facts, asnPages }
    // 必须在叙述器或任何模型调用之前完成确定性容量判定。
    assertCountryOutageEvidenceCapacity(evidence)
    const draft = await this.#narrator.generate({
      reference,
      evidence,
      ...(signal ? { signal } : {}),
    })
    const validation = validateReportDraft(draft, evidence)
    if (!validation.passed) {
      throw new ReportValidationError(validation.errors)
    }
    const reportContentSha256 = canonicalJsonSha256({
      factSetId: facts.factSetId,
      draft,
      reportSpecificationVersion: 'country_outage_report_spec_v1',
      projectKnowledgeVersion:
        COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
      validatorRulesVersion: this.#narrator.validatorRulesVersion,
      skillBundleSha256: this.#startupSkillBundleSha256,
    })
    const artifactId = `report_${canonicalJsonSha256({
      snapshot: facts.snapshot,
      factSetId: facts.factSetId,
      reportContentSha256,
      model: this.#narrator.identity,
      reportSpecificationVersion: 'country_outage_report_spec_v1',
      projectKnowledgeVersion:
        COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
      validatorRulesVersion: this.#narrator.validatorRulesVersion,
      skillBundleSha256: this.#startupSkillBundleSha256,
    }).slice(0, 32)}`
    const document: CountryOutageReportDocument = {
      schemaVersion: 'country_outage_report_document_v1',
      artifactId,
      reportContentSha256,
      reportSpecificationVersion: 'country_outage_report_spec_v1',
      projectKnowledgeVersion:
        COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
      validatorRulesVersion: this.#narrator.validatorRulesVersion,
      skillBundleSha256: this.#startupSkillBundleSha256,
      generatedAt: this.#now().toISOString(),
      aiGenerated: true,
      humanReviewed: false,
      event: facts.event,
      snapshot: facts.snapshot,
      factSetId: facts.factSetId,
      model: this.#narrator.identity,
      validation,
      draft,
    }
    this.#assertNarratorIdentity()
    return { document, evidence }
  }
}
