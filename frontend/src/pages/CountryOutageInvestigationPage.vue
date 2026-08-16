<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import {
  CountryOutageInvestigationRequestError,
  cancelCountryOutageInvestigation,
  cancelCountryOutageInvestigationNode,
  countryOutageInvestigationExportArtifactUrl,
  createCountryOutageInvestigation,
  createCountryOutageInvestigationExport,
  createCountryOutageInvestigationTurn,
  getCountryOutageInvestigation,
  getCountryOutageInvestigationEvidenceGraph,
  getCountryOutageInvestigationExport,
  getCountryOutageInvestigationReceipts,
  getCountryOutageInvestigationResultSet,
  getCountryOutageInvestigationTurn,
  rerunCountryOutageInvestigationNode,
  startCountryOutageInvestigation,
} from '@/api/countryOutageInvestigation'
import CountryOutageInvestigationPlan from '@/components/CountryOutageInvestigationPlan.vue'
import PageState from '@/components/PageState.vue'
import type {
  CountryOutageInvestigation,
  CountryOutageInvestigationEvidenceGraph,
  CountryOutageInvestigationExport,
  CountryOutageInvestigationNode,
  CountryOutageInvestigationReceiptPage,
  CountryOutageInvestigationResultSet,
  CountryOutageInvestigationTurn,
} from '@/types/api'
import { errorMessage, isRecord } from '@/utils/normalize'

defineOptions({ name: 'CountryOutageInvestigationPage' })

const route = useRoute()
const router = useRouter()
const investigation = ref<CountryOutageInvestigation | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const goal = ref('给出事件全景、一个精确时点下钻，并检查关键指标的证据一致性。')
const selectedNode = ref<CountryOutageInvestigationNode | null>(null)
const selectedResultSet = ref<CountryOutageInvestigationResultSet | null>(null)
const evidenceGraph = ref<CountryOutageInvestigationEvidenceGraph | null>(null)
const receiptPage = ref<CountryOutageInvestigationReceiptPage | null>(null)
const followup = ref('')
const selectionRef = ref('')
const exportFormat = ref<'csv' | 'json' | 'markdown'>('csv')
const exportJob = ref<CountryOutageInvestigationExport | null>(null)
const turns = ref<CountryOutageInvestigationTurn[]>([])
let snapshotGeneration = 0
let resultGeneration = 0
let pollTimer: ReturnType<typeof setTimeout> | undefined
let exportPollTimer: ReturnType<typeof setTimeout> | undefined

const eventReference = computed(() => typeof route.query.ref === 'string' ? route.query.ref : '')
const publicationId = computed(() => typeof route.query.publication_id === 'string' ? route.query.publication_id : '')
const publicationRevision = computed(() => {
  const value = Number(route.query.revision)
  return Number.isInteger(value) && value > 0 ? value : 0
})
const investigationId = computed(() => typeof route.query.investigation === 'string' ? route.query.investigation : '')
const active = computed(() => ['running', 'cancel_requested'].includes(investigation.value?.status ?? ''))
const canStart = computed(() => ['admitted', 'pending', 'draft'].includes(investigation.value?.status ?? ''))
const canCancel = computed(() => ['admitted', 'pending', 'running', 'cancel_requested'].includes(investigation.value?.status ?? ''))
const anchorReady = computed(() => Boolean(selectedNode.value && followup.value.trim()))

const statusLabels: Record<string, string> = {
  draft: '计划草稿', admitted: '已准入，等待开始', pending: '等待执行', running: '执行中',
  cancel_requested: '正在取消', partially_completed: '部分完成', completed: '已完成',
  cancelled: '已取消', failed: '失败',
}

const completenessLabels: Record<string, string> = {
  complete: '完整结果（限绑定 publication 与冻结人口）',
  partial: '稳定预览 / 部分结果',
  limited_sample: '有限样本',
  source_incomplete: '来源不完整',
  missing: '数据缺失',
  not_comparable: '不可比较',
}

function idempotencyKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`.slice(0, 128)
}

function recordLabel(value: unknown): string {
  if (typeof value === 'string') return value
  if (!isRecord(value)) return JSON.stringify(value)
  for (const key of ['text', 'message', 'message_zh', 'label', 'code', 'finding']) {
    if (typeof value[key] === 'string') return value[key]
  }
  return JSON.stringify(value)
}

function answerReceipt(digest: string): unknown {
  return receiptPage.value?.receipts.find((receipt) => receipt.receipt_digest === digest) ?? null
}

function resultRef(node: CountryOutageInvestigationNode): { id: string; revision: number } | null {
  const candidate = node.result_set_refs?.[0]
  if (!isRecord(candidate)) return null
  const id = candidate.result_set_id
  const revision = candidate.result_set_revision
  return typeof id === 'string' && typeof revision === 'number' ? { id, revision } : null
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
}

function schedulePoll() {
  stopPolling()
  if (!active.value) return
  pollTimer = setTimeout(() => void refreshInvestigation(true), 1200)
}

async function loadRelated(current: CountryOutageInvestigation, generation: number) {
  const id = current.investigation_id
  const revision = current.investigation_revision
  const graphPromise = current.evidence_graph_revision
    ? getCountryOutageInvestigationEvidenceGraph(id, current.evidence_graph_revision)
    : Promise.resolve(null)
  const receiptPromise = getCountryOutageInvestigationReceipts(id)
  const turnPromise = Promise.all((current.turn_refs || []).map((ref) => (
    getCountryOutageInvestigationTurn(id, ref.turn_id, ref.turn_revision).then((response) => response.turn)
  )))
  const [graph, receipts, loadedTurns] = await Promise.allSettled([graphPromise, receiptPromise, turnPromise])
  if (
    generation !== snapshotGeneration
    || investigation.value?.investigation_id !== id
    || investigation.value?.investigation_revision !== revision
  ) return
  evidenceGraph.value = graph.status === 'fulfilled' ? graph.value : null
  receiptPage.value = receipts.status === 'fulfilled' ? receipts.value : null
  turns.value = loadedTurns.status === 'fulfilled' ? loadedTurns.value : []
}

async function refreshInvestigation(background = false) {
  const id = investigationId.value
  if (!id) return
  const generation = ++snapshotGeneration
  if (!background) loading.value = true
  error.value = ''
  try {
    const response = await getCountryOutageInvestigation(id)
    if (generation !== snapshotGeneration || investigationId.value !== id) return
    investigation.value = response.investigation
    if (
      selectedNode.value
      && !response.investigation.nodes.some((node) => (
        node.node_id === selectedNode.value?.node_id
        && node.execution_revision === selectedNode.value?.execution_revision
      ))
    ) {
      selectedNode.value = null
      selectedResultSet.value = null
      selectionRef.value = ''
    }
    void loadRelated(response.investigation, generation)
  } catch (cause) {
    if (generation === snapshotGeneration) error.value = errorMessage(cause)
  } finally {
    if (generation === snapshotGeneration) {
      loading.value = false
      schedulePoll()
    }
  }
}

async function createInvestigation() {
  if (!eventReference.value || !publicationId.value || !publicationRevision.value || !goal.value.trim()) {
    error.value = '缺少冻结事件引用、publication、revision 或调查目标。'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const response = await createCountryOutageInvestigation({
      event_reference: eventReference.value,
      publication_id: publicationId.value,
      revision: publicationRevision.value,
      goal: goal.value.trim(),
      idempotency_key: idempotencyKey('w5-create'),
    })
    await router.replace({
      name: 'country-outage-investigation',
      query: { ...route.query, investigation: response.investigation.investigation_id },
    })
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    busy.value = false
  }
}

function cas(prefix: string) {
  if (!investigation.value) throw new Error('调查尚未加载')
  return {
    idempotency_key: idempotencyKey(prefix),
    expected_investigation_revision: investigation.value.investigation_revision,
    expected_current_digest: investigation.value.current_digest,
  }
}

async function mutate(action: () => Promise<unknown>, success: string) {
  busy.value = true
  error.value = ''
  try {
    await action()
    notice.value = success
    await refreshInvestigation()
  } catch (cause) {
    if (cause instanceof CountryOutageInvestigationRequestError && cause.status === 409) {
      notice.value = '调查已产生新 revision，已重新读取；请确认后再操作。'
      await refreshInvestigation()
    } else {
      error.value = errorMessage(cause)
    }
  } finally {
    busy.value = false
  }
}

function start() {
  if (!investigation.value) return
  const id = investigation.value.investigation_id
  void mutate(() => startCountryOutageInvestigation(id, cas('w5-start')), '执行请求已接受。')
}

function cancelInvestigation() {
  if (!investigation.value) return
  const id = investigation.value.investigation_id
  void mutate(() => cancelCountryOutageInvestigation(id, cas('w5-cancel')), '取消请求已接受；已提交 Evidence 将保留。')
}

function cancelNode(node: CountryOutageInvestigationNode) {
  if (!investigation.value) return
  const id = investigation.value.investigation_id
  void mutate(
    () => cancelCountryOutageInvestigationNode(id, node.node_id, cas('w5-node-cancel')),
    `节点 ${node.node_id} 的取消请求已接受。`,
  )
}

function rerunNode(node: CountryOutageInvestigationNode) {
  if (!investigation.value) return
  const id = investigation.value.investigation_id
  void mutate(
    () => rerunCountryOutageInvestigationNode(id, node.node_id, cas('w5-node-rerun')),
    `已为 ${node.node_id} 及其静态下游影响闭包请求新 revision。`,
  )
}

async function selectNode(node: CountryOutageInvestigationNode) {
  selectedNode.value = node
  selectedResultSet.value = null
  selectionRef.value = ''
  const ref = resultRef(node)
  if (!ref || !investigation.value) return
  const generation = ++resultGeneration
  const id = investigation.value.investigation_id
  try {
    const result = await getCountryOutageInvestigationResultSet(id, ref.id, ref.revision, { pageSize: 50 })
    if (
      generation === resultGeneration
      && selectedNode.value?.node_id === node.node_id
      && selectedNode.value?.execution_revision === node.execution_revision
    ) selectedResultSet.value = result
  } catch (cause) {
    if (generation === resultGeneration) error.value = errorMessage(cause)
  }
}

async function loadNextResultPage() {
  if (
    !investigation.value
    || !selectedNode.value
    || !selectedResultSet.value?.next_page_token
  ) return
  const id = investigation.value.investigation_id
  const nodeId = selectedNode.value.node_id
  const nodeRevision = selectedNode.value.execution_revision
  const resultSetId = selectedResultSet.value.result_set_id
  const resultSetRevision = selectedResultSet.value.result_set_revision
  const pageToken = selectedResultSet.value.next_page_token
  const generation = ++resultGeneration
  try {
    const result = await getCountryOutageInvestigationResultSet(
      id,
      resultSetId,
      resultSetRevision,
      { pageSize: 50, pageToken },
    )
    if (
      generation === resultGeneration
      && investigation.value?.investigation_id === id
      && selectedNode.value?.node_id === nodeId
      && selectedNode.value?.execution_revision === nodeRevision
      && result.result_set_id === resultSetId
      && result.result_set_revision === resultSetRevision
    ) selectedResultSet.value = result
  } catch (cause) {
    if (generation === resultGeneration) error.value = errorMessage(cause)
  }
}

async function submitFollowup() {
  if (!investigation.value || !selectedNode.value || !anchorReady.value) return
  const current = investigation.value
  const anchor = selectedNode.value
  busy.value = true
  error.value = ''
  try {
    const response = await createCountryOutageInvestigationTurn(current.investigation_id, {
      ...cas('w5-turn'),
      question: followup.value.trim(),
      anchor: {
        node_id: anchor.node_id,
        node_revision: anchor.execution_revision,
        ...(selectionRef.value.trim() ? { selection_ref: selectionRef.value.trim() } : {}),
      },
    })
    turns.value = [...turns.value.filter((item) => item.turn_id !== response.turn.turn_id), response.turn]
    investigation.value = response.investigation
    followup.value = ''
    notice.value = `回答已绑定 ${anchor.node_id} · REV ${anchor.execution_revision}，并与调查 REV ${response.investigation.investigation_revision} 原子提交。`
  } catch (cause) {
    if (cause instanceof CountryOutageInvestigationRequestError && cause.status === 409) {
      notice.value = '调查已产生新 revision，已重新读取；请确认后再追问。'
      await refreshInvestigation()
    } else error.value = errorMessage(cause)
  } finally {
    busy.value = false
  }
}

function stopExportPolling() {
  if (exportPollTimer) clearTimeout(exportPollTimer)
  exportPollTimer = undefined
}

async function pollExport(investigationIdValue: string, exportId: string) {
  stopExportPolling()
  try {
    const response = await getCountryOutageInvestigationExport(investigationIdValue, exportId)
    if (investigation.value?.investigation_id !== investigationIdValue) return
    exportJob.value = response.export
    if (['requested', 'rendering', 'prepared'].includes(response.export.state)) {
      exportPollTimer = setTimeout(() => void pollExport(investigationIdValue, exportId), 1200)
    }
  } catch (cause) {
    error.value = errorMessage(cause)
  }
}

async function requestExport() {
  if (!investigation.value || !selectedResultSet.value) return
  busy.value = true
  error.value = ''
  try {
    const response = await createCountryOutageInvestigationExport(
      investigation.value.investigation_id,
      {
        ...cas('w5-export'),
        result_set_id: selectedResultSet.value.result_set_id,
        result_set_revision: selectedResultSet.value.result_set_revision,
        format: exportFormat.value,
      },
    )
    exportJob.value = response.export
    void pollExport(investigation.value.investigation_id, response.export.export_id)
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    busy.value = false
  }
}

watch(investigationId, () => {
  snapshotGeneration += 1
  resultGeneration += 1
  stopPolling()
  stopExportPolling()
  investigation.value = null
  selectedNode.value = null
  selectedResultSet.value = null
  evidenceGraph.value = null
  receiptPage.value = null
  exportJob.value = null
  turns.value = []
  if (investigationId.value) void refreshInvestigation()
}, { immediate: true })

onBeforeUnmount(() => {
  snapshotGeneration += 1
  resultGeneration += 1
  stopPolling()
  stopExportPolling()
})
</script>

<template>
  <article class="investigation-page">
    <header class="investigation-hero">
      <div>
        <RouterLink :to="{ name: 'event-detail', query: { ref: eventReference } }">← 返回国家中断事件</RouterLink>
        <p>LOCAL ISOLATED · P2-S1 W5</p>
        <h1>组合调查</h1>
        <strong>仅 RRC25 控制面观测，不代表用户影响、因果、责任、恢复或生产部署。</strong>
      </div>
      <dl v-if="investigation">
        <div><dt>状态</dt><dd>{{ statusLabels[investigation.status] || investigation.status }}</dd></div>
        <div><dt>调查 REV</dt><dd>{{ investigation.investigation_revision }}</dd></div>
        <div><dt>Publication</dt><dd>{{ investigation.identity.publication_id }}</dd></div>
        <div><dt>Registry</dt><dd>{{ investigation.identity.registry_snapshot_id }}</dd></div>
      </dl>
    </header>

    <p class="investigation-boundary" role="note">
      本页只运行冻结 W1–W4 原子 Tool/Operator 的静态组合；不接入外部数据，不执行动态逐成员 fan-out。
    </p>

    <PageState v-if="loading && !investigation" kind="loading" title="正在读取调查" detail="校验当前用户、revision 与本地恢复状态" />
    <PageState v-else-if="error && !investigation && investigationId" kind="error" title="调查暂不可用" :detail="error" @retry="() => refreshInvestigation()" />

    <section v-else-if="!investigationId" class="investigation-create" aria-labelledby="create-investigation-title">
      <p>CREATE ADMITTED PLAN</p>
      <h2 id="create-investigation-title">先创建可见计划，再显式开始执行</h2>
      <dl>
        <div><dt>事件引用</dt><dd>{{ eventReference || '缺失' }}</dd></div>
        <div><dt>Publication</dt><dd>{{ publicationId || '缺失' }} · REV {{ publicationRevision || '缺失' }}</dd></div>
      </dl>
      <label for="investigation-goal">调查目标</label>
      <textarea id="investigation-goal" v-model="goal" rows="4" maxlength="4000" />
      <button type="button" :disabled="busy" @click="createInvestigation">创建调查计划</button>
      <p v-if="error" class="investigation-error" role="alert">{{ error }}</p>
      <p>P2.1 延期：逐成员动态路径位置、RouteEvent 变化分类和 PLAN-CAP-02 不进入本次计划。</p>
    </section>

    <template v-else-if="investigation">
      <p v-if="notice" class="investigation-notice" role="status">{{ notice }}</p>
      <p v-if="error" class="investigation-error" role="alert">{{ error }}</p>
      <nav class="investigation-actions" aria-label="调查操作">
        <button type="button" :disabled="busy || !canStart" @click="start">开始执行</button>
        <button type="button" :disabled="busy || !canCancel" @click="cancelInvestigation">取消调查</button>
        <button type="button" :disabled="busy" @click="refreshInvestigation()">刷新当前 REV</button>
      </nav>

      <CountryOutageInvestigationPlan
        :plan="investigation.plan"
        :nodes="investigation.nodes"
        :selected-node-id="selectedNode?.node_id || ''"
        :busy="busy"
        @select="selectNode"
        @cancel="cancelNode"
        @rerun="rerunNode"
      />

      <section class="investigation-grid">
        <section class="investigation-panel" aria-labelledby="limitations-title">
          <h2 id="limitations-title">限制与状态语义</h2>
          <ul><li v-for="(item, index) in investigation.limitations" :key="index">{{ recordLabel(item) }}</li></ul>
          <div class="investigation-semantic-states">
            <span>完整结果</span><span>稳定预览</span><span>有限样本</span><span>来源不完整</span>
            <span>数据缺失</span><span>不可比较</span><span>P2.1 延期</span><span>需要外部证据</span>
          </div>
        </section>
      </section>

      <section v-if="selectedNode" class="investigation-panel" aria-labelledby="result-title">
        <header class="investigation-panel__heading">
          <div><p>FROZEN RESULTSET</p><h2 id="result-title">节点结果与稳定预览</h2></div>
          <span>{{ selectedNode.node_id }} · REV {{ selectedNode.execution_revision }}</span>
        </header>
        <p v-if="!resultRef(selectedNode)">该节点没有已提交 ResultSet。</p>
        <template v-else-if="selectedResultSet">
          <p class="result-completeness">{{ completenessLabels[selectedResultSet.set_completeness] || selectedResultSet.set_completeness }}</p>
          <p>本页 {{ selectedResultSet.returned_count }} 条 / 冻结人口 {{ selectedResultSet.total_count }} 条；预览不冒充总体。</p>
          <div class="result-table" role="table" aria-label="ResultSet 稳定预览">
            <pre v-for="(member, index) in selectedResultSet.members" :key="index">{{ JSON.stringify(member, null, 2) }}</pre>
          </div>
          <button
            v-if="selectedResultSet.next_page_token"
            type="button"
            :disabled="busy"
            @click="loadNextResultPage"
          >读取同一冻结 REV 的下一页</button>
          <div class="export-controls">
            <label>完整导出格式
              <select v-model="exportFormat"><option value="csv">CSV</option><option value="json">JSON</option><option value="markdown">Markdown</option></select>
            </label>
            <button type="button" :disabled="busy" @click="requestExport">请求冻结 REV 导出</button>
            <span v-if="exportJob">{{ exportJob.state }}<template v-if="exportJob.sha256"> · {{ exportJob.sha256 }}</template></span>
            <a
              v-if="exportJob?.state === 'committed' && investigation"
              :href="countryOutageInvestigationExportArtifactUrl(investigation.investigation_id, exportJob.export_id)"
              download
            >下载已提交字节</a>
          </div>
        </template>
      </section>

      <section class="investigation-panel" aria-labelledby="followup-title">
        <h2 id="followup-title">绑定节点的追问</h2>
        <p v-if="selectedNode">当前 anchor：{{ selectedNode.node_id }} · REV {{ selectedNode.execution_revision }}</p>
        <p v-else role="alert">请先选择一个明确节点；系统不会猜测“那个时间点”。</p>
        <label for="selection-ref">已提交选择引用（可选）</label>
        <input id="selection-ref" v-model="selectionRef" placeholder="例如 timepoint:peak" />
        <label for="investigation-followup">追问</label>
        <textarea id="investigation-followup" v-model="followup" rows="3" maxlength="4000" />
        <button type="button" :disabled="busy || !anchorReady" @click="submitFollowup">提交显式 anchor 追问</button>
      </section>

      <section class="investigation-panel" aria-labelledby="answers-title">
        <header class="investigation-panel__heading">
          <div><p>VERSIONED TURN / ANSWER</p><h2 id="answers-title">已提交回答</h2></div>
          <span>{{ turns.length }} 个版本化 Turn</span>
        </header>
        <p v-if="turns.length === 0">当前调查 revision 尚无已提交回答。</p>
        <article v-for="turn in turns" :key="`${turn.turn_id}:${turn.turn_revision}`" class="answer-card">
          <p><strong>{{ turn.turn_id }} · TURN REV {{ turn.turn_revision }}</strong></p>
          <p>{{ turn.answer.answer_text }}</p>
          <h3>Claims</h3>
          <ul><li v-for="claim in turn.answer.claims" :key="claim.claim_id">{{ claim.text }}</li></ul>
          <h3>Limitations</h3>
          <ul><li v-for="item in turn.answer.limitations" :key="item">{{ item }}</li></ul>
          <h3>Unknowns</h3>
          <ul><li v-for="item in turn.answer.unknowns" :key="item">{{ item }}</li></ul>
          <details>
            <summary>Evidence 与治理绑定</summary>
            <p>Evidence refs：{{ turn.answer.evidence_refs.join(' · ') || '无' }}</p>
            <p>Plan：{{ turn.answer.plan_ref.plan_id }} · REV {{ turn.answer.plan_ref.plan_revision }}</p>
            <p>Graph：{{ turn.answer.evidence_graph_ref.graph_id }} · REV {{ turn.answer.evidence_graph_ref.graph_revision }}</p>
            <h4>Model receipts（owner-scoped 可读详情）</h4>
            <pre v-for="digest in turn.answer.model_receipt_digests" :key="digest">{{ JSON.stringify(answerReceipt(digest) || { receipt_digest: digest, state: '尚未加载' }, null, 2) }}</pre>
            <h4>Gate receipts（owner-scoped 可读详情）</h4>
            <pre v-for="digest in turn.answer.gate_receipt_digests" :key="digest">{{ JSON.stringify(answerReceipt(digest) || { receipt_digest: digest, state: '尚未加载' }, null, 2) }}</pre>
          </details>
          <p class="fixture-boundary" role="note">
            离线 fixture replay：external_provider_called=false · fixture_replay_only=true · runtime_integrated=true · production_deployed=false。
            这不是外部 Sol/DS 模型调用或生产效果证明。
          </p>
        </article>
      </section>

      <section class="investigation-grid">
        <section class="investigation-panel" aria-labelledby="graph-title">
          <h2 id="graph-title">Evidence Graph</h2>
          <p v-if="evidenceGraph">GRAPH REV {{ evidenceGraph.graph_revision }} · {{ evidenceGraph.graph_digest }}</p>
          <p v-else>当前 revision 尚无已提交 Evidence Graph。</p>
          <ul v-if="evidenceGraph"><li v-for="(node, index) in evidenceGraph.nodes" :key="index">{{ recordLabel(node) }}</li></ul>
        </section>
        <section class="investigation-panel" aria-labelledby="receipts-title">
          <h2 id="receipts-title">执行与治理回执</h2>
          <p>Tool · Operator · Sol/DS 模型制品 · 费用 · 延迟 · 复用 · 事务</p>
          <ul><li v-for="(receipt, index) in receiptPage?.receipts || []" :key="index">{{ recordLabel(receipt) }}</li></ul>
        </section>
      </section>
    </template>
  </article>
</template>

<style scoped>
.investigation-page { display: grid; gap: 1rem; max-width: 1280px; margin: 0 auto; padding: 1.5rem; color: #172536; }
.investigation-hero { display: grid; grid-template-columns: 1fr minmax(280px, .65fr); gap: 1.5rem; padding: 1.5rem; color: #f4f8fb; background: #132b40; }
.investigation-hero p { color: #87bedf; letter-spacing: .12em; font-size: .72rem; }
.investigation-hero h1 { margin: .15rem 0 .5rem; font-size: clamp(2rem, 5vw, 4.5rem); }
.investigation-hero a { color: #b9d8ec; }
.investigation-hero dl { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; margin: 0; }
.investigation-hero dl div { border-top: 1px solid #416177; padding-top: .5rem; overflow-wrap: anywhere; }
.investigation-hero dt { color: #9eb5c5; font-size: .72rem; text-transform: uppercase; }
.investigation-hero dd { margin: .2rem 0; }
.investigation-boundary { margin: 0; border-left: 4px solid #3c8a73; padding: .8rem 1rem; background: #edf7f4; }
.investigation-create, .investigation-panel { border: 1px solid #d9e0ea; padding: 1.25rem; background: #fff; }
.investigation-create { display: grid; gap: .7rem; }
.investigation-create p:first-child, .investigation-panel__heading p { color: #6a778a; font-size: .72rem; letter-spacing: .12em; }
.investigation-create textarea, .investigation-panel textarea, .investigation-panel input { width: 100%; box-sizing: border-box; padding: .7rem; }
.investigation-actions { display: flex; gap: .6rem; }
.investigation-actions button, .investigation-create button, .investigation-panel button { padding: .65rem .85rem; }
.investigation-notice { background: #edf7f4; padding: .75rem; }
.investigation-error { background: #fff0ef; color: #8e2e27; padding: .75rem; }
.investigation-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.investigation-semantic-states { display: flex; flex-wrap: wrap; gap: .4rem; }
.investigation-semantic-states span { padding: .25rem .5rem; background: #eef2f6; font-size: .78rem; }
.investigation-panel__heading { display: flex; justify-content: space-between; gap: 1rem; }
.investigation-panel__heading h2 { margin: 0; }
.investigation-panel__heading span { font: 600 .75rem ui-monospace, monospace; }
.result-completeness { display: inline-block; padding: .35rem .55rem; color: #194e42; background: #e5f4ef; }
.result-table { display: grid; gap: .5rem; max-height: 480px; overflow: auto; }
.result-table pre { margin: 0; padding: .75rem; background: #f5f7f9; white-space: pre-wrap; overflow-wrap: anywhere; }
.export-controls { display: flex; flex-wrap: wrap; gap: .7rem; align-items: center; margin-top: 1rem; }
.investigation-panel label { display: block; margin: .65rem 0 .25rem; font-weight: 600; }
.answer-card { margin-top: 1rem; border-left: 4px solid #275f8f; padding: .85rem 1rem; background: #f7fafc; }
.answer-card h3 { margin-bottom: .25rem; font-size: .9rem; }
.fixture-boundary { padding: .65rem; color: #704814; background: #fff5df; }
@media (max-width: 780px) {
  .investigation-page { padding: .75rem; }
  .investigation-hero, .investigation-grid { grid-template-columns: 1fr; }
}
</style>
