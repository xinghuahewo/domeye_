export class CountryOutageAgentError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly retryable = false,
  ) {
    super(message)
    this.name = new.target.name
  }
}

export class InvalidCountryOutageReferenceError extends CountryOutageAgentError {
  constructor() {
    super(
      '只接受已有合法 country_outage 事件引用',
      'invalid_country_outage_reference',
      false,
    )
  }
}

export class DomeyeApiError extends CountryOutageAgentError {
  constructor(
    message: string,
    readonly status: number,
    retryable: boolean,
  ) {
    super(message, 'domeye_api_error', retryable)
  }
}

export class SnapshotConflictError extends CountryOutageAgentError {
  constructor(message: string) {
    super(message, 'snapshot_conflict', true)
  }
}

export class UnsupportedCollectorError extends CountryOutageAgentError {
  constructor(collector: string) {
    super(
      `国家中断 Agent 只接受 RRC25，当前快照为 ${collector || '未知 collector'}`,
      'unsupported_collector',
      false,
    )
  }
}

export class ReportDataInsufficientError extends CountryOutageAgentError {
  constructor(readonly reasons: string[]) {
    super(
      `当前快照未达到正式报告最低数据门槛：${reasons.join('；')}`,
      'report_data_insufficient',
      false,
    )
  }
}
