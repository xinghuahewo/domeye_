import { defineStore } from 'pinia';
import { Local } from '/@/utils/storage';
import type { CollectorContextState, CollectorOption } from '/@/types/collector';

const COLLECTOR_STORAGE_KEY = 'collectorContext';

const defaultCollectorOptions: CollectorOption[] = [
  {
    id: '9808',
    label: '9808',
    alias: '中国移动',
    collectorParam: 9808,
  },
  {
    id: '4134',
    label: '4134',
    alias: '中国电信',
    collectorParam: 4134,
  },
  {
    id: '4837',
    label: '4837',
    alias: '中国联通',
    collectorParam: 4837,
  },
  {
    id: 'global',
    label: 'global',
    alias: '全球',
    collectorParam: 'global',
  },
];

const getStoredCollectorId = () => {
  const stored = Local.get(COLLECTOR_STORAGE_KEY) as Partial<CollectorContextState> | null;
  if (!stored?.activeCollectorId) return 'global';
  return defaultCollectorOptions.some((item) => item.id === stored.activeCollectorId) ? stored.activeCollectorId : 'global';
};

export const useCollectorStore = defineStore('collector', {
  state: (): CollectorContextState => ({
    collectorOptions: defaultCollectorOptions,
    activeCollectorId: getStoredCollectorId(),
  }),
  getters: {
    activeCollector: (state) => state.collectorOptions.find((item) => item.id === state.activeCollectorId) || state.collectorOptions[3],
  },
  actions: {
    setActiveCollector(id: string) {
      if (!this.collectorOptions.some((item) => item.id === id)) return;
      this.activeCollectorId = id;
      Local.set(COLLECTOR_STORAGE_KEY, {
        activeCollectorId: id,
      });
    },
  },
});
