<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  cancelCountryOutageChatTurn,
  COUNTRY_OUTAGE_FIRST_SLICE_QUESTION,
  createCountryOutageChatConversation,
  createCountryOutageChatTurn,
  getCountryOutageChatConversation,
  type CountryOutageChatConversation,
  type CountryOutageChatEvidence,
  type CountryOutageChatTurn,
} from '@/api/countryOutageChat'
import { getCountryOutageGeneralPage } from '@/api/events'
import PageState from '@/components/PageState.vue'
import type { CountryOutageGeneralPageModel } from '@/types/api'
import { errorMessage } from '@/utils/normalize'

defineOptions({ name: 'CountryOutageChatPage' })

const route = useRoute()
const reference = computed(() => typeof route.query.ref === 'string' ? route.query.ref : '')
const page = ref<CountryOutageGeneralPageModel | null>(null)
const conversation = ref<CountryOutageChatConversation | null>(null)
const loading = ref(false)
const sending = ref(false)
const cancelling = ref(false)
const error = ref('')
const draft = ref('')
const activeTurnId = ref<string | null>(null)
const selectedTurnId = ref<string | null>(null)
const selectedEvidenceRef = ref<string | null>(null)
const composer = ref<HTMLTextAreaElement | null>(null)
const dialogue = ref<HTMLElement | null>(null)
let conversationGeneration = 0
let submitRequestId = 0
let cancelRequestId = 0
let polling: number | undefined

interface ConversationRequestIdentity {
  readonly generation: number
  readonly conversationId: string
}

const contractQuestion = COUNTRY_OUTAGE_FIRST_SLICE_QUESTION

const countryName = computed(() => {
  const code = conversation.value?.binding.country_code
    ?? page.value?.resolution.country_code
  if (!code) return '国家'
  try {
    return new Intl.DisplayNames(['zh-CN'], { type: 'region' }).of(code) || code
  } catch {
    return code
  }
})

const turns = computed(() => conversation.value?.turns ?? [])
const selectedTurn = computed(() => {
  const id = selectedTurnId.value
  return turns.value.find((turn) => turn.turn_id === id)
    ?? turns.value.at(-1)
    ?? null
})
const selectedEvidence = computed(() => {
  const evidence = selectedTurn.value?.answer?.evidence ?? []
  return evidence.find((item) => item.evidence_ref === selectedEvidenceRef.value)
    ?? evidence[0]
    ?? null
})

function idempotency(prefix: string): string {
  const suffix = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replaceAll('-', '')
    : `${Date.now()}${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`.slice(0, 128)
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '未知'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  const formatted = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed)
  return `${formatted} UTC`
}

function formatValue(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return 'null / 未观测'
  return typeof value === 'number'
    ? value.toLocaleString('zh-CN')
    : value
}

function compactId(value: string | null | undefined): string {
  if (!value) return '—'
  if (value.length <= 30) return value
  return `${value.slice(0, 14)}…${value.slice(-10)}`
}

function statusLabel(value: string): string {
  return ({
    supported: '可发布',
    clarification_required: '需要澄清',
    stopped: '安全停止',
    executing: '执行中',
    completed: '已完成',
    failed: '执行失败',
    cancelled: '已取消',
  } as Record<string, string>)[value] || value
}

function sourceLabel(value: string): string {
  return ({
    renderer: '模型渲染 · Guard 通过',
    deterministic_fallback: '确定性回退',
    none: '未形成答案',
  } as Record<string, string>)[value] || value
}

function capabilityLabel(value: string): string {
  return ({
    'CAP-006': 'TOOL-03 · 读取指标序列',
    'CAP-016': 'OP-01 · 计算序列极值',
  } as Record<string, string>)[value] || value
}

function receiptStatusLabel(value: string): string {
  return ({
    admitted: '已准入',
    rejected: '已拒绝',
    succeeded: '成功',
    failed: '失败',
    pass: '通过',
    block: '阻断',
  } as Record<string, string>)[value] || value
}

function guardLabel(value: 'pass' | 'block' | null | undefined): string {
  return value ? receiptStatusLabel(value) : '未运行'
}

function unitLabel(value: string | null | undefined): string {
  return value === 'unique_ipv4_address' ? '唯一 IPv4 地址' : value || '未标注'
}

function selectTurn(turn: CountryOutageChatTurn) {
  selectedTurnId.value = turn.turn_id
  selectedEvidenceRef.value = turn.answer?.evidence[0]?.evidence_ref ?? null
}

function inspectEvidence(turn: CountryOutageChatTurn, evidence: CountryOutageChatEvidence) {
  selectedTurnId.value = turn.turn_id
  selectedEvidenceRef.value = evidence.evidence_ref
}

function currentConversationRequestIdentity(): ConversationRequestIdentity | null {
  if (!conversation.value) return null
  return {
    generation: conversationGeneration,
    conversationId: conversation.value.conversation_id,
  }
}

function isCurrentConversationRequest(
  identity: ConversationRequestIdentity,
): boolean {
  return identity.generation === conversationGeneration
    && identity.conversationId === conversation.value?.conversation_id
}

function clearPolling(expectedTimer?: number) {
  if (expectedTimer !== undefined && polling !== expectedTimer) {
    window.clearInterval(expectedTimer)
    return
  }
  if (polling !== undefined) window.clearInterval(polling)
  polling = undefined
}

async function refreshConversation(
  identity = currentConversationRequestIdentity(),
): Promise<boolean> {
  if (!identity) return false
  const snapshot = await getCountryOutageChatConversation(
    identity.conversationId,
  )
  if (!isCurrentConversationRequest(identity)) return false
  if (snapshot.conversation.conversation_id !== identity.conversationId) {
    throw new Error('会话轮询响应身份不一致')
  }
  conversation.value = snapshot.conversation
  const active = snapshot.conversation.turns.find((turn) => turn.state === 'executing')
  activeTurnId.value = active?.turn_id ?? null
  if (!active) clearPolling()
  return Boolean(active)
}

function startPolling(identity: ConversationRequestIdentity) {
  clearPolling()
  const timer = window.setInterval(() => {
    void refreshConversation(identity)
      .then((active) => {
        if (!isCurrentConversationRequest(identity)) {
          window.clearInterval(timer)
          return
        }
        if (!active) {
          clearPolling(timer)
          void scrollToLatest()
        }
      })
      .catch((cause) => {
        if (!isCurrentConversationRequest(identity)) {
          window.clearInterval(timer)
          return
        }
        clearPolling(timer)
        error.value = errorMessage(cause)
      })
  }, 750)
  polling = timer
}

async function scrollToLatest() {
  await nextTick()
  dialogue.value?.scrollTo({
    top: dialogue.value.scrollHeight,
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
      ? 'auto' : 'smooth',
  })
}

async function load() {
  const generation = ++conversationGeneration
  submitRequestId += 1
  cancelRequestId += 1
  clearPolling()
  activeTurnId.value = null
  sending.value = false
  cancelling.value = false
  loading.value = true
  error.value = ''
  page.value = null
  conversation.value = null
  try {
    if (!reference.value) throw new Error('缺少国家中断事件引用')
    const event = await getCountryOutageGeneralPage(reference.value)
    if (generation !== conversationGeneration) return
    page.value = event.page
    const resolution = event.page.resolution
    const created = await createCountryOutageChatConversation({
      event_reference: resolution.legacy_reference,
      publication_id: resolution.publication_id,
      revision: resolution.revision,
      idempotency_key: idempotency('chat-create'),
    })
    if (generation !== conversationGeneration) return
    conversation.value = created.conversation
    const active = created.conversation.turns.find((turn) => turn.state === 'executing')
    activeTurnId.value = active?.turn_id ?? null
    if (active) startPolling({
      generation,
      conversationId: created.conversation.conversation_id,
    })
  } catch (cause) {
    if (generation === conversationGeneration) {
      error.value = errorMessage(cause)
    }
  } finally {
    if (generation === conversationGeneration) {
      loading.value = false
    }
  }
}

async function submit(question = draft.value) {
  const value = question.trim()
  const identity = currentConversationRequestIdentity()
  if (!value || !identity || sending.value || activeTurnId.value) return
  const requestId = ++submitRequestId
  sending.value = true
  error.value = ''
  try {
    const response = await createCountryOutageChatTurn(
      identity.conversationId,
      value,
      idempotency('chat-turn'),
    )
    if (
      requestId !== submitRequestId
      || !isCurrentConversationRequest(identity)
    ) return
    const current = conversation.value
    if (!current) return
    conversation.value = {
      ...current,
      turns: [
        ...current.turns.filter((turn) => turn.turn_id !== response.turn.turn_id),
        response.turn,
      ],
    }
    draft.value = ''
    selectedTurnId.value = response.turn.turn_id
    selectedEvidenceRef.value = response.turn.answer?.evidence[0]?.evidence_ref ?? null
    activeTurnId.value = response.turn.state === 'executing'
      ? response.turn.turn_id
      : null
    if (activeTurnId.value) startPolling(identity)
    await scrollToLatest()
  } catch (cause) {
    if (
      requestId === submitRequestId
      && isCurrentConversationRequest(identity)
    ) {
      error.value = errorMessage(cause)
      await refreshConversation(identity).catch(() => undefined)
    }
  } finally {
    if (
      requestId === submitRequestId
      && isCurrentConversationRequest(identity)
    ) {
      sending.value = false
      void nextTick(() => composer.value?.focus())
    }
  }
}

async function cancelActiveTurn() {
  const identity = currentConversationRequestIdentity()
  const turnId = activeTurnId.value
  if (!identity || !turnId || cancelling.value) return
  const requestId = ++cancelRequestId
  cancelling.value = true
  error.value = ''
  try {
    await cancelCountryOutageChatTurn(
      identity.conversationId,
      turnId,
    )
    if (
      requestId !== cancelRequestId
      || !isCurrentConversationRequest(identity)
    ) return
    await refreshConversation(identity)
  } catch (cause) {
    if (
      requestId === cancelRequestId
      && isCurrentConversationRequest(identity)
    ) error.value = errorMessage(cause)
  } finally {
    if (
      requestId === cancelRequestId
      && isCurrentConversationRequest(identity)
    ) cancelling.value = false
  }
}

function onComposerKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void submit()
  }
}

watch(reference, () => void load(), { immediate: true })
onBeforeUnmount(() => {
  conversationGeneration += 1
  submitRequestId += 1
  cancelRequestId += 1
  clearPolling()
})
</script>

<template>
  <main class="chat-page">
    <header v-if="conversation" class="identity-rail">
      <div class="identity-title">
        <RouterLink :to="{ name: 'event-detail', query: { ref: reference } }">← 返回事件观测</RouterLink>
        <p>FIRST VERTICAL SLICE · RRC25</p>
        <h1>{{ countryName }}国家中断调查 Agent</h1>
      </div>
      <dl>
        <div><dt>Publication</dt><dd :title="conversation.binding.publication_id">{{ compactId(conversation.binding.publication_id) }}</dd></div>
        <div><dt>Revision</dt><dd>r{{ conversation.binding.revision }}</dd></div>
        <div><dt>Candidate</dt><dd :title="conversation.candidate_id">{{ compactId(conversation.candidate_id) }}</dd></div>
        <div><dt>Identity receipt</dt><dd :title="conversation.identity_receipt_id">{{ compactId(conversation.identity_receipt_id) }}</dd></div>
      </dl>
      <div class="lifecycle-chip">
        <span></span>
        {{ conversation.binding.is_final_in_data_range ? '数据范围内已结束' : '事件结束未知' }}
      </div>
    </header>

    <PageState v-if="loading" kind="loading" title="正在验证冻结身份" detail="核对 event reference、publication、revision 与当前 Candidate。" />
    <PageState v-else-if="!conversation" kind="error" title="调查会话暂不可用" :detail="error" @retry="load" />

    <section v-else class="chat-workbench">
      <div class="dialogue-panel">
        <div ref="dialogue" class="dialogue-stream" aria-live="polite">
          <article class="welcome-card">
            <div class="welcome-heading">
              <div>
                <p class="section-index">01 / FIXED INVESTIGATION CONTRACT</p>
                <h2>一条问题，一条可核验闭环</h2>
              </div>
              <span>CAP-006 → CAP-016</span>
            </div>
            <p>当前 Candidate 只交付固定 IPv4 地址量极值调查。每个动作独立准入，Finding 经 Context、Renderer 与 Response Guard 后才可发布。</p>
            <button class="contract-question" type="button" :disabled="sending || Boolean(activeTurnId)" @click="submit(contractQuestion)">
              <span>合同问题</span>
              <strong>{{ contractQuestion }}</strong>
              <b>提交调查 ↗</b>
            </button>
            <div class="scope-note">
              <b>输入边界</b>
              <p>输入框可以直接填写；合同外问题会收到明确拒绝，不会转入其他请求路径。</p>
            </div>
          </article>

          <article
            v-for="turn in turns"
            :key="turn.turn_id"
            class="turn-block"
            :class="{ 'is-selected': selectedTurn?.turn_id === turn.turn_id }"
            @click="selectTurn(turn)"
          >
            <div class="user-message">
              <span>{{ String(turn.turn_number).padStart(2, '0') }}</span>
              <p>{{ turn.question }}</p>
            </div>

            <div v-if="turn.answer" class="agent-answer">
              <header>
                <div>
                  <span class="answer-mark">RRC25</span>
                  <b>{{ statusLabel(turn.answer.answerability) }}</b>
                  <em :class="`source-${turn.answer.answer_source}`">{{ sourceLabel(turn.answer.answer_source) }}</em>
                </div>
                <small>{{ formatTime(turn.completed_at) }}</small>
              </header>

              <section class="answer-copy">
                <p>{{ turn.answer.answer_text }}</p>
              </section>

              <section v-if="turn.answer.finding" class="finding-sheet">
                <header>
                  <div>
                    <span>TYPED FINDING</span>
                    <b>{{ turn.answer.finding.value_state }}</b>
                  </div>
                  <code :title="turn.answer.finding.finding_id">{{ compactId(turn.answer.finding.finding_id) }}</code>
                </header>
                <dl class="finding-metrics">
                  <div><dt>最低值</dt><dd>{{ formatValue(turn.answer.finding.values.minimum) }}</dd><small>{{ formatTime(turn.answer.finding.values.minimum_at_utc) }}</small></div>
                  <div><dt>首值</dt><dd>{{ formatValue(turn.answer.finding.values.first) }}</dd><small>{{ formatTime(turn.answer.finding.values.first_at_utc) }}</small></div>
                  <div><dt>末值</dt><dd>{{ formatValue(turn.answer.finding.values.last) }}</dd><small>{{ formatTime(turn.answer.finding.values.last_at_utc) }}</small></div>
                  <div><dt>最大值</dt><dd>{{ formatValue(turn.answer.finding.values.maximum) }}</dd><small>{{ formatTime(turn.answer.finding.values.maximum_at_utc) }}</small></div>
                  <div><dt>极差</dt><dd>{{ formatValue(turn.answer.finding.values.difference) }}</dd><small>{{ unitLabel(turn.answer.finding.unit) }}</small></div>
                </dl>
              </section>

              <section v-if="turn.answer.evidence.length" class="evidence-strip">
                <button
                  v-for="item in turn.answer.evidence"
                  :key="item.evidence_ref"
                  type="button"
                  :class="{ 'is-active': selectedEvidence?.evidence_ref === item.evidence_ref }"
                  @click.stop="inspectEvidence(turn, item)"
                >
                  <span>{{ item.label }}</span>
                  <b>{{ formatValue(item.value) }}</b>
                  <small>{{ item.observed_at_utc ? formatTime(item.observed_at_utc) : unitLabel(item.unit) }}</small>
                </button>
              </section>

              <section v-if="turn.answer.limitations.length" class="limitation-list">
                <b>发布限制</b>
                <ul>
                  <li v-for="item in turn.answer.limitations" :key="item">{{ item }}</li>
                </ul>
              </section>

              <footer>
                <span>Candidate 已绑定</span>
                <span>Guard {{ guardLabel(turn.answer.trace.response_guard?.decision) }}</span>
                <span>{{ turn.answer.usage.attempt_count }} / {{ turn.answer.usage.maximum_attempt_count }} 次模型调用</span>
                <span>{{ turn.answer.usage.cost_policy }}</span>
              </footer>
            </div>

            <div v-else-if="turn.error" class="turn-error">
              <b>{{ turn.state === 'cancelled' ? '本轮已取消' : '本轮未发布答案' }}</b>
              <p>{{ turn.error.message }}</p>
              <code>{{ turn.error.code }}</code>
              <button v-if="turn.error.retryable" type="button" @click.stop="submit(turn.question)">重试这个问题</button>
            </div>
            <div v-else class="turn-progress">
              <span></span>
              <div><b>正在运行首片闭环</b><p>逐动作准入 → 证据读取 → 极值计算 → 输出守卫</p></div>
            </div>
          </article>
        </div>

        <form class="composer" @submit.prevent="submit()">
          <label for="agent-question">向当前冻结事件提交问题</label>
          <div>
            <textarea
              id="agent-question"
              ref="composer"
              v-model="draft"
              rows="2"
              maxlength="2000"
              :disabled="sending"
              :placeholder="activeTurnId ? '当前调查仍在执行，可先取消本轮。' : '可直接输入；当前 Candidate 只接受上方固定合同问题。'"
              @keydown="onComposerKeydown"
            ></textarea>
            <button v-if="activeTurnId" type="button" class="cancel-button" :disabled="cancelling" @click="cancelActiveTurn">
              {{ cancelling ? '正在取消' : '取消本轮' }}
            </button>
            <button v-else type="submit" :disabled="sending || !draft.trim()">
              {{ sending ? '提交中' : '发送' }}
              <span>↑</span>
            </button>
          </div>
          <p v-if="error" role="alert"><b>请求未执行</b>{{ error }}</p>
          <small>Enter 发送 · Shift + Enter 换行 · 合同外目标失败关闭</small>
        </form>
      </div>

      <aside class="audit-panel" aria-label="当前回答核对面板">
        <header>
          <p class="section-index">02 / VERIFY THE RECEIPTS</p>
          <h2>证据与回执账本</h2>
        </header>

        <template v-if="selectedTurn?.answer">
          <section class="audit-section identity-section">
            <h3>Candidate 与数据身份</h3>
            <dl>
              <div><dt>Candidate</dt><dd>{{ selectedTurn.answer.candidate_id }}</dd></div>
              <div><dt>Identity receipt</dt><dd>{{ conversation.identity_receipt_id }}</dd></div>
              <div><dt>Event reference</dt><dd>{{ conversation.binding.event_reference }}</dd></div>
              <div><dt>Incident</dt><dd>{{ selectedTurn.answer.data_identity.incident_id }}</dd></div>
              <div><dt>Publication</dt><dd>{{ selectedTurn.answer.data_identity.publication_id }}</dd></div>
              <div><dt>Revision / Collector</dt><dd>r{{ selectedTurn.answer.data_identity.revision }} · {{ selectedTurn.answer.data_identity.collector_id.toUpperCase() }}</dd></div>
              <div><dt>Window</dt><dd>{{ selectedTurn.answer.data_identity.window_start_utc }} → {{ selectedTurn.answer.data_identity.window_end_utc }}</dd></div>
              <div><dt>Data through</dt><dd>{{ selectedTurn.answer.data_identity.data_through }}</dd></div>
            </dl>
          </section>

          <section v-if="selectedEvidence" class="audit-section evidence-focus">
            <h3>选中证据</h3>
            <span>{{ selectedEvidence.label }}</span>
            <code>{{ selectedEvidence.evidence_ref }}</code>
            <strong>{{ formatValue(selectedEvidence.value) }}</strong>
            <p>{{ unitLabel(selectedEvidence.unit) }}<template v-if="selectedEvidence.observed_at_utc"> · {{ formatTime(selectedEvidence.observed_at_utc) }}</template></p>
          </section>

          <details v-if="selectedTurn.answer.finding" class="audit-section" open>
            <summary>Finding <span>{{ selectedTurn.answer.finding.value_state }}</span></summary>
            <dl class="ledger-list">
              <div><dt>Finding ID</dt><dd>{{ selectedTurn.answer.finding.finding_id }}</dd></div>
              <div><dt>Result digest</dt><dd>{{ selectedTurn.answer.finding.result_digest }}</dd></div>
              <div><dt>Metric / Unit</dt><dd>{{ selectedTurn.answer.finding.metric }} · {{ selectedTurn.answer.finding.unit }}</dd></div>
              <div><dt>Population</dt><dd>{{ selectedTurn.answer.finding.population_definition }}</dd></div>
              <div><dt>Completeness</dt><dd>{{ selectedTurn.answer.finding.completeness_state }} · {{ selectedTurn.answer.finding.observed_point_count }}/{{ selectedTurn.answer.finding.time_slot_count }} observed · {{ selectedTurn.answer.finding.null_point_count }} null</dd></div>
              <div><dt>Execution versions</dt><dd>TOOL-03@{{ selectedTurn.answer.finding.tool_version }} · OP-01@{{ selectedTurn.answer.finding.operator_version }}</dd></div>
            </dl>
          </details>

          <details class="audit-section" open>
            <summary>Admission receipts <span>{{ selectedTurn.answer.trace.admission_receipts.length }}</span></summary>
            <ol class="receipt-list">
              <li v-for="receipt in selectedTurn.answer.trace.admission_receipts" :key="receipt.receipt_id">
                <div><b>{{ receiptStatusLabel(receipt.decision) }}</b><code>{{ receipt.reason_code ?? 'policy_passed' }}</code></div>
                <small>{{ receipt.receipt_id }}</small>
              </li>
            </ol>
          </details>

          <details class="audit-section" open>
            <summary>Action receipts <span>{{ selectedTurn.answer.trace.action_receipts.length }}</span></summary>
            <ol class="receipt-list">
              <li v-for="receipt in selectedTurn.answer.trace.action_receipts" :key="receipt.receipt_id">
                <div><b>{{ capabilityLabel(receipt.capability_id) }}</b><code>{{ receiptStatusLabel(receipt.status) }}</code></div>
                <small>{{ receipt.receipt_id }}</small>
                <em v-if="receipt.failure_code">{{ receipt.failure_code }}</em>
              </li>
            </ol>
          </details>

          <details class="audit-section">
            <summary>Artifacts <span>{{ selectedTurn.answer.trace.artifacts.length }}</span></summary>
            <ol class="receipt-list">
              <li v-for="artifact in selectedTurn.answer.trace.artifacts" :key="artifact.artifact_id">
                <div><b>{{ artifact.artifact_kind }}</b><code>immutable</code></div>
                <small>{{ artifact.artifact_id }}</small>
                <small>{{ artifact.content_digest }}</small>
              </li>
            </ol>
          </details>

          <details class="audit-section">
            <summary>Observations <span>{{ selectedTurn.answer.trace.observations.length }}</span></summary>
            <ol class="receipt-list">
              <li v-for="observation in selectedTurn.answer.trace.observations" :key="observation.observation_id">
                <div><b>{{ capabilityLabel(observation.capability_id) }}</b><code>{{ receiptStatusLabel(observation.status) }}</code></div>
                <small>{{ observation.observation_id }}</small>
                <em v-if="observation.reason_code">{{ observation.reason_code }}</em>
              </li>
            </ol>
          </details>

          <section class="audit-section guard-section" :class="`is-${selectedTurn.answer.trace.response_guard?.decision ?? 'not-run'}`">
            <h3>Response Guard</h3>
            <strong>{{ guardLabel(selectedTurn.answer.trace.response_guard?.decision) }}</strong>
            <p v-if="selectedTurn.answer.trace.response_guard">{{ selectedTurn.answer.trace.response_guard.reason_codes.join(' · ') || '所有确定性发布检查均通过' }}</p>
            <p v-else>未形成可供发布检查的答案草稿。</p>
            <code>goal {{ selectedTurn.answer.trace.goal_id }} · state r{{ selectedTurn.answer.trace.goal_state_revision }} · {{ selectedTurn.answer.trace.disposition }}</code>
          </section>

          <details class="audit-section usage-section" open>
            <summary>Provider usage <span>{{ selectedTurn.answer.usage.attempt_count }}/{{ selectedTurn.answer.usage.maximum_attempt_count }}</span></summary>
            <dl class="usage-ledger">
              <div><dt>Policy</dt><dd>{{ selectedTurn.answer.usage.cost_policy }}</dd></div>
              <div><dt>Tokens</dt><dd>{{ selectedTurn.answer.usage.tokens.total.toLocaleString('zh-CN') }}</dd></div>
              <div><dt>Estimated cost</dt><dd>${{ selectedTurn.answer.usage.estimated_cost_usd.toFixed(6) }}</dd></div>
            </dl>
            <ol class="attempt-list">
              <li v-for="attempt in selectedTurn.answer.usage.attempts" :key="`${attempt.attempt_id}-${attempt.phase}`">
                <span>{{ attempt.attempt_id }}</span>
                <div><b>{{ attempt.phase }} · {{ attempt.model }}</b><small>{{ attempt.provider }} · {{ attempt.outcome }} · {{ attempt.latency_ms ?? '—' }} ms</small></div>
              </li>
            </ol>
          </details>
        </template>

        <div v-else class="audit-empty">
          <span>⌖</span>
          <p>提交合同问题后，可在这里核对 Candidate、身份回执、逐动作准入、Artifacts、Observations、Guard 和模型用量。</p>
        </div>
      </aside>
    </section>
  </main>
</template>

<style scoped>
.chat-page {
  --ink: #102d3c;
  --ink-soft: #37515f;
  --line: #d3dde1;
  --paper: #fbfaf5;
  --signal: #e8792f;
  --success: #2f8069;
  min-height: calc(100vh - 92px);
  color: var(--ink);
}
.identity-rail {
  position: sticky;
  z-index: 8;
  top: 66px;
  display: grid;
  grid-template-columns: minmax(300px, .78fr) minmax(590px, 1.35fr) auto;
  gap: 22px;
  align-items: center;
  padding: 17px 22px;
  color: #edf7fa;
  background:
    linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px) 0 0 / 28px 28px,
    linear-gradient(118deg, #0d2938 0%, #143e50 68%, #1a4d5d 100%);
  border-bottom: 3px solid var(--signal);
  box-shadow: 0 10px 28px rgba(11, 35, 48, .18);
}
.identity-title a { color: #9dd2df; font-size: 10px; font-weight: 750; text-decoration: none; }
.identity-title p { margin: 7px 0 2px; color: #75b6c7; font: 750 8px/1.2 var(--mono); letter-spacing: .13em; }
.identity-title h1 { margin: 0; font-size: 20px; letter-spacing: -.025em; }
.identity-rail dl { display: grid; grid-template-columns: 1.3fr .45fr 1fr 1fr; gap: 1px; margin: 0; background: rgba(255,255,255,.13); }
.identity-rail dl div { min-width: 0; padding: 8px 10px; background: rgba(7, 29, 41, .5); }
.identity-rail dt { color: #82aebb; font-size: 8px; text-transform: uppercase; }
.identity-rail dd { overflow: hidden; margin: 4px 0 0; font: 650 9px/1.35 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.lifecycle-chip { display: flex; gap: 7px; align-items: center; padding: 8px 10px; color: #ffd5b5; background: rgba(101, 48, 24, .36); border: 1px solid rgba(255, 171, 108, .35); border-radius: 2px; font-size: 9px; font-weight: 750; white-space: nowrap; }
.lifecycle-chip span { width: 6px; height: 6px; background: #ef8b45; border-radius: 50%; box-shadow: 0 0 0 4px rgba(239,139,69,.14); }
.chat-workbench { display: grid; grid-template-columns: minmax(560px, 1.34fr) minmax(380px, .66fr); min-height: calc(100vh - 184px); background: #e5eaec; }
.dialogue-panel { position: relative; min-width: 0; background: radial-gradient(circle at 12% 5%, rgba(233, 176, 120, .12), transparent 28%), var(--paper); border-right: 1px solid #cbd5da; }
.dialogue-stream { height: calc(100vh - 286px); min-height: 440px; overflow-y: auto; padding: 30px max(24px, 5vw) 42px; scroll-behavior: smooth; }
.section-index { margin: 0; color: var(--signal); font: 800 8px/1.2 var(--mono); letter-spacing: .12em; }
.welcome-card { max-width: 850px; margin: 0 auto 38px; padding-bottom: 30px; border-bottom: 1px solid var(--line); }
.welcome-heading { display: flex; justify-content: space-between; gap: 24px; align-items: end; }
.welcome-heading h2 { margin: 8px 0 0; font-size: clamp(27px, 3vw, 42px); line-height: 1.04; letter-spacing: -.05em; }
.welcome-heading > span { flex: 0 0 auto; padding: 7px 9px; color: #46616d; background: #edf2f2; border: 1px solid #cedadd; font: 750 8px/1 var(--mono); }
.welcome-card > p { max-width: 720px; margin: 13px 0 0; color: #61727b; font-size: 11px; line-height: 1.8; }
.contract-question { display: grid; grid-template-columns: 82px minmax(0, 1fr) auto; gap: 14px; align-items: center; width: 100%; margin-top: 20px; padding: 15px; cursor: pointer; color: #223e4b; background: #fff; border: 1px solid #cbd7db; border-left: 4px solid var(--signal); text-align: left; box-shadow: 0 8px 24px rgba(25,51,64,.06); transition: transform .18s ease, box-shadow .18s ease; }
.contract-question:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 13px 30px rgba(25,51,64,.1); }
.contract-question:disabled { cursor: not-allowed; opacity: .55; }
.contract-question span { color: #ac5b24; font: 800 8px/1 var(--mono); text-transform: uppercase; }
.contract-question strong { font-size: 11px; line-height: 1.65; }
.contract-question b { color: #236478; font-size: 9px; white-space: nowrap; }
.scope-note { display: flex; gap: 12px; align-items: start; margin-top: 10px; padding: 9px 12px; color: #675b4f; background: #f4eee6; border: 1px solid #e5d8c7; }
.scope-note b { flex: 0 0 auto; color: #9b5727; font: 800 8px/1.5 var(--mono); }
.scope-note p { margin: 0; font-size: 9px; line-height: 1.55; }
.turn-block { max-width: 850px; margin: 0 auto 34px; padding-left: 13px; border-left: 2px solid transparent; transition: border-color .18s ease; }
.turn-block.is-selected { border-left-color: var(--signal); }
.user-message { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 12px; align-items: start; margin-bottom: 12px; }
.user-message span { display: grid; place-items: center; width: 28px; height: 28px; color: #fff; background: var(--ink); font: 750 8px/1 var(--mono); border-radius: 50%; }
.user-message p { justify-self: start; margin: 0; padding: 10px 14px; color: #fff; background: var(--ink); border-radius: 2px 13px 13px 13px; font-size: 12px; line-height: 1.65; }
.agent-answer { margin-left: 40px; background: #fff; border: 1px solid var(--line); box-shadow: 0 8px 24px rgba(25, 51, 64, .06); }
.agent-answer > header { display: flex; justify-content: space-between; gap: 16px; align-items: center; padding: 10px 13px; background: #f0f4f5; border-bottom: 1px solid var(--line); }
.agent-answer > header div { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.answer-mark { padding: 4px 6px; color: #fff; background: #176b86; font: 800 7px/1 var(--mono); letter-spacing: .08em; }
.agent-answer > header b { color: #405762; font-size: 9px; }
.agent-answer > header em { padding: 4px 6px; color: #386256; background: #e7f2ee; font: normal 750 7px/1 var(--mono); }
.agent-answer > header em.source-deterministic_fallback { color: #855126; background: #fff0df; }
.agent-answer > header small { color: #829099; font: 650 8px/1 var(--mono); }
.answer-copy { padding: 19px 20px; border-left: 3px solid var(--success); }
.answer-copy p { margin: 0; color: #2e4652; font-size: 12px; line-height: 1.9; white-space: pre-line; }
.finding-sheet { border-top: 1px solid #dfe6e8; }
.finding-sheet > header { display: flex; justify-content: space-between; gap: 12px; padding: 9px 14px; background: #f7f8f6; }
.finding-sheet > header div { display: flex; gap: 8px; align-items: center; }
.finding-sheet > header span { color: #347564; font: 800 7px/1 var(--mono); letter-spacing: .08em; }
.finding-sheet > header b { color: #546c75; font: 750 8px/1 var(--mono); }
.finding-sheet > header code { color: #829096; font: 650 7px/1.3 var(--mono); }
.finding-metrics { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); margin: 0; background: #dfe6e8; gap: 1px; }
.finding-metrics > div { min-width: 0; padding: 12px 10px; background: #fff; }
.finding-metrics dt { color: #809097; font-size: 8px; }
.finding-metrics dd { overflow: hidden; margin: 5px 0 4px; color: #173b4a; font: 800 15px/1 var(--mono); text-overflow: ellipsis; }
.finding-metrics small { display: block; overflow: hidden; color: #849198; font-size: 7px; text-overflow: ellipsis; white-space: nowrap; }
.evidence-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; padding-top: 1px; background: #dfe6e8; border-top: 1px solid #dfe6e8; }
.evidence-strip button { display: grid; gap: 4px; min-width: 0; padding: 10px 12px; cursor: pointer; color: #294956; background: #f8fbfb; border: 0; text-align: left; }
.evidence-strip button:hover, .evidence-strip button.is-active { background: #edf7f7; box-shadow: inset 3px 0 #2b8397; }
.evidence-strip span { color: #697f88; font-size: 8px; }
.evidence-strip b { overflow: hidden; font: 750 11px/1.2 var(--mono); text-overflow: ellipsis; }
.evidence-strip small { color: #8b989d; font-size: 7px; }
.limitation-list { padding: 13px 17px; color: #71533e; background: #fbf5ed; border-top: 1px solid #eadbca; }
.limitation-list > b { color: #a25c29; font: 800 8px/1 var(--mono); }
.limitation-list ul { display: grid; gap: 4px; margin: 7px 0 0; padding-left: 16px; }
.limitation-list li { font-size: 8px; line-height: 1.55; }
.agent-answer > footer { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 9px 14px; color: #72828b; background: #fbfcfc; border-top: 1px solid #e4e9eb; font-size: 8px; }
.agent-answer > footer span::before { content: '·'; margin-right: 5px; color: var(--signal); }
.turn-progress, .turn-error { margin-left: 40px; padding: 13px 15px; background: #fff; border: 1px solid var(--line); }
.turn-progress { display: flex; gap: 11px; align-items: center; color: #667881; }
.turn-progress > span { width: 10px; height: 10px; border: 2px solid #9fb6bf; border-top-color: var(--signal); border-radius: 50%; animation: spin .8s linear infinite; }
.turn-progress b { color: #3d5661; font-size: 10px; }
.turn-progress p { margin: 3px 0 0; font-size: 8px; }
.turn-error { border-left: 3px solid #b65a53; }
.turn-error b { color: #8d3d37; font-size: 11px; }
.turn-error p { margin: 5px 0 0; color: #6d7478; font-size: 9px; }
.turn-error code { display: block; margin-top: 6px; color: #9b5b55; font: 7px/1.4 var(--mono); }
.turn-error button { margin-top: 9px; cursor: pointer; color: #225b70; background: transparent; border: 0; font-size: 9px; font-weight: 750; }
.composer { position: sticky; bottom: 0; padding: 12px max(24px, 5vw) 15px; background: rgba(251, 250, 246, .94); border-top: 1px solid var(--line); backdrop-filter: blur(14px); }
.composer > label { display: block; margin-bottom: 6px; color: #50636d; font-size: 9px; font-weight: 750; }
.composer > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: stretch; background: #fff; border: 1px solid #9cafb7; box-shadow: 0 8px 25px rgba(24, 51, 64, .09); }
.composer textarea { min-height: 58px; resize: none; padding: 12px 14px; color: var(--ink); background: transparent; border: 0; outline: 0; font: 11px/1.65 var(--sans); }
.composer button { min-width: 92px; margin: 6px; cursor: pointer; color: #fff; background: var(--ink); border: 0; border-radius: 2px; font-size: 10px; font-weight: 800; }
.composer button span { margin-left: 7px; color: #ffb27a; }
.composer button:disabled { cursor: not-allowed; opacity: .45; }
.composer .cancel-button { background: #89423d; }
.composer > p { display: flex; gap: 7px; margin: 7px 0 0; color: #a5443d; font-size: 9px; }
.composer > p b { font: 800 8px/1.4 var(--mono); }
.composer > small { display: block; margin-top: 6px; color: #829097; font-size: 8px; }
.audit-panel { min-width: 0; height: calc(100vh - 184px); overflow-y: auto; padding: 24px 20px 40px; background: #f2f5f5; }
.audit-panel > header { padding: 0 2px 15px; border-bottom: 1px solid #cad5d9; }
.audit-panel h2 { margin: 6px 0 0; font-size: 19px; letter-spacing: -.025em; }
.audit-section { margin-top: 12px; padding: 13px; background: #fff; border: 1px solid #d3dde0; border-radius: 2px; }
.audit-section h3, .audit-section summary { margin: 0; color: #38505c; font-size: 9px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; }
.audit-section summary { display: flex; justify-content: space-between; cursor: pointer; list-style: none; }
.audit-section summary span { color: var(--signal); font: 750 9px/1 var(--mono); }
.identity-section dl, .ledger-list { display: grid; gap: 7px; margin: 12px 0 0; }
.identity-section dl div, .ledger-list div { min-width: 0; }
.identity-section dt, .ledger-list dt { color: #87949a; font-size: 7px; }
.identity-section dd, .ledger-list dd { overflow-wrap: anywhere; margin: 2px 0 0; color: #2d4957; font: 650 8px/1.45 var(--mono); }
.evidence-focus { display: grid; gap: 7px; border-top: 3px solid #2d8298; }
.evidence-focus > span { justify-self: start; padding: 3px 5px; color: #fff; background: #2d8298; font: 750 7px/1 var(--mono); }
.evidence-focus code { overflow-wrap: anywhere; color: #3d6677; font: 650 8px/1.5 var(--mono); }
.evidence-focus strong { overflow-wrap: anywhere; color: var(--ink); font: 800 17px/1.25 var(--mono); }
.evidence-focus p { margin: 0; color: #74848c; font-size: 8px; }
.receipt-list, .attempt-list { display: grid; gap: 8px; margin: 12px 0 0; padding: 0; list-style: none; }
.receipt-list li { display: grid; gap: 4px; padding-top: 8px; border-top: 1px solid #e3e8ea; }
.receipt-list li div { display: flex; justify-content: space-between; gap: 8px; }
.receipt-list b { color: #314d5a; font-size: 8px; }
.receipt-list code { color: #367063; font: 700 7px/1.3 var(--mono); }
.receipt-list small { overflow-wrap: anywhere; color: #78878e; font: 7px/1.45 var(--mono); }
.receipt-list em { color: #a24740; font: normal 7px/1.3 var(--mono); }
.guard-section { border-top: 3px solid #33816b; }
.guard-section.is-block { border-top-color: #a84d46; }
.guard-section.is-not-run { border-top-color: #87969c; }
.guard-section strong { display: block; margin-top: 10px; color: #2e735f; font: 800 18px/1 var(--mono); }
.guard-section.is-block strong { color: #9f463f; }
.guard-section.is-not-run strong { color: #697b82; }
.guard-section p { margin: 7px 0; color: #657780; font-size: 8px; line-height: 1.5; }
.guard-section code { overflow-wrap: anywhere; color: #6e8088; font: 7px/1.5 var(--mono); }
.usage-ledger { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; margin: 12px 0 0; background: #dbe3e6; }
.usage-ledger > div { min-width: 0; padding: 8px; background: #f7f9f9; }
.usage-ledger dt { color: #87949a; font-size: 7px; }
.usage-ledger dd { overflow: hidden; margin: 4px 0 0; color: #294653; font: 750 9px/1.2 var(--mono); text-overflow: ellipsis; }
.attempt-list li { display: grid; grid-template-columns: 21px minmax(0, 1fr); gap: 8px; align-items: center; padding-top: 8px; border-top: 1px solid #e3e8ea; }
.attempt-list > li > span { display: grid; place-items: center; width: 21px; height: 21px; color: #fff; background: #526e7a; font: 750 7px/1 var(--mono); }
.attempt-list div { display: grid; gap: 2px; min-width: 0; }
.attempt-list b { overflow: hidden; color: #35505b; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.attempt-list small { color: #839197; font-size: 7px; }
.audit-empty { display: grid; place-items: center; min-height: 320px; padding: 30px; color: #829096; text-align: center; }
.audit-empty span { color: #b4c1c6; font-size: 32px; }
.audit-empty p { max-width: 290px; font-size: 10px; line-height: 1.7; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 1120px) {
  .identity-rail { grid-template-columns: 1fr auto; }
  .identity-rail dl { grid-column: 1 / -1; grid-row: 2; }
  .chat-workbench { grid-template-columns: minmax(500px, 1.2fr) minmax(340px, .8fr); }
  .audit-panel { height: calc(100vh - 244px); }
  .dialogue-stream { height: calc(100vh - 346px); }
  .finding-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 820px) {
  .identity-rail { position: relative; top: 0; grid-template-columns: 1fr; }
  .identity-rail dl { grid-column: auto; grid-row: auto; grid-template-columns: 1fr 1fr; }
  .lifecycle-chip { justify-self: start; }
  .chat-workbench { display: flex; flex-direction: column; }
  .dialogue-stream { height: auto; min-height: 520px; overflow: visible; padding: 25px 16px 32px; }
  .composer { padding-inline: 16px; }
  .audit-panel { height: auto; overflow: visible; border-top: 3px solid var(--ink); }
}
@media (max-width: 580px) {
  .identity-rail { padding: 16px 14px; }
  .identity-rail dl { grid-template-columns: 1fr; }
  .welcome-heading { align-items: start; flex-direction: column; }
  .contract-question { grid-template-columns: 1fr; }
  .contract-question b { justify-self: start; }
  .user-message { grid-template-columns: 24px minmax(0, 1fr); gap: 8px; }
  .user-message span { width: 24px; height: 24px; }
  .agent-answer, .turn-progress, .turn-error { margin-left: 32px; }
  .agent-answer > header { align-items: flex-start; }
  .finding-metrics, .evidence-strip { grid-template-columns: 1fr 1fr; }
  .usage-ledger { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .contract-question, .turn-block { transition: none; }
  .turn-progress > span { animation-duration: 1.6s; }
}
</style>
