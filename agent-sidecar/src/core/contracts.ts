import type {
  AbortRunResponse,
  CountryOutageAgentEvent,
  CountryOutagePrincipal,
  CountryOutageServerLimits,
  CreateDomeyeOnlyQuestionRequest,
  CreateQuestionResponse,
  CreateReportRequest,
  CreateReportResponse,
  DownloadArtifact,
  EventSubscription,
} from '../server/contracts.js'

/**
 * 国家中断 Agent 核心只包含 RRC25 报告、Domeye-only 追问、短期会话和
 * 基础制品。外部证据 Provider、外部运行和附件组合均不得成为该接口成员。
 */
export interface CountryOutageCore {
  readonly limits: Readonly<CountryOutageServerLimits>

  createReport(
    principal: CountryOutagePrincipal,
    request: CreateReportRequest,
  ): Promise<CreateReportResponse>

  createQuestion(
    principal: CountryOutagePrincipal,
    reportId: string,
    request: CreateDomeyeOnlyQuestionRequest,
  ): Promise<CreateQuestionResponse>

  abortRun(
    principal: CountryOutagePrincipal,
    runId: string,
  ): Promise<AbortRunResponse>

  subscribe(
    principal: CountryOutagePrincipal,
    reportId: string,
    afterEventId: number,
    listener: (event: CountryOutageAgentEvent) => void,
  ): Promise<EventSubscription>

  getArtifact(
    principal: CountryOutagePrincipal,
    reportId: string,
    format: 'markdown' | 'pdf',
  ): Promise<DownloadArtifact>

  sweep(): void
}
