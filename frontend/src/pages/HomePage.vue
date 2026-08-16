<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { getDashboardOverview } from '@/api/dashboard'
import { getTopEvents } from '@/api/events'
import { getTopFeatures } from '@/api/features'
import EventTable from '@/components/EventTable.vue'
import EventTrendChart from '@/components/EventTrendChart.vue'
import LineChart, { type ChartMarker, type ChartSeries } from '@/components/LineChart.vue'
import PageState from '@/components/PageState.vue'
import {
  CORE_EVENT_TYPES,
  type DashboardOverview,
  type EventLabel,
  type EventRow,
  type FeaturePoint,
} from '@/types/api'
import { summarizeFeatureWindow } from '@/utils/featureSummary'
import { errorMessage } from '@/utils/normalize'
import { parseInputTime, recentRange, toBackendTime } from '@/utils/time'

const router = useRouter()
const events = ref<EventRow[]>([])
const features = ref<FeaturePoint[]>([])
const previousFeatures = ref<FeaturePoint[]>([])
const overview = ref<DashboardOverview | null>(null)
const loading = ref(true)
const eventError = ref('')
const chartError = ref('')
const overviewError = ref('')

const windowSummary = computed(() => summarizeFeatureWindow(features.value))
const previousSummary = computed(() => summarizeFeatureWindow(previousFeatures.value))
const announceShare = computed(() => {
  if (windowSummary.value.updateTotal === 0) return 0
  return windowSummary.value.announceTotal / windowSummary.value.updateTotal * 100
})
const withdrawShare = computed(() => {
  if (windowSummary.value.updateTotal === 0) return 0
  return windowSummary.value.withdrawTotal / windowSummary.value.updateTotal * 100
})
const withdrawRateLabel = computed(() => {
  const rate = windowSummary.value.withdrawRate
  return rate === null ? '—' : `${(rate * 100).toFixed(1)}%`
})
const updateChange = computed(() => {
  if (previousSummary.value.observedPoints === 0 || previousSummary.value.updateTotal === 0) return null
  return (windowSummary.value.updateTotal - previousSummary.value.updateTotal)
    / previousSummary.value.updateTotal * 100
})
const withdrawRateChange = computed(() => {
  const current = windowSummary.value.withdrawRate
  const previous = previousSummary.value.withdrawRate
  if (current === null || previous === null) return null
  return (current - previous) * 100
})
const eventChangeLabel = computed(() => comparisonLabel(overview.value?.eventChangeRate ?? null))
const updateChangeLabel = computed(() => comparisonLabel(updateChange.value))
const withdrawChangeLabel = computed(() => pointChangeLabel(withdrawRateChange.value))
const latestObservation = computed(() => overview.value?.latestObservation || features.value.at(-1)?.time || null)
const freshnessLabel = computed(() => {
  const observed = latestObservation.value ? parseInputTime(latestObservation.value.replace(' ', 'T')) : null
  const end = overview.value?.endTime ? parseInputTime(overview.value.endTime.replace(' ', 'T')) : null
  if (!observed || !end) return '等待观测'
  const minutes = Math.max(0, Math.round((end.getTime() - observed.getTime()) / 60_000))
  if (minutes <= 10) return '数据新鲜'
  return `延迟 ${minutes} 分钟`
})
const scopeLabel = computed(() => {
  if (!overview.value) return '—'
  return `${overview.value.affectedCountryCount} / ${overview.value.affectedAsnCount}`
})

const messageSeries = computed<ChartSeries[]>(() => [
  {
    name: 'ANNOUNCE',
    color: '#0b57b7',
    data: features.value.map((point) => [point.time, point.announce]),
  },
  {
    name: 'WITHDRAW',
    color: '#35b6d4',
    data: features.value.map((point) => [point.time, point.withdraw]),
  },
])

const eventMarkers = computed<ChartMarker[]>(() => (overview.value?.eventSeries ?? [])
  .filter((point) => point.total > 0)
  .map((point) => ({ time: point.time, label: `${point.total} 起异常` })))

const typeTotals = computed(() => CORE_EVENT_TYPES.map((eventType) => ({
  eventType,
  count: (overview.value?.eventSeries ?? []).reduce(
    (sum, point) => sum + point.counts[eventType],
    0,
  ),
})))
const maxTypeTotal = computed(() => Math.max(1, ...typeTotals.value.map((item) => item.count)))

function formatFeatureCount(value: number | null) {
  if (value === null || windowSummary.value.observedPoints === 0) return '—'
  return value.toLocaleString('zh-CN')
}

function comparisonLabel(value: number | null) {
  if (value === null) return '上一窗口无有效基线'
  if (value === 0) return '→ 与上一窗口持平'
  return `${value > 0 ? '↑' : '↓'} ${Math.abs(value).toFixed(1)}% 较上一窗口`
}

function pointChangeLabel(value: number | null) {
  if (value === null) return '上一窗口无有效基线'
  if (value === 0) return '→ 与上一窗口持平'
  return `${value > 0 ? '↑' : '↓'} ${Math.abs(value).toFixed(1)} 个百分点`
}

async function load() {
  loading.value = true
  eventError.value = ''
  chartError.value = ''
  overviewError.value = ''
  const range = recentRange(24)
  const comparisonRange = recentRange(48)
  const params = {
    start_time: toBackendTime(range.start),
    end_time: toBackendTime(range.end),
  }
  const previousTask = comparisonRange.start === range.start
    ? Promise.resolve([])
    : getTopFeatures('collector', {
        start_time: toBackendTime(comparisonRange.start),
        end_time: toBackendTime(range.start),
      })
  const [eventResult, featureResult, previousResult, overviewResult] = await Promise.allSettled([
    getTopEvents(),
    getTopFeatures('collector', params),
    previousTask,
    getDashboardOverview(params),
  ])

  if (eventResult.status === 'fulfilled') events.value = eventResult.value
  else eventError.value = errorMessage(eventResult.reason)

  if (featureResult.status === 'fulfilled') features.value = featureResult.value
  else chartError.value = errorMessage(featureResult.reason)

  if (previousResult.status === 'fulfilled') previousFeatures.value = previousResult.value
  else chartError.value ||= errorMessage(previousResult.reason)

  if (overviewResult.status === 'fulfilled') overview.value = overviewResult.value
  else overviewError.value = errorMessage(overviewResult.reason)
  loading.value = false
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
        <p class="eyebrow">核心态势 / Overview</p>
        <h1>路由异常监测概览</h1>
      </div>
      <p class="page-heading-copy">
        以 24 小时观测窗口汇总 BGP 报文、六类核心异常和影响范围，所有时间均按 Asia/Shanghai 展示。
      </p>
    </header>

    <section class="metric-ledger" aria-label="24 小时核心指标">
      <div>
        <span>24H BGP 更新总量</span>
        <strong>{{ formatFeatureCount(windowSummary.updateTotal) }}</strong>
        <small :class="{ 'is-rise': (updateChange ?? 0) > 0 }">{{ updateChangeLabel }}</small>
      </div>
      <div>
        <span>撤回率</span>
        <strong>{{ withdrawRateLabel }}</strong>
        <small :class="{ 'is-rise': (withdrawRateChange ?? 0) > 0 }">{{ withdrawChangeLabel }}</small>
      </div>
      <div>
        <span>24H 异常事件</span>
        <strong>{{ overview ? overview.eventCount.toLocaleString('zh-CN') : '—' }}</strong>
        <small :class="{ 'is-rise': (overview?.eventChangeRate ?? 0) > 0 }">
          {{ overviewError ? '聚合暂不可用' : eventChangeLabel }}
        </small>
      </div>
      <div>
        <span>影响范围</span>
        <strong>{{ scopeLabel }}</strong>
        <small>
          {{ overview ? `${overview.affectedCountryCount} COUNTRIES · ${overview.affectedAsnCount} ASN` : 'COUNTRY / ASN' }}
        </small>
      </div>
    </section>

    <section class="home-grid">
      <div class="home-chart dashboard-card">
        <div class="section-heading">
          <div>
            <h2>采集点报文脉冲</h2>
            <p>虚线标注同小时发生的核心异常</p>
          </div>
          <RouterLink to="/features">进入特征分析 →</RouterLink>
        </div>
        <PageState
          v-if="loading"
          kind="loading"
          title="正在同步 24 小时特征"
          detail="读取采集点 ANNOUNCE 与 WITHDRAW 时序"
        />
        <PageState
          v-else-if="chartError"
          kind="error"
          title="特征数据暂不可用"
          :detail="chartError"
          @retry="load"
        />
        <LineChart v-else :series="messageSeries" :markers="eventMarkers" unit="条" :height="340" />
      </div>

      <aside class="window-summary dashboard-card" aria-label="24 小时报文窗口摘要">
        <div class="section-heading">
          <h2>窗口摘要</h2>
          <span>24H · {{ windowSummary.observedPoints }} SAMPLES</span>
        </div>
        <div v-if="loading" class="summary-state">
          <PageState kind="loading" title="正在计算窗口摘要" />
        </div>
        <div v-else-if="chartError" class="summary-state">
          <PageState kind="error" title="窗口摘要暂不可用" :detail="chartError" @retry="load" />
        </div>
        <div v-else class="window-summary-body">
          <div class="observation-reading">
            <span>观测状态</span>
            <strong>{{ freshnessLabel }}</strong>
            <time :datetime="latestObservation || undefined">{{ latestObservation || '暂无有效观测' }}</time>
          </div>

          <dl class="traffic-split">
            <div class="announce-reading">
              <dt><i aria-hidden="true"></i>ANNOUNCE</dt>
              <dd>{{ formatFeatureCount(windowSummary.announceTotal) }}</dd>
            </div>
            <div class="withdraw-reading">
              <dt><i aria-hidden="true"></i>WITHDRAW</dt>
              <dd>{{ formatFeatureCount(windowSummary.withdrawTotal) }}</dd>
            </div>
          </dl>

          <div class="message-mix">
            <div>
              <span>报文构成</span>
              <strong>{{ announceShare.toFixed(1) }} / {{ withdrawShare.toFixed(1) }}</strong>
            </div>
            <div
              class="message-mix-track"
              role="img"
              :aria-label="`ANNOUNCE 占 ${announceShare.toFixed(1)}%，WITHDRAW 占 ${withdrawShare.toFixed(1)}%`"
            >
              <i class="announce-share" :style="{ width: `${announceShare}%` }"></i>
              <i class="withdraw-share" :style="{ width: `${withdrawShare}%` }"></i>
            </div>
          </div>

          <div class="peak-reading">
            <div>
              <span>窗口峰值</span>
              <strong>{{ formatFeatureCount(windowSummary.peakUpdates) }}<small> 条</small></strong>
            </div>
            <time :datetime="windowSummary.peakTime || undefined">
              {{ windowSummary.peakTime || '暂无有效观测' }}
            </time>
          </div>
        </div>
      </aside>
    </section>

    <section class="event-analysis-grid">
      <div class="event-trend dashboard-card">
        <div class="section-heading">
          <div>
            <h2>六类异常趋势</h2>
            <p>按小时聚合 · 点击类别进入事件检索</p>
          </div>
          <span>{{ overview?.eventCount ?? 0 }} EVENTS / 24H</span>
        </div>
        <PageState v-if="loading" kind="loading" title="正在聚合六类异常" />
        <PageState
          v-else-if="overviewError"
          kind="error"
          title="异常趋势暂不可用"
          :detail="overviewError"
          @retry="load"
        />
        <EventTrendChart
          v-else
          :points="overview?.eventSeries ?? []"
          :height="300"
          @select="openEventType"
        />
      </div>

      <aside class="type-distribution dashboard-card" aria-label="异常类型占比">
        <div class="section-heading">
          <h2>类型与风险</h2>
          <span>CORE 06</span>
        </div>
        <div class="risk-strip">
          <div>
            <span>高风险</span>
            <strong>{{ overview?.highRiskCount ?? 0 }}</strong>
          </div>
          <div>
            <span>进行中</span>
            <strong>{{ overview?.activeEventCount ?? 0 }}</strong>
          </div>
        </div>
        <ol class="type-list">
          <li v-for="item in typeTotals" :key="item.eventType">
            <button type="button" @click="openEventType(item.eventType)">
              <span>{{ item.eventType }}</span>
              <b>{{ item.count }}</b>
              <i><em :style="{ width: `${item.count / maxTypeTotal * 100}%` }"></em></i>
            </button>
          </li>
        </ol>
      </aside>
    </section>

    <section class="ranking-grid" aria-label="影响对象排行">
      <article class="ranking-panel dashboard-card">
        <div class="section-heading">
          <h2>受影响国家</h2>
          <span>BY EVENTS</span>
        </div>
        <PageState v-if="!loading && !overviewError && !overview?.countryRankings.length" title="当前窗口没有国家影响记录" />
        <ol v-else class="ranking-list">
          <li v-for="(item, index) in overview?.countryRankings ?? []" :key="item.name">
            <b>{{ String(index + 1).padStart(2, '0') }}</b>
            <RouterLink :to="{ name: 'country-detail', params: { country: item.name } }">{{ item.name }}</RouterLink>
            <span>{{ item.eventCount }} 起<small v-if="item.highRiskCount"> · {{ item.highRiskCount }} 高风险</small></span>
          </li>
        </ol>
      </article>

      <article class="ranking-panel dashboard-card">
        <div class="section-heading">
          <h2>受影响 ASN</h2>
          <span>BY EVENTS</span>
        </div>
        <PageState v-if="!loading && !overviewError && !overview?.asnRankings.length" title="当前窗口没有 ASN 影响记录" />
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
          <h2>最新核心事件</h2>
          <p>按六类异常各取最近观测</p>
        </div>
        <RouterLink to="/events">查看全部事件 →</RouterLink>
      </div>
      <PageState v-if="loading" kind="loading" title="正在读取事件总表" detail="只查询六类核心异常" />
      <PageState v-else-if="eventError" kind="error" title="事件数据暂不可用" :detail="eventError" @retry="load" />
      <PageState v-else-if="events.length === 0" title="当前范围没有核心异常事件" />
      <EventTable v-else :events="events" compact @select="openEvent" />
    </section>
  </article>
</template>

<style scoped>
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

.metric-ledger > div {
  min-width: 0;
  min-height: 118px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 16px 18px;
  background: var(--paper);
}

.metric-ledger span,
.metric-ledger small {
  overflow: hidden;
  color: var(--muted);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.045em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-ledger strong {
  overflow: hidden;
  color: #17212b;
  font: 720 32px/1 var(--mono);
  letter-spacing: -0.045em;
  text-overflow: ellipsis;
}

.metric-ledger small.is-rise {
  color: var(--warning);
}

.home-grid,
.event-analysis-grid,
.ranking-grid {
  display: grid;
  gap: 16px;
}

.home-grid {
  grid-template-columns: minmax(0, 1.72fr) minmax(280px, 0.68fr);
}

.event-analysis-grid {
  grid-template-columns: minmax(0, 1.55fr) minmax(280px, 0.55fr);
}

.ranking-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.home-chart,
.window-summary,
.event-trend,
.type-distribution,
.ranking-panel {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 14px;
  padding: 18px;
}

.section-heading > div {
  min-width: 0;
}

.section-heading p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 9px;
}

.summary-state,
.window-summary-body {
  min-height: 340px;
  border: 1px solid var(--line);
  border-radius: 6px;
}

.summary-state {
  display: grid;
  place-items: center;
  padding: 16px;
}

.window-summary-body {
  overflow: hidden;
  background: linear-gradient(135deg, rgba(11, 87, 183, 0.035), transparent 45%), var(--paper);
}

.observation-reading {
  display: grid;
  gap: 8px;
  padding: 19px 16px 17px;
  border-bottom: 1px solid var(--line);
}

.observation-reading span,
.traffic-split dt,
.message-mix span,
.peak-reading span {
  color: var(--muted);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.055em;
}

.observation-reading strong {
  color: #17212b;
  font-size: 21px;
  line-height: 1;
}

.observation-reading time {
  color: var(--muted);
  font: 600 9px/1.4 var(--mono);
}

.traffic-split {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin: 0;
  border-bottom: 1px solid var(--line);
}

.traffic-split > div {
  min-width: 0;
  display: grid;
  gap: 10px;
  padding: 15px 16px;
}

.traffic-split > div + div {
  border-left: 1px solid var(--line);
}

.traffic-split dt {
  display: flex;
  align-items: center;
  gap: 6px;
}

.traffic-split dt i {
  width: 14px;
  height: 2px;
  display: inline-block;
  background: #0b57b7;
}

.traffic-split .withdraw-reading i {
  background: #35b6d4;
}

.traffic-split dd {
  overflow: hidden;
  margin: 0;
  color: #344054;
  font: 700 14px/1.2 var(--mono);
  text-overflow: ellipsis;
}

.message-mix {
  display: grid;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}

.message-mix > div:first-child {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.message-mix strong {
  color: #17212b;
  font: 750 13px/1 var(--mono);
}

.message-mix-track {
  height: 5px;
  display: flex;
  overflow: hidden;
  background: #e8edf2;
  border-radius: 999px;
}

.message-mix-track i {
  height: 100%;
  display: block;
}

.announce-share { background: #0b57b7; }
.withdraw-share { background: #35b6d4; }

.peak-reading {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: end;
  gap: 12px;
  padding: 15px 16px 16px;
}

.peak-reading > div {
  display: grid;
  gap: 8px;
}

.peak-reading strong {
  color: #17212b;
  font: 720 17px/1 var(--mono);
}

.peak-reading strong small {
  color: var(--muted);
  font: 650 9px/1 var(--mono);
}

.peak-reading time {
  color: var(--signal);
  font: 650 9px/1.35 var(--mono);
  text-align: right;
}

.risk-strip {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
}

.risk-strip div {
  display: grid;
  gap: 7px;
  padding: 12px;
  background: #f8fafc;
}

.risk-strip span {
  color: var(--muted);
  font-size: 9px;
}

.risk-strip strong {
  color: #17212b;
  font: 720 20px/1 var(--mono);
}

.type-list,
.ranking-list {
  padding: 0;
  margin: 0;
  list-style: none;
}

.type-list {
  display: grid;
  gap: 1px;
  overflow: hidden;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: 6px;
}

.type-list button {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 7px 12px;
  padding: 11px 12px;
  cursor: pointer;
  color: #344054;
  background: var(--paper);
  border: 0;
  text-align: left;
}

.type-list button:hover {
  background: #f8fafc;
}

.type-list span,
.type-list b {
  font-size: 10px;
}

.type-list i {
  grid-column: 1 / -1;
  height: 3px;
  overflow: hidden;
  background: #edf1f5;
}

.type-list em {
  height: 100%;
  display: block;
  background: var(--signal);
}

.ranking-list {
  display: grid;
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
}

.ranking-list li {
  min-width: 0;
  min-height: 48px;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  background: var(--paper);
}

.ranking-list li > b {
  color: var(--signal);
  font: 700 9px/1 var(--mono);
}

.ranking-list a {
  overflow: hidden;
  color: #344054;
  font-size: 11px;
  font-weight: 700;
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ranking-list a:hover {
  color: var(--primary);
}

.ranking-list li > span {
  color: #344054;
  font: 650 10px/1 var(--mono);
  white-space: nowrap;
}

.ranking-list small {
  color: var(--warning);
  font: inherit;
}

.events-card {
  overflow: hidden;
  padding-top: 18px;
}

.events-card > .section-heading {
  margin: 0 18px 14px;
}

.events-card > .page-state {
  margin: 0 18px 18px;
}

@media (max-width: 1100px) {
  .metric-ledger {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .home-grid,
  .event-analysis-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .ranking-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .metric-ledger {
    grid-template-columns: 1fr;
  }

  .metric-ledger > div {
    min-height: 104px;
  }

  .metric-ledger strong {
    font-size: 28px;
  }

  .home-chart,
  .window-summary,
  .event-trend,
  .type-distribution,
  .ranking-panel {
    padding: 14px;
  }

  .ranking-list li {
    grid-template-columns: 24px minmax(0, 1fr);
  }

  .ranking-list li > span {
    grid-column: 2;
  }
}
</style>
