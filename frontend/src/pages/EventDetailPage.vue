<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { getEventDetail } from '@/api/events'
import PageState from '@/components/PageState.vue'
import { EVENT_KIND_LABELS, type ParsedDetailRef } from '@/types/api'
import { cleanText, errorMessage, isRecord } from '@/utils/normalize'

interface FactItem {
  label: string
  value: string
}

interface EvidenceGroup {
  label: string
  lines: string[]
}

const route = useRoute()
const loading = ref(false)
const error = ref('')
const parsed = ref<ParsedDetailRef | null>(null)
const detail = ref<Record<string, unknown>>({})

const reference = computed(() => typeof route.query.ref === 'string' ? route.query.ref : '')
const title = computed(() => parsed.value ? EVENT_KIND_LABELS[parsed.value.kind] : '事件证据')

const keyLabels: Record<string, string> = {
  hijacked_prefix: '被劫持前缀',
  hijacker_prefix: '异常子前缀',
  outage_prefix: '中断前缀',
  leak_prefix: '泄漏前缀',
  outage_as: '中断 ASN',
  outage_country: '中断国家',
  attacked_as: '受影响 ASN',
  attacked_as_name: '受影响网络',
  attacked_org: '受影响机构',
  attacked_country: '受影响国家',
  attacker_as: '异常来源 ASN',
  attacker_as_name: '异常来源网络',
  attacker_org: '异常来源机构',
  attacker_country: '异常来源国家',
  leak_to: '泄漏接收 ASN',
  leak_to_name: '泄漏接收网络',
  leak_to_org: '泄漏接收机构',
  leak_to_country: '泄漏接收国家',
  total_prefix_num: 'AS 总前缀数',
  outage_prefix_num: '中断前缀数',
  total_as_num: '国家 AS 总数',
  outage_as_num: '中断 AS 数',
  as_type: 'AS 类型',
  event_level: '事件等级',
  start_time: '开始时间',
  end_time: '结束时间',
  duration: '持续时间',
}

const factOrder = [
  'hijacked_prefix', 'hijacker_prefix', 'outage_prefix', 'leak_prefix',
  'outage_as', 'outage_country', 'attacked_as', 'attacked_as_name',
  'attacked_org', 'attacked_country', 'attacker_as', 'attacker_as_name',
  'attacker_org', 'attacker_country', 'leak_to', 'leak_to_name', 'leak_to_org',
  'leak_to_country', 'total_prefix_num', 'outage_prefix_num', 'total_as_num',
  'outage_as_num', 'as_type', 'event_level', 'start_time', 'end_time', 'duration',
]

function valueText(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((item) => isRecord(item) ? JSON.stringify(item) : cleanText(item)).filter(Boolean).join('、')
  }
  if (isRecord(value)) return JSON.stringify(value)
  return cleanText(value)
}

const facts = computed<FactItem[]>(() => factOrder.flatMap((key) => {
  const value = valueText(detail.value[key])
  return value ? [{ label: keyLabels[key] || key, value }] : []
}))

function evidenceLines(value: unknown): string[] {
  let source = value
  if (typeof value === 'string') {
    try {
      source = JSON.parse(value)
    } catch {
      return value ? [value] : []
    }
  }
  if (!Array.isArray(source)) return source ? [valueText(source)] : []
  return source.flatMap((item) => {
    if (Array.isArray(item)) return [item.map(cleanText).filter(Boolean).join(' → ')]
    if (isRecord(item)) {
      const candidate = item.path ?? item.as_path ?? item.route
      if (Array.isArray(candidate)) return [candidate.map(cleanText).filter(Boolean).join(' → ')]
      return [JSON.stringify(item)]
    }
    const text = cleanText(item)
    return text ? [text] : []
  })
}

const evidence = computed<EvidenceGroup[]>(() => [
  { label: '事件前路径', lines: evidenceLines(detail.value.pre_vp_paths) },
  { label: '事件中路径', lines: evidenceLines(detail.value.eve_vp_paths) },
  { label: '恢复后路径', lines: evidenceLines(detail.value.next_vp_paths) },
  { label: '泄漏 AS_PATH', lines: evidenceLines(detail.value.as_path) },
  { label: '受影响前缀集合', lines: evidenceLines(detail.value.outage_prefixes) },
  { label: '受影响 AS 集合', lines: evidenceLines(detail.value.outage_ases ?? detail.value.attacked_ases) },
].filter((group) => group.lines.length > 0))

const description = computed(() =>
  cleanText(detail.value.event_info) || cleanText(detail.value.event_descr) || '该事实记录未包含补充描述。'
)

async function load() {
  loading.value = true
  error.value = ''
  parsed.value = null
  detail.value = {}
  try {
    const response = await getEventDetail(reference.value)
    parsed.value = response.parsed
    if (!isRecord(response.payload) || Object.keys(response.payload).length === 0) {
      throw new Error('业务事实表中未找到该事件记录')
    }
    detail.value = response.payload
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

watch(reference, load, { immediate: true })
</script>

<template>
  <article class="page detail-page">
    <header class="detail-header">
      <div>
        <RouterLink class="back-link" to="/events">← 返回异常事件</RouterLink>
        <p class="eyebrow">事件证据 / Evidence</p>
        <h1>{{ title }}</h1>
      </div>
      <dl v-if="parsed" class="reference-block">
        <div><dt>TYPE</dt><dd>{{ parsed.kind }}</dd></div>
        <div><dt>OBJECT</dt><dd>{{ parsed.problem }}</dd></div>
        <div><dt>EVENT ID</dt><dd>{{ parsed.eventId }}</dd></div>
        <div><dt>SOURCE</dt><dd>{{ parsed.source }}</dd></div>
      </dl>
    </header>

    <PageState
      v-if="loading"
      kind="loading"
      title="正在回查业务事实表"
      detail="详情引用将定位到对应月份和异常类型"
    />
    <PageState
      v-else-if="error"
      kind="error"
      title="事件证据暂不可用"
      :detail="error"
      @retry="load"
    />

    <template v-else>
      <section class="narrative">
        <span>摘要</span>
        <p>{{ description }}</p>
      </section>

      <section class="detail-section dashboard-card">
        <div class="section-heading">
          <h2>事实字段</h2>
          <span>{{ facts.length }} verified fields</span>
        </div>
        <dl class="fact-grid">
          <div v-for="fact in facts" :key="fact.label">
            <dt>{{ fact.label }}</dt>
            <dd>{{ fact.value }}</dd>
          </div>
        </dl>
      </section>

      <section class="detail-section dashboard-card">
        <div class="section-heading">
          <h2>路径与影响证据</h2>
          <span>before / event / recovery</span>
        </div>
        <PageState v-if="evidence.length === 0" title="该事件没有附带路径或影响集合" />
        <div v-else class="evidence-stack">
          <article v-for="group in evidence" :key="group.label">
            <h3>{{ group.label }}</h3>
            <ol>
              <li v-for="(line, index) in group.lines" :key="`${group.label}-${index}`">
                <b>{{ String(index + 1).padStart(2, '0') }}</b>
                <code>{{ line }}</code>
              </li>
            </ol>
          </article>
        </div>
      </section>

      <details class="raw-facts">
        <summary>查看原始事实字段</summary>
        <pre>{{ JSON.stringify(detail, null, 2) }}</pre>
      </details>
    </template>
  </article>
</template>

<style scoped>
.detail-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 0.55fr);
  gap: 24px;
  align-items: start;
  padding: 18px 20px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.detail-header h1 {
  margin: 0;
  color: #17212b;
  font-size: clamp(25px, 3vw, 34px);
  font-weight: 750;
  line-height: 1.2;
  letter-spacing: -0.035em;
}

.back-link {
  display: inline-block;
  margin-bottom: 13px;
  color: var(--primary);
  font-size: 11px;
  font-weight: 650;
  text-decoration: none;
}

.reference-block {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  overflow: hidden;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: 6px;
}

.reference-block div {
  min-width: 0;
  display: block;
  padding: 10px 12px;
  background: #f8fafc;
}

.reference-block dt {
  margin-bottom: 5px;
  color: var(--muted);
  font: 8px/1.3 var(--mono);
  letter-spacing: 0.045em;
}

.reference-block dd {
  margin: 0;
  overflow-wrap: anywhere;
  color: #344054;
  font: 600 10px/1.4 var(--mono);
}

.narrative {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 18px;
  padding: 18px 20px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-left: 3px solid var(--signal);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.narrative span {
  color: var(--signal);
  font-size: 10px;
  font-weight: 750;
}

.narrative p {
  margin: 0;
  color: #344054;
  font-size: 13px;
  line-height: 1.65;
}

.detail-section {
  padding: 18px;
}

.fact-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin: 14px 0 0;
  overflow: hidden;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: 6px;
}

.fact-grid div {
  min-height: 88px;
  padding: 14px;
  background: var(--paper);
}

.fact-grid dt {
  margin-bottom: 10px;
  color: var(--muted);
  font-size: 9px;
  font-weight: 650;
}

.fact-grid dd {
  margin: 0;
  overflow-wrap: anywhere;
  color: #344054;
  font: 600 11px/1.5 var(--mono);
}

.evidence-stack {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.evidence-stack article {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
}

.evidence-stack h3 {
  margin: 0;
  padding: 15px;
  color: #124b9f;
  background: #f0f5ff;
  border-right: 1px solid #d7e5ff;
  font-size: 11px;
}

.evidence-stack ol {
  list-style: none;
  margin: 0;
  padding: 6px 16px;
}

.evidence-stack li {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #edf0f3;
}

.evidence-stack li:last-child {
  border-bottom: 0;
}

.evidence-stack b {
  color: var(--primary);
  font: 650 9px/1.5 var(--mono);
}

.evidence-stack code {
  overflow-wrap: anywhere;
  white-space: normal;
  color: #344054;
  font: 10px/1.5 var(--mono);
}

.raw-facts {
  overflow: hidden;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
}

.raw-facts summary {
  cursor: pointer;
  padding: 14px 18px;
  color: #344054;
  font-size: 11px;
  font-weight: 650;
}

.raw-facts pre {
  max-height: 420px;
  overflow: auto;
  margin: 0;
  padding: 16px 18px;
  color: #d9e2ec;
  background: #17212b;
  font: 10px/1.55 var(--mono);
}

@media (max-width: 900px) {
  .detail-header {
    grid-template-columns: 1fr;
  }

  .fact-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .narrative,
  .evidence-stack article {
    grid-template-columns: 1fr;
  }

  .reference-block {
    grid-template-columns: 1fr;
  }

  .fact-grid {
    grid-template-columns: 1fr;
  }

  .evidence-stack h3 {
    padding: 13px 16px;
    border-right: 0;
    border-bottom: 1px solid #d7e5ff;
  }
}
</style>
