<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import type { P0MetricSeries, P0Status } from '@/api/p0'
import EventTable from '@/components/EventTable.vue'
import EventTrendChart from '@/components/EventTrendChart.vue'
import LineChart, { type ChartSeries } from '@/components/LineChart.vue'
import PageState from '@/components/PageState.vue'
import {
  CORE_EVENT_TYPES,
  type DashboardOverview,
  type EventLabel,
  type EventRow,
} from '@/types/api'
import { errorMessage } from '@/utils/normalize'
import {
  formatProfileWindow,
  ratioOfSums,
  sumObservedMetric,
  toChartSeries,
} from '@/utils/p0Metrics'
import {
  loadP0HomeDataset,
  type P0HomeMetricName,
} from '@/utils/p0Home'

const router = useRouter()
const status = ref<P0Status | null>(null)
const metrics = ref<Record<P0HomeMetricName, P0MetricSeries> | null>(null)
const overview = ref<DashboardOverview | null>(null)
const events = ref<EventRow[]>([])
const loading = ref(true)
const p0Error = ref('')
const overviewError = ref('')
const eventError = ref('')

const announceMetric = computed(() => metrics.value?.bgp_announce_record_count ?? null)
const withdrawMetric = computed(() => metrics.value?.bgp_withdraw_record_count ?? null)
const updateMetric = computed(() => metrics.value?.bgp_update_record_count ?? null)
const ratioMetric = computed(() => metrics.value?.bgp_withdraw_ratio ?? null)
const incidentMetric = computed(() => metrics.value?.anomaly_incident_count ?? null)

const updateTotal = computed(() => updateMetric.value ? sumObservedMetric(updateMetric.value) : null)
const withdrawalRate = computed(() => ratioMetric.value ? ratioOfSums(ratioMetric.value) : null)
const incidentTotal = computed(() => incidentMetric.value ? sumObservedMetric(incidentMetric.value) : null)
const profileWindow = computed(() => status.value ? formatProfileWindow(status.value.profile) : '等待数据档身份')
const finalDayWindow = computed(() => {
  if (!overview.value) return '固定窗口末日 24H'
  return `${overview.value.startTime} — ${overview.value.endTime}`
})

const messageSeries = computed<ChartSeries[]>(() => {
  if (!announceMetric.value || !withdrawMetric.value) return []
  return [
    toChartSeries(announceMetric.value, 'ANNOUNCE', '#0b57b7'),
    toChartSeries(withdrawMetric.value, 'WITHDRAW', '#35b6d4'),
  ]
})

const typeTotals = computed(() => CORE_EVENT_TYPES.map((eventType) => ({
  eventType,
  count: (overview.value?.eventSeries ?? []).reduce(
    (sum, point) => sum + point.counts[eventType],
    0,
  ),
})))
const maxTypeTotal = computed(() => Math.max(1, ...typeTotals.value.map((item) => item.count)))

const rawCoverageLabel = computed(() => {
  const coverage = status.value?.raw_coverage
  return coverage ? `${(coverage.coverage_ratio * 100).toFixed(1)}%` : '—'
})
const reportFingerprint = computed(() => status.value?.releases.quality_report_fingerprint_sha256 ?? '')
const repositoryFingerprint = computed(() => status.value?.releases.repository_fingerprint_sha256 ?? '')
const repositoryStateLabel = computed(() => (
  status.value?.production_active ? 'PRODUCTION · ACTIVE' : 'CANDIDATE · NOT ACTIVE'
))
const repositoryStateDescription = computed(() => (
  status.value?.production_active
    ? '当前准入仓库已由显式发布流程切换为生产活动数据'
    : '候选只读数据尚未切换为生产活动数据'
))

function formatCount(value: number | null) {
  return value === null ? '—' : value.toLocaleString('zh-CN')
}

function formatRate(value: number | null) {
  if (value === null) return '—'
  return new Intl.NumberFormat('zh-CN', {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 2,
  }).format(value)
}

function shortFingerprint(value: string) {
  if (!value) return '—'
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value
}

async function load() {
  loading.value = true
  p0Error.value = ''
  overviewError.value = ''
  eventError.value = ''
  status.value = null
  metrics.value = null
  overview.value = null
  events.value = []

  try {
    const dataset = await loadP0HomeDataset()
    status.value = dataset.status
    metrics.value = dataset.metrics

    if (dataset.dashboard.status === 'fulfilled') overview.value = dataset.dashboard.value
    else overviewError.value = errorMessage(dataset.dashboard.reason)

    if (dataset.events.status === 'fulfilled') events.value = dataset.events.value
    else eventError.value = errorMessage(dataset.events.reason)
  } catch (cause) {
    // 这是准入失败，而非空数据；不得再调用旧特征接口拼接首页。
    p0Error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

function openEvent(event: EventRow) {
  if (!event.detailUrl) return
  void router.push({ name: 'event-detail', query: { ref: event.detailUrl } })
}

function openEventType(eventType: EventLabel) {
  void router.push({ name: 'events', query: { event_type: eventType } })
}

onMounted(load)
</script>

<template>
  <article class="page home-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">P0 数据态势 / Fixed evidence desk</p>
        <h1>路由异常监测概览</h1>
      </div>
      <p class="page-heading-copy">
        先确认数据身份、覆盖与缺口，再阅读固定 2026 年 2—3 月窗口的报文观测和历史异常事实；所有时间按 Asia/Shanghai 展示。
      </p>
    </header>

    <section class="profile-ribbon" aria-label="P0 固定数据档">
      <div>
        <span>DATA PROFILE</span>
        <strong>{{ status?.profile.id || '等待 P0 状态' }}</strong>
      </div>
      <p>{{ profileWindow }} · [start, end) · 5 MINUTE SLOTS</p>
      <b :class="{ 'is-ready': status?.quality_decision.status === 'passed' }">
        {{ status ? `${status.quality_decision.status} / ${status.quality_decision.admission_level}` : 'CHECKING ADMISSION' }}
      </b>
    </section>

    <PageState
      v-if="p0Error"
      class="admission-error"
      kind="error"
      title="P0 数据不可用，首页已停止叙事"
      :detail="`${p0Error}。为避免口径漂移，本页不会回退到旧 24 小时特征统计。`"
      @retry="load"
    />

    <section class="metric-ledger" aria-label="P0 固定窗口核心指标">
      <div>
        <span>固定窗口已观测 BGP 更新量</span>
        <strong>{{ formatCount(updateTotal) }}</strong>
        <small v-if="updateMetric">
          {{ updateMetric.metric_observed_sample_count.toLocaleString('zh-CN') }} / {{ updateMetric.expected_sample_count.toLocaleString('zh-CN') }} 个指标槽有值
        </small>
        <small v-else>{{ loading ? '正在读取准入指标' : 'P0 指标不可用' }}</small>
      </div>
      <div>
        <span>固定窗口已观测撤回率</span>
        <strong>{{ formatRate(withdrawalRate) }}</strong>
        <small v-if="ratioMetric">Σ WITHDRAW / Σ UPDATE · {{ ratioMetric.formula_version }}</small>
        <small v-else>{{ loading ? '正在复核公式输入' : '不使用逐点比率平均' }}</small>
      </div>
      <div>
        <span>固定窗口 Incident 总数</span>
        <strong>{{ formatCount(incidentTotal) }}</strong>
        <small v-if="incidentMetric">
          DISTINCT INCIDENTS · {{ incidentMetric.metric_observed_sample_count.toLocaleString('zh-CN') }} 个指标槽
        </small>
        <small v-else>{{ loading ? '正在读取异常指标' : 'P0 指标不可用' }}</small>
      </div>
      <div>
        <span>原始 UPDATE 完整性覆盖</span>
        <strong>{{ rawCoverageLabel }}</strong>
        <small v-if="status">
          {{ status.raw_coverage.observed_count.toLocaleString('zh-CN') }} / {{ status.raw_coverage.expected_count.toLocaleString('zh-CN') }} 个槽通过 · 已发现 {{ status.raw_coverage.present_count.toLocaleString('zh-CN') }}
        </small>
        <small v-else>{{ loading ? '正在读取制品清单' : '覆盖合同不可用' }}</small>
      </div>
    </section>

    <section class="home-grid">
      <div class="home-chart dashboard-card">
        <div class="section-heading">
          <div>
            <h2>P0 全窗口报文观测</h2>
            <p>{{ profileWindow }} · {{ updateMetric?.expected_sample_count.toLocaleString('zh-CN') || '—' }} 个五分钟槽 · null 缺口保持断线</p>
          </div>
          <RouterLink to="/features">进入探索分析 →</RouterLink>
        </div>
        <PageState
          v-if="loading"
          kind="loading"
          title="正在读取 P0 MetricSeries"
          detail="校验准入状态后加载 ANNOUNCE 与 WITHDRAW 全窗口时序"
        />
        <PageState
          v-else-if="p0Error"
          kind="error"
          title="P0 报文时序不可用"
          detail="缺失不是 0；当前不展示旧接口替代数据。"
          @retry="load"
        />
        <template v-else>
          <LineChart
            :series="messageSeries"
            unit="条"
            :height="390"
            timezone="Asia/Shanghai"
            show-data-zoom
          />
          <div class="gap-legend">
            <span>缺口统计</span>
            <span><i class="is-source"></i>SOURCE UNAVAILABLE <b>{{ status?.raw_coverage.missing_state_counts.source_unavailable.toLocaleString('zh-CN') }}</b></span>
            <span><i class="is-parse"></i>PARSE FAILED <b>{{ status?.raw_coverage.missing_state_counts.parse_failed.toLocaleString('zh-CN') }}</b></span>
            <span><i class="is-processing"></i>PROCESSING GAP <b>{{ updateMetric?.coverage.processing_gap_sample_count.toLocaleString('zh-CN') }}</b></span>
            <span>折线 <b>connectNulls = false</b></span>
          </div>
        </template>
      </div>

      <aside class="admission-rail" aria-label="数据状态与证据边界">
        <header>
          <div>
            <span>DATA STATUS</span>
            <h2>准入与证据边界</h2>
          </div>
          <b>{{ status?.quality_decision.admission_level || 'PENDING' }}</b>
        </header>

        <PageState v-if="loading" class="rail-state" kind="loading" title="正在验证候选仓库" />
        <PageState v-else-if="p0Error" class="rail-state" kind="error" title="状态端点不可用" :detail="p0Error" @retry="load" />

        <div v-else-if="status" class="rail-body">
          <dl class="status-ledger">
            <div>
              <dt>发布状态</dt>
              <dd>{{ repositoryStateLabel }}</dd>
              <small>{{ repositoryStateDescription }}</small>
            </div>
            <div>
              <dt>原始 UPDATE 完整性覆盖</dt>
              <dd>{{ status.raw_coverage.observed_count.toLocaleString('zh-CN') }} / {{ status.raw_coverage.expected_count.toLocaleString('zh-CN') }}</dd>
              <small>发现 {{ status.raw_coverage.present_count.toLocaleString('zh-CN') }} 个文件槽 · {{ status.raw_coverage.collector_scope.join(' · ') || 'collector scope 未知' }}</small>
            </div>
            <div>
              <dt>缺口分类</dt>
              <dd>{{ status.raw_coverage.missing_state_counts.source_unavailable.toLocaleString('zh-CN') }} source_unavailable · {{ status.raw_coverage.missing_state_counts.parse_failed.toLocaleString('zh-CN') }} parse_failed · {{ updateMetric?.coverage.processing_gap_sample_count.toLocaleString('zh-CN') }} processing_gap</dd>
              <small>损坏明细：空文件 {{ status.raw_coverage.invalid_reason_counts.empty_file.toLocaleString('zh-CN') }} · 流完整性失败 {{ status.raw_coverage.invalid_reason_counts.compressed_stream_invalid.toLocaleString('zh-CN') }} · magic 错误 {{ status.raw_coverage.invalid_reason_counts.compression_magic_mismatch.toLocaleString('zh-CN') }}；均不得补 0</small>
            </div>
            <div>
              <dt>完整性口径</dt>
              <dd>CONTAINER EOF / CRC</dd>
              <small>通过仅表示压缩容器完整，不等于全量 MRT/BGP 语义解析通过</small>
            </div>
            <div>
              <dt>已准入指标</dt>
              <dd>{{ status.available_metrics.length }} METRICS</dd>
              <small>仅发布通过缺失值与来源覆盖校验的指标序列</small>
            </div>
          </dl>

          <div class="fingerprint-block">
            <span>QUALITY REPORT FINGERPRINT</span>
            <code :title="reportFingerprint">{{ shortFingerprint(reportFingerprint) }}</code>
            <span>REPOSITORY FINGERPRINT</span>
            <code :title="repositoryFingerprint">{{ shortFingerprint(repositoryFingerprint) }}</code>
          </div>

          <div class="limitation-block">
            <span>KNOWN LIMITATIONS · {{ status.limitations.length }}</span>
            <ol>
              <li v-for="item in status.limitations.slice(0, 4)" :key="`${item.code}-${item.message_zh}`">
                <b>{{ item.severity }}</b>{{ item.message_zh }}
              </li>
            </ol>
          </div>
        </div>
      </aside>
    </section>

    <section class="fact-boundary">
      <div>
        <span>HISTORICAL FACT ADAPTER</span>
        <strong>以下异常趋势与排行仅取固定窗口末日 24H；事件表仍限定固定 2026 年 2—3 月事实分区。</strong>
      </div>
      <code>{{ finalDayWindow }}</code>
    </section>

    <section class="event-analysis-grid">
      <div class="event-trend dashboard-card">
        <div class="section-heading">
          <div>
            <h2>六类异常趋势</h2>
            <p>固定窗口末日按小时聚合 · 与全窗口 P0 指标分区呈现</p>
          </div>
          <span>{{ overview?.eventCount ?? '—' }} EVENTS / FINAL 24H</span>
        </div>
        <PageState v-if="loading" kind="loading" title="等待 P0 准入后读取历史事实" />
        <PageState
          v-else-if="p0Error || overviewError"
          kind="error"
          title="异常事实趋势暂不可用"
          :detail="p0Error || overviewError"
          @retry="load"
        />
        <EventTrendChart
          v-else
          :points="overview?.eventSeries ?? []"
          :height="300"
          @select="openEventType"
        />
      </div>

      <aside class="type-distribution dashboard-card" aria-label="异常类型与风险">
        <div class="section-heading">
          <h2>类型与风险</h2>
          <span>LEGACY FACTS · 06</span>
        </div>
        <div class="risk-strip">
          <div><span>高风险</span><strong>{{ overview?.highRiskCount ?? '—' }}</strong></div>
          <div><span>进行中</span><strong>{{ overview?.activeEventCount ?? '—' }}</strong></div>
        </div>
        <ol class="type-list">
          <li v-for="item in typeTotals" :key="item.eventType">
            <button type="button" @click="openEventType(item.eventType)">
              <span>{{ item.eventType }}</span>
              <b>{{ overview ? item.count : '—' }}</b>
              <i><em :style="{ width: `${item.count / maxTypeTotal * 100}%` }"></em></i>
            </button>
          </li>
        </ol>
      </aside>
    </section>

    <section class="ranking-grid" aria-label="固定窗口末日影响对象排行">
      <article class="ranking-panel dashboard-card">
        <div class="section-heading"><h2>受影响国家</h2><span>FINAL 24H · BY EVENTS</span></div>
        <PageState v-if="!loading && !p0Error && !overviewError && !overview?.countryRankings.length" title="固定窗口末日没有国家影响记录" />
        <ol v-else class="ranking-list">
          <li v-for="(item, index) in overview?.countryRankings ?? []" :key="item.name">
            <b>{{ String(index + 1).padStart(2, '0') }}</b>
            <RouterLink :to="{ name: 'country-detail', params: { country: item.name } }">{{ item.name }}</RouterLink>
            <span>{{ item.eventCount }} 起<small v-if="item.highRiskCount"> · {{ item.highRiskCount }} 高风险</small></span>
          </li>
        </ol>
      </article>

      <article class="ranking-panel dashboard-card">
        <div class="section-heading"><h2>受影响 ASN</h2><span>FINAL 24H · BY EVENTS</span></div>
        <PageState v-if="!loading && !p0Error && !overviewError && !overview?.asnRankings.length" title="固定窗口末日没有 ASN 影响记录" />
        <ol v-else class="ranking-list">
          <li v-for="(item, index) in overview?.asnRankings ?? []" :key="item.asn || item.name">
            <b>{{ String(index + 1).padStart(2, '0') }}</b>
            <RouterLink :to="{ name: 'features', query: { target: item.name } }">{{ item.name }}</RouterLink>
            <span>{{ item.eventCount }} 起<small v-if="item.highRiskCount"> · {{ item.highRiskCount }} 高风险</small></span>
          </li>
        </ol>
      </article>
    </section>

    <section class="events-card dashboard-card">
      <div class="section-heading">
        <div>
          <h2>固定窗口最新核心事件</h2>
          <p>2026 年 2—3 月六类历史事实各取最近观测，不代表原始记录全量覆盖</p>
        </div>
        <RouterLink to="/events">查看历史事件 →</RouterLink>
      </div>
      <PageState v-if="loading" kind="loading" title="等待 P0 准入后读取事件事实" />
      <PageState v-else-if="p0Error || eventError" kind="error" title="事件事实暂不可用" :detail="p0Error || eventError" @retry="load" />
      <PageState v-else-if="events.length === 0" title="固定窗口没有核心异常事件" />
      <EventTable v-else :events="events" compact @select="openEvent" />
    </section>
  </article>
</template>

<style scoped>
.home-page { display: grid; gap: 16px; }

.profile-ribbon,
.fact-boundary {
  display: grid;
  align-items: center;
  gap: 18px;
  padding: 11px 15px;
  border: 1px solid #cdd8e2;
  border-left: 3px solid var(--primary);
  background: #f7f9fc;
}

.profile-ribbon { grid-template-columns: minmax(220px, auto) 1fr auto; }
.profile-ribbon div { display: flex; align-items: baseline; gap: 10px; }
.profile-ribbon span,
.fact-boundary span { color: var(--primary); font: 750 8px/1 var(--mono); letter-spacing: .08em; }
.profile-ribbon strong { color: #24313d; font: 720 11px/1.3 var(--mono); }
.profile-ribbon p { margin: 0; color: var(--muted); font: 600 9px/1.4 var(--mono); }
.profile-ribbon > b { padding: 5px 7px; color: #7a4c12; background: #fff3dc; font: 750 8px/1 var(--mono); }
.profile-ribbon > b.is-ready { color: #116548; background: #e6f4ed; }

.admission-error { border-left: 4px solid #c9372c; }

.metric-ledger {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}
.metric-ledger > div { min-width: 0; min-height: 122px; display: flex; flex-direction: column; justify-content: space-between; padding: 16px 18px; background: var(--paper); }
.metric-ledger span,
.metric-ledger small { overflow: hidden; color: var(--muted); font-size: 9px; font-weight: 700; letter-spacing: .045em; text-overflow: ellipsis; white-space: nowrap; }
.metric-ledger strong { overflow: hidden; color: #17212b; font: 720 31px/1 var(--mono); letter-spacing: -.045em; text-overflow: ellipsis; }

.home-grid,
.event-analysis-grid,
.ranking-grid { display: grid; gap: 16px; }
.home-grid { grid-template-columns: minmax(0, 1.72fr) minmax(300px, .62fr); }
.event-analysis-grid { grid-template-columns: minmax(0, 1.55fr) minmax(280px, .55fr); }
.ranking-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }

.home-chart,
.event-trend,
.type-distribution,
.ranking-panel { min-width: 0; display: grid; align-content: start; gap: 14px; padding: 18px; }
.section-heading > div { min-width: 0; }
.section-heading p { margin: 4px 0 0; color: var(--muted); font-size: 9px; }

.gap-legend { display: flex; flex-wrap: wrap; gap: 9px 18px; padding-top: 11px; border-top: 1px solid var(--line); }
.gap-legend span { display: flex; align-items: center; gap: 6px; color: var(--muted); font: 650 8px/1.2 var(--mono); }
.gap-legend i { width: 8px; height: 8px; border: 1px solid #98a2b3; background: #eef2f5; }
.gap-legend i.is-parse { border-color: #b54708; background: #fedf89; }
.gap-legend i.is-processing { border-color: #df7a1f; background: #fff0da; }
.gap-legend b { color: #344054; }

.admission-rail { min-width: 0; overflow: hidden; color: #dce6ee; background: #17232d; border: 1px solid #283a47; border-radius: var(--radius); box-shadow: 0 12px 30px rgba(23, 35, 45, .12); }
.admission-rail > header { display: flex; align-items: start; justify-content: space-between; gap: 12px; padding: 18px; border-bottom: 1px solid #30424f; }
.admission-rail header span,
.fingerprint-block span,
.limitation-block > span { color: #86a0b3; font: 700 8px/1.2 var(--mono); letter-spacing: .08em; }
.admission-rail h2 { margin: 6px 0 0; color: #f4f7f9; font-size: 17px; }
.admission-rail header > b { padding: 5px 7px; color: #a9ddc9; background: #1e493b; font: 750 8px/1 var(--mono); }
.rail-state { min-height: 390px; color: #cbd7df; }
.rail-body { display: grid; }
.status-ledger { margin: 0; }
.status-ledger > div { display: grid; gap: 5px; padding: 14px 17px; border-bottom: 1px solid #30424f; }
.status-ledger dt { color: #8ea3b2; font-size: 9px; }
.status-ledger dd { margin: 0; overflow-wrap: anywhere; color: #f1f5f7; font: 700 11px/1.4 var(--mono); }
.status-ledger small { color: #9fb0bc; font-size: 8px; line-height: 1.55; }
.fingerprint-block { display: grid; gap: 7px; padding: 14px 17px; border-bottom: 1px solid #30424f; }
.fingerprint-block span:nth-of-type(2) { margin-top: 5px; }
.fingerprint-block code { overflow-wrap: anywhere; color: #b9d8f4; font: 650 8px/1.45 var(--mono); }
.limitation-block { display: grid; gap: 9px; padding: 14px 17px 17px; }
.limitation-block ol { display: grid; gap: 7px; margin: 0; padding-left: 17px; }
.limitation-block li { color: #aebdc8; font-size: 8px; line-height: 1.5; }
.limitation-block li b { margin-right: 5px; color: #ffcc8b; font: 700 7px/1 var(--mono); text-transform: uppercase; }

.fact-boundary { grid-template-columns: 1fr auto; border-left-color: #df7a1f; background: #fffaf2; }
.fact-boundary div { display: grid; gap: 5px; }
.fact-boundary span { color: #9a4f0c; }
.fact-boundary strong { color: #684c31; font-size: 10px; line-height: 1.5; }
.fact-boundary code { color: #7a5b38; font: 650 8px/1.4 var(--mono); }

.risk-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; overflow: hidden; background: var(--line); border: 1px solid var(--line); border-radius: 6px; }
.risk-strip div { display: grid; gap: 7px; padding: 12px; background: #f8fafc; }
.risk-strip span { color: var(--muted); font-size: 9px; }
.risk-strip strong { color: #17212b; font: 720 20px/1 var(--mono); }

.type-list,
.ranking-list { padding: 0; margin: 0; list-style: none; }
.type-list { display: grid; gap: 1px; overflow: hidden; background: var(--line); border: 1px solid var(--line); border-radius: 6px; }
.type-list button { width: 100%; display: grid; grid-template-columns: 1fr auto; gap: 7px 12px; padding: 11px 12px; cursor: pointer; color: #344054; background: var(--paper); border: 0; text-align: left; }
.type-list button:hover { background: #f8fafc; }
.type-list span,
.type-list b { font-size: 10px; }
.type-list i { grid-column: 1 / -1; height: 3px; overflow: hidden; background: #edf1f5; }
.type-list em { height: 100%; display: block; background: var(--signal); }

.ranking-list { display: grid; gap: 1px; overflow: hidden; background: var(--line); border: 1px solid var(--line); border-radius: 6px; }
.ranking-list li { min-width: 0; min-height: 48px; display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 9px 12px; background: var(--paper); }
.ranking-list li > b { color: var(--signal); font: 700 9px/1 var(--mono); }
.ranking-list a { overflow: hidden; color: #344054; font-size: 11px; font-weight: 700; text-decoration: none; text-overflow: ellipsis; white-space: nowrap; }
.ranking-list a:hover { color: var(--primary); }
.ranking-list li > span { color: #344054; font: 650 10px/1 var(--mono); white-space: nowrap; }
.ranking-list small { color: var(--warning); font: inherit; }

.events-card { overflow: hidden; padding-top: 18px; }
.events-card > .section-heading { margin: 0 18px 14px; }
.events-card > .page-state { margin: 0 18px 18px; }

@media (max-width: 1180px) {
  .home-grid,
  .event-analysis-grid { grid-template-columns: 1fr; }
  .admission-rail { display: grid; grid-template-columns: minmax(220px, .65fr) 1.35fr; }
  .admission-rail > header { border-right: 1px solid #30424f; border-bottom: 0; }
  .rail-body { grid-template-columns: 1fr 1fr; }
  .status-ledger { grid-row: 1 / span 2; }
}

@media (max-width: 900px) {
  .metric-ledger { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .admission-rail { display: block; }
  .admission-rail > header { border-right: 0; border-bottom: 1px solid #30424f; }
  .rail-body { display: grid; grid-template-columns: 1fr 1fr; }
  .status-ledger { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, 1fr); }
  .status-ledger > div:nth-child(odd) { border-right: 1px solid #30424f; }
}

@media (max-width: 720px) {
  .profile-ribbon,
  .fact-boundary,
  .ranking-grid { grid-template-columns: 1fr; }
  .profile-ribbon > b { justify-self: start; }
  .fact-boundary code { overflow-wrap: anywhere; }
  .rail-body { grid-template-columns: 1fr; }
  .status-ledger { display: block; }
  .status-ledger > div:nth-child(odd) { border-right: 0; }
}

@media (max-width: 560px) {
  .metric-ledger { grid-template-columns: 1fr; }
  .metric-ledger > div { min-height: 104px; }
  .metric-ledger strong { font-size: 28px; }
  .home-chart,
  .event-trend,
  .type-distribution,
  .ranking-panel { padding: 14px; }
  .ranking-list li { grid-template-columns: 24px minmax(0, 1fr); }
  .ranking-list li > span { grid-column: 2; }
}
</style>
