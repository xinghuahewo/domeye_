import type {
  CountryOutageReportService,
  ReportGenerationInput,
  ReportGenerationResult,
} from '../server/contracts.js'

/**
 * 固定 RRC25 报告生成结果。SessionManager 只负责短期会话与运行状态，
 * 具体事实读取、叙述和制品构建均位于该端口之后。
 */
export type Rrc25Report = ReportGenerationResult

export interface CountryOutageReportGenerationPort {
  generateReport(
    input: ReportGenerationInput,
  ): Promise<Rrc25Report>
}

/**
 * 旧 ReportService 的窄兼容适配器。正式装配直接注入 generateReport 端口；
 * 现有测试夹具可以逐步迁移，不需要在 SessionManager 内保留生成细节。
 */
export function adaptCountryOutageReportService(
  service: CountryOutageReportService,
): CountryOutageReportGenerationPort {
  return Object.freeze({
    generateReport: (input: ReportGenerationInput) =>
      service.generate(input),
  })
}
