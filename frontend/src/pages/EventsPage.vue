<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getEvents } from '@/api/events'
import EventTable from '@/components/EventTable.vue'
import PageState from '@/components/PageState.vue'
import { CORE_EVENT_TYPES, type EventPage, type EventRow } from '@/types/api'
import { errorMessage } from '@/utils/normalize'
import { recentDateRange } from '@/utils/time'

const router = useRouter()
const defaultDates = recentDateRange(7)
const filters = reactive({
  eventType: '',
  level: '',
  country: 'all',
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

async function load(resetPage = false) {
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
      event_info: filters.keyword.trim() || undefined,
      date: filters.startDate && filters.endDate
        ? `${filters.startDate}_${filters.endDate}`
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
        <p class="eyebrow">Unified event ledger / Six core classes</p>
        <h1>异常事件账本</h1>
      </div>
      <p class="page-heading-copy">
        统一读取事件总表，并沿详情引用回到六类业务事实表。筛选只保留类型、等级、范围和摘要，不包含研判与通报流程。
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
        <span>开始日期</span>
        <input v-model="filters.startDate" type="date" />
      </label>
      <label>
        <span>结束日期</span>
        <input v-model="filters.endDate" type="date" />
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
    </form>

    <section>
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
  grid-template-columns: repeat(3, minmax(150px, 1fr)) minmax(210px, 1.4fr);
  gap: 1px;
  padding: 1px;
  background: var(--line);
  border: 1px solid var(--line);
}

.filter-console label {
  min-height: 76px;
  display: grid;
  align-content: center;
  gap: 8px;
  padding: 12px 14px;
  background: var(--paper);
}

.filter-console label span {
  color: var(--muted);
  font: 9px/1 var(--mono);
  letter-spacing: 0.07em;
}

.filter-console input,
.filter-console select {
  width: 100%;
  min-width: 0;
  height: 31px;
  padding: 0 4px;
  color: var(--ink);
  background: transparent;
  border: 0;
  border-bottom: 1px solid #8e9498;
  border-radius: 0;
  font-size: 13px;
}

.filter-console .solid-action {
  min-height: 76px;
}

.result-heading {
  margin-bottom: 16px;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 18px;
  margin-top: 18px;
  font: 11px/1 var(--mono);
}

.pagination button {
  min-height: 38px;
  cursor: pointer;
  padding: 0 16px;
  color: var(--paper);
  background: var(--ink);
  border: 0;
}

.pagination button:disabled {
  cursor: not-allowed;
  opacity: 0.35;
}

@media (max-width: 1000px) {
  .filter-console {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .filter-console {
    grid-template-columns: 1fr;
  }

  .pagination {
    justify-content: space-between;
  }
}
</style>
