<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  cancelCountryOutageChatTurn,
  COUNTRY_OUTAGE_FIRST_SLICE_QUESTION,
  createCountryOutageChatConversation,
  createCountryOutageChatTurn,
  getCountryOutageChatConversation,
  type CountryOutageChatBasis,
  type CountryOutageChatConversation,
  type CountryOutageChatTurn,
  type CountryOutageChatTurnAnswer,
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
const turns = computed(() => conversation.value?.turns ?? [])
const countryName = computed(() => {
  const code = conversation.value?.binding.country_code
    ?? page.value?.resolution.country_code
  if (!code) return '当前国家'
  try {
    return new Intl.DisplayNames(['zh-CN'], { type: 'region' }).of(code) || code
  } catch {
    return code
  }
})

function answerFor(turn: CountryOutageChatTurn): CountryOutageChatTurnAnswer | null {
  return 'answer' in turn ? turn.answer : null
}

function basisFor(turn: CountryOutageChatTurn): CountryOutageChatBasis | null {
  const answer = answerFor(turn)
  return answer && 'basis' in answer ? answer.basis : null
}

function turnErrorFor(turn: CountryOutageChatTurn) {
  return 'error' in turn ? turn.error : null
}

function completedAtFor(turn: CountryOutageChatTurn): string | null {
  return 'completed_at' in turn ? turn.completed_at : null
}

function idempotency(prefix: string): string {
  const suffix = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replaceAll('-', '')
    : `${Date.now()}${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`.slice(0, 128)
}

function formatTime(value: string | null | undefined): string {
  if (!value) return ''
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

function statusLabel(value: CountryOutageChatTurn['state']): string {
  return ({
    executing: '正在分析',
    completed: '回答完成',
    clarification_required: '需要补充信息',
    stopped: '本轮已停止',
    failed: '没有形成回答',
    cancelled: '本轮已取消',
  } as const)[value]
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
  const snapshot = await getCountryOutageChatConversation(identity.conversationId)
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
    if (active) {
      startPolling({
        generation,
        conversationId: created.conversation.conversation_id,
      })
    }
  } catch (cause) {
    if (generation === conversationGeneration) error.value = errorMessage(cause)
  } finally {
    if (generation === conversationGeneration) loading.value = false
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
    if (requestId !== submitRequestId || !isCurrentConversationRequest(identity)) return
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
    activeTurnId.value = response.turn.state === 'executing'
      ? response.turn.turn_id
      : null
    if (activeTurnId.value) startPolling(identity)
    await scrollToLatest()
  } catch (cause) {
    if (requestId === submitRequestId && isCurrentConversationRequest(identity)) {
      error.value = errorMessage(cause)
      await refreshConversation(identity).catch(() => undefined)
    }
  } finally {
    if (requestId === submitRequestId && isCurrentConversationRequest(identity)) {
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
    await cancelCountryOutageChatTurn(identity.conversationId, turnId)
    if (requestId !== cancelRequestId || !isCurrentConversationRequest(identity)) return
    await refreshConversation(identity)
  } catch (cause) {
    if (requestId === cancelRequestId && isCurrentConversationRequest(identity)) {
      error.value = errorMessage(cause)
    }
  } finally {
    if (requestId === cancelRequestId && isCurrentConversationRequest(identity)) {
      cancelling.value = false
    }
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
    <header v-if="conversation" class="page-masthead">
      <div>
        <RouterLink :to="{ name: 'event-detail', query: { ref: reference } }">
          ← 返回事件观测
        </RouterLink>
        <p>RRC25 控制面观测</p>
        <h1>{{ countryName }}观测问答</h1>
      </div>
      <span class="scope-chip">
        {{ conversation.binding.is_final_in_data_range ? '当前数据范围完整' : '事件结束时间未知' }}
      </span>
    </header>

    <PageState
      v-if="loading"
      kind="loading"
      title="正在准备观测数据"
      detail="正在核对这次事件的数据版本与观测窗口。"
    />
    <PageState
      v-else-if="!conversation"
      kind="error"
      title="问答暂不可用"
      :detail="error"
      @retry="load"
    />

    <section v-else class="conversation-shell">
      <div ref="dialogue" class="dialogue-stream" aria-live="polite">
        <article class="welcome-note">
          <p class="eyebrow">当前可问</p>
          <h2>这段观测里，固定前缀可见 IPv4 地址量怎么变化？</h2>
          <p>回答会先给结论，再给关键数字与必要边界。</p>
          <button
            class="suggested-question"
            type="button"
            :disabled="sending || Boolean(activeTurnId)"
            @click="submit(contractQuestion)"
          >
            <span>{{ contractQuestion }}</span>
            <b>直接提问</b>
          </button>
          <small>目前支持这一类固定观测问题；其他目标会明确说明暂不支持。</small>
        </article>

        <article v-for="turn in turns" :key="turn.turn_id" class="turn-block">
          <div class="question-row">
            <span>{{ String(turn.turn_number).padStart(2, '0') }}</span>
            <p>{{ turn.question }}</p>
          </div>

          <div v-if="answerFor(turn)" class="answer-card">
            <header>
              <b>{{ statusLabel(turn.state) }}</b>
              <time>{{ formatTime(completedAtFor(turn)) }}</time>
            </header>
            <p class="answer-copy">{{ answerFor(turn)?.answer_text }}</p>

            <details v-if="basisFor(turn)" class="answer-basis">
              <summary>
                <span>查看依据</span>
                <small>来源、对象、窗口与边界</small>
              </summary>
              <dl>
                <div>
                  <dt>来源</dt>
                  <dd>{{ basisFor(turn)?.source_label_zh }}</dd>
                </div>
                <div>
                  <dt>观测对象</dt>
                  <dd>{{ basisFor(turn)?.observed_object_zh }}</dd>
                </div>
                <div class="window-basis">
                  <dt>观测窗口</dt>
                  <dd>
                    {{ formatTime(basisFor(turn)?.window_start_utc) }}
                    <span>至</span>
                    {{ formatTime(basisFor(turn)?.window_end_utc) }}
                  </dd>
                </div>
                <div class="boundary-basis">
                  <dt>重要边界</dt>
                  <dd>{{ basisFor(turn)?.important_boundary_zh }}</dd>
                </div>
              </dl>
            </details>
          </div>

          <div v-else-if="turnErrorFor(turn)" class="turn-error" role="status">
            <b>{{ statusLabel(turn.state) }}</b>
            <p>{{ turnErrorFor(turn)?.message }}</p>
            <button
              v-if="turnErrorFor(turn)?.retryable"
              type="button"
              @click="submit(turn.question)"
            >
              再试一次
            </button>
          </div>

          <div v-else class="turn-progress">
            <span aria-hidden="true"></span>
            <div>
              <b>正在分析这段观测</b>
              <p>读取并核对数据后再形成回答。</p>
            </div>
          </div>
        </article>
      </div>

      <form class="composer" @submit.prevent="submit()">
        <label for="agent-question">继续提问</label>
        <div>
          <textarea
            id="agent-question"
            ref="composer"
            v-model="draft"
            rows="2"
            maxlength="2000"
            :disabled="sending"
            :placeholder="activeTurnId ? '当前问题仍在分析，可先取消。' : '输入关于当前冻结观测的问题'"
            @keydown="onComposerKeydown"
          ></textarea>
          <button
            v-if="activeTurnId"
            type="button"
            class="cancel-button"
            :disabled="cancelling"
            @click="cancelActiveTurn"
          >
            {{ cancelling ? '正在取消' : '取消本轮' }}
          </button>
          <button v-else type="submit" :disabled="sending || !draft.trim()">
            {{ sending ? '提交中' : '发送' }}
          </button>
        </div>
        <p v-if="error" role="alert">{{ error }}</p>
        <small>Enter 发送 · Shift + Enter 换行</small>
      </form>
    </section>
  </main>
</template>

<style scoped>
.chat-page {
  --ink: #17313d;
  --ink-soft: #526873;
  --line: #d5dedf;
  --paper: #fbfaf6;
  --signal: #d8642f;
  min-height: calc(100vh - 92px);
  color: var(--ink);
  background:
    linear-gradient(rgba(21, 55, 68, .035) 1px, transparent 1px) 0 0 / 100% 32px,
    #e9eeed;
}
.page-masthead {
  position: sticky;
  z-index: 5;
  top: 66px;
  display: flex;
  gap: 24px;
  align-items: center;
  justify-content: space-between;
  padding: 19px max(24px, calc((100vw - 980px) / 2));
  color: #f2f6f4;
  background:
    linear-gradient(105deg, rgba(255, 255, 255, .035) 1px, transparent 1px) 0 0 / 29px 29px,
    #163846;
  border-bottom: 3px solid var(--signal);
  box-shadow: 0 10px 30px rgba(17, 46, 59, .16);
}
.page-masthead a {
  color: #9cc8d1;
  font-size: 11px;
  font-weight: 750;
  text-decoration: none;
}
.page-masthead p {
  margin: 8px 0 2px;
  color: #82b9c4;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: .12em;
}
.page-masthead h1 { margin: 0; font-size: 23px; letter-spacing: -.035em; }
.scope-chip {
  padding: 8px 11px;
  color: #ffd9c6;
  background: rgba(102, 47, 23, .34);
  border: 1px solid rgba(237, 139, 93, .4);
  font-size: 10px;
  font-weight: 750;
}
.conversation-shell {
  width: min(980px, calc(100% - 48px));
  min-height: calc(100vh - 185px);
  margin: 0 auto;
  background: var(--paper);
  border-inline: 1px solid rgba(46, 76, 87, .13);
  box-shadow: 0 18px 60px rgba(35, 58, 66, .08);
}
.dialogue-stream {
  height: calc(100vh - 292px);
  min-height: 470px;
  overflow-y: auto;
  padding: 44px clamp(22px, 7vw, 90px) 56px;
  scroll-behavior: smooth;
}
.welcome-note {
  margin-bottom: 44px;
  padding-bottom: 34px;
  border-bottom: 1px solid var(--line);
}
.eyebrow {
  margin: 0;
  color: var(--signal);
  font-size: 9px;
  font-weight: 850;
  letter-spacing: .16em;
}
.welcome-note h2 {
  max-width: 720px;
  margin: 10px 0 9px;
  font-family: 'Songti SC', 'STSong', serif;
  font-size: clamp(27px, 4vw, 43px);
  font-weight: 700;
  line-height: 1.16;
  letter-spacing: -.045em;
}
.welcome-note > p:not(.eyebrow) {
  margin: 0;
  color: var(--ink-soft);
  font-size: 12px;
  line-height: 1.8;
}
.suggested-question {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 20px;
  align-items: center;
  width: 100%;
  margin-top: 21px;
  padding: 16px 18px;
  cursor: pointer;
  color: var(--ink);
  background: #fff;
  border: 1px solid #cad6d8;
  border-left: 4px solid var(--signal);
  text-align: left;
  box-shadow: 0 8px 22px rgba(32, 57, 68, .06);
  transition: transform .18s ease, box-shadow .18s ease;
}
.suggested-question:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 13px 28px rgba(32, 57, 68, .1);
}
.suggested-question:disabled { cursor: not-allowed; opacity: .55; }
.suggested-question span { font-size: 12px; line-height: 1.7; }
.suggested-question b { color: #23667a; font-size: 10px; white-space: nowrap; }
.welcome-note > small {
  display: block;
  margin-top: 10px;
  color: #76878e;
  font-size: 10px;
}
.turn-block { margin: 0 0 38px; }
.question-row {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  margin-bottom: 13px;
}
.question-row > span {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  color: #fff;
  background: var(--ink);
  border-radius: 50%;
  font-size: 9px;
  font-weight: 800;
}
.question-row p {
  justify-self: start;
  margin: 0;
  padding: 11px 15px;
  color: #fff;
  background: var(--ink);
  border-radius: 2px 14px 14px 14px;
  font-size: 12px;
  line-height: 1.65;
}
.answer-card,
.turn-error,
.turn-progress {
  margin-left: 42px;
  background: #fff;
  border: 1px solid var(--line);
  box-shadow: 0 8px 24px rgba(28, 53, 62, .055);
}
.answer-card > header {
  display: flex;
  justify-content: space-between;
  padding: 10px 14px;
  color: #5c7079;
  background: #f1f5f4;
  border-bottom: 1px solid var(--line);
  font-size: 10px;
}
.answer-card > header b { color: #2b6d5d; }
.answer-card time { font-variant-numeric: tabular-nums; }
.answer-copy {
  margin: 0;
  padding: 24px clamp(18px, 4vw, 34px);
  font-family: 'Songti SC', 'STSong', serif;
  font-size: clamp(17px, 2vw, 20px);
  font-weight: 600;
  line-height: 1.85;
  white-space: pre-wrap;
}
.answer-basis {
  margin: 0;
  border-top: 1px solid var(--line);
}
.answer-basis summary {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 12px 15px;
  cursor: pointer;
  color: #3f5c67;
  background: #f7f8f5;
  font-size: 10px;
  font-weight: 800;
  list-style: none;
}
.answer-basis summary::-webkit-details-marker { display: none; }
.answer-basis summary::after { content: '＋'; color: var(--signal); font-size: 14px; }
.answer-basis[open] summary::after { content: '−'; }
.answer-basis summary small { margin-left: auto; color: #829097; font-size: 9px; font-weight: 500; }
.answer-basis dl {
  display: grid;
  grid-template-columns: 1fr 1.25fr;
  gap: 1px;
  margin: 0;
  padding: 1px;
  background: var(--line);
}
.answer-basis dl > div { padding: 13px 15px; background: #f7f8f5; }
.answer-basis dt {
  color: #849198;
  font-size: 8px;
  font-weight: 800;
  letter-spacing: .12em;
}
.answer-basis dd {
  margin: 6px 0 0;
  color: #425963;
  font-size: 11px;
  line-height: 1.6;
}
.window-basis,
.boundary-basis { grid-column: 1 / -1; }
.window-basis span { padding: 0 6px; color: #9a684e; }
.boundary-basis { border-left: 3px solid var(--signal); }
.turn-error { padding: 17px 19px; border-left: 3px solid #a94d37; }
.turn-error b { color: #873b2b; font-size: 11px; }
.turn-error p { margin: 7px 0 0; color: #5c6670; font-size: 12px; line-height: 1.6; }
.turn-error button {
  margin-top: 12px;
  padding: 7px 11px;
  cursor: pointer;
  color: #733321;
  background: #fff5ee;
  border: 1px solid #e0b59f;
  font-size: 10px;
  font-weight: 750;
}
.turn-progress { display: flex; gap: 13px; align-items: center; padding: 16px 18px; }
.turn-progress > span {
  width: 12px;
  height: 12px;
  border: 2px solid #bdd0d3;
  border-top-color: var(--signal);
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
.turn-progress b { font-size: 11px; }
.turn-progress p { margin: 4px 0 0; color: #73848b; font-size: 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.composer {
  position: sticky;
  bottom: 0;
  padding: 14px clamp(22px, 7vw, 90px) 16px;
  background: rgba(251, 250, 246, .96);
  border-top: 1px solid var(--line);
  backdrop-filter: blur(12px);
}
.composer label {
  display: block;
  margin-bottom: 7px;
  color: #64767d;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: .08em;
}
.composer > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; }
.composer textarea {
  min-height: 54px;
  resize: none;
  padding: 12px 14px;
  color: var(--ink);
  background: #fff;
  border: 1px solid #bdcccf;
  border-right: 0;
  border-radius: 0;
  outline: none;
  font: inherit;
  font-size: 12px;
  line-height: 1.55;
}
.composer textarea:focus { border-color: #4b8797; box-shadow: inset 3px 0 var(--signal); }
.composer button {
  min-width: 88px;
  cursor: pointer;
  color: #fff;
  background: #1d5567;
  border: 0;
  font-size: 11px;
  font-weight: 800;
}
.composer button:disabled { cursor: not-allowed; opacity: .55; }
.composer .cancel-button { background: #8b4632; }
.composer > p { margin: 8px 0 0; color: #9b3d2d; font-size: 11px; }
.composer > small { display: block; margin-top: 7px; color: #89969b; font-size: 9px; }

@media (max-width: 760px) {
  .page-masthead { position: static; padding: 16px 18px; }
  .page-masthead h1 { font-size: 20px; }
  .scope-chip { max-width: 130px; text-align: right; }
  .conversation-shell { width: 100%; min-height: calc(100vh - 154px); border: 0; }
  .dialogue-stream { height: calc(100vh - 258px); padding: 28px 16px 42px; }
  .welcome-note { margin-bottom: 32px; padding-bottom: 27px; }
  .welcome-note h2 { font-size: 28px; }
  .suggested-question { grid-template-columns: 1fr; gap: 9px; }
  .question-row { grid-template-columns: 24px minmax(0, 1fr); gap: 8px; }
  .question-row > span { width: 24px; height: 24px; }
  .answer-card,
  .turn-error,
  .turn-progress { margin-left: 32px; }
  .answer-card > header { align-items: flex-start; }
  .answer-copy { padding: 20px 17px; font-size: 17px; }
  .answer-basis dl { grid-template-columns: 1fr; }
  .answer-basis dl > div { grid-column: auto; }
  .composer { padding: 12px 16px 14px; }
  .composer button { min-width: 72px; }
}

@media (prefers-reduced-motion: reduce) {
  .suggested-question { transition: none; }
  .turn-progress > span { animation-duration: 1.6s; }
}
</style>
