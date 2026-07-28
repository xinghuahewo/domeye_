<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  getEventEvidenceBundle,
  getEventObservation,
  isEventObservationNotConfigured,
} from '@/api/events'
import CountryOutageDashboard from '@/components/CountryOutageDashboard.vue'
import PageState from '@/components/PageState.vue'
import type {
  EvidenceBundle,
  EvidenceItem,
  EvidencePhase,
  EvidencePhaseCoverage,
  EventObservation,
  ParsedDetailRef,
} from '@/types/api'
import { cleanText, errorMessage, isRecord } from '@/utils/normalize'

interface FactItem {
  label: string
  value: string
}

interface PhaseView {
  key: Exclude<EvidencePhase, 'context'>
  index: string
  label: string
  kicker: string
  coverage: EvidencePhaseCoverage
  items: EvidenceItem[]
}

const route = useRoute()
const loading = ref(false)
const error = ref('')
const parsed = ref<ParsedDetailRef | null>(null)
const bundle = ref<EvidenceBundle | null>(null)
const observation = ref<EventObservation | null>(null)
let observationRefreshTimer: ReturnType<typeof setInterval> | undefined

const reference = computed(() => typeof route.query.ref === 'string' ? route.query.ref : '')

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

const phaseConfig = [
  { key: 'before' as const, index: '01', label: '异常前', kicker: 'BASELINE OBSERVATION' },
  { key: 'during' as const, index: '02', label: '异常期间', kicker: 'EVENT OBSERVATION' },
  { key: 'after' as const, index: '03', label: '异常后', kicker: 'POST-EVENT OBSERVATION' },
]

const statusLabels: Record<EvidencePhaseCoverage['status'], string> = {
  observed_paths: '已观测路径',
  observed_no_path: '快照中无可见路径',
  not_available: '记录未提供',
}

const kindLabels: Record<EvidenceItem['kind'], string> = {
  fact_record: '事实记录',
  route_observation: '路径观测',
  affected_object_set: '影响集合',
}

function valueText(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((item) => isRecord(item) ? JSON.stringify(item) : cleanText(item)).filter(Boolean).join('、')
  }
  if (isRecord(value)) return JSON.stringify(value)
  return cleanText(value)
}

const facts = computed<FactItem[]>(() => {
  const record = bundle.value?.factRecord ?? {}
  return factOrder.flatMap((key) => {
    const value = valueText(record[key])
    return value ? [{ label: keyLabels[key] || key, value }] : []
  })
})

const phases = computed<PhaseView[]>(() => {
  if (!bundle.value) return []
  return phaseConfig.map((phase) => ({
    ...phase,
    coverage: bundle.value!.phaseCoverage[phase.key],
    items: bundle.value!.evidenceItems.filter(
      (item) => item.phase === phase.key && item.kind === 'route_observation',
    ),
  }))
})

const registry = computed(() => bundle.value?.evidenceItems ?? [])
const impactEvidence = computed(() => registry.value.filter((item) => item.kind === 'affected_object_set'))

const coverageLabel = computed(() => {
  if (!bundle.value) return '0 / 3'
  return `${bundle.value.dataQuality.observedPhaseCount} / ${bundle.value.dataQuality.expectedPhaseCount}`
})

function pathPreview(item: EvidenceItem) {
  return item.paths.slice(0, 4)
}

async function load() {
  if (observationRefreshTimer) clearInterval(observationRefreshTimer)
  loading.value = true
  error.value = ''
  parsed.value = null
  bundle.value = null
  observation.value = null
  try {
    try {
      const response = await getEventObservation(reference.value)
      parsed.value = response.parsed
      observation.value = response.observation
      if (!response.observation.is_final) {
        observationRefreshTimer = setInterval(async () => {
          try {
            const refreshed = await getEventObservation(reference.value)
            observation.value = refreshed.observation
          } catch {
            // 保留最近一次已发布修订，下一轮继续尝试。
          }
        }, 45_000)
      }
      return
    } catch (observationCause) {
      if (!isEventObservationNotConfigured(observationCause)) {
        throw new Error(`事件观测数据暂不可用：${errorMessage(observationCause)}`)
      }
    }
    const legacyResponse = await getEventEvidenceBundle(reference.value)
    parsed.value = legacyResponse.parsed
    bundle.value = legacyResponse.bundle
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

watch(reference, load, { immediate: true })
onBeforeUnmount(() => {
  if (observationRefreshTimer) clearInterval(observationRefreshTimer)
})
</script>

<template>
  <article class="page evidence-page">
    <header v-if="!observation" class="incident-header">
      <div class="incident-title">
        <RouterLink class="back-link" to="/events">← 返回异常事件</RouterLink>
        <p class="eyebrow">事件研判 / Evidence Bundle</p>
        <h1 v-if="bundle">
          <span>{{ bundle.event.label }}</span>
          {{ bundle.event.object }}
        </h1>
        <h1 v-else>事件证据包</h1>
        <div v-if="bundle" class="incident-badges">
          <span :class="['risk-badge', `is-${bundle.event.level || 'unknown'}`]">
            {{ bundle.event.level || 'unknown' }} risk
          </span>
          <span>OBSERVATION ONLY</span>
          <span>{{ bundle.sourceRecord.sourceTable }}</span>
        </div>
      </div>

      <dl v-if="bundle" class="identity-block">
        <div class="identity-primary">
          <dt>INCIDENT ID · V1</dt>
          <dd>{{ bundle.incidentId }}</dd>
        </div>
        <div><dt>EVENT TIME · LOCAL</dt><dd>{{ bundle.event.eventTimeLocal || '未记录' }}</dd></div>
        <div><dt>EVENT TIME · UTC</dt><dd>{{ bundle.event.eventTimeUtc || '未记录' }}</dd></div>
        <div><dt>DURATION</dt><dd>{{ bundle.event.duration || '未记录' }}</dd></div>
        <div><dt>COLLECTOR CODE</dt><dd>{{ bundle.sourceRecord.sourceCode || '未记录' }}</dd></div>
      </dl>
    </header>

    <PageState
      v-if="loading"
      kind="loading"
      title="正在组装只读 Evidence Bundle"
      detail="回查业务事实记录，并生成稳定事件与证据标识"
    />
    <PageState
      v-else-if="error"
      kind="error"
      title="Evidence Bundle 暂不可用"
      :detail="error"
      @retry="load"
    />

    <CountryOutageDashboard v-else-if="observation" :observation="observation" />

    <template v-else-if="bundle">
      <section class="evidence-boundary" aria-label="Legacy 与 P0 证据边界">
        <div>
          <span>CURRENT PAGE</span>
          <strong>LEGACY EVIDENCE BUNDLE v1</strong>
          <p>由历史事实表只读适配生成，不等同于原始 BGP 证据。</p>
        </div>
        <div>
          <span>RAW EVIDENCE</span>
          <strong>NOT ATTACHED</strong>
          <p>未附 MRT / UPDATE 原始记录；AS_PATH 仅为路径观测快照。</p>
        </div>
        <div>
          <span>STATE EVIDENCE</span>
          <strong>DIRECT LINKS ONLY</strong>
          <p>逐槽状态与原始记录通过 Incident、RouteEvent 和 raw ref 直接关联；本页不合成额外证据包。</p>
        </div>
      </section>

      <section class="interpretation-band">
        <b>ROUTE OBSERVATION ≠ CAUSAL TRACE</b>
        <p>以下 AS_PATH 与 VP 路径均为特定时点的路径快照，只能支撑可见性研判，不能单独证明传播因果或根因。</p>
      </section>

      <section class="summary-strip">
        <div class="summary-copy">
          <span>事件摘要</span>
          <p>{{ bundle.event.summary || '该事实记录未包含补充描述。' }}</p>
        </div>
        <dl class="summary-metrics">
          <div><dt>阶段覆盖</dt><dd>{{ coverageLabel }}</dd><small>record coverage</small></div>
          <div><dt>证据条目</dt><dd>{{ bundle.dataQuality.evidenceItemCount }}</dd><small>stable evidence ids</small></div>
          <div><dt>路径快照</dt><dd>{{ bundle.dataQuality.routeObservationCount }}</dd><small>observation records</small></div>
          <div><dt>数据截止</dt><dd class="is-time">{{ bundle.dataSnapshot.snapshotTimeLocal || '未固定' }}</dd><small>{{ bundle.dataSnapshot.timezone }}</small></div>
        </dl>
      </section>

      <section class="timeline-section">
        <div class="section-heading">
          <div><span>01 / TIMELINE</span><h2>异常前 · 异常中 · 异常后</h2></div>
          <p>缺失表示当前事实记录未保留该阶段数据，不表示网络中没有路径。</p>
        </div>

        <div class="phase-grid">
          <article v-for="phase in phases" :key="phase.key" :class="['phase-column', `is-${phase.coverage.status}`]">
            <header>
              <b>{{ phase.index }}</b>
              <div><span>{{ phase.kicker }}</span><h3>{{ phase.label }}</h3></div>
              <em>{{ statusLabels[phase.coverage.status] }}</em>
            </header>
            <dl class="phase-stats">
              <div><dt>快照</dt><dd>{{ phase.coverage.snapshotCount }}</dd></div>
              <div><dt>路径</dt><dd>{{ phase.coverage.pathCount }}</dd></div>
            </dl>

            <div v-if="phase.items.length" class="observation-list">
              <article v-for="item in phase.items" :key="item.evidenceId" class="observation-item">
                <div class="observation-meta">
                  <code>{{ item.evidenceId }}</code>
                  <span>{{ item.observedAtLocal || '未记录时间' }}</span>
                  <small>{{ item.observedAtUtc || 'UTC 未派生' }}</small>
                </div>
                <p v-if="item.observationState === 'no_path_in_snapshot'" class="empty-snapshot">
                  该时点快照已记录，但未保留可见路径。
                </p>
                <ol v-else class="path-list">
                  <li v-for="(path, index) in pathPreview(item)" :key="`${item.evidenceId}-${index}`">
                    <b>{{ String(index + 1).padStart(2, '0') }}</b><code>{{ path }}</code>
                  </li>
                </ol>
                <details v-if="item.paths.length > 4" class="more-paths">
                  <summary>其余 {{ item.paths.length - 4 }} 条观测路径</summary>
                  <code v-for="path in item.paths.slice(4)" :key="path">{{ path }}</code>
                </details>
              </article>
            </div>
            <p v-else class="phase-missing">事实记录未提供该阶段路径快照，无法据此判断路径存在或消失。</p>
          </article>
        </div>
      </section>

      <section class="assessment-section">
        <div class="section-heading">
          <div><span>02 / ASSESSMENT</span><h2>支持、反证与证据缺口</h2></div>
          <p>自动整理观察关系，不生成根因结论。</p>
        </div>
        <div class="assessment-grid">
          <article class="assessment-list is-support">
            <header><span>SUPPORTS</span><h3>支持当前描述</h3></header>
            <p v-if="bundle.assessment.supports.length === 0">没有足够的阶段组合形成额外支持项。</p>
            <ol v-else><li v-for="item in bundle.assessment.supports" :key="item">{{ item }}</li></ol>
          </article>
          <article class="assessment-list is-counter">
            <header><span>COUNTEREVIDENCE</span><h3>反证与恢复信号</h3></header>
            <p v-if="bundle.assessment.counterevidence.length === 0">当前事实记录没有可用的反证或恢复后观测。</p>
            <ol v-else><li v-for="item in bundle.assessment.counterevidence" :key="item">{{ item }}</li></ol>
          </article>
          <article class="assessment-list is-gap">
            <header><span>GAPS</span><h3>仍缺少什么</h3></header>
            <ol><li v-for="item in bundle.assessment.gaps" :key="item">{{ item }}</li></ol>
          </article>
        </div>
      </section>

      <section class="registry-layout">
        <div class="registry-panel">
          <div class="section-heading compact">
            <div><span>03 / REGISTRY</span><h2>证据登记簿</h2></div>
            <p>{{ registry.length }} stable ids</p>
          </div>
          <div class="registry-list">
            <article v-for="item in registry" :key="item.evidenceId">
              <div class="registry-id"><span>{{ kindLabels[item.kind] }}</span><code>{{ item.evidenceId }}</code></div>
              <div class="registry-main"><b>{{ item.label }}</b><p>{{ item.semantics }}</p></div>
              <dl>
                <div><dt>PHASE</dt><dd>{{ item.phase }}</dd></div>
                <div><dt>FIELD</dt><dd>{{ item.sourceField }}</dd></div>
                <div v-if="item.pathCount"><dt>PATHS</dt><dd>{{ item.pathCount }}</dd></div>
                <div v-if="item.objectCount"><dt>OBJECTS</dt><dd>{{ item.objectCount }}</dd></div>
              </dl>
            </article>
          </div>
        </div>

        <aside class="facts-panel">
          <div class="section-heading compact">
            <div><span>04 / FACTS</span><h2>事实字段</h2></div>
            <p>{{ facts.length }} fields</p>
          </div>
          <dl class="fact-list">
            <div v-for="fact in facts" :key="fact.label"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
          </dl>
          <div v-if="impactEvidence.length" class="impact-sets">
            <article v-for="item in impactEvidence" :key="item.evidenceId">
              <span>{{ item.label }}</span>
              <b>{{ item.objectCount }} objects</b>
              <p>{{ item.objects.slice(0, 8).join('、') }}</p>
            </article>
          </div>
        </aside>
      </section>

      <section class="quality-section">
        <div class="section-heading">
          <div><span>05 / DATA QUALITY</span><h2>数据质量与解释边界</h2></div>
          <p>{{ bundle.dataQuality.timezoneSemantics }}</p>
        </div>
        <div class="quality-grid">
          <dl>
            <div><dt>VP 身份</dt><dd>{{ bundle.dataQuality.vantagePointIdentityAvailable ? '已保留' : '未保留' }}</dd></div>
            <div><dt>原始 BGP 报文</dt><dd>{{ bundle.dataQuality.rawBgpMessageAvailable ? '可用' : '未附带' }}</dd></div>
            <div><dt>因果结论</dt><dd>未生成</dd></div>
            <div><dt>源表</dt><dd>{{ bundle.sourceRecord.sourceTable }}</dd></div>
          </dl>
          <ol><li v-for="item in bundle.dataQuality.limitations" :key="item">{{ item }}</li></ol>
        </div>
      </section>

      <details class="raw-facts">
        <summary>查看原始事实记录与定位信息</summary>
        <pre>{{ JSON.stringify({ source_record: bundle.sourceRecord, fact_record: bundle.factRecord }, null, 2) }}</pre>
      </details>
    </template>
  </article>
</template>

<style scoped>
.evidence-page { display: grid; gap: 16px; }

.incident-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(430px, .72fr);
  gap: 28px;
  align-items: start;
  padding: 24px;
  color: #eef4f8;
  background: #16212b;
  border: 1px solid #263745;
  border-radius: var(--radius);
  box-shadow: 0 14px 36px rgba(17, 31, 43, .12);
}

.back-link { display: inline-block; margin-bottom: 16px; color: #8dc3ff; font-size: 11px; font-weight: 700; text-decoration: none; }
.incident-title .eyebrow { color: #7cb5ef; }
.incident-title h1 { margin: 5px 0 16px; font-size: clamp(26px, 3vw, 38px); line-height: 1.08; letter-spacing: -.04em; }
.incident-title h1 span { display: block; margin-bottom: 7px; color: #8fa4b5; font: 700 10px/1.2 var(--mono); letter-spacing: .1em; }
.incident-badges { display: flex; flex-wrap: wrap; gap: 7px; }
.incident-badges > span { padding: 5px 8px; color: #b6c7d5; border: 1px solid #3a4c5b; border-radius: 3px; font: 700 9px/1 var(--mono); letter-spacing: .045em; }
.incident-badges .risk-badge.is-high { color: #ffb1a7; border-color: #884b44; }
.incident-badges .risk-badge.is-middle { color: #ffd39a; border-color: #815f35; }
.incident-badges .risk-badge.is-low { color: #9ed8c0; border-color: #3d6a5b; }

.identity-block { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 0; overflow: hidden; background: #304250; border: 1px solid #304250; border-radius: 5px; }
.identity-block > div { min-width: 0; padding: 11px 13px; background: #1d2a35; }
.identity-block .identity-primary { grid-column: 1 / -1; background: #22313d; }
.identity-block dt { margin-bottom: 5px; color: #7f95a6; font: 700 8px/1.2 var(--mono); letter-spacing: .09em; }
.identity-block dd { margin: 0; overflow-wrap: anywhere; color: #d9e4ec; font: 600 10px/1.45 var(--mono); }
.identity-primary dd { color: #fff; font-size: 12px; }

.evidence-boundary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  color: #dce6ee;
  background: #30424f;
  border: 1px solid #30424f;
  border-radius: var(--radius);
}
.evidence-boundary > div { display: grid; align-content: start; gap: 6px; padding: 14px 16px; background: #1d2a35; }
.evidence-boundary span { color: #86a0b3; font: 700 8px/1.2 var(--mono); letter-spacing: .08em; }
.evidence-boundary strong { color: #f1f5f7; font: 750 11px/1.35 var(--mono); }
.evidence-boundary p { margin: 0; color: #adbdc8; font-size: 9px; line-height: 1.55; }

.interpretation-band { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 18px; align-items: center; padding: 11px 16px; background: #fff7e8; border: 1px solid #ebd6aa; border-left: 4px solid #df7a1f; border-radius: 4px; }
.interpretation-band b { color: #9a4f0c; font: 800 10px/1.3 var(--mono); letter-spacing: .065em; }
.interpretation-band p { margin: 0; color: #684c31; font-size: 12px; line-height: 1.65; }

.summary-strip { display: grid; grid-template-columns: minmax(280px, .8fr) minmax(0, 1.2fr); border: 1px solid var(--line); border-radius: var(--radius); background: var(--paper); box-shadow: var(--shadow-sm); }
.summary-copy { padding: 19px 21px; border-right: 1px solid var(--line); }
.summary-copy span, .section-heading span { color: var(--primary); font: 750 9px/1.2 var(--mono); letter-spacing: .08em; }
.summary-copy p { margin: 8px 0 0; color: #344054; font-size: 13px; line-height: 1.75; }
.summary-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; }
.summary-metrics > div { min-width: 0; padding: 17px 14px; border-right: 1px solid var(--line); }
.summary-metrics > div:last-child { border-right: 0; }
.summary-metrics dt { color: var(--muted); font-size: 10px; }
.summary-metrics dd { margin: 6px 0 3px; color: #17212b; font: 760 21px/1 var(--mono); }
.summary-metrics dd.is-time { overflow-wrap: anywhere; font-size: 10px; line-height: 1.35; }
.summary-metrics small { color: #8b98a5; font: 8px/1.3 var(--mono); }

.timeline-section, .assessment-section, .registry-panel, .facts-panel, .quality-section { padding: 20px; background: var(--paper); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.section-heading { display: flex; justify-content: space-between; gap: 18px; align-items: end; margin-bottom: 16px; padding-bottom: 13px; border-bottom: 1px solid var(--line); }
.section-heading h2 { margin: 5px 0 0; color: #17212b; font-size: 18px; letter-spacing: -.025em; }
.section-heading > p { max-width: 520px; margin: 0; color: var(--muted); font-size: 10px; line-height: 1.55; text-align: right; }
.section-heading.compact { margin-bottom: 4px; }

.phase-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.phase-column { min-width: 0; overflow: hidden; border: 1px solid #dce4ea; border-top: 3px solid #9eacb7; border-radius: 5px; background: #fbfcfd; }
.phase-column.is-observed_paths { border-top-color: #15845f; }
.phase-column.is-observed_no_path { border-top-color: #df7a1f; }
.phase-column > header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 13px; border-bottom: 1px solid #e2e8ed; }
.phase-column > header > b { color: #a0acb6; font: 800 18px/1 var(--mono); }
.phase-column > header span { color: #8493a0; font: 700 7px/1.2 var(--mono); letter-spacing: .07em; }
.phase-column > header h3 { margin: 3px 0 0; color: #283642; font-size: 14px; }
.phase-column > header em { padding: 4px 6px; color: #5d6a74; background: #eef2f5; border-radius: 3px; font: normal 700 8px/1.2 var(--mono); }
.phase-column.is-observed_paths > header em { color: #116548; background: #e5f4ed; }
.phase-column.is-observed_no_path > header em { color: #9a4f0c; background: #fff0da; }
.phase-stats { display: grid; grid-template-columns: repeat(2, 1fr); margin: 0; border-bottom: 1px solid #e2e8ed; }
.phase-stats > div { padding: 8px 13px; border-right: 1px solid #e2e8ed; }
.phase-stats > div:last-child { border-right: 0; }
.phase-stats dt { color: #8895a0; font-size: 9px; }
.phase-stats dd { margin: 2px 0 0; color: #25323c; font: 700 13px/1 var(--mono); }
.observation-list { display: grid; gap: 1px; background: #e2e8ed; }
.observation-item { min-width: 0; padding: 12px; background: #fff; }
.observation-meta { display: grid; gap: 3px; }
.observation-meta code { overflow-wrap: anywhere; color: #2266a5; font: 650 8px/1.35 var(--mono); }
.observation-meta span { color: #3c4954; font: 600 9px/1.35 var(--mono); }
.observation-meta small { color: #909ba4; font: 8px/1.3 var(--mono); }
.empty-snapshot, .phase-missing { margin: 10px 0 0; padding: 10px; color: #80501f; background: #fff7e8; border: 1px dashed #e7cfa7; font-size: 10px; line-height: 1.6; }
.phase-missing { margin: 12px; color: #67737d; background: #f5f7f9; border-color: #d8e0e6; }
.path-list { display: grid; gap: 0; margin: 10px 0 0; padding: 0; list-style: none; border-top: 1px solid #e4eaef; }
.path-list li { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 8px; padding: 7px 0; border-bottom: 1px solid #e4eaef; }
.path-list li > b { color: #9aa6af; font: 700 8px/1.5 var(--mono); }
.path-list code, .more-paths code { overflow-wrap: anywhere; color: #263946; font: 8px/1.55 var(--mono); }
.more-paths { margin-top: 8px; color: #5c6974; font-size: 9px; }
.more-paths summary { cursor: pointer; }
.more-paths code { display: block; padding: 5px 0; border-bottom: 1px solid #edf1f4; }

.assessment-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.assessment-list { padding: 15px; border: 1px solid #dae2e8; border-left: 3px solid #5a91bd; border-radius: 4px; background: #fbfcfd; }
.assessment-list.is-counter { border-left-color: #df7a1f; }
.assessment-list.is-gap { border-left-color: #7b8792; }
.assessment-list header span { color: #71808c; font: 700 8px/1.2 var(--mono); letter-spacing: .08em; }
.assessment-list h3 { margin: 4px 0 10px; color: #273540; font-size: 13px; }
.assessment-list > p, .assessment-list li { color: #586672; font-size: 10px; line-height: 1.65; }
.assessment-list ol { display: grid; gap: 8px; margin: 0; padding-left: 17px; }

.registry-layout { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr); gap: 16px; }
.registry-list { display: grid; }
.registry-list > article { display: grid; grid-template-columns: minmax(185px, .72fr) minmax(180px, 1fr) minmax(170px, .65fr); gap: 14px; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--line); }
.registry-list > article:last-child { border-bottom: 0; }
.registry-id { display: grid; gap: 4px; min-width: 0; }
.registry-id span { color: #697783; font-size: 9px; }
.registry-id code { overflow-wrap: anywhere; color: #2266a5; font: 650 8px/1.4 var(--mono); }
.registry-main b { color: #2c3944; font-size: 11px; }
.registry-main p { margin: 3px 0 0; color: #88949e; font: 8px/1.45 var(--mono); overflow-wrap: anywhere; }
.registry-list dl { display: flex; flex-wrap: wrap; gap: 9px 12px; margin: 0; }
.registry-list dl div { min-width: 45px; }
.registry-list dt { color: #9aa5ae; font: 7px/1.2 var(--mono); }
.registry-list dd { margin: 2px 0 0; color: #46545f; font: 650 8px/1.3 var(--mono); overflow-wrap: anywhere; }
.fact-list { margin: 0; }
.fact-list > div { display: grid; grid-template-columns: 105px minmax(0, 1fr); gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--line); }
.fact-list dt { color: #7f8c97; font-size: 9px; }
.fact-list dd { margin: 0; overflow-wrap: anywhere; color: #34424d; font: 600 9px/1.5 var(--mono); }
.impact-sets { display: grid; gap: 8px; margin-top: 12px; }
.impact-sets article { padding: 10px; background: #f5f8fa; border: 1px solid #e0e7ec; }
.impact-sets span { color: #7a8893; font-size: 9px; }
.impact-sets b { float: right; color: #33414c; font: 700 9px/1.3 var(--mono); }
.impact-sets p { margin: 6px 0 0; color: #5e6b75; font-size: 9px; line-height: 1.55; }

.quality-grid { display: grid; grid-template-columns: minmax(280px, .7fr) minmax(0, 1.3fr); gap: 24px; }
.quality-grid dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 0; background: var(--line); border: 1px solid var(--line); }
.quality-grid dl > div { padding: 12px; background: #f8fafb; }
.quality-grid dt { color: #84919b; font-size: 9px; }
.quality-grid dd { margin: 4px 0 0; overflow-wrap: anywhere; color: #33414c; font: 650 9px/1.4 var(--mono); }
.quality-grid ol { display: grid; gap: 7px; margin: 0; padding-left: 19px; }
.quality-grid li { color: #586672; font-size: 10px; line-height: 1.65; }

.raw-facts { padding: 12px 16px; background: #f8fafb; border: 1px solid var(--line); border-radius: 4px; color: #53616c; font-size: 10px; }
.raw-facts summary { cursor: pointer; font-weight: 700; }
.raw-facts pre { max-height: 430px; margin: 12px 0 0; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 9px/1.55 var(--mono); }

@media (max-width: 1100px) {
  .incident-header, .registry-layout { grid-template-columns: 1fr; }
  .summary-strip { grid-template-columns: 1fr; }
  .summary-copy { border-right: 0; border-bottom: 1px solid var(--line); }
  .phase-grid, .assessment-grid { grid-template-columns: 1fr; }
}

@media (max-width: 720px) {
  .evidence-boundary { grid-template-columns: 1fr; }
  .incident-header { gap: 18px; padding: 18px; }
  .identity-block { grid-template-columns: 1fr; }
  .identity-block .identity-primary { grid-column: 1; }
  .interpretation-band { grid-template-columns: 1fr; gap: 4px; }
  .summary-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .summary-metrics > div:nth-child(2) { border-right: 0; }
  .summary-metrics > div:nth-child(-n + 2) { border-bottom: 1px solid var(--line); }
  .timeline-section, .assessment-section, .registry-panel, .facts-panel, .quality-section { padding: 15px; }
  .section-heading { align-items: start; flex-direction: column; }
  .section-heading > p { text-align: left; }
  .registry-list > article { grid-template-columns: 1fr; gap: 8px; }
  .quality-grid { grid-template-columns: 1fr; }
}

@media (max-width: 420px) {
  .summary-metrics { grid-template-columns: 1fr; }
  .summary-metrics > div { border-right: 0; border-bottom: 1px solid var(--line); }
  .phase-column > header { grid-template-columns: auto minmax(0, 1fr); }
  .phase-column > header em { grid-column: 2; justify-self: start; }
  .fact-list > div { grid-template-columns: 1fr; gap: 3px; }
  .quality-grid dl { grid-template-columns: 1fr; }
}
</style>
