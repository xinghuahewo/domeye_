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
const defaultDates = recentDateRange(7)
const dataWindow = resolveDataWindow(import.meta.env)
// VITE_DATA_WINDOW_END 是闭区间终点，直接截取即为最后一个可选日期。
const minimumDate = dataWindow?.start.slice(0, 10)
const maximumDate = dataWindow?.end.slice(0, 10)

const DATE_PRESETS = [
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
  { label: '整个数据窗口', days: 0 },
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

// 在发请求前就把不可能有结果的范围拦下来，避免用户只看到一个后端 400。
const rangeError = computed(() => {
  const { startDate, endDate } = filters
  if (!startDate || !endDate) return '请选择完整的开始日期和结束日期'
  if (startDate > endDate) return '开始日期不能晚于结束日期'
  if (minimumDate && startDate < minimumDate) return `数据窗口自 ${minimumDate} 起，请调整开始日期`
  if (maximumDate && endDate > maximumDate) return `数据窗口至 ${maximumDate} 止，请调整结束日期`
  return ''
})

function applyPreset(days: number) {
  if (days === 0) {
    filters.startDate = minimumDate ?? filters.startDate
    filters.endDate = maximumDate ?? filters.endDate
  } else {
    const range = recentDateRange(days)
    filters.startDate = range.start
    filters.endDate = range.end
  }
  void load(true)
}

async function load(resetPage = false) {
  if (rangeError.value) {
    error.value = rangeError.value
    result.value = { data: [], totalPage: 0, recordCount: 0 }
    return
  }
  if (resetPage) page.value = 1
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
      // 用户习惯连 AS 前缀一起输入，这里统一去掉再做模糊匹配。
      attacked_as: filters.attackedAs.trim().replace(/^AS/i, '') || undefined,
      event_info: filters.keyword.trim() || undefined,
      date: eventDateTimeRange(filters.startDate, filters.endDate),
      sort_mode: 'start_timeB',
    })
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.eventType = ''
  filters.level = ''
  filters.country = 'all'
  filters.attackedCountry = ''
  filters.attackedAs = ''
  filters.keyword = ''
  filters.startDate = defaultDates.start
  filters.endDate = defaultDates.end
  void load(true)
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
      <div class="filter-actions">
        <button class="solid-action" type="submit" :disabled="Boolean(rangeError)">执行查询</button>
        <button class="ghost-action" type="button" @click="resetFilters">重置</button>
      </div>

      <div class="filter-presets">
        <span class="preset-label">快捷范围</span>
        <button
          v-for="preset in DATE_PRESETS"
          :key="preset.label"
          class="preset-chip"
          type="button"
          @click="applyPreset(preset.days)"
        >{{ preset.label }}</button>
        <span v-if="minimumDate && maximumDate" class="preset-hint">
          可选范围 {{ minimumDate }} ~ {{ maximumDate }}
        </span>
      </div>

      <p v-if="rangeError" class="filter-error" role="alert">{{ rangeError }}</p>
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

.filter-actions {
  display: flex;
  align-self: end;
  gap: 8px;
}

.filter-actions .solid-action {
  flex: 1;
}

.filter-actions .solid-action:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.ghost-action {
  height: 38px;
  padding: 0 14px;
  cursor: pointer;
  color: var(--muted);
  background: var(--paper);
  border: 1px solid #cfd7e1;
  border-radius: 5px;
  font-size: 11px;
}

.ghost-action:hover {
  color: var(--primary);
  border-color: #b8cdf5;
}

/* 快捷范围与错误提示始终占满整行，不参与上方的四列网格。 */
.filter-presets {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  grid-column: 1 / -1;
}

.preset-label {
  color: var(--muted);
  font-size: 10px;
  font-weight: 650;
}

.preset-chip {
  height: 26px;
  padding: 0 11px;
  cursor: pointer;
  color: var(--primary);
  background: var(--paper);
  border: 1px solid #b8cdf5;
  border-radius: 13px;
  font-size: 10px;
}

.preset-chip:hover {
  background: #eef4ff;
}

.preset-hint {
  margin-left: auto;
  color: var(--muted);
  font-size: 10px;
}

.filter-error {
  margin: 0;
  grid-column: 1 / -1;
  color: #b42318;
  font-size: 11px;
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

  .pagination {
    justify-content: space-between;
    padding: 10px 12px;
  }
}
</style>
