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

const messageSeries = computed<ChartSeries[]>(() => [
  {
    name: 'ANNOUNCE',
    color: '#0b9b9d',
    data: features.value.map((point) => [point.time, point.announce]),
  },
  {
    name: 'WITHDRAW',
    color: '#ff6542',
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
        <p class="eyebrow">Routing anomaly intelligence / Core edition</p>
        <h1>把路由偏离<br />变成可读证据</h1>
      </div>
      <p class="page-heading-copy">
        以 BGP 路由事实为基线，集中查看前缀劫持、子前缀劫持、路由泄漏及三级中断。
        当前精简版只保留检测结果、路径证据与时序特征。
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
      <div class="home-chart">
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

      <aside class="detection-index">
        <div class="section-heading">
          <h2>检测索引</h2>
          <span>06 classes</span>
        </div>
        <ol>
          <li><b>01</b><span>前缀劫持</span><small>起源 AS 偏离</small></li>
          <li><b>02</b><span>子前缀劫持</span><small>更具体前缀偏离</small></li>
          <li><b>03</b><span>路由泄漏</span><small>AS_PATH 关系异常</small></li>
          <li><b>04</b><span>前缀中断</span><small>可见性消失</small></li>
          <li><b>05</b><span>AS 中断</span><small>前缀聚合异常</small></li>
          <li><b>06</b><span>国家中断</span><small>AS 聚合异常</small></li>
        </ol>
      </aside>
    </section>

    <section>
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
  border-top: 1px solid var(--ink);
  border-bottom: 1px solid var(--ink);
}

.metric-ledger > div {
  min-height: 146px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 18px 20px;
  border-right: 1px solid var(--line);
}

.metric-ledger > div:last-child {
  border-right: 0;
}

.metric-ledger span,
.metric-ledger small {
  color: var(--muted);
  font: 10px/1.2 var(--mono);
  letter-spacing: 0.06em;
}

.metric-ledger strong {
  font-family: "Arial Narrow", "DIN Alternate", sans-serif;
  font-size: 52px;
  line-height: 1;
  letter-spacing: -0.04em;
}

.metric-ledger .metric-time {
  font: 700 16px/1.35 var(--mono);
  letter-spacing: -0.03em;
}

.home-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(290px, 0.65fr);
  gap: 24px;
}

.home-chart,
.detection-index {
  display: grid;
  align-content: start;
  gap: 16px;
}

.detection-index ol {
  list-style: none;
  margin: 0;
  padding: 0;
  background: var(--ink);
  color: var(--paper);
}

.detection-index li {
  display: grid;
  grid-template-columns: 36px 1fr auto;
  align-items: baseline;
  gap: 10px;
  padding: 15px 16px;
  border-bottom: 1px solid var(--line-dark);
}

.detection-index li:last-child {
  border-bottom: 0;
}

.detection-index b {
  color: var(--signal);
  font: 11px/1 var(--mono);
}

.detection-index span {
  font-weight: 700;
}

.detection-index small {
  color: #8f9aa1;
  font: 9px/1 var(--mono);
}

@media (max-width: 1000px) {
  .metric-ledger {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metric-ledger > div:nth-child(2) {
    border-right: 0;
  }

  .metric-ledger > div:nth-child(-n + 2) {
    border-bottom: 1px solid var(--line);
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
    min-height: 110px;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .metric-ledger > div:last-child {
    border-bottom: 0;
  }

  .metric-ledger strong {
    font-size: 42px;
  }

  .detection-index li {
    grid-template-columns: 30px 1fr;
  }

  .detection-index small {
    grid-column: 2;
  }
}
</style>
