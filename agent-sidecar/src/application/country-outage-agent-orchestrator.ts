import type { CountryOutageCore } from '../core/contracts.js'
import type {
  CountryOutageAgentEvent,
  CountryOutagePrincipal,
  CreateReportRequest,
} from '../server/contracts.js'
import { CountryOutageHttpError } from '../server/errors.js'
import {
  DisabledAnnexComposer,
  type AnnexComposer,
} from './annex-composer.js'
import {
  DisabledExternalEvidenceProvider,
} from './external-evidence-provider.js'
import type {
  CreateOrchestratedQuestionRequest,
  ExternalEvidenceProvider,
} from './contracts.js'

export interface CountryOutageAgentOrchestratorOptions {
  core: CountryOutageCore
  externalEvidenceProvider?: ExternalEvidenceProvider
  annexComposer?: AnnexComposer
}

/**
 * Domeye 应用编排层。Core、Provider 与 AnnexComposer 是并列依赖；
 * Provider 从不注入 Core，外部失败也不会进入核心报告或问答状态机。
 */
export class CountryOutageAgentOrchestrator {
  readonly #core: CountryOutageCore
  readonly #externalEvidenceProvider: ExternalEvidenceProvider
  readonly #annexComposer: AnnexComposer

  constructor(options: CountryOutageAgentOrchestratorOptions) {
    this.#core = options.core
    this.#externalEvidenceProvider =
      options.externalEvidenceProvider ??
      new DisabledExternalEvidenceProvider()
    this.#annexComposer =
      options.annexComposer ?? new DisabledAnnexComposer()
  }

  get limits() {
    return this.#core.limits
  }

  getExternalEvidenceReadiness() {
    return this.#externalEvidenceProvider.readiness()
  }

  async createReport(
    principal: CountryOutagePrincipal,
    request: CreateReportRequest,
  ) {
    return await this.#core.createReport(principal, request)
  }

  async createQuestion(
    principal: CountryOutagePrincipal,
    reportId: string,
    request: CreateOrchestratedQuestionRequest,
  ) {
    if (request.evidence_mode !== 'domeye_only') {
      const readiness = this.#externalEvidenceProvider.readiness()
      if (readiness.state !== 'ready') {
        throw new CountryOutageHttpError(
          409,
          'external_evidence_not_configured',
          '当前环境未配置外部证据能力',
          false,
          '关闭外部证据后继续使用 Domeye 固定快照追问',
        )
      }
      throw new CountryOutageHttpError(
        501,
        'external_evidence_pack_not_deployed',
        '独立 external run 尚未部署',
      )
    }
    return await this.#core.createQuestion(
      principal,
      reportId,
      request,
    )
  }

  async abortRun(
    principal: CountryOutagePrincipal,
    runId: string,
  ) {
    return await this.#core.abortRun(principal, runId)
  }

  async subscribe(
    principal: CountryOutagePrincipal,
    reportId: string,
    afterEventId: number,
    listener: (event: CountryOutageAgentEvent) => void,
  ) {
    return await this.#core.subscribe(
      principal,
      reportId,
      afterEventId,
      listener,
    )
  }

  async getArtifact(
    principal: CountryOutagePrincipal,
    reportId: string,
    format: 'markdown' | 'pdf',
  ) {
    return await this.#core.getArtifact(principal, reportId, format)
  }

  async getExternalAppendixArtifact(
    _principal: CountryOutagePrincipal,
    _reportId: string,
    _questionId: string,
  ): Promise<never> {
    const readiness = this.#externalEvidenceProvider.readiness()
    throw new CountryOutageHttpError(
      409,
      'external_evidence_not_configured',
      readiness.state === 'self_check_failed'
        ? '外部证据能力自检失败'
        : '当前环境未配置外部证据能力',
      false,
      '外部证据包部署并通过独立验收后再生成附件',
    )
  }

  sweep(): void {
    this.#core.sweep()
  }

  /**
   * 保留引用，防止未来实现把附件组合器偷偷下沉到 Core。
   */
  get annexComposer(): AnnexComposer {
    return this.#annexComposer
  }
}
