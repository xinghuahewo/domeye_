<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { getEventCounts } from '@/api/dashboard'
import { getTopEvents } from '@/api/events'
import { getTopFeatures } from '@/api/features'
import EventTable from '@/components/EventTable.vue'
import LineChart, { type ChartSeries } from '@/components/LineChart.vue'
import PageState from '@/components/PageState.vue'
import type { CountPoint, EventRow, FeaturePoint } from '@/types/api'
import { summarizeFeatureWindow } from '@/utils/featureSummary'
import { errorMessage } from '@/utils/normalize'
import { recentRange, toBackendTime } from '@/utils/time'

const router = useRouter()
const events = ref<EventRow[]>([])
const counts = ref<CountPoint[]>([])
const features = ref<FeaturePoint[]>([])
const loading = ref(true)
const eventError = ref('')
const chartError = ref('')

const totalEvents = computed(() => counts.value.reduce((sum, point) => sum + point.count, 0))
const latestCount = computed(() => counts.value.at(-1)?.count ?? 0)
const latestObservation = computed(() => features.value.at(-1)?.time || '等待特征数据')
const windowSummary = computed(() => summarizeFeatureWindow(features.value))
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

function formatFeatureCount(value: number | null) {
  if (value === null || windowSummary.value.observedPoints === 0) return '—'
  return value.toLocaleString('zh-CN')
}

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

async function load() {
  loading.value = true
  eventError.value = ''
  chartError.value = ''
  const range = recentRange(24)
  const [eventResult, countResult, featureResult] = await Promise.allSettled([
    getTopEvents(),
    getEventCounts(),
    getTopFeatures('collector', {
      start_time: toBackendTime(range.start),
      end_time: toBackendTime(range.end),
    }),
  ])

  if (eventResult.status === 'fulfilled') events.value = eventResult.value
  else eventError.value = errorMessage(eventResult.reason)

  if (countResult.status === 'fulfilled') counts.value = countResult.value
  else eventError.value ||= errorMessage(countResult.reason)

  if (featureResult.status === 'fulfilled') features.value = featureResult.value
  else chartError.value = errorMessage(featureResult.reason)
  loading.value = false
}

function openEvent(event: EventRow) {
  if (!event.detailUrl) return
  void router.push({ name: 'event-detail', query: { ref: event.detailUrl } })
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
        汇总 BGP 报文变化、六类核心异常和最新事件，数据范围固定保留自 2026 年 2 月 1 日以来的发布快照。
      </p>
    </header>

    <section class="metric-ledger" aria-label="核心指标">
      <div>
        <span>近 30 日事件</span>
        <strong>{{ totalEvents.toLocaleString('zh-CN') }}</strong>
        <small>EVENT RECORDS</small>
      </div>
      <div>
        <span>最近统计日</span>
        <strong>{{ latestCount.toLocaleString('zh-CN') }}</strong>
        <small>{{ counts.at(-1)?.time || 'NO SAMPLE' }}</small>
      </div>
      <div>
        <span>核心异常类型</span>
        <strong>06</strong>
        <small>HIJACK / LEAK / OUTAGE</small>
      </div>
      <div>
        <span>最后观测</span>
        <strong class="metric-time">{{ latestObservation }}</strong>
        <small>COLLECTOR FEATURE</small>
      </div>
    </section>

    <section class="home-grid">
      <div class="home-chart dashboard-card">
        <div class="section-heading">
          <h2>采集点报文脉冲</h2>
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
        <LineChart v-else :series="messageSeries" unit="条" :height="330" />
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
          <PageState
            kind="error"
            title="窗口摘要暂不可用"
            :detail="chartError"
            @retry="load"
          />
        </div>
        <div v-else class="window-summary-body">
          <div class="window-total">
            <span>报文更新总量</span>
            <strong>{{ formatFeatureCount(windowSummary.updateTotal) }}</strong>
            <small>ANNOUNCE + WITHDRAW</small>
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
              <span>撤回率</span>
              <strong>{{ withdrawRateLabel }}</strong>
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

    <section class="events-card dashboard-card">
      <div class="section-heading">
        <h2>最新核心事件</h2>
        <RouterLink to="/events">查看全部事件 →</RouterLink>
      </div>
      <PageState
        v-if="loading"
        kind="loading"
        title="正在读取事件总表"
        detail="只查询六类核心异常"
      />
      <PageState
        v-else-if="eventError"
        kind="error"
        title="事件数据暂不可用"
        :detail="eventError"
        @retry="load"
      />
      <PageState v-else-if="events.length === 0" title="当前范围没有核心异常事件" />
      <EventTable v-else :events="events" compact @select="openEvent" />
    </section>
  </article>
</template>

<style scoped>
.metric-ledger {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.metric-ledger > div {
  min-height: 112px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 15px 16px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.metric-ledger span,
.metric-ledger small {
  color: var(--muted);
  font-size: 9px;
  font-weight: 650;
  letter-spacing: 0.035em;
}

.metric-ledger strong {
  color: #17212b;
  font-size: 34px;
  font-weight: 720;
  line-height: 1;
  letter-spacing: -0.035em;
}

.metric-ledger .metric-time {
  font: 650 13px/1.35 var(--mono);
  letter-spacing: -0.02em;
}

.home-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(280px, 0.72fr);
  gap: 16px;
}

.home-chart,
.window-summary {
  display: grid;
  align-content: start;
  gap: 14px;
  padding: 18px;
}

.summary-state,
.window-summary-body {
  min-height: 330px;
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
  background:
    linear-gradient(135deg, rgba(11, 87, 183, 0.035), transparent 45%),
    var(--paper);
}

.window-total {
  display: grid;
  gap: 8px;
  padding: 19px 16px 17px;
  border-bottom: 1px solid var(--line);
}

.window-total span,
.window-total small,
.traffic-split dt,
.message-mix span,
.peak-reading span {
  color: var(--muted);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.055em;
}

.window-total strong {
  color: #17212b;
  font: 720 32px/1 var(--mono);
  letter-spacing: -0.045em;
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
  font: 700 15px/1.2 var(--mono);
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
  font: 750 15px/1 var(--mono);
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

.announce-share {
  background: #0b57b7;
}

.withdraw-share {
  background: #35b6d4;
}

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

  .home-grid {
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
    font-size: 30px;
  }

  .home-chart,
  .window-summary {
    padding: 14px;
  }
}
</style>
