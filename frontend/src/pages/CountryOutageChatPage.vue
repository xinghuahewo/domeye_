<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  countryOutageChatApi,
  createP1IdempotencyKey,
  type P1Answerability,
  type P1ChatConversation,
  type P1ChatEvent,
  type P1ChatTurn,
} from '@/api/countryOutageChat'
import PageState from '@/components/PageState.vue'
import { errorMessage } from '@/utils/normalize'

defineOptions({ name: 'CountryOutageChatPage' })

const route = useRoute()
const reference = computed(() => textQuery('ref'))
const publicationId = computed(() => textQuery('publication_id'))
const revision = computed(() => Number(textQuery('revision')))
const conversation = ref<P1ChatConversation | null>(null)
const question = ref('')
const loading = ref(true)
const sending = ref(false)
const cancelling = ref(false)
const error = ref('')
const recoveryNotice = ref('')
const connection = ref<'connecting' | 'connected' | 'retrying'>('connecting')
const activeTurnId = ref('')
const liveStatus = ref('正在建立事件绑定会话')
const composer = ref<HTMLTextAreaElement | null>(null)
let subscription: { close(): void } | null = null

const suggestions = [
  '这次伊朗事件发生了什么？',
  '哪个时点的中断前缀最多？',
  '数据截止时还剩多少路由不可见？',
  '页面列出的前五个受影响 AS 是哪些？',
  'IPv4 和 IPv6 的可见地址变化有什么不同？',
  '仅凭这页 RRC25 数据能证明什么、不能证明什么？',
]

const statusLabel: Record<P1Answerability, string> = {
  answerable: '已回答',
  partial: '部分回答',
  clarify: '需要澄清',
  unsupported: '当前不支持',
  invalid_data: '数据无效',
}

function textQuery(key: string): string {
  const value = route.query[key]
  return typeof value === 'string' ? value : ''
}

function localTime(value: string | null | undefined): string {
  if (!value) return '未知'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

function shortIdentity(value: string): string {
  if (value.length < 22) return value
  return `${value.slice(0, 12)}…${value.slice(-8)}`
}

const storageKey = computed(() => [
  'domeye-p1-chat', reference.value, publicationId.value, revision.value,
].join(':'))

const backLink = computed(() => ({
  name: 'event-detail',
  query: { ref: reference.value },
}))

const contextChips = computed(() => {
  const state = conversation.value?.state
  if (!state) return []
  return [
    state.topic ? `主题 · ${state.topic}` : '',
    state.asn !== null ? `对象 · AS${state.asn}` : '',
    state.address_family ? `地址族 · ${state.address_family.toUpperCase()}` : '',
    state.metric ? `指标 · ${state.metric}` : '',
  ].filter(Boolean)
})

function mergeTurn(next: P1ChatTurn) {
  if (!conversation.value) return
  const index = conversation.value.turns.findIndex((turn) => turn.turn_id === next.turn_id)
  if (index < 0) conversation.value.turns.push(next)
  else conversation.value.turns[index] = next
  if (next.answer?.validation.passed) {
    const transition = next.answer.transition
    const state = conversation.value.state as unknown as Record<string, unknown>
    for (const key of transition.clear) state[key] = null
    for (const [key, value] of Object.entries(transition.set)) state[key] = value
    conversation.value.state.last_committed_turn_number = next.turn_number
  }
}

function receiveEvent(event: P1ChatEvent) {
  if (event.turn_id && event.state && sending.value) {
    activeTurnId.value = event.turn_id
    const labels: Partial<Record<P1ChatTurn['state'], string>> = {
      queued: '问题已排队',
      understanding: '正在理解问题并锁定上下文',
      reading_facts: '正在读取同一 publication 的事实',
      validating: '正在校验事实、引用与边界',
      completed: '回答完成',
      failed: '回答失败',
      cancelled: '本轮已取消',
    }
    liveStatus.value = labels[event.state] || '正在处理'
  }
  if (event.answer) {
    mergeTurn({
      turn_id: event.answer.turn_id,
      turn_number: event.answer.turn_number,
      question: conversation.value?.turns.find((turn) => turn.turn_id === event.answer?.turn_id)?.question || question.value,
      state: event.state || 'completed',
      answer: event.answer,
      created_at: event.emitted_at,
      completed_at: event.answer.completed_at,
    })
  }
}

function connectEvents() {
  subscription?.close()
  if (!conversation.value) return
  connection.value = 'connecting'
  subscription = countryOutageChatApi.subscribe(conversation.value.conversation_id, {
    onEvent: receiveEvent,
    onConnectionChange: (state) => { connection.value = state },
    onProtocolError: (message) => { error.value = message },
  })
}

async function createConversation() {
  const response = await countryOutageChatApi.createConversation({
    event_reference: reference.value,
    publication_id: publicationId.value,
    revision: revision.value,
    idempotency_key: createP1IdempotencyKey('conversation'),
  })
  conversation.value = response.conversation
  localStorage.setItem(storageKey.value, response.conversation.conversation_id)
}

async function load() {
  loading.value = true
  error.value = ''
  recoveryNotice.value = ''
  if (
    !/^country_outage\//.test(reference.value)
    || !publicationId.value
    || !Number.isSafeInteger(revision.value)
    || revision.value < 1
  ) {
    error.value = '缺少合法事件、publication 或 revision，不能建立 P1 事件绑定会话。'
    loading.value = false
    return
  }
  try {
    const savedId = localStorage.getItem(storageKey.value)
    if (savedId) {
      try {
        conversation.value = await countryOutageChatApi.getConversation(savedId)
      } catch {
        localStorage.removeItem(storageKey.value)
        recoveryNotice.value = '上一短期会话已到期或因服务重启不可恢复；已按同一事件身份创建新会话，旧回答不会被静默改写。'
      }
    }
    if (!conversation.value) await createConversation()
    connectEvents()
    liveStatus.value = '事件绑定会话已就绪'
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

async function restart() {
  subscription?.close()
  if (conversation.value) localStorage.removeItem(storageKey.value)
  conversation.value = null
  await load()
}

async function submit(value = question.value) {
  const normalized = value.trim()
  if (!conversation.value || !normalized || sending.value) return
  question.value = ''
  sending.value = true
  activeTurnId.value = ''
  error.value = ''
  liveStatus.value = '正在提交问题'
  const placeholder: P1ChatTurn = {
    turn_id: `pending-${Date.now()}`,
    turn_number: conversation.value.turns.length + 1,
    question: normalized,
    state: 'queued',
    created_at: new Date().toISOString(),
  }
  conversation.value.turns.push(placeholder)
  try {
    const response = await countryOutageChatApi.createTurn(
      conversation.value.conversation_id,
      { question: normalized, idempotency_key: createP1IdempotencyKey('turn') },
    )
    conversation.value.turns = conversation.value.turns.filter((turn) => turn.turn_id !== placeholder.turn_id)
    mergeTurn(response.turn)
    liveStatus.value = response.turn.state === 'completed' ? '回答完成' : `本轮状态：${response.turn.state}`
  } catch (cause) {
    conversation.value.turns = conversation.value.turns.filter((turn) => turn.turn_id !== placeholder.turn_id)
    error.value = errorMessage(cause)
    liveStatus.value = '本轮请求失败'
  } finally {
    sending.value = false
    activeTurnId.value = ''
    await nextTick()
    composer.value?.focus()
  }
}

async function cancel() {
  if (!conversation.value || !activeTurnId.value || cancelling.value) return
  cancelling.value = true
  try {
    await countryOutageChatApi.cancelTurn(conversation.value.conversation_id, activeTurnId.value)
    liveStatus.value = '取消请求已确认，本轮不会提交新上下文'
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    cancelling.value = false
  }
}

function onComposerKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void submit()
  }
}

onMounted(() => { void load() })
onBeforeUnmount(() => subscription?.close())
</script>

<template>
  <main class="chat-page">
    <PageState
      v-if="loading"
      kind="loading"
      title="正在绑定事件证据"
      detail="锁定 incident、publication、revision 与 RRC25 观测窗口"
    />

    <section v-else-if="error && !conversation" class="fatal-panel">
      <p>EVENT BINDING FAILED</p>
      <h1>无法进入事件问答</h1>
      <strong>{{ error }}</strong>
      <div>
        <RouterLink :to="backLink">← 返回事件证据页</RouterLink>
        <button type="button" @click="load">重新绑定</button>
      </div>
    </section>

    <template v-else-if="conversation">
      <header class="identity-rail">
        <div class="identity-title">
          <RouterLink :to="backLink">← 返回事件证据</RouterLink>
          <p>RRC25 · EVENT-BOUND Q&amp;A</p>
          <h1>伊朗事件问答台</h1>
          <span>只回答当前页面/API 的确定性事实</span>
        </div>
        <dl>
          <div class="wide"><dt>PUBLICATION</dt><dd :title="conversation.binding.publication_id">{{ shortIdentity(conversation.binding.publication_id) }}</dd></div>
          <div><dt>REVISION</dt><dd>R{{ conversation.binding.revision }}</dd></div>
          <div><dt>COLLECTOR</dt><dd>{{ conversation.binding.collector_id.toUpperCase() }}</dd></div>
          <div><dt>DATA THROUGH</dt><dd>{{ localTime(conversation.binding.data_through) }}</dd></div>
          <div><dt>FINALITY</dt><dd>{{ conversation.binding.is_final_in_data_range ? 'FINAL' : 'END UNKNOWN' }}</dd></div>
        </dl>
      </header>

      <div class="workspace">
        <aside class="context-panel" aria-label="当前对话上下文">
          <div class="panel-heading">
            <span>01</span>
            <div><small>BOUND CONTEXT</small><h2>当前上下文</h2></div>
          </div>
          <p class="context-note">上下文只继承已校验的结构化状态，不把原始聊天历史当作事实。</p>
          <div class="context-list">
            <span v-if="contextChips.length === 0">尚未选择子主题</span>
            <b v-for="chip in contextChips" :key="chip">{{ chip }}</b>
          </div>
          <dl class="session-facts">
            <div><dt>会话</dt><dd :title="conversation.conversation_id">{{ shortIdentity(conversation.conversation_id) }}</dd></div>
            <div><dt>已提交轮次</dt><dd>{{ conversation.state.last_committed_turn_number }}</dd></div>
            <div><dt>到期时间</dt><dd>{{ localTime(conversation.expires_at) }}</dd></div>
            <div><dt>状态流</dt><dd :class="`is-${connection}`">{{ connection }}</dd></div>
          </dl>
          <button class="restart" type="button" @click="restart">以当前事件新建会话</button>
          <p class="boundary">不接入 OONI / IODA / Cloudflare，不判断真实用户影响、责任或原因。</p>
        </aside>

        <section class="conversation-panel" aria-label="事件问答消息">
          <div class="conversation-head">
            <div class="panel-heading">
              <span>02</span>
              <div><small>VERIFIED DIALOGUE</small><h2>证据对话</h2></div>
            </div>
            <i aria-hidden="true"></i>
            <b>{{ conversation.turns.length }} TURNS</b>
          </div>

          <p v-if="recoveryNotice" class="recovery-notice" role="status">
            <b>会话恢复说明</b>{{ recoveryNotice }}
          </p>

          <div v-if="conversation.turns.length === 0" class="empty-state">
            <span>NO TURNS YET</span>
            <h2>从当前事件已有事实开始</h2>
            <p>每轮都会显示回答等级、证据字段、限制和本轮状态变化。</p>
            <div>
              <button v-for="item in suggestions" :key="item" type="button" @click="submit(item)">{{ item }}</button>
            </div>
          </div>

          <ol v-else class="turn-list">
            <li v-for="turn in conversation.turns" :key="turn.turn_id" class="turn">
              <article class="user-message">
                <span>YOU · {{ String(turn.turn_number).padStart(2, '0') }}</span>
                <p>{{ turn.question }}</p>
              </article>

              <article v-if="turn.answer" :class="['agent-answer', `is-${turn.answer.answerability}`]">
                <header>
                  <div>
                    <span>RRC25 FACT SERVICE</span>
                    <h3>{{ statusLabel[turn.answer.answerability] }}</h3>
                  </div>
                  <time>{{ localTime(turn.answer.completed_at) }}</time>
                </header>

                <section v-for="result in turn.answer.results" :key="result.subrequest_id" class="subanswer">
                  <b>{{ statusLabel[result.answerability] }} · {{ result.intents.join(' + ') }}</b>
                  <p>{{ result.text }}</p>
                </section>

                <div v-if="turn.answer.limitations.length || turn.answer.unknowns.length" class="limits">
                  <section v-if="turn.answer.limitations.length">
                    <h4>限制</h4>
                    <ul><li v-for="item in turn.answer.limitations" :key="item">{{ item }}</li></ul>
                  </section>
                  <section v-if="turn.answer.unknowns.length">
                    <h4>未知</h4>
                    <ul><li v-for="item in turn.answer.unknowns" :key="item">{{ item }}</li></ul>
                  </section>
                </div>

                <details v-if="turn.answer.evidence.length" class="evidence-drawer">
                  <summary>查看 {{ turn.answer.evidence.length }} 条字段级证据</summary>
                  <ol>
                    <li v-for="item in turn.answer.evidence" :key="item.evidence_ref">
                      <code>{{ item.evidence_ref }}</code>
                      <span>{{ item.label }}</span>
                      <b>{{ item.value === null ? 'NULL / 未知' : item.value }} {{ item.unit || '' }}</b>
                    </li>
                  </ol>
                </details>

                <footer>
                  <span>R{{ turn.answer.binding.revision }} · {{ turn.answer.binding.collector_id.toUpperCase() }}</span>
                  <span>{{ turn.answer.validation.passed ? '✓ FACTS VALIDATED' : '× FAILED CLOSED' }}</span>
                  <span v-if="turn.answer.transition.reason_codes.length">{{ turn.answer.transition.reason_codes.join(' · ') }}</span>
                </footer>
              </article>

              <article v-else :class="['processing-card', `is-${turn.state}`]">
                <span class="pulse" aria-hidden="true"></span>
                <div><b>{{ liveStatus }}</b><small>不会在校验完成前发布答案或更新上下文</small></div>
              </article>
            </li>
          </ol>

          <p v-if="error" class="inline-error" role="alert">{{ error }}</p>

          <form class="composer" @submit.prevent="submit()">
            <div class="composer-meta">
              <span>03 · ASK WITHIN THIS PUBLICATION</span>
              <span aria-live="polite">{{ liveStatus }}</span>
            </div>
            <label>
              <span class="sr-only">围绕当前事件提问</span>
              <textarea
                ref="composer"
                v-model="question"
                maxlength="2000"
                rows="3"
                :disabled="sending"
                placeholder="例如：峰值之后还有多少前缀持续异常？"
                @keydown="onComposerKeydown"
              ></textarea>
            </label>
            <div class="composer-actions">
              <small>Enter 发送 · Shift+Enter 换行 · 仅当前事件事实</small>
              <button v-if="sending" type="button" :disabled="!activeTurnId || cancelling" class="cancel" @click="cancel">
                {{ cancelling ? '正在取消…' : '取消本轮' }}
              </button>
              <button v-else type="submit" :disabled="!question.trim()">发送问题 <span>↗</span></button>
            </div>
          </form>
        </section>
      </div>
    </template>
  </main>
</template>

<style scoped>
.chat-page { --ink: #102b3b; --ink-2: #173e50; --signal: #e46f2e; --paper: #f4f0e7; --line: #cfd7da; --muted: #697982; min-height: calc(100vh - 62px); padding: 22px; color: #172832; background: #e7ecec; font-family: "IBM Plex Sans Condensed", "Noto Sans SC", sans-serif; }
.identity-rail { display: grid; grid-template-columns: minmax(300px, .8fr) minmax(560px, 1.2fr); min-height: 190px; color: #f8fbfb; background: var(--ink); border-bottom: 5px solid var(--signal); box-shadow: 0 14px 36px rgba(16,43,59,.18); }
.identity-title { padding: 25px 30px; background: linear-gradient(110deg, rgba(255,255,255,.03), transparent 70%); }
.identity-title > a { color: #9bd0dc; font-size: 11px; font-weight: 750; text-decoration: none; }
.identity-title p { margin: 29px 0 6px; color: #ef9a62; font: 800 9px/1.1 var(--mono); letter-spacing: .12em; }
.identity-title h1 { margin: 0; font-size: clamp(30px, 4vw, 46px); letter-spacing: -.045em; }
.identity-title > span { display: block; margin-top: 8px; color: #aec3cc; font-size: 11px; }
.identity-rail dl { display: grid; grid-template-columns: 2fr repeat(2, 1fr); gap: 1px; align-self: stretch; margin: 0; background: rgba(255,255,255,.12); }
.identity-rail dl div { display: grid; align-content: center; padding: 18px; background: #173646; }
.identity-rail dl .wide { grid-row: span 2; }
.identity-rail dt { color: #83a5b3; font: 800 8px/1 var(--mono); letter-spacing: .08em; }
.identity-rail dd { overflow-wrap: anywhere; margin: 8px 0 0; color: #f4f8f9; font: 750 12px/1.45 var(--mono); }
.workspace { display: grid; grid-template-columns: 285px minmax(0, 1fr); max-width: 1500px; margin: 0 auto; background: #f8faf9; border: 1px solid #cfd7da; border-top: 0; }
.context-panel { min-width: 0; padding: 24px 20px; background: var(--paper); border-right: 1px solid #d5d1c8; }
.panel-heading { display: flex; align-items: center; gap: 12px; }
.panel-heading > span { color: var(--signal); font: 850 19px/1 var(--mono); }
.panel-heading small { color: #857a6f; font: 800 8px/1 var(--mono); letter-spacing: .09em; }
.panel-heading h2 { margin: 3px 0 0; color: var(--ink); font-size: 17px; }
.context-note { margin: 20px 0; color: #68757a; font-size: 10px; line-height: 1.65; }
.context-list { display: grid; gap: 6px; padding: 12px 0; border-top: 1px solid #d5d1c8; border-bottom: 1px solid #d5d1c8; }
.context-list span { color: #8c8a83; font-size: 10px; }
.context-list b { padding: 7px 9px; color: #214354; background: #fff; border-left: 3px solid #2f7c93; font: 750 10px/1.25 var(--mono); }
.session-facts { display: grid; gap: 0; margin: 18px 0; }
.session-facts div { display: grid; grid-template-columns: 78px 1fr; padding: 9px 0; border-bottom: 1px solid #ddd8ce; }
.session-facts dt { color: #8a8178; font-size: 9px; }
.session-facts dd { overflow-wrap: anywhere; margin: 0; color: #344b56; font: 700 9px/1.4 var(--mono); }
.session-facts dd.is-connected { color: #23765c; }
.session-facts dd.is-retrying { color: #a85128; }
.restart { width: 100%; min-height: 38px; cursor: pointer; color: var(--ink); background: transparent; border: 1px solid #9aa7aa; font-size: 10px; font-weight: 800; }
.restart:hover { background: #fff; }
.boundary { margin: 14px 0 0; padding-left: 9px; color: #87573b; border-left: 3px solid var(--signal); font-size: 9px; line-height: 1.6; }
.conversation-panel { display: flex; flex-direction: column; min-width: 0; min-height: 720px; background: #fbfcfb; }
.conversation-head { display: flex; align-items: center; gap: 15px; padding: 21px 25px; border-bottom: 1px solid var(--line); }
.conversation-head i { flex: 1; height: 1px; background: var(--line); }
.conversation-head > b { color: #819097; font: 800 8px/1 var(--mono); letter-spacing: .08em; }
.recovery-notice { margin: 12px 24px 0; padding: 10px 12px; color: #6f4a31; background: #fff3e7; border-left: 4px solid var(--signal); font-size: 10px; line-height: 1.55; }
.recovery-notice b { margin-right: 8px; color: #8b4823; }
.empty-state { display: grid; flex: 1; align-content: center; justify-items: center; min-height: 300px; padding: 45px 30px; text-align: center; background-image: radial-gradient(#dce3e3 1px, transparent 1px); background-size: 22px 22px; }
.empty-state > span { color: var(--signal); font: 800 9px/1 var(--mono); letter-spacing: .12em; }
.empty-state h2 { margin: 9px 0 5px; color: var(--ink); font-size: 27px; }
.empty-state p { margin: 0; color: var(--muted); font-size: 11px; }
.empty-state > div { display: grid; grid-template-columns: repeat(2, minmax(250px, 1fr)); gap: 8px; width: min(760px, 100%); margin-top: 28px; }
.empty-state button { padding: 11px 13px; cursor: pointer; color: #29434f; background: rgba(255,255,255,.94); border: 1px solid #cbd5d7; text-align: left; font-size: 10px; }
.empty-state button:hover { color: #fff; background: var(--ink-2); border-color: var(--ink-2); }
.turn-list { display: grid; flex: 1; gap: 30px; align-content: start; overflow: auto; max-height: 720px; margin: 0; padding: 28px 30px 42px; list-style: none; }
.turn { display: grid; gap: 12px; }
.user-message { justify-self: end; width: min(700px, 86%); padding: 13px 16px; color: #f8fbfb; background: var(--ink-2); border-radius: 3px 3px 0 3px; }
.user-message span { color: #94c4d1; font: 800 8px/1 var(--mono); letter-spacing: .08em; }
.user-message p { margin: 6px 0 0; font-size: 12px; line-height: 1.6; }
.agent-answer { width: min(920px, 94%); overflow: hidden; background: #fff; border: 1px solid var(--line); border-left: 5px solid #277b68; box-shadow: 0 7px 19px rgba(21,48,60,.06); }
.agent-answer.is-partial { border-left-color: #d58832; }
.agent-answer.is-clarify { border-left-color: #477f9a; }
.agent-answer.is-unsupported { border-left-color: #8a6572; }
.agent-answer.is-invalid_data { border-left-color: #a13c35; }
.agent-answer > header { display: flex; justify-content: space-between; gap: 20px; align-items: center; padding: 13px 17px; background: #f1f5f4; border-bottom: 1px solid #d9e0df; }
.agent-answer > header span { color: #74868c; font: 800 8px/1 var(--mono); letter-spacing: .08em; }
.agent-answer > header h3 { margin: 4px 0 0; color: var(--ink); font-size: 14px; }
.agent-answer time { color: #75858b; font: 700 8px/1 var(--mono); }
.subanswer { padding: 17px 19px; }
.subanswer + .subanswer { border-top: 1px dashed #d8dfe0; }
.subanswer > b { color: #27705f; font: 800 9px/1 var(--mono); }
.subanswer p { margin: 8px 0 0; color: #33454e; font-size: 12px; line-height: 1.75; }
.limits { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 0 18px 16px; background: #dfd7ca; border: 1px solid #dfd7ca; }
.limits section { padding: 11px 13px; background: #fbf6ed; }
.limits h4 { margin: 0; color: #895634; font-size: 9px; }
.limits ul { margin: 6px 0 0; padding-left: 16px; color: #6d5c50; font-size: 9px; line-height: 1.6; }
.evidence-drawer { margin: 0 18px 16px; border: 1px solid #d4dddf; }
.evidence-drawer summary { padding: 10px 12px; cursor: pointer; color: #23677f; background: #f3f7f7; font-size: 9px; font-weight: 800; }
.evidence-drawer ol { display: grid; margin: 0; padding: 0; list-style: none; }
.evidence-drawer li { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(120px, .6fr) minmax(140px, .7fr); gap: 10px; padding: 9px 11px; border-top: 1px solid #e1e7e8; }
.evidence-drawer code { overflow-wrap: anywhere; color: #2b677c; font: 8px/1.45 var(--mono); }
.evidence-drawer li span { color: #78868b; font-size: 9px; }
.evidence-drawer li b { overflow-wrap: anywhere; color: #2b3f48; font: 750 9px/1.4 var(--mono); }
.agent-answer > footer { display: flex; flex-wrap: wrap; gap: 14px; padding: 10px 17px; color: #6e8087; background: #f5f7f6; border-top: 1px solid #dce2e1; font: 800 7px/1 var(--mono); letter-spacing: .06em; }
.processing-card { display: flex; width: min(620px, 88%); align-items: center; gap: 12px; padding: 14px 16px; color: #39525e; background: #edf3f3; border: 1px solid #d4dfe1; }
.pulse { width: 10px; height: 10px; background: var(--signal); border-radius: 50%; box-shadow: 0 0 0 0 rgba(228,111,46,.4); animation: pulse 1.4s infinite; }
.processing-card div { display: grid; gap: 4px; }
.processing-card b { font-size: 10px; }
.processing-card small { color: #75868d; font-size: 9px; }
.inline-error { margin: 0 24px 12px; padding: 10px 12px; color: #8f302b; background: #fff0ed; border-left: 4px solid #a13c35; font-size: 10px; }
.composer { position: sticky; bottom: 0; z-index: 2; margin: 0 24px 24px; padding: 12px; background: var(--ink); box-shadow: 0 -8px 28px rgba(16,43,59,.12); }
.composer-meta { display: flex; justify-content: space-between; gap: 20px; padding: 0 2px 9px; color: #91b0bc; font: 800 8px/1 var(--mono); letter-spacing: .07em; }
.composer label { display: block; }
.composer textarea { display: block; width: 100%; min-height: 76px; resize: vertical; padding: 13px 14px; color: #172d38; background: #fff; border: 0; border-radius: 0; font: 12px/1.6 "IBM Plex Sans Condensed", "Noto Sans SC", sans-serif; box-sizing: border-box; }
.composer textarea:focus { outline: 3px solid #f2a06a; outline-offset: -3px; }
.composer-actions { display: flex; justify-content: space-between; align-items: center; gap: 15px; padding-top: 9px; }
.composer-actions small { color: #8fa9b3; font-size: 8px; }
.composer-actions button { min-width: 130px; min-height: 36px; cursor: pointer; color: var(--ink); background: #f09a60; border: 0; font-size: 10px; font-weight: 850; }
.composer-actions button.cancel { color: #fff; background: #94453d; }
.composer-actions button:disabled { cursor: not-allowed; opacity: .45; }
.fatal-panel { max-width: 760px; margin: 12vh auto 0; padding: 38px; color: #f8fbfb; background: var(--ink); border-bottom: 5px solid #a13c35; }
.fatal-panel > p { color: #ef9a62; font: 800 9px/1 var(--mono); }
.fatal-panel h1 { margin: 8px 0; font-size: 34px; }
.fatal-panel strong { color: #f4d2c5; font-size: 12px; }
.fatal-panel div { display: flex; gap: 10px; margin-top: 25px; }
.fatal-panel a, .fatal-panel button { padding: 10px 12px; color: #fff; background: transparent; border: 1px solid #8aa5b0; font-size: 10px; text-decoration: none; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; }
@keyframes pulse { 70% { box-shadow: 0 0 0 8px rgba(228,111,46,0); } 100% { box-shadow: 0 0 0 0 rgba(228,111,46,0); } }
@media (prefers-reduced-motion: reduce) { .pulse { animation: none; } }
@media (max-width: 980px) {
  .identity-rail { grid-template-columns: 1fr; }
  .identity-rail dl { grid-template-columns: repeat(2, 1fr); }
  .identity-rail dl .wide { grid-row: auto; grid-column: span 2; }
  .workspace { grid-template-columns: 1fr; }
  .context-panel { border-right: 0; border-bottom: 1px solid #d5d1c8; }
  .context-panel .session-facts { grid-template-columns: repeat(2, 1fr); gap: 0 14px; }
  .conversation-panel { min-height: 600px; }
}
@media (max-width: 640px) {
  .chat-page { padding: 0; }
  .identity-title { padding: 21px 18px; }
  .identity-rail dl { grid-template-columns: 1fr; }
  .identity-rail dl .wide { grid-column: auto; }
  .context-panel { padding: 19px 16px; }
  .context-panel .session-facts { grid-template-columns: 1fr; }
  .conversation-head { padding: 17px 16px; }
  .turn-list { max-height: none; padding: 20px 13px 32px; }
  .user-message, .agent-answer, .processing-card { width: 100%; box-sizing: border-box; }
  .empty-state { padding: 35px 16px; }
  .empty-state > div, .limits { grid-template-columns: 1fr; }
  .evidence-drawer li { grid-template-columns: 1fr; }
  .composer { margin: 0; }
  .composer-meta span:first-child, .composer-actions small { display: none; }
  .composer-actions { justify-content: flex-end; }
}
</style>
