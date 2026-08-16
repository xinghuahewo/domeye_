<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  cancelCountryOutageChatTurn,
  createCountryOutageChatConversation,
  createCountryOutageChatTurn,
  getCountryOutageChatConversation,
  type CountryOutageChatEvidence,
  type CountryOutageChatGoalResult,
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
const conversation = ref<Awaited<ReturnType<typeof getCountryOutageChatConversation>>['conversation'] | null>(null)
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
let loadToken = 0
let polling: number | undefined

const suggestions = [
  '这次事件发生了什么',
  'IP地址变化情况',
  'IP地址变化趋势',
  '现在还有多少前缀不可见，是不是全国都断了',
]

const countryName = computed(() => {
  const code = page.value?.resolution.country_code
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
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed)
}

function statusLabel(value: string): string {
  return ({
    supported: '可回答',
    partial: '局部可答',
    clarify: '需要澄清',
    unsupported: '当前越界',
    invalid_data: '数据异常',
  } as Record<string, string>)[value] || value
}

function unitLabel(value: string | null | undefined): string {
  return ({
    unique_ipv4_address: '唯一 IPv4 地址',
    ipv6_slash48_equivalent: 'IPv6 /48 等价块',
    prefix: '前缀',
    asn: 'ASN',
    utc_timestamp: 'UTC 时间',
    metadata: '元数据',
  } as Record<string, string>)[value ?? ''] || value || '未标注'
}

function valueLabel(item: CountryOutageChatEvidence | null): string {
  if (!item) return '—'
  if (item.value === null || item.value === undefined) return 'null / unknown'
  if (typeof item.value === 'object') return JSON.stringify(item.value)
  return typeof item.value === 'number'
    ? item.value.toLocaleString('zh-CN')
    : String(item.value)
}

function resultEvidence(
  turn: CountryOutageChatTurn,
  result: CountryOutageChatGoalResult,
) {
  const wanted = new Set(result.evidence_refs)
  return (turn.answer?.evidence ?? []).filter((item) => wanted.has(item.evidence_ref))
}

function selectTurn(turn: CountryOutageChatTurn) {
  selectedTurnId.value = turn.turn_id
  selectedEvidenceRef.value = turn.answer?.evidence[0]?.evidence_ref ?? null
}

function inspectEvidence(turn: CountryOutageChatTurn, evidenceRef: string) {
  selectedTurnId.value = turn.turn_id
  selectedEvidenceRef.value = evidenceRef
}

function stopPolling() {
  if (polling !== undefined) window.clearInterval(polling)
  polling = undefined
  activeTurnId.value = null
}

async function refreshConversation() {
  if (!conversation.value) return
  const snapshot = await getCountryOutageChatConversation(
    conversation.value.conversation_id,
  )
  conversation.value = snapshot.conversation
  const active = snapshot.conversation.turns.find((turn) =>
    !['completed', 'failed', 'cancelled'].includes(turn.state)
  )
  activeTurnId.value = active?.turn_id ?? null
}

function startPolling() {
  stopPolling()
  polling = window.setInterval(() => {
    void refreshConversation().catch(() => undefined)
  }, 750)
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
  const token = ++loadToken
  stopPolling()
  loading.value = true
  error.value = ''
  page.value = null
  conversation.value = null
  try {
    if (!reference.value) throw new Error('缺少国家中断事件引用')
    const event = await getCountryOutageGeneralPage(reference.value)
    if (token !== loadToken) return
    page.value = event.page
    const resolution = event.page.resolution
    const created = await createCountryOutageChatConversation({
      event_reference: resolution.legacy_reference,
      publication_id: resolution.publication_id,
      revision: resolution.revision,
      idempotency_key: idempotency('chat-create'),
    })
    if (token !== loadToken) return
    conversation.value = created.conversation
  } catch (cause) {
    if (token === loadToken) error.value = errorMessage(cause)
  } finally {
    if (token === loadToken) loading.value = false
  }
}

async function submit(question = draft.value) {
  const value = question.trim()
  if (!value || !conversation.value || sending.value) return
  sending.value = true
  error.value = ''
  draft.value = ''
  startPolling()
  try {
    const response = await createCountryOutageChatTurn(
      conversation.value.conversation_id,
      value,
      idempotency('chat-turn'),
    )
    await refreshConversation()
    selectedTurnId.value = response.turn.turn_id
    selectedEvidenceRef.value = response.turn.answer?.evidence[0]?.evidence_ref ?? null
    await scrollToLatest()
  } catch (cause) {
    error.value = errorMessage(cause)
    await refreshConversation().catch(() => undefined)
  } finally {
    stopPolling()
    sending.value = false
    void nextTick(() => composer.value?.focus())
  }
}

async function cancelActiveTurn() {
  if (!conversation.value || !activeTurnId.value || cancelling.value) return
  cancelling.value = true
  try {
    await cancelCountryOutageChatTurn(
      conversation.value.conversation_id,
      activeTurnId.value,
    )
    await refreshConversation()
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    cancelling.value = false
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
  loadToken += 1
  stopPolling()
})
</script>

<template>
  <main class="chat-page">
    <header v-if="conversation" class="identity-rail">
      <div class="identity-title">
        <RouterLink :to="{ name: 'event-detail', query: { ref: reference } }">← 返回事件观测</RouterLink>
        <p>P1 · RRC25 EVIDENCE CHAT</p>
        <h1>{{ countryName }}国家中断问答</h1>
      </div>
      <dl>
        <div><dt>Publication</dt><dd>{{ conversation.binding.publication_id }}</dd></div>
        <div><dt>Revision</dt><dd>r{{ conversation.binding.revision }} · G{{ conversation.binding_generation }}</dd></div>
        <div><dt>数据来源</dt><dd>{{ conversation.binding.collector_id.toUpperCase() }} · 控制面</dd></div>
        <div><dt>数据截至</dt><dd>{{ formatTime(conversation.binding.data_through) }}</dd></div>
      </dl>
      <div class="lifecycle-chip">
        <span></span>
        {{ conversation.binding.is_final_in_data_range ? '数据范围内已结束' : '事件结束未知' }}
      </div>
    </header>

    <PageState v-if="loading" kind="loading" title="正在绑定事件证据" detail="校验 publication、revision、RRC25 和页面能力。" />
    <PageState v-else-if="!conversation" kind="error" title="问答会话暂不可用" :detail="error" @retry="load" />

    <section v-else class="chat-workbench">
      <div class="dialogue-panel">
        <div ref="dialogue" class="dialogue-stream" aria-live="polite">
          <article class="welcome-card">
            <p class="section-index">01 / ASK THE EVIDENCE</p>
            <h2>用自然语言问当前事件</h2>
            <p>可以口语、省略、修正或一句问多件事。系统只会在当前 publication 内调用已登记的只读能力。</p>
            <div class="suggestion-grid">
              <button v-for="item in suggestions" :key="item" type="button" @click="submit(item)">
                <span>↗</span>{{ item }}
              </button>
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
                </div>
                <small>G{{ turn.binding_generation }} · {{ formatTime(turn.completed_at) }}</small>
              </header>

              <section
                v-for="result in turn.answer.results"
                :key="result.goal_id"
                class="goal-card"
                :class="`is-${result.answerability}`"
              >
                <div class="goal-heading">
                  <span>{{ statusLabel(result.answerability) }}</span>
                  <code>{{ result.normalized_kind }}</code>
                </div>
                <p>{{ result.text }}</p>
                <div v-if="result.evidence_refs.length" class="evidence-links">
                  <button
                    v-for="item in resultEvidence(turn, result).slice(0, 6)"
                    :key="item.evidence_ref"
                    type="button"
                    @click.stop="inspectEvidence(turn, item.evidence_ref)"
                  >
                    {{ item.source }}{{ item.field_path }}
                  </button>
                  <small v-if="result.evidence_refs.length > 6">+{{ result.evidence_refs.length - 6 }} 条证据</small>
                </div>
              </section>

              <footer>
                <span>身份已核验</span>
                <span>{{ turn.answer.execution_trace.nodes.length }} 个执行节点</span>
                <span>状态 {{ turn.answer.state_receipt.status === 'committed' ? '已提交' : '未改变' }}</span>
              </footer>
            </div>

            <div v-else-if="turn.error" class="turn-error">
              <b>{{ turn.state === 'cancelled' ? '本轮已取消' : '本轮未发布答案' }}</b>
              <p>{{ turn.error.message }}</p>
              <button type="button" @click.stop="submit(turn.question)">重试这个问题</button>
            </div>
            <div v-else class="turn-progress">
              <span></span><p>正在理解目标并核验只读证据…</p>
            </div>
          </article>
        </div>

        <form class="composer" @submit.prevent="submit()">
          <label for="p1-chat-question">继续追问当前事件</label>
          <div>
            <textarea
              id="p1-chat-question"
              ref="composer"
              v-model="draft"
              rows="2"
              maxlength="2000"
              :disabled="sending"
              placeholder="例如：那 IPv6 呢？把新出现前缀也带上。"
              @keydown="onComposerKeydown"
            ></textarea>
            <button v-if="activeTurnId" type="button" class="cancel-button" :disabled="cancelling" @click="cancelActiveTurn">
              {{ cancelling ? '正在取消' : '取消本轮' }}
            </button>
            <button v-else type="submit" :disabled="sending || !draft.trim()">
              {{ sending ? '核验中' : '发送' }}
              <span>↑</span>
            </button>
          </div>
          <p v-if="error" role="alert">{{ error }}</p>
          <small>Enter 发送 · Shift + Enter 换行 · 模型不能创造事实或提交状态</small>
        </form>
      </div>

      <aside class="audit-panel" aria-label="当前回答核对面板">
        <header>
          <p class="section-index">02 / VERIFY THE CHAIN</p>
          <h2>证据与执行链</h2>
        </header>
        <template v-if="selectedTurn?.answer">
          <section class="audit-section identity-section">
            <h3>本轮身份</h3>
            <dl>
              <div><dt>Incident</dt><dd>{{ selectedTurn.answer.binding.incident_id }}</dd></div>
              <div><dt>Publication</dt><dd>{{ selectedTurn.answer.binding.publication_id }}</dd></div>
              <div><dt>Revision</dt><dd>{{ selectedTurn.answer.binding.revision }}</dd></div>
              <div><dt>Collector</dt><dd>{{ selectedTurn.answer.binding.collector_id }}</dd></div>
            </dl>
          </section>

          <section v-if="selectedEvidence" class="audit-section evidence-focus">
            <h3>选中证据</h3>
            <span>{{ selectedEvidence.source }}</span>
            <code>{{ selectedEvidence.field_path }}</code>
            <strong>{{ valueLabel(selectedEvidence) }}</strong>
            <p>{{ unitLabel(selectedEvidence.unit) }}<template v-if="selectedEvidence.observed_at_utc"> · {{ formatTime(selectedEvidence.observed_at_utc) }}</template></p>
          </section>

          <details class="audit-section" open>
            <summary>UserGoalPlan <span>{{ selectedTurn.answer.semantic_plan.user_goal_plan.goals.length }}</span></summary>
            <ol>
              <li v-for="goal in selectedTurn.answer.semantic_plan.user_goal_plan.goals" :key="goal.goal_id">
                <b>{{ goal.normalized_kind }}</b>
                <small>{{ goal.requested_goal }}</small>
                <code>{{ JSON.stringify(goal.entities) }}</code>
              </li>
            </ol>
          </details>

          <details class="audit-section">
            <summary>GroundingPlan <span>{{ selectedTurn.answer.semantic_plan.grounding_plan.nodes.length }}</span></summary>
            <ol>
              <li v-for="decision in selectedTurn.answer.semantic_plan.grounding_plan.decisions" :key="decision.goal_id">
                <b>{{ decision.goal_id }} · {{ decision.answerability }}</b>
                <small>{{ decision.reason_codes.join(' · ') }}</small>
              </li>
            </ol>
          </details>

          <details class="audit-section">
            <summary>Tool / Operator <span>{{ selectedTurn.answer.execution_trace.nodes.length }}</span></summary>
            <ol>
              <li v-for="node in selectedTurn.answer.execution_trace.nodes" :key="node.node_id">
                <b>{{ node.execution_unit }} · {{ node.status }}</b>
                <small>{{ node.capability_ids.join(', ') }}</small>
              </li>
            </ol>
          </details>

          <details class="audit-section state-section">
            <summary>DialogState <span>{{ selectedTurn.answer.state_receipt.status }}</span></summary>
            <div class="state-pair">
              <div><b>BEFORE</b><pre>{{ JSON.stringify(selectedTurn.answer.state_receipt.before, null, 2) }}</pre></div>
              <div><b>AFTER</b><pre>{{ JSON.stringify(selectedTurn.answer.state_receipt.after, null, 2) }}</pre></div>
            </div>
          </details>
        </template>
        <div v-else class="audit-empty">
          <span>⌖</span>
          <p>选择一条回答后，可在这里核对身份、语义目标、执行节点、证据和状态提交。</p>
        </div>
      </aside>
    </section>
  </main>
</template>

<style scoped>
.chat-page { --ink: #112d3d; --ink-soft: #375160; --line: #d6dee2; --paper: #fbfaf6; --orange: #ed7a32; min-height: calc(100vh - 92px); color: var(--ink); }
.identity-rail { position: sticky; z-index: 8; top: 66px; display: grid; grid-template-columns: minmax(300px, .78fr) minmax(560px, 1.35fr) auto; gap: 22px; align-items: center; padding: 17px 22px; color: #edf7fa; background: linear-gradient(118deg, #0d2938 0%, #143e50 68%, #1b5060 100%); border-bottom: 3px solid var(--orange); box-shadow: 0 10px 28px rgba(11, 35, 48, .18); }
.identity-title a { color: #9dd2df; font-size: 10px; font-weight: 750; text-decoration: none; }
.identity-title p { margin: 7px 0 2px; color: #75b6c7; font: 750 8px/1.2 var(--mono); letter-spacing: .13em; }
.identity-title h1 { margin: 0; font-size: 20px; letter-spacing: -.025em; }
.identity-rail dl { display: grid; grid-template-columns: 1.4fr .55fr .72fr .72fr; gap: 1px; margin: 0; background: rgba(255,255,255,.13); }
.identity-rail dl div { min-width: 0; padding: 8px 10px; background: rgba(7, 29, 41, .46); }
.identity-rail dt { color: #82aebb; font-size: 8px; text-transform: uppercase; }
.identity-rail dd { overflow: hidden; margin: 4px 0 0; font: 650 9px/1.35 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.lifecycle-chip { display: flex; gap: 7px; align-items: center; padding: 8px 10px; color: #ffd5b5; background: rgba(101, 48, 24, .36); border: 1px solid rgba(255, 171, 108, .35); border-radius: 2px; font-size: 9px; font-weight: 750; white-space: nowrap; }
.lifecycle-chip span { width: 6px; height: 6px; background: #ef8b45; border-radius: 50%; box-shadow: 0 0 0 4px rgba(239,139,69,.14); }
.chat-workbench { display: grid; grid-template-columns: minmax(520px, 1.35fr) minmax(360px, .65fr); min-height: calc(100vh - 184px); background: #e5eaec; }
.dialogue-panel { position: relative; min-width: 0; background: radial-gradient(circle at 12% 5%, rgba(233, 176, 120, .12), transparent 28%), var(--paper); border-right: 1px solid #cbd5da; }
.dialogue-stream { height: calc(100vh - 286px); min-height: 420px; overflow-y: auto; padding: 30px max(24px, 5vw) 40px; scroll-behavior: smooth; }
.section-index { margin: 0; color: var(--orange); font: 800 8px/1.2 var(--mono); letter-spacing: .12em; }
.welcome-card { max-width: 780px; margin: 0 auto 38px; padding-bottom: 30px; border-bottom: 1px solid var(--line); }
.welcome-card h2 { margin: 8px 0 8px; font-size: clamp(26px, 3vw, 40px); line-height: 1.08; letter-spacing: -.045em; }
.welcome-card > p:not(.section-index) { max-width: 650px; margin: 0; color: #61727b; font-size: 11px; line-height: 1.8; }
.suggestion-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 20px; }
.suggestion-grid button { display: flex; gap: 9px; align-items: center; min-height: 44px; padding: 9px 12px; cursor: pointer; color: #294451; background: rgba(255,255,255,.78); border: 1px solid #d7dfe2; border-radius: 2px; font-size: 10px; text-align: left; transition: .18s ease; }
.suggestion-grid button:hover { border-color: #e49a63; transform: translateY(-1px); box-shadow: 0 6px 18px rgba(38, 66, 80, .08); }
.suggestion-grid span { color: var(--orange); font-size: 14px; }
.turn-block { max-width: 780px; margin: 0 auto 34px; padding-left: 13px; border-left: 2px solid transparent; transition: border-color .18s ease; }
.turn-block.is-selected { border-left-color: var(--orange); }
.user-message { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 12px; align-items: start; margin-bottom: 12px; }
.user-message span { display: grid; place-items: center; width: 28px; height: 28px; color: #fff; background: var(--ink); font: 750 8px/1 var(--mono); border-radius: 50%; }
.user-message p { justify-self: start; margin: 0; padding: 10px 14px; color: #fff; background: var(--ink); border-radius: 2px 13px 13px 13px; font-size: 12px; line-height: 1.65; }
.agent-answer { margin-left: 40px; background: #fff; border: 1px solid var(--line); box-shadow: 0 8px 24px rgba(25, 51, 64, .06); }
.agent-answer > header { display: flex; justify-content: space-between; gap: 16px; align-items: center; padding: 10px 13px; background: #f0f4f5; border-bottom: 1px solid var(--line); }
.agent-answer > header div { display: flex; gap: 8px; align-items: center; }
.answer-mark { padding: 4px 6px; color: #fff; background: #176b86; font: 800 7px/1 var(--mono); letter-spacing: .08em; }
.agent-answer > header b { color: #405762; font-size: 9px; }
.agent-answer > header small { color: #829099; font: 650 8px/1 var(--mono); }
.goal-card { padding: 17px 18px 16px; border-left: 3px solid #3b8d78; }
.goal-card + .goal-card { border-top: 1px solid #e1e7e9; }
.goal-card.is-partial { border-left-color: #ce853f; }
.goal-card.is-unsupported, .goal-card.is-invalid_data { border-left-color: #b85d55; }
.goal-card.is-clarify { border-left-color: #7f70a9; }
.goal-heading { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.goal-heading span { color: #477367; font-size: 9px; font-weight: 800; }
.is-unsupported .goal-heading span, .is-invalid_data .goal-heading span { color: #99483f; }
.goal-heading code { color: #83919a; font: 650 8px/1.2 var(--mono); }
.goal-card > p { margin: 9px 0 0; color: #344b57; font-size: 11px; line-height: 1.82; white-space: pre-line; }
.evidence-links { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 12px; }
.evidence-links button { max-width: 210px; overflow: hidden; padding: 5px 7px; cursor: pointer; color: #256579; background: #f0f7f8; border: 1px solid #cde1e5; border-radius: 2px; font: 650 7px/1.2 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.evidence-links small { align-self: center; color: #7a8991; font-size: 8px; }
.agent-answer > footer { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 9px 14px; color: #72828b; background: #fbfcfc; border-top: 1px solid #e4e9eb; font-size: 8px; }
.agent-answer > footer span::before { content: '·'; margin-right: 5px; color: var(--orange); }
.turn-progress, .turn-error { margin-left: 40px; padding: 13px 15px; background: #fff; border: 1px solid var(--line); }
.turn-progress { display: flex; gap: 10px; align-items: center; color: #667881; font-size: 10px; }
.turn-progress span { width: 9px; height: 9px; border: 2px solid #9fb6bf; border-top-color: var(--orange); border-radius: 50%; animation: spin .8s linear infinite; }
.turn-progress p, .turn-error p { margin: 0; }
.turn-error { border-left: 3px solid #b65a53; }
.turn-error b { color: #8d3d37; font-size: 11px; }
.turn-error p { margin-top: 5px; color: #6d7478; font-size: 9px; }
.turn-error button { margin-top: 9px; cursor: pointer; color: #225b70; background: transparent; border: 0; font-size: 9px; font-weight: 750; }
.composer { position: sticky; bottom: 0; padding: 12px max(24px, 5vw) 15px; background: rgba(251, 250, 246, .94); border-top: 1px solid var(--line); backdrop-filter: blur(14px); }
.composer > label { display: block; margin-bottom: 6px; color: #50636d; font-size: 9px; font-weight: 750; }
.composer > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: stretch; background: #fff; border: 1px solid #9cafb7; box-shadow: 0 8px 25px rgba(24, 51, 64, .09); }
.composer textarea { min-height: 58px; resize: none; padding: 12px 14px; color: var(--ink); background: transparent; border: 0; outline: 0; font: 11px/1.65 var(--sans); }
.composer button { min-width: 92px; margin: 6px; cursor: pointer; color: #fff; background: var(--ink); border: 0; border-radius: 2px; font-size: 10px; font-weight: 800; }
.composer button span { margin-left: 7px; color: #ffb27a; }
.composer button:disabled { cursor: not-allowed; opacity: .45; }
.composer .cancel-button { background: #89423d; }
.composer > p { margin: 7px 0 0; color: #a5443d; font-size: 9px; }
.composer > small { display: block; margin-top: 6px; color: #829097; font-size: 8px; }
.audit-panel { min-width: 0; height: calc(100vh - 184px); overflow-y: auto; padding: 24px 20px 40px; background: #f2f5f5; }
.audit-panel > header { padding: 0 2px 15px; border-bottom: 1px solid #cad5d9; }
.audit-panel h2 { margin: 6px 0 0; font-size: 19px; letter-spacing: -.025em; }
.audit-section { margin-top: 12px; padding: 13px; background: #fff; border: 1px solid #d3dde0; border-radius: 2px; }
.audit-section h3, .audit-section summary { margin: 0; color: #38505c; font-size: 9px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; }
.audit-section summary { display: flex; justify-content: space-between; cursor: pointer; list-style: none; }
.audit-section summary span { color: var(--orange); font: 750 9px/1 var(--mono); }
.identity-section dl { display: grid; gap: 7px; margin: 12px 0 0; }
.identity-section dl div { min-width: 0; }
.identity-section dt { color: #87949a; font-size: 7px; }
.identity-section dd { overflow-wrap: anywhere; margin: 2px 0 0; color: #2d4957; font: 650 8px/1.4 var(--mono); }
.evidence-focus { display: grid; gap: 7px; border-top: 3px solid #2d8298; }
.evidence-focus > span { justify-self: start; padding: 3px 5px; color: #fff; background: #2d8298; font: 750 7px/1 var(--mono); text-transform: uppercase; }
.evidence-focus code { overflow-wrap: anywhere; color: #3d6677; font: 650 8px/1.5 var(--mono); }
.evidence-focus strong { overflow-wrap: anywhere; color: var(--ink); font: 800 17px/1.25 var(--mono); }
.evidence-focus p { margin: 0; color: #74848c; font-size: 8px; }
.audit-section ol { display: grid; gap: 8px; margin: 12px 0 0; padding: 0; list-style: none; }
.audit-section li { display: grid; gap: 3px; padding-top: 8px; border-top: 1px solid #e3e8ea; }
.audit-section li b { color: #314d5a; font-size: 9px; }
.audit-section li small { color: #73838b; font-size: 8px; line-height: 1.4; }
.audit-section li code { overflow-wrap: anywhere; color: #486b79; font: 7px/1.5 var(--mono); }
.state-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-top: 12px; }
.state-pair > div { min-width: 0; }
.state-pair b { color: #87949b; font: 750 7px/1 var(--mono); }
.state-pair pre { max-height: 230px; overflow: auto; margin: 5px 0 0; padding: 8px; color: #40606d; background: #f4f7f7; font: 7px/1.45 var(--mono); white-space: pre-wrap; }
.audit-empty { display: grid; place-items: center; min-height: 320px; padding: 30px; color: #829096; text-align: center; }
.audit-empty span { color: #b4c1c6; font-size: 32px; }
.audit-empty p { max-width: 280px; font-size: 10px; line-height: 1.7; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 1080px) {
  .identity-rail { grid-template-columns: 1fr auto; }
  .identity-rail dl { grid-column: 1 / -1; grid-row: 2; }
  .chat-workbench { grid-template-columns: minmax(480px, 1.2fr) minmax(320px, .8fr); }
  .audit-panel { height: calc(100vh - 244px); }
  .dialogue-stream { height: calc(100vh - 346px); }
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
@media (max-width: 560px) {
  .identity-rail { padding: 16px 14px; }
  .identity-rail dl { grid-template-columns: 1fr; }
  .suggestion-grid { grid-template-columns: 1fr; }
  .user-message { grid-template-columns: 24px minmax(0, 1fr); gap: 8px; }
  .user-message span { width: 24px; height: 24px; }
  .agent-answer, .turn-progress, .turn-error { margin-left: 32px; }
  .agent-answer > header { align-items: flex-start; }
  .state-pair { grid-template-columns: 1fr; }
}
</style>
