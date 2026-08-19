export const P1_SEMANTIC_PROMOTION_RETIREMENT_EVENT =
  'country_outage_p1_semantic_model_promotion_retired' as const

export const P1_SEMANTIC_PROMOTION_RETIREMENT_CODE =
  'p1_semantic_promotion_retired' as const

process.stderr.write(`${JSON.stringify({
  event: P1_SEMANTIC_PROMOTION_RETIREMENT_EVENT,
  code: P1_SEMANTIC_PROMOTION_RETIREMENT_CODE,
  message: '旧 P1 语义模型晋级命令已退役',
})}\n`)
process.exitCode = 1
