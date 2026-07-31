import type {
  CountryOutageAsnPage,
  CountryOutageFactSet,
} from '../domain/contracts.js'
import type {
  AsnQuery,
  DomeyeCountryOutageClient,
} from '../domain/domeye-client.js'
import { assembleCountryOutageFacts } from '../domain/observation-assembler.js'
import {
  DOMEYE_ONLY_EVIDENCE_MODE,
  DeterministicCountryOutageQuestionEngine,
  type CountryOutageQuestionContext,
  type ReportQuestionAnchor,
} from '../qa/index.js'
import type { CountryOutageArtifactBuilder } from '../report/artifact-builder.js'
import type {
  ReportEvidenceBundle,
  ReportNarrator,
} from '../report/contracts.js'
import { CountryOutageReportCompiler } from '../report/report-compiler.js'
import {
  canonicalJsonSha256,
  compareUnicodeCodePoints,
} from '../shared/deterministic-json.js'
import type {
  CountryOutageQuestionService,
  CountryOutageReportService,
  QuestionAnswerInput,
  ReportGenerationInput,
  ReportQuestionContext,
  ReportQuote,
} from '../server/contracts.js'
import { CountryOutageHttpError } from '../server/errors.js'

interface RuntimeQuestionPayload {
  schemaVersion: 'country_outage_runtime_question_context_v1'
  facts: CountryOutageFactSet
  asnPages: CountryOutageAsnPage[]
}

export interface RuntimeReportServiceOptions {
  client: Pick<
    DomeyeCountryOutageClient,
    'getObservationBatch' | 'getAsns'
  >
  narrator: ReportNarrator
  artifactBuilder: CountryOutageArtifactBuilder
  now?: () => Date
  asnPageSize?: number
}

function evidenceRefFromProvenance(
  endpoint: string,
  pointer: string,
): string {
  return `${endpoint}:${pointer}`
}

/**
 * 枚举当前冻结事实合同内、确定性问答引擎可能合法返回的证据标识。
 * Server 仍会逐条要求回答引用命中该集合，不能凭前缀扩大到其他快照。
 */
export function collectRuntimeQuestionEvidenceRefs(
  context: CountryOutageQuestionContext,
): string[] {
  const refs = new Set<string>()
  const add = (values: readonly string[]): void => {
    for (const value of values) refs.add(value)
  }

  add(context.report.draft.summary.evidenceRefs)
  refs.add('report:/summary')
  context.report.draft.highlights.forEach((highlight, index) => {
    add(highlight.evidenceRefs)
    refs.add(`report:/highlights/${index}`)
  })
  context.report.draft.sections.forEach((section) => {
    section.paragraphs.forEach((paragraph, index) => {
      add(paragraph.evidenceRefs)
      refs.add(`report:/sections/${section.id}/paragraphs/${index}`)
    })
  })
  context.report.draft.unknowns.forEach((_unknown, index) => {
    refs.add(`report:/unknowns/${index}`)
  })

  for (const point of context.facts.keyVisibilityPoints) {
    refs.add(
      evidenceRefFromProvenance(
        point.provenance.endpoint,
        point.provenance.pointer,
      ),
    )
  }
  for (const fact of context.facts.derivedFacts) refs.add(fact.factId)

  add([
    'overview:/limitations',
    'overview:/cohort',
    'overview:/cohort/origin_asn_count',
    'overview:/cohort/prefix_vp_count',
    'overview:/cohort/denominator_policy',
    'overview:/observation_scope',
    'overview:/observation_scope/window_end_utc',
    'overview:/observation_scope/last_observation_at_utc',
    'overview:/observation_scope/interval_seconds',
    'audit:/evidence_level',
  ])
  for (const capability of Object.keys(context.facts.capabilities)) {
    refs.add(`overview:/capabilities/${capability}`)
  }
  for (const metric of Object.keys(context.facts.metricExtrema)) {
    refs.add(`series:/metric_extrema/${metric}/min`)
    refs.add(`series:/metric_extrema/${metric}/max`)
  }
  for (const metric of Object.keys(context.facts.resourceMetricExtrema)) {
    refs.add(`series:/resource_metric_extrema/${metric}/min`)
    refs.add(`series:/resource_metric_extrema/${metric}/max`)
  }
  context.asnPages.forEach((page) => {
    page.items.forEach((_item, index) => {
      refs.add(`asns:/pages/${page.page}/items/${index}`)
      refs.add(`asns:/items/${index}`)
    })
  })
  return [...refs].sort(compareUnicodeCodePoints)
}

function assertRequestedSnapshot(
  input: ReportGenerationInput,
  evidence: ReportEvidenceBundle,
): void {
  const { snapshot, event } = evidence.facts
  if (
    event.legacy_reference.replace(' ', '+') !==
      input.eventReference.replace(' ', '+') ||
    snapshot.publicationId !== input.publicationId ||
    snapshot.revision !== input.revision ||
    snapshot.collectorId !== 'rrc25'
  ) {
    throw new CountryOutageHttpError(
      409,
      'snapshot_identity_conflict',
      '读取到的国家中断快照与用户触发时固定的身份不一致',
      true,
      '刷新数据观测页后生成新版报告',
    )
  }
}

export class RuntimeCountryOutageReportService
implements CountryOutageReportService {
  readonly #client: RuntimeReportServiceOptions['client']
  readonly #narrator: ReportNarrator
  readonly #artifactBuilder: CountryOutageArtifactBuilder
  readonly #now: (() => Date) | undefined
  readonly #asnPageSize: number

  constructor(options: RuntimeReportServiceOptions) {
    this.#client = options.client
    this.#narrator = options.narrator
    this.#artifactBuilder = options.artifactBuilder
    this.#now = options.now
    this.#asnPageSize = Math.min(60, Math.max(1, options.asnPageSize ?? 10))
  }

  async generate(
    input: ReportGenerationInput,
  ): Promise<Awaited<ReturnType<CountryOutageReportService['generate']>>> {
    input.signal.throwIfAborted()
    const batch = await this.#client.getObservationBatch(
      input.eventReference,
      input.signal,
    )
    input.signal.throwIfAborted()
    const preliminaryFacts = assembleCountryOutageFacts(batch)
    assertRequestedSnapshot(input, {
      facts: preliminaryFacts,
      asnPages: [],
    })

    const pinnedClient = {
      getObservationBatch: async (
        reference: string,
        signal?: AbortSignal,
      ) => {
        const activeSignal = signal ?? input.signal
        activeSignal.throwIfAborted()
        if (
          reference.replace(' ', '+') !==
          input.eventReference.replace(' ', '+')
        ) {
          throw new CountryOutageHttpError(
            400,
            'event_binding_conflict',
            '报告运行不能切换到其他事件',
          )
        }
        return batch
      },
      getAsns: async (
        snapshot: CountryOutageFactSet['snapshot'],
        query?: AsnQuery,
        signal?: AbortSignal,
      ) =>
        await this.#client.getAsns(
          snapshot,
          query,
          signal ?? input.signal,
        ),
    }
    input.onPhase('generating_report')
    const compiler = new CountryOutageReportCompiler({
      client: pinnedClient,
      narrator: this.#narrator,
      asnPageSize: this.#asnPageSize,
      ...(this.#now ? { now: this.#now } : {}),
    })
    const compiled = await compiler.compileWithEvidence(
      input.eventReference,
      input.signal,
    )
    input.signal.throwIfAborted()
    assertRequestedSnapshot(input, compiled.evidence)

    input.onPhase('validating')
    const artifacts = await this.#artifactBuilder.build(
      compiled.document,
      input.signal,
    )
    input.signal.throwIfAborted()
    const qaContext: CountryOutageQuestionContext = {
      report: compiled.document,
      facts: compiled.evidence.facts,
      asnPages: compiled.evidence.asnPages,
    }
    const payload: RuntimeQuestionPayload = {
      schemaVersion: 'country_outage_runtime_question_context_v1',
      facts: compiled.evidence.facts,
      asnPages: compiled.evidence.asnPages,
    }
    return {
      document: compiled.document,
      artifacts,
      questionContext: {
        factSetId: compiled.document.factSetId,
        snapshot: compiled.document.snapshot,
        evidenceRefs: collectRuntimeQuestionEvidenceRefs(qaContext),
        payload,
      },
    }
  }
}

function runtimePayload(
  context: ReportQuestionContext,
): RuntimeQuestionPayload {
  const value = context.payload
  if (
    !value ||
    typeof value !== 'object' ||
    (value as RuntimeQuestionPayload).schemaVersion !==
      'country_outage_runtime_question_context_v1'
  ) {
    throw new CountryOutageHttpError(
      422,
      'question_context_unavailable',
      '当前短期会话缺少冻结的追问事实合同',
    )
  }
  return value as RuntimeQuestionPayload
}

function qaAnchor(quote: ReportQuote | undefined): ReportQuestionAnchor | undefined {
  if (!quote) return undefined
  if (quote.kind === 'summary') return { kind: 'summary' }
  if (quote.kind === 'highlight') {
    return { kind: 'highlight', highlightIndex: quote.highlightIndex }
  }
  return {
    kind: 'section_paragraph',
    sectionId: quote.sectionId as ReportQuestionAnchor extends {
      sectionId: infer T
    } ? T : never,
    paragraphIndex: quote.paragraphIndex,
  }
}

export class RuntimeCountryOutageQuestionService
implements CountryOutageQuestionService {
  readonly #engine: DeterministicCountryOutageQuestionEngine

  constructor(
    engine: DeterministicCountryOutageQuestionEngine =
      new DeterministicCountryOutageQuestionEngine(),
  ) {
    this.#engine = engine
  }

  async answer(input: QuestionAnswerInput) {
    if (input.evidenceMode !== 'domeye_only') {
      throw new CountryOutageHttpError(
        409,
        'external_evidence_disabled',
        '当前追问只允许“仅使用 Domeye 数据”',
      )
    }
    const payload = runtimePayload(input.questionContext)
    const context: CountryOutageQuestionContext = {
      report: input.report,
      facts: payload.facts,
      asnPages: payload.asnPages,
    }
    const fingerprint = canonicalJsonSha256({
      reportId: input.reportId,
      artifactId: input.report.artifactId,
      question: input.question.trim(),
      quote: input.quote ?? null,
    })
    const anchor = qaAnchor(input.quote)
    const result = await this.#engine.answer(
      {
        schemaVersion: 'country_outage_question_request_v1',
        requestId: `question_${fingerprint.slice(0, 32)}`,
        idempotencyKey: `answer:${fingerprint}`,
        binding: {
          reportArtifactId: input.report.artifactId,
          reportContentSha256: input.report.reportContentSha256,
          factSetId: input.report.factSetId,
          snapshot: input.report.snapshot,
          evidenceMode: DOMEYE_ONLY_EVIDENCE_MODE,
          ...(anchor ? { anchor } : {}),
        },
        question: input.question,
      },
      context,
      { signal: input.signal },
    )
    return {
      kind: result.kind,
      text: result.text,
      evidenceRefs: result.evidenceRefs,
      evidenceRecords: result.evidence.map((record) => ({
        evidenceRef: record.ref,
        source: record.source,
        label: record.label,
        metric: record.metric,
        value: record.value,
        observedAtUtc: record.observedAtUtc,
        observedAtLocal: record.observedAtLocal,
        statisticalScope: record.statisticalScope,
      })),
      missingEvidence: result.missingEvidence,
      limitations:
        result.kind === 'evidence_boundary'
          ? ['现有证据仅支持 RRC25 BGP 控制面观测，不能外推为数据面结论。']
          : [],
    }
  }
}
