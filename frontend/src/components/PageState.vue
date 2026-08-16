<script setup lang="ts">
defineProps<{
  kind?: 'loading' | 'empty' | 'error'
  title: string
  detail?: string
}>()

defineEmits<{
  retry: []
}>()
</script>

<template>
  <div class="page-state" :data-kind="kind || 'empty'">
    <span class="page-state-code">{{ kind === 'error' ? 'ERR' : kind === 'loading' ? 'SYNC' : 'NULL' }}</span>
    <div>
      <strong>{{ title }}</strong>
      <p v-if="detail">{{ detail }}</p>
    </div>
    <button v-if="kind === 'error'" class="text-action" type="button" @click="$emit('retry')">
      重新读取
    </button>
  </div>
</template>
