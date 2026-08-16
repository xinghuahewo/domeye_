<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import {
  countryOutageChatApi,
  createP1IdempotencyKey,
  type P1RuntimeV2Conversation,
  type P1RuntimeV2ConversationTurn,
  type P1RuntimeV2SingleTurnAnswer,
  type P1SemanticAnswerability,
} from '@/api/countryOutageChat'
import PageState from '@/components/PageState.vue'
import { errorMessage } from '@/utils/normalize'

defineOptions({ name: 'CountryOutageChatPage' })

const route = useRoute()
const router = useRouter()
const reference = computed(() => textQuery('ref'))
const publicationId = computed(() => textQuery('publication_id'))
const revision = computed(() => Number(textQuery('revision')))
const conversation = ref<P1RuntimeV2Conversation | null>(null)
const question = ref('')
const loading = ref(true)
const sending = ref(false)
const cancelling = ref(false)
const error = ref('')
const recoveryNotice = ref('')
const connection = ref<'connecting' | 'connected' | 'retrying'>('connecting')
const activeTurnId = ref('')
const liveStatus = ref('正在建立事件绑定会话')
const rebindOpen = ref(false)
const rebindReference = ref('')
const rebindPublication = ref('')
const rebindRevision = ref(1)
const rebinding = ref(false)
const rebindError = ref('')
const runtimeSummary = ref<P1RuntimeV2SingleTurnAnswer | null>(null)
const runtimeSummaryLoading = ref(false)
const runtimeSummaryError = ref('')
const composer = ref<HTMLTextAreaElement | null>(null)
let runtimeSummaryController: AbortController | null = null
let conversationTurnController: AbortController | null = null

const suggestions = [
  '这次伊朗事件发生了什么？',
  '哪个时点的中断前缀最多？',
  '数据截止时还剩多少路由不可见？',
  '页面列出的前五个受影响 AS 是哪些？',
  'IPv4 和 IPv6 的可见地址变化有什么不同？',
  '仅凭这页 RRC25 数据能证明什么、不能证明什么？',
]

const statusLabel: Record<P1SemanticAnswerability, string> = {
  supported: '已回答',
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
  const state = conversation.value?.dialog_state
  if (!state) return []
  return [
    state.topic ? `主题 · ${state.topic}` : '',
    state.asn !== null ? `对象 · AS${state.asn}` : '',
    state.address_family ? `地址族 · ${state.address_family.toUpperCase()}` : '',
    state.metric ? `指标 · ${state.metric}` : '',
  ].filter(Boolean)
})

function mergeRuntimeTurn(next: P1RuntimeV2ConversationTurn, temporaryId?: string) {
  if (!conversation.value) return
  const index = conversation.value.turns.findIndex((turn) =>
    turn.turn_id === next.turn_id || turn.turn_id === temporaryId)
  if (index < 0) conversation.value.turns.push(next)
  else conversation.value.turns[index] = next
  if (next.answer?.state_receipt.status === 'committed') {
    conversation.value.dialog_state = next.answer.state_receipt.after
  }
  if (next.answer?.state_receipt.proposed.clear.includes('event_binding')) {
    conversation.value.active_binding_generation = null
  }
}

async function createConversation() {
  const response = await countryOutageChatApi.createRuntimeV2Conversation({
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
        conversation.value = await countryOutageChatApi.getRuntimeV2Conversation(savedId)
      } catch {
        localStorage.removeItem(storageKey.value)
        recoveryNotice.value = '上一短期会话已到期或因服务重启不可恢复；已按同一事件身份创建新会话，旧回答不会被静默改写。'
      }
    }
    if (!conversation.value) await createConversation()
    connection.value = 'connected'
    liveStatus.value = '事件绑定会话已就绪'
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

async function restart() {
  if (conversation.value) localStorage.removeItem(storageKey.value)
  conversation.value = null
  await load()
}

async function runControlledEventSummary() {
  if (!conversation.value || runtimeSummaryLoading.value) return
  runtimeSummaryController?.abort()
  runtimeSummaryController = new AbortController()
  runtimeSummary.value = null
  runtimeSummaryError.value = ''
  runtimeSummaryLoading.value = true
  try {
    runtimeSummary.value = await countryOutageChatApi.createRuntimeV2SingleTurn(
      {
        event_reference: conversation.value.binding.legacy_reference,
        publication_id: conversation.value.binding.publication_id,
        revision: conversation.value.binding.revision,
        controlled_goal: 'event_summary',
      },
      runtimeSummaryController.signal,
    )
  } catch (cause) {
    runtimeSummaryError.value = runtimeSummaryController.signal.aborted
      ? '本次确定性读取已取消，未发布回答或提交状态。'
      : errorMessage(cause)
  } finally {
    runtimeSummaryLoading.value = false
    runtimeSummaryController = null
  }
}

function cancelControlledEventSummary() {
  runtimeSummaryController?.abort()
}

async function submit(value = question.value) {
  const normalized = value.trim()
  if (!conversation.value || !normalized || sending.value) return
  question.value = ''
  sending.value = true
  activeTurnId.value = ''
  error.value = ''
  liveStatus.value = '正在生成开放 UserGoalPlan'
  conversationTurnController?.abort()
  conversationTurnController = new AbortController()
  const placeholder: P1RuntimeV2ConversationTurn = {
    turn_id: `pending-${Date.now()}`,
    turn_number: conversation.value.turns.length + 1,
    question: normalized,
    state: 'understanding',
    created_at: new Date().toISOString(),
  }
  conversation.value.turns.push(placeholder)
  activeTurnId.value = placeholder.turn_id
  try {
    const response = await countryOutageChatApi.createRuntimeV2ConversationTurn(
      conversation.value.conversation_id,
      {
        question: normalized,
        idempotency_key: createP1IdempotencyKey('turn'),
      },
      conversationTurnController.signal,
    )
    mergeRuntimeTurn(response.turn, placeholder.turn_id)
    const requiresAuthoritativeConversationRefresh = response.turn.answer
      && conversation.value
      && (
        response.turn.answer.binding.publication_id
          !== conversation.value.binding.publication_id
        || response.turn.answer.binding.revision
          !== conversation.value.binding.revision
        || response.turn.answer.state_receipt.proposed.reason_codes.includes(
          'event_switch_rebound_atomically',
        )
      )
    if (requiresAuthoritativeConversationRefresh) {
      try {
        conversation.value = await countryOutageChatApi.getRuntimeV2Conversation(
          conversation.value.conversation_id,
        )
      } catch {
        connection.value = 'retrying'
      }
    }
    activeTurnId.value = response.turn.turn_id
    liveStatus.value = response.turn.state === 'completed'
      ? response.turn.answer?.execution_trace.state_commit === 'committed'
        ? '回答完成：事实与状态事务均已校验并提交'
        : '回答完成：本轮没有可提交的上下文变化'
      : response.turn.error?.message || '本轮失败关闭'
  } catch (cause) {
    placeholder.state = conversationTurnController.signal.aborted ? 'cancelled' : 'failed'
    placeholder.error = {
      code: placeholder.state === 'cancelled' ? 'cancelled' : 'request_failed',
      message: placeholder.state === 'cancelled'
        ? '本轮已取消；未发布回答，也未提交状态。'
        : errorMessage(cause),
      retryable: placeholder.state !== 'cancelled',
    }
    liveStatus.value = placeholder.state === 'cancelled'
      ? '本轮已取消'
      : '本轮失败关闭'
    if (conversation.value) {
      try {
        connection.value = 'retrying'
        conversation.value = await countryOutageChatApi.getRuntimeV2Conversation(
          conversation.value.conversation_id,
        )
        connection.value = 'connected'
      } catch {
        connection.value = 'retrying'
      }
    }
  } finally {
    sending.value = false
    activeTurnId.value = ''
    conversationTurnController = null
    await nextTick()
    composer.value?.focus()
  }
}

async function cancel() {
  if (!sending.value || cancelling.value) return
  cancelling.value = true
  try {
    conversationTurnController?.abort()
    liveStatus.value = '取消信号已发送；本轮不会提交状态'
  } finally {
    cancelling.value = false
  }
}

function openRebind() {
  rebindOpen.value = !rebindOpen.value
  rebindError.value = ''
  rebindReference.value = conversation.value?.binding.legacy_reference || ''
  rebindPublication.value = conversation.value?.binding.publication_id || ''
  rebindRevision.value = conversation.value?.binding.revision || 1
}

async function rebindConversation() {
  if (!conversation.value || rebinding.value) return
  const oldStorageKey = storageKey.value
  rebinding.value = true
  rebindError.value = ''
  try {
    const response = await countryOutageChatApi.rebindRuntimeV2Conversation(
      conversation.value.conversation_id,
      {
        event_reference: rebindReference.value.trim(),
        publication_id: rebindPublication.value.trim(),
        revision: rebindRevision.value,
        idempotency_key: createP1IdempotencyKey('conversation'),
      },
    )
    conversation.value = response.conversation
    await router.replace({
      query: {
        ...route.query,
        ref: response.conversation.binding.legacy_reference,
        publication_id: response.conversation.binding.publication_id,
        revision: String(response.conversation.binding.revision),
      },
    })
    localStorage.removeItem(oldStorageKey)
    localStorage.setItem(storageKey.value, response.conversation.conversation_id)
    rebindOpen.value = false
    recoveryNotice.value = `已切换到 binding generation ${response.conversation.binding_generation}；旧回答保留原 publication，执行上下文已清空。`
  } catch (cause) {
    rebindError.value = errorMessage(cause)
  } finally {
    rebinding.value = false
  }
}

function onComposerKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void submit()
  }
}

onMounted(() => { void load() })
onBeforeUnmount(() => {
  runtimeSummaryController?.abort()
  conversationTurnController?.abort()
})
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
            <div><dt>状态提交轮次</dt><dd>{{ conversation.dialog_state.last_committed_turn_number }}</dd></div>
            <div><dt>绑定代次</dt><dd>G{{ conversation.binding_generation }}</dd></div>
            <div><dt>活动执行绑定</dt><dd>{{ conversation.active_binding_generation === null ? 'SUSPENDED' : `G${conversation.active_binding_generation}` }}</dd></div>
            <div><dt>证据状态</dt><dd>IMMUTABLE · {{ conversation.evidence_state.collector_id.toUpperCase() }}</dd></div>
            <div><dt>到期时间</dt><dd>{{ localTime(conversation.expires_at) }}</dd></div>
            <div><dt>状态流</dt><dd :class="`is-${connection}`">{{ connection }}</dd></div>
          </dl>
          <button class="restart" type="button" @click="restart">以当前事件新建会话</button>
          <button class="restart switch-event" type="button" @click="openRebind">
            {{ rebindOpen ? '收起事件切换' : '切换事件 / revision' }}
          </button>
          <form v-if="rebindOpen" class="rebind-form" @submit.prevent="rebindConversation">
            <label>事件引用<input v-model="rebindReference" required /></label>
            <label>Publication<input v-model="rebindPublication" required /></label>
            <label>Revision<input v-model.number="rebindRevision" type="number" min="1" required /></label>
            <p v-if="rebindError" role="alert">{{ rebindError }}</p>
            <button type="submit" :disabled="rebinding">
              {{ rebinding ? '正在验证新 EvidenceState…' : '验证后原子切换' }}
            </button>
          </form>
          <p class="boundary">不接入 OONI / IODA / Cloudflare，不判断真实用户影响、责任或原因。</p>
        </aside>

        <section class="conversation-panel" aria-label="事件问答消息">
          <div class="conversation-head">
            <div class="panel-heading">
              <span>02</span>
              <div><small>VERIFIED DIALOGUE</small><h2>证据对话</h2></div>
            </div>
            <i aria-hidden="true"></i>
            <b>{{ conversation.turns.length }} S3 TURNS</b>
          </div>

          <section class="runtime-slice" aria-labelledby="runtime-slice-title">
            <header>
              <div>
                <small>S1 · CONTROLLED SINGLE TURN</small>
                <h2 id="runtime-slice-title">确定性事件概览</h2>
                <p>固定目标 event_summary；这是 S1 同候选垂直切片，不冒充开放自然语言规划。</p>
              </div>
              <button
                v-if="!runtimeSummaryLoading"
                type="button"
                @click="runControlledEventSummary"
              >{{ runtimeSummary ? '重新读取并校验' : '读取当前事件概览' }}</button>
              <button
                v-else
                class="cancel-runtime"
                type="button"
                @click="cancelControlledEventSummary"
              >取消读取</button>
            </header>

            <p v-if="runtimeSummaryLoading" class="runtime-progress" role="status" aria-live="polite">
              正在解析事件身份、读取 RRC25 overview 并校验证据……
            </p>
            <p v-if="runtimeSummaryError" class="runtime-error" role="alert">
              <b>FAILED CLOSED</b>{{ runtimeSummaryError }}
            </p>

            <article v-if="runtimeSummary" class="runtime-answer">
              <div class="runtime-verdict">
                <strong>部分回答</strong>
                <span>✓ FACTS VALIDATED</span>
                <span>0 MODEL FACTS</span>
                <span>NO STATE COMMIT</span>
              </div>
              <p class="runtime-answer-text">{{ runtimeSummary.answer_text }}</p>
              <div class="runtime-boundaries">
                <section>
                  <h3>限制</h3>
                  <ul><li v-for="item in runtimeSummary.limitations" :key="item">{{ item }}</li></ul>
                </section>
                <section>
                  <h3>未知</h3>
                  <ul><li v-for="item in runtimeSummary.unknowns" :key="item">{{ item }}</li></ul>
                </section>
              </div>
              <details class="runtime-evidence">
                <summary>查看 {{ runtimeSummary.evidence.length }} 条字段级证据</summary>
                <ol>
                  <li v-for="item in runtimeSummary.evidence" :key="item.evidence_ref">
                    <code>{{ item.evidence_ref }}</code>
                    <span>{{ item.value === null ? 'NULL / 未知' : item.value }} {{ item.unit || '' }}</span>
                    <small>{{ item.observed_at_utc || '无单独观测时点' }}</small>
                  </li>
                </ol>
              </details>
              <footer>
                <span>{{ runtimeSummary.execution_trace.nodes.map((node) => node.execution_unit).join(' → ') }}</span>
                <span>{{ runtimeSummary.binding.publication_id }} · R{{ runtimeSummary.binding.revision }}</span>
                <span>{{ runtimeSummary.runtime_identity.contract_revision }}</span>
              </footer>
            </article>
          </section>

          <p v-if="recoveryNotice" class="recovery-notice" role="status">
            <b>会话恢复说明</b>{{ recoveryNotice }}
          </p>

          <div v-if="conversation.turns.length === 0" class="empty-state">
            <span>NO TURNS YET</span>
            <h2>自然表达目标，确定执行事实</h2>
            <p>同义、口语、错序和多意图先形成开放目标，再经过封闭能力与证据门。</p>
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
                    <span>OPEN USER GOAL → CLOSED GROUNDING</span>
                    <h3>{{ statusLabel[turn.answer.answerability] }}</h3>
                  </div>
                  <time>{{ localTime(turn.answer.completed_at) }}</time>
                </header>

                <section v-for="result in turn.answer.results" :key="result.goal_id" class="subanswer">
                  <b>{{ statusLabel[result.answerability] }} · {{ result.normalized_kind }}</b>
                  <small>原目标：{{ result.requested_goal }}</small>
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
                      <span>{{ item.source }} · {{ item.field_path }}</span>
                      <b>{{ item.value === null ? 'NULL / 未知' : item.value }} {{ item.unit || '' }}</b>
                    </li>
                  </ol>
                </details>

                <footer>
                  <span :title="turn.answer.binding.publication_id">{{ shortIdentity(turn.answer.binding.publication_id) }} · R{{ turn.answer.binding.revision }} · {{ turn.answer.binding.collector_id.toUpperCase() }}</span>
                  <span>{{ turn.answer.validation.grounding_legality === 'passed' ? '✓ GROUNDING 100% LEGAL' : '× FAILED CLOSED' }}</span>
                  <span>{{ turn.answer.execution_trace.planner_outcome }} · 0 MODEL FACTS · STATE {{ turn.answer.execution_trace.state_commit.toUpperCase() }}</span>
                  <span>TX {{ turn.answer.state_receipt.status.toUpperCase() }} · {{ turn.answer.execution_trace.nodes.length }} RECEIPTS</span>
                </footer>
              </article>

              <article v-else :class="['processing-card', `is-${turn.state}`]">
                <span class="pulse" aria-hidden="true"></span>
                <div>
                  <b>{{ turn.error?.message || liveStatus }}</b>
                  <small>不会在校验完成前发布答案或更新上下文</small>
                </div>
              </article>
            </li>
          </ol>

          <p v-if="error" class="inline-error" role="alert">{{ error }}</p>

          <form class="composer" @submit.prevent="submit()">
            <div class="composer-meta">
              <span>03 · OPEN GOAL / CLOSED EXECUTION</span>
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
              <button v-if="sending" type="button" :disabled="cancelling" class="cancel" @click="cancel">
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
.switch-event { margin-top: 7px; }
.rebind-form { display: grid; gap: 8px; margin-top: 9px; padding: 10px; background: #fff; border: 1px solid #d4d0c7; }
.rebind-form label { display: grid; gap: 4px; color: #766f68; font-size: 8px; font-weight: 800; text-transform: uppercase; }
.rebind-form input { width: 100%; min-width: 0; padding: 7px; color: #263d48; background: #f7f8f6; border: 1px solid #bdc7c8; font: 8px/1.4 var(--mono); box-sizing: border-box; }
.rebind-form p { margin: 0; color: #9a3730; font-size: 8px; line-height: 1.4; }
.rebind-form button { min-height: 34px; cursor: pointer; color: #fff; background: #275f70; border: 0; font-size: 9px; font-weight: 800; }
.rebind-form button:disabled { cursor: wait; opacity: .55; }
.boundary { margin: 14px 0 0; padding-left: 9px; color: #87573b; border-left: 3px solid var(--signal); font-size: 9px; line-height: 1.6; }
.conversation-panel { display: flex; flex-direction: column; min-width: 0; min-height: 720px; background: #fbfcfb; }
.conversation-head { display: flex; align-items: center; gap: 15px; padding: 21px 25px; border-bottom: 1px solid var(--line); }
.conversation-head i { flex: 1; height: 1px; background: var(--line); }
.conversation-head > b { color: #819097; font: 800 8px/1 var(--mono); letter-spacing: .08em; }
.runtime-slice { margin: 18px 24px 4px; border: 1px solid #b8c8cc; background: #eef4f3; }
.runtime-slice > header { display: flex; justify-content: space-between; gap: 22px; align-items: center; padding: 17px 18px; border-bottom: 1px solid #cbd8da; }
.runtime-slice > header small { color: var(--signal); font: 850 8px/1 var(--mono); letter-spacing: .1em; }
.runtime-slice > header h2 { margin: 5px 0 3px; color: var(--ink); font-size: 18px; }
.runtime-slice > header p { margin: 0; color: #64777e; font-size: 9px; line-height: 1.55; }
.runtime-slice > header button { flex: none; min-width: 145px; min-height: 38px; padding: 8px 13px; cursor: pointer; color: #fff; background: #216b77; border: 0; font-size: 10px; font-weight: 850; }
.runtime-slice > header button.cancel-runtime { background: #94453d; }
.runtime-progress, .runtime-error { margin: 0; padding: 13px 18px; font-size: 10px; }
.runtime-progress { color: #315864; background: #f6faf9; }
.runtime-error { color: #8f302b; background: #fff0ed; }
.runtime-error b { margin-right: 9px; font: 850 8px/1 var(--mono); }
.runtime-answer { background: #fff; }
.runtime-verdict { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; padding: 11px 18px; color: #5f777e; background: #f6f8f7; border-bottom: 1px solid #d9e1e1; font: 800 8px/1 var(--mono); }
.runtime-verdict strong { color: #a75b27; font-size: 11px; }
.runtime-answer-text { margin: 0; padding: 17px 18px; color: #2d424b; white-space: pre-line; font-size: 12px; line-height: 1.75; }
.runtime-boundaries { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; margin: 0 18px 15px; background: #ddd7ce; border: 1px solid #ddd7ce; }
.runtime-boundaries section { padding: 10px 12px; background: #fbf7ef; }
.runtime-boundaries h3 { margin: 0; color: #87573b; font-size: 9px; }
.runtime-boundaries ul { margin: 6px 0 0; padding-left: 16px; color: #665d56; font-size: 9px; line-height: 1.55; }
.runtime-evidence { margin: 0 18px 15px; border: 1px solid #d4dddf; }
.runtime-evidence summary { padding: 9px 11px; cursor: pointer; color: #23677f; background: #f3f7f7; font-size: 9px; font-weight: 800; }
.runtime-evidence ol { margin: 0; padding: 0; list-style: none; }
.runtime-evidence li { display: grid; grid-template-columns: minmax(200px, 1fr) minmax(130px, .7fr) minmax(150px, .7fr); gap: 9px; padding: 8px 10px; border-top: 1px solid #e2e8e8; }
.runtime-evidence code { overflow-wrap: anywhere; color: #2b677c; font: 8px/1.4 var(--mono); }
.runtime-evidence span, .runtime-evidence small { color: #596c73; font-size: 8px; }
.runtime-answer > footer { display: flex; flex-wrap: wrap; gap: 12px; padding: 10px 18px; color: #73858b; background: #f4f7f6; border-top: 1px solid #dce3e2; font: 800 7px/1.4 var(--mono); }
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
  .runtime-slice > header { align-items: flex-start; }
}
@media (max-width: 640px) {
  .chat-page { padding: 0; }
  .identity-title { padding: 21px 18px; }
  .identity-rail dl { grid-template-columns: 1fr; }
  .identity-rail dl .wide { grid-column: auto; }
  .context-panel { padding: 19px 16px; }
  .context-panel .session-facts { grid-template-columns: 1fr; }
  .conversation-head { padding: 17px 16px; }
  .runtime-slice { margin: 12px 12px 4px; }
  .runtime-slice > header { display: grid; }
  .runtime-slice > header button { width: 100%; }
  .runtime-boundaries, .runtime-evidence li { grid-template-columns: 1fr; }
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
