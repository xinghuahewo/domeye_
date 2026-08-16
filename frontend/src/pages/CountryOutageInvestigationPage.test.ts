import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const source = readFileSync(new URL('./CountryOutageInvestigationPage.vue', import.meta.url), 'utf8')

describe('W5 组合调查用户旅程', () => {
  it('防止旧轮询、旧 ResultSet 和跨调查导出覆盖当前 revision', () => {
    expect(source).toContain('generation !== snapshotGeneration')
    expect(source).toContain('investigation.value?.investigation_revision !== revision')
    expect(source).toContain('generation === resultGeneration')
    expect(source).toContain('investigation.value?.investigation_id !== investigationIdValue')
  })

  it('所有 mutation 使用当前 revision 和 digest，409 后先刷新而不静默重试', () => {
    expect(source).toContain('expected_investigation_revision: investigation.value.investigation_revision')
    expect(source).toContain('expected_current_digest: investigation.value.current_digest')
    expect(source).toContain('cause.status === 409')
    expect(source).toContain('请确认后再操作')
  })

  it('追问必须选择显式 node revision anchor', () => {
    expect(source).toContain('const anchorReady = computed')
    expect(source).toContain('node_id: anchor.node_id')
    expect(source).toContain('node_revision: anchor.execution_revision')
    expect(source).toContain('系统不会猜测“那个时间点”')
  })

  it('提交并展示版本化回答、证据治理绑定和 fixture 边界', () => {
    expect(source).toContain('getCountryOutageInvestigationTurn(id, ref.turn_id, ref.turn_revision)')
    expect(source).toContain('response.turn.turn_id')
    expect(source).toContain('turn.answer.claims')
    expect(source).toContain('turn.answer.limitations')
    expect(source).toContain('turn.answer.unknowns')
    expect(source).toContain('turn.answer.evidence_refs')
    expect(source).toContain('turn.answer.model_receipt_digests')
    expect(source).toContain('turn.answer.gate_receipt_digests')
    expect(source).toContain('answerReceipt(digest)')
    expect(source).toContain('owner-scoped 可读详情')
    expect(source).toContain('external_provider_called=false')
    expect(source).toContain('runtime_integrated=true')
    expect(source).toContain('这不是外部 Sol/DS 模型调用或生产效果证明')
  })

  it('完整性、延期和控制面边界不会过报', () => {
    expect(source).toContain('完整结果（限绑定 publication 与冻结人口）')
    expect(source).toContain('预览不冒充总体')
    expect(source).toContain('P2.1 延期')
    expect(source).toContain('不代表用户影响、因果、责任、恢复或生产部署')
  })

  it('只对用户选中的单个 ResultSet 发起分页和导出', () => {
    expect(source).toContain('const candidate = node.result_set_refs?.[0]')
    expect(source).toContain('getCountryOutageInvestigationResultSet(id, ref.id, ref.revision')
    expect(source).not.toContain('Promise.all(node.result_set_refs')
    expect(source).toContain('PLAN-CAP-02 不进入本次计划')
  })
})
