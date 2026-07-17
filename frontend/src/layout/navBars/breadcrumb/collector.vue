<template>
  <div class="layout-collector" v-if="isHomeRoute">
    <span class="layout-collector__label">采集点</span>
    <el-select v-model="selectedCollectorId" size="small" class="layout-collector__select">
      <el-option
        v-for="item in collectorOptions"
        :key="item.id"
        :label="item.label"
        :value="item.id"
      >
        <div class="layout-collector__option">
          <span class="layout-collector__option-label">{{ item.label }}</span>
          <span class="layout-collector__option-alias">{{ item.alias }}</span>
        </div>
      </el-option>
    </el-select>
    <span class="layout-collector__time">更新时间 {{ currentCollectorTime }}</span>
  </div>
</template>

<script setup lang="ts" name="layoutBreadcrumbCollector">
import { computed, onMounted, reactive, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useRoute } from 'vue-router';
import request from '/@/utils/request';
import { useCollectorStore } from '/@/stores/collector';
import type { VantagePointState } from '/@/types/collector';

const route = useRoute();
const collectorStore = useCollectorStore();
const { collectorOptions, activeCollectorId } = storeToRefs(collectorStore);

const collectorCache = reactive<Record<string, VantagePointState>>({});

const isHomeRoute = computed(() => route.name === 'home');
const selectedCollectorId = computed({
  get: () => activeCollectorId.value,
  set: (value: string) => {
    collectorStore.setActiveCollector(value);
  },
});

const normalizeCollectorState = (value: unknown): VantagePointState => {
  if (Array.isArray(value)) return (value[0] as VantagePointState) || {};
  if (value && typeof value === 'object') return value as VantagePointState;
  return {};
};

const currentCollectorTime = computed(() => collectorCache[activeCollectorId.value]?.time || '-');

const loadCollectorState = async (collectorId: string) => {
  if (collectorCache[collectorId]?.time) return;
  const collector = collectorStore.collectorOptions.find((item) => item.id === collectorId);
  if (!collector) return;

  const result = await request({
    url: 'dashboard/vantage-points/state',
    method: 'get',
    params: {
      collector: collector.collectorParam,
    },
  });
  collectorCache[collectorId] = normalizeCollectorState(result);
};

watch(
  () => [isHomeRoute.value, activeCollectorId.value] as const,
  async ([homeRoute, collectorId]) => {
    if (!homeRoute) return;
    await loadCollectorState(collectorId);
  },
  { immediate: true }
);

onMounted(async () => {
  if (!isHomeRoute.value) return;
  await loadCollectorState(activeCollectorId.value);
});
</script>

<style scoped lang="scss">
.layout-collector {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: 16px;

  &__label {
    font-size: 12px;
    color: var(--next-bg-topBarColor);
    opacity: 0.8;
    white-space: nowrap;
  }

  &__select {
    width: 180px;
  }

  &__time {
    font-size: 12px;
    color: var(--next-bg-topBarColor);
    opacity: 0.8;
    white-space: nowrap;
  }

  :deep(.layout-collector__select .el-select__wrapper) {
    min-height: 32px;
    height: 32px;
    line-height: 32px;
  }

  &__option {
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }

  &__option-label {
    color: var(--el-text-color-primary);
    font-weight: 500;
  }

  &__option-alias {
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
}

@media (max-width: 768px) {
  .layout-collector {
    margin-left: 8px;

    &__label,
    &__time {
      display: none;
    }

    &__select {
      width: 132px;
    }
  }
}
</style>
