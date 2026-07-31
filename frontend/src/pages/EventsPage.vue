<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getEvents } from '@/api/events'
import EventTable from '@/components/EventTable.vue'
import PageState from '@/components/PageState.vue'
import { CORE_EVENT_TYPES, type EventPage, type EventRow } from '@/types/api'
import { errorMessage } from '@/utils/normalize'
import { eventDateTimeRange, recentDateRange, resolveDataWindow } from '@/utils/time'

const router = useRouter()
const route = useRoute()
const dataWindow = resolveDataWindow(import.meta.env)
const defaultDates = dataWindow
  ? recentDateRange(7, import.meta.env)
  : { start: '', end: '' }
const minimumDate = dataWindow?.start.slice(0, 10)
const maximumDate = dataWindow?.end.slice(0, 10)
const DATE_PRESETS = [
  { id: 'recent-7', label: '近 7 天', days: 7 },
  { id: 'recent-30', label: '近 30 天', days: 30 },
  { id: 'full-window', label: '整个数据窗口', days: null },
] as const
const requestedEventType = typeof route.query.event_type === 'string'
  && CORE_EVENT_TYPES.includes(route.query.event_type as (typeof CORE_EVENT_TYPES)[number])
  ? route.query.event_type
  : ''
const requestedAttackedCountry = typeof route.query.attacked_country === 'string'
  ? route.query.attacked_country.trim()
  : ''
const requestedAttackedAs = typeof route.query.attacked_as === 'string'
  ? route.query.attacked_as.trim().replace(/^AS/i, '')
  : ''
const filters = reactive({
  eventType: requestedEventType,
  level: '',
  country: 'all',
  attackedCountry: requestedAttackedCountry,
  attackedAs: requestedAttackedAs,
  keyword: '',
  startDate: defaultDates.start,
  endDate: defaultDates.end,
  pageSize: 10,
})
const page = ref(1)
const result = ref<EventPage>({ data: [], totalPage: 0, recordCount: 0 })
const loading = ref(false)
const error = ref('')

const pageLabel = computed(() => {
  const total = Math.max(1, result.value.totalPage)
  return `${page.value.toString().padStart(2, '0')} / ${total.toString().padStart(2, '0')}`
})

const activeDatePreset = computed(() => DATE_PRESETS.find((preset) => {
  const range = preset.days === null
    ? { start: minimumDate, end: maximumDate }
    : recentDateRange(preset.days, import.meta.env)
  return filters.startDate === range.start && filters.endDate === range.end
})?.id ?? '')

function applyDatePreset(preset: (typeof DATE_PRESETS)[number]) {
  const range = preset.days === null
    ? { start: minimumDate ?? '', end: maximumDate ?? '' }
    : recentDateRange(preset.days, import.meta.env)
  filters.startDate = range.start
  filters.endDate = range.end
  void load(true)
}

async function load(resetPage = false) {
  if (resetPage) page.value = 1
  if (!dataWindow) {
    result.value = { data: [], totalPage: 0, recordCount: 0 }
    loading.value = false
    error.value = '缺少固定数据窗口配置，已阻止按当前日期查询。请重新构建并发布完整前端。'
    return
  }
  loading.value = true
  error.value = ''
  try {
    result.value = await getEvents({
      page_num: page.value,
      page_size: filters.pageSize,
      event_type: filters.eventType || undefined,
      level: filters.level || undefined,
      country: filters.country,
      attacked_country: filters.attackedCountry.trim() || undefined,
      attacked_as: filters.attackedAs.trim() || undefined,
      event_info: filters.keyword.trim() || undefined,
      date: filters.startDate && filters.endDate
        ? eventDateTimeRange(filters.startDate, filters.endDate)
        : undefined,
      sort_mode: 'start_timeB',
    })
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

function changePage(next: number) {
  if (next < 1 || next > Math.max(1, result.value.totalPage)) return
  page.value = next
  void load()
}

function openEvent(event: EventRow) {
  if (!event.detailUrl) return
  void router.push({ name: 'event-detail', query: { ref: event.detailUrl } })
}

onMounted(() => load())
</script>

<template>
  <article class="page events-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">异常监测 / Events</p>
        <h1>异常事件</h1>
      </div>
      <p class="page-heading-copy">
        在六类核心异常中按等级、范围、日期和摘要检索，点击事件可继续查看对应业务事实与路径证据。
      </p>
    </header>

    <form class="filter-console" @submit.prevent="load(true)">
      <label>
        <span>异常类型</span>
        <select v-model="filters.eventType">
          <option value="">全部六类</option>
          <option v-for="type in CORE_EVENT_TYPES" :key="type" :value="type">{{ type }}</option>
        </select>
      </label>
      <label>
        <span>风险等级</span>
        <select v-model="filters.level">
          <option value="">全部等级</option>
          <option value="high">高风险</option>
          <option value="middle">中风险</option>
          <option value="low">低风险</option>
        </select>
      </label>
      <label>
        <span>事件范围</span>
        <select v-model="filters.country">
          <option value="all">全部事件</option>
          <option value="domestic">国内相关</option>
          <option value="foreign">国外事件</option>
        </select>
      </label>
      <label class="filter-keyword">
        <span>摘要检索</span>
        <input v-model="filters.keyword" type="search" placeholder="输入 ASN、前缀或事件描述" />
      </label>
      <label>
        <span>受影响国家</span>
        <input v-model="filters.attackedCountry" type="search" placeholder="例如：中国" />
      </label>
      <label>
        <span>受影响 ASN</span>
        <input v-model="filters.attackedAs" type="search" placeholder="例如：3356" />
      </label>
      <label>
        <span>开始日期</span>
        <input v-model="filters.startDate" type="date" :min="minimumDate" :max="maximumDate" />
      </label>
      <label>
        <span>结束日期</span>
        <input v-model="filters.endDate" type="date" :min="minimumDate" :max="maximumDate" />
      </label>
      <label>
        <span>每页数量</span>
        <select v-model.number="filters.pageSize">
          <option :value="10">10</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
          <option :value="200">200</option>
        </select>
      </label>
      <button class="solid-action" type="submit">执行查询</button>
      <div class="filter-presets" role="group" aria-label="快捷时间范围">
        <span class="preset-label">快捷范围</span>
        <button
          v-for="preset in DATE_PRESETS"
          :key="preset.id"
          class="preset-chip"
          :class="{ 'is-active': activeDatePreset === preset.id }"
          type="button"
          :aria-pressed="activeDatePreset === preset.id"
          @click="applyDatePreset(preset)"
        >
          {{ preset.label }}
        </button>
        <span v-if="minimumDate && maximumDate" class="preset-hint">
          可选范围 {{ minimumDate }} — {{ maximumDate }}
        </span>
      </div>
    </form>

    <section class="data-panel">
      <div class="section-heading result-heading">
        <h2>查询结果</h2>
        <span>{{ result.recordCount.toLocaleString('zh-CN') }} records · page {{ pageLabel }}</span>
      </div>
      <PageState
        v-if="loading"
        kind="loading"
        title="正在读取月度事件表"
        detail="跨月范围由后端自动合并"
      />
      <PageState
        v-else-if="error"
        kind="error"
        title="事件查询失败"
        :detail="error"
        @retry="load()"
      />
      <PageState v-else-if="result.data.length === 0" title="所选范围内没有匹配事件" />
      <EventTable v-else :events="result.data" @select="openEvent" />

      <nav v-if="!loading && !error && result.totalPage > 1" class="pagination" aria-label="事件分页">
        <button type="button" :disabled="page <= 1" @click="changePage(page - 1)">← 上一页</button>
        <span>{{ pageLabel }}</span>
        <button type="button" :disabled="page >= result.totalPage" @click="changePage(page + 1)">下一页 →</button>
      </nav>
    </section>
  </article>
</template>

<style scoped>
.filter-console {
  display: grid;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
  gap: 13px;
  padding: 16px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.filter-console label {
  display: grid;
  gap: 7px;
}

.filter-console label span {
  color: var(--muted);
  font-size: 10px;
  font-weight: 650;
}

.filter-console input,
.filter-console select {
  width: 100%;
  min-width: 0;
  height: 38px;
  padding: 0 10px;
  color: var(--ink);
  background: #fff;
  border: 1px solid #cfd7e1;
  border-radius: 5px;
  font-size: 12px;
}

.filter-console .solid-action {
  align-self: end;
  height: 38px;
  min-height: 38px;
}

.filter-presets {
  display: flex;
  flex-wrap: wrap;
  grid-column: 1 / -1;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding-top: 3px;
  border-top: 1px solid #e4e9ef;
}

.preset-label {
  margin-right: 2px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 650;
}

.preset-chip {
  min-height: 28px;
  padding: 0 12px;
  cursor: pointer;
  color: var(--primary);
  background: #f7faff;
  border: 1px solid #b8cdf5;
  border-radius: 14px;
  font-size: 10px;
  font-weight: 650;
  transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
}

.preset-chip:hover {
  background: #eaf2ff;
  border-color: #7da4eb;
}

.preset-chip.is-active {
  color: #fff;
  background: var(--primary);
  border-color: var(--primary);
}

.preset-hint {
  margin-left: auto;
  color: var(--muted);
  font-size: 10px;
}

.result-heading {
  margin: 0;
  padding: 15px 18px;
  border-bottom: 0;
}

.data-panel {
  overflow: hidden;
}

.data-panel > .page-state {
  margin: 0 18px 18px;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
  min-height: 58px;
  margin: 0;
  padding: 10px 18px;
  border-top: 1px solid var(--line);
  font-size: 10px;
}

.pagination button {
  min-height: 34px;
  cursor: pointer;
  padding: 0 13px;
  color: var(--primary);
  background: var(--paper);
  border: 1px solid #b8cdf5;
  border-radius: 5px;
  font-size: 10px;
}

.pagination button:disabled {
  cursor: not-allowed;
  color: #98a2b3;
  background: #f2f4f7;
  border-color: var(--line);
}

@media (max-width: 1180px) {
  .filter-console {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .filter-console {
    grid-template-columns: 1fr;
    gap: 11px;
    padding: 13px;
  }

  .preset-hint {
    flex-basis: 100%;
    margin-left: 0;
  }

  .pagination {
    justify-content: space-between;
    padding: 10px 12px;
  }
}
</style>
