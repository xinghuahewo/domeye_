export const P1_SEMANTIC_CERTIFICATION_RETIREMENT_EVENT =
  'country_outage_p1_semantic_model_certification_retired' as const

export const P1_SEMANTIC_CERTIFICATION_RETIREMENT_CODE =
  'p1_semantic_certification_retired' as const

process.stderr.write(`${JSON.stringify({
  event: P1_SEMANTIC_CERTIFICATION_RETIREMENT_EVENT,
  code: P1_SEMANTIC_CERTIFICATION_RETIREMENT_CODE,
  message: '旧 P1 语义模型认证命令已退役',
})}\n`)
process.exitCode = 1
