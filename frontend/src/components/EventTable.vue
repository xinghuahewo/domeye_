<script setup lang="ts">
import type { EventRow } from '@/types/api'

defineProps<{
  events: EventRow[]
  compact?: boolean
}>()

defineEmits<{
  select: [event: EventRow]
}>()

const levelLabels = {
  high: '高',
  middle: '中',
  low: '低',
  unknown: '—',
}
</script>

<template>
  <div class="event-table-wrap" :class="{ 'is-compact': compact }">
    <table class="event-table">
      <thead>
        <tr>
          <th>等级</th>
          <th>异常类型 / 时间</th>
          <th>受影响对象</th>
          <th>异常来源</th>
          <th>事件摘要</th>
          <th><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="event in events" :key="event.key">
          <td data-label="等级">
            <span class="severity" :data-level="event.level">{{ levelLabels[event.level] }}</span>
          </td>
          <td data-label="异常类型 / 时间">
            <strong class="event-type">{{ event.type }}</strong>
            <time>{{ event.startTime || '时间未知' }}</time>
          </td>
          <td data-label="受影响对象">
            <span>{{ event.attackedAs || event.affectedPrefix || '—' }}</span>
            <small>{{ event.attackedOrg || event.attackedCountry }}</small>
          </td>
          <td data-label="异常来源">
            <span>{{ event.attackerAs || '—' }}</span>
            <small>{{ event.attackerOrg || event.attackerCountry }}</small>
          </td>
          <td data-label="事件摘要" class="event-summary">{{ event.summary || '暂无补充描述' }}</td>
          <td class="event-action">
            <button
              type="button"
              :disabled="!event.detailUrl"
              :aria-label="`查看${event.type}详情`"
              @click="$emit('select', event)"
            >
              →
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
