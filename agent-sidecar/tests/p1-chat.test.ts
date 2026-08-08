import assert from 'node:assert/strict'
import test from 'node:test'

import {
  P1ConversationManager,
  type P1FactBundle,
  type P1GeneralReadModelProvider,
} from '../src/chat/index.js'

const reference = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const binding = {
  incident_id: 'incident-ir',
  legacy_reference: reference,
  publication_id: 'publication-ir',
  revision: 1,
  collector_id: 'rrc25' as const,
  cohort_id: 'cohort-ir',
  country_code: 'IR',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through: '2026-03-11T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
}

function bundle(): P1FactBundle {
  const metadata = {
    ...binding,
    quality_state: 'complete',
    missing_slot_count: 0,
  }
  return {
    binding,
    resolution: metadata,
    overview: {
      ...metadata,
      event: {
        detected_at_utc: '2026-02-27T01:12:32Z',
        event_end_at_utc: null,
        event_duration_seconds: null,
      },
      cohort: {
        fixed_asn_count: 572,
        fixed_prefix_count: 9257,
        independent_direction_relation_count: 368675,
      },
      current: {
        affected_asn_count: 121,
        interrupted_prefix_count: 1024,
        completely_interrupted_prefix_count: 318,
        invisible_direction_count: 14867,
        fixed_visible_ipv4_address_count: 10069760,
        new_cumulative_ipv4_prefix_count: 700,
        new_cumulative_ipv6_prefix_count: 1,
        new_visible_ipv4_prefix_count: 111,
        new_visible_ipv6_prefix_count: 1,
      },
      peaks: { interrupted_prefix_count: { value: 3855, state_point_utc: '2026-02-27T23:15:00Z' } },
      affected_as_count: 525,
      semantic_boundary: 'rrc25_control_plane_observation_not_user_impact_or_cause',
    },
    series: {
      point_count: 3,
      timestamps: ['a', 'b', 'c'],
      track_definitions: {
        interrupted_prefix_count: { definition: '部分中断与完全中断的固定唯一前缀合计。' },
        completely_interrupted_prefix_count: { definition: '全部预期独立 peer ASN 方向均不可见的固定唯一前缀。' },
        invisible_direction_count: { definition: '按 RRC25 peer ASN 去重的不可见观察方向。' },
      },
    },
    asns: {
      items: [
        { asn: 48715, as_name: 'SEFROYEKPARDAZENG-AS', fixed_prefix_count: 73, peak_complete_prefix_count: 73, peak_invisible_direction_count: 3138, path_downstream_asn_count: 1 },
        { asn: 49556, as_name: 'webdade', fixed_prefix_count: 50, peak_complete_prefix_count: 50, peak_invisible_direction_count: 2103, path_downstream_asn_count: 0 },
        { asn: 204650, as_name: 'ErtebatateSabeteAvaArvand' },
        { asn: 34369, as_name: 'AS-NAMAVA' },
        { asn: 48147, as_name: 'AminIDC' },
      ],
    },
    paths: {
      items: [{
        affected_asn: 49666,
        downstream_asn: 58224,
        downstream_as_name: 'TCI',
        path_samples: [{ prefix: '109.74.224.0/20', as_path_canonical: '33874 6758 1273 3257 49666 48159 58224' }],
      }],
    },
    audit: {
      dataset_id: 'dataset-ir',
      implementation_id: 'git:test',
      event_content_sha256: 'a'.repeat(64),
    },
    derived: {
      ipv4: { maximum: 10156800, minimum: 9577728, drop: 579072, drop_percent: 5.701323, recovery: 492032, recovery_percent: 84.969054 },
      ipv6: { maximum: 267292, minimum: 267288, drop: 4, drop_percent: 0.001496 },
    },
  }
}

class FakeProvider implements P1GeneralReadModelProvider {
  current = binding
  async load() { return bundle() }
  async resolve() { return this.current }
  async findAsn(value: P1FactBundle, asn: number) {
    return value.asns.items.find((item: { asn: number }) => item.asn === asn) ?? null
  }
}

const principal = { userId: 'p1-user', authorizationScope: 'country_outage_event_read' }

async function createManager() {
  const provider = new FakeProvider()
  const manager = new P1ConversationManager({ provider })
  const created = await manager.createConversation(principal, {
    event_reference: reference,
    publication_id: binding.publication_id,
    revision: 1,
    idempotency_key: 'conversation-0001',
  })
  return { manager, provider, conversationId: created.conversation.conversation_id }
}

test('P1 直接事实回答绑定同一 publication 并带字段级证据', async () => {
  const { manager, conversationId } = await createManager()
  const result = await manager.createTurn(principal, conversationId, {
    question: '这次伊朗事件发生了什么？',
    idempotency_key: 'turn-summary-0001',
  })
  assert.equal(result.turn.state, 'completed')
  assert.equal(result.turn.answer?.answerability, 'answerable')
  assert.equal(result.turn.answer?.binding.publication_id, binding.publication_id)
  assert.deepEqual(
    result.turn.answer?.evidence.map((item) => item.evidence_ref),
    ['cohort.fixed_prefix_count', 'scope_counts.affected_as_count', 'peaks.interrupted_prefix_count.value'],
  )
  assert.match(result.turn.answer?.answer_text ?? '', /3,855/)
  assert.match(result.turn.answer?.limitations.join('') ?? '', /RRC25/)
})

test('P1 多轮显式 ASN 修正覆盖旧对象且幂等重试不重复', async () => {
  const { manager, conversationId } = await createManager()
  await manager.createTurn(principal, conversationId, {
    question: 'AS48715 在这次事件中的观测结果怎样？',
    idempotency_key: 'turn-asn-0000001',
  })
  const corrected = await manager.createTurn(principal, conversationId, {
    question: '修正一下，是 AS49556',
    idempotency_key: 'turn-asn-0000002',
  })
  assert.match(corrected.turn.answer?.answer_text ?? '', /AS49556/)
  assert.doesNotMatch(corrected.turn.answer?.answer_text ?? '', /AS48715/)
  const snapshot = await manager.getConversation(principal, conversationId)
  assert.equal(snapshot.state.asn, 49556)
  const retry = await manager.createTurn(principal, conversationId, {
    question: '任意不同文本也不能改变同一幂等结果',
    idempotency_key: 'turn-asn-0000002',
  })
  assert.equal(retry.deduplicated, true)
  assert.equal(retry.turn.turn_id, corrected.turn.turn_id)
  assert.equal((await manager.getConversation(principal, conversationId)).turns.length, 2)
})

test('P1 边界、缺失能力和 null 不补零', async () => {
  const { manager, conversationId } = await createManager()
  const update = await manager.createTurn(principal, conversationId, {
    question: 'series 里没有 Update 轨道，是不是说明 Update 数量一直为 0？',
    idempotency_key: 'turn-boundary-01',
  })
  assert.equal(update.turn.answer?.answerability, 'invalid_data')
  assert.match(update.turn.answer?.answer_text ?? '', /不等于.*0/)
  const duration = await manager.createTurn(principal, conversationId, {
    question: 'event_end_at_utc 是 null，事件持续了多久？',
    idempotency_key: 'turn-boundary-02',
  })
  assert.equal(duration.turn.answer?.answerability, 'partial')
  assert.equal(duration.turn.answer?.evidence[0]?.value, null)
  assert.match(duration.turn.answer?.answer_text ?? '', /未知/)
})

test('P1 revision 漂移阻断新轮次并保留历史', async () => {
  const { manager, provider, conversationId } = await createManager()
  await manager.createTurn(principal, conversationId, {
    question: '观测窗口是什么？',
    idempotency_key: 'turn-before-drift',
  })
  provider.current = { ...binding, revision: 2, publication_id: 'publication-ir-r2' }
  await assert.rejects(
    manager.createTurn(principal, conversationId, {
      question: '峰值呢？',
      idempotency_key: 'turn-after-drift',
    }),
    (error: unknown) => Boolean(
      error && typeof error === 'object' && 'code' in error && error.code === 'revision_drift',
    ),
  )
  assert.equal((await manager.getConversation(principal, conversationId)).turns.length, 1)
})

test('原因追问降级并清除上一轮实体槽位', async () => {
  const { manager, conversationId } = await createManager()
  await manager.createTurn(principal, conversationId, {
    question: 'AS49556 的情况呢？',
    idempotency_key: 'turn-cause-context-1',
  })
  const cause = await manager.createTurn(principal, conversationId, {
    question: '所以到底是谁造成的？',
    idempotency_key: 'turn-cause-context-2',
  })
  assert.equal(cause.turn.answer?.answerability, 'partial')
  const snapshot = await manager.getConversation(principal, conversationId)
  assert.equal(snapshot.state.topic, 'boundary')
  assert.equal(snapshot.state.asn, null)
  assert.equal(snapshot.state.metric, null)
})

test('处理中取消不会提交答案或实体状态', async () => {
  let enteredFind: (() => void) | undefined
  const entered = new Promise<void>((resolve) => { enteredFind = resolve })
  class SlowProvider extends FakeProvider {
    override async findAsn(
      _value: P1FactBundle,
      _asn: number,
      signal?: AbortSignal,
    ): Promise<Record<string, unknown> | null> {
      enteredFind?.()
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => resolve(null), 5_000)
        signal?.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(new Error('cancelled'))
        }, { once: true })
      })
    }
  }
  const manager = new P1ConversationManager({ provider: new SlowProvider() })
  const created = await manager.createConversation(principal, {
    event_reference: reference,
    publication_id: binding.publication_id,
    revision: 1,
    idempotency_key: 'conversation-cancel-01',
  })
  const pending = manager.createTurn(principal, created.conversation.conversation_id, {
    question: 'AS123456 的情况呢？',
    idempotency_key: 'turn-cancel-0001',
  })
  await entered
  const snapshot = await manager.getConversation(principal, created.conversation.conversation_id)
  const turnId = snapshot.turns[0]!.turn_id
  await manager.cancelTurn(principal, created.conversation.conversation_id, turnId)
  const completed = await pending
  assert.equal(completed.turn.state, 'cancelled')
  assert.equal(completed.turn.answer, undefined)
  assert.equal((await manager.getConversation(principal, created.conversation.conversation_id)).state.asn, null)
})

test('SSE 重连按 Last-Event-ID 重放且会话到期失败关闭', async () => {
  let now = new Date('2026-08-08T12:00:00Z')
  const manager = new P1ConversationManager({
    provider: new FakeProvider(),
    ttlMs: 1_000,
    reminderBeforeMs: 200,
    now: () => now,
  })
  const created = await manager.createConversation(principal, {
    event_reference: reference,
    publication_id: binding.publication_id,
    revision: 1,
    idempotency_key: 'conversation-replay-01',
  })
  await manager.createTurn(principal, created.conversation.conversation_id, {
    question: '观测窗口是什么？',
    idempotency_key: 'turn-replay-0001',
  })
  const all = await manager.subscribe(
    principal, created.conversation.conversation_id, 0, () => {},
  )
  assert.deepEqual(all.replay.map((event) => event.event_id), [1, 2, 3, 4])
  const afterTwo = await manager.subscribe(
    principal, created.conversation.conversation_id, 2, () => {},
  )
  assert.deepEqual(afterTwo.replay.map((event) => event.event_id), [3, 4])
  now = new Date('2026-08-08T12:00:01.001Z')
  await assert.rejects(
    manager.getConversation(principal, created.conversation.conversation_id),
    (error: unknown) => Boolean(
      error && typeof error === 'object' && 'code' in error && error.code === 'conversation_expired',
    ),
  )
})
