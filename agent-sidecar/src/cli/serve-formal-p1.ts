import { FORMAL_P1_SIDECAR_RETIREMENT_CODE } from './formal-p1-sidecar.js'

process.stderr.write(
  `${JSON.stringify({
    event: 'country_outage_formal_p1_sidecar_retired',
    code: FORMAL_P1_SIDECAR_RETIREMENT_CODE,
    message: '旧正式 P1 Sidecar 已退役；请使用新 Agent 运行入口',
  })}\n`,
)
process.exitCode = 1
