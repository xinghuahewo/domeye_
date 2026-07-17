<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { getHealth } from '@/api/health'

const route = useRoute()
const healthy = ref(false)
const checkedAt = ref('')
let timer: number | undefined

const sectionCode = computed(() => {
  const codeByRoute: Record<string, string> = {
    home: '01 / 态势',
    events: '02 / 事件',
    'event-detail': '02A / 证据',
    features: '03 / 特征',
  }
  return codeByRoute[String(route.name)] || '00 / 系统'
})

async function checkHealth() {
  try {
    const payload = await getHealth()
    healthy.value = payload.status === 'ok'
    checkedAt.value = new Date(payload.time).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    healthy.value = false
    checkedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }
}

onMounted(() => {
  void checkHealth()
  timer = window.setInterval(checkHealth, 30_000)
})

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <RouterLink class="brand" to="/" aria-label="返回 Domeye Core 首页">
        <span class="brand-mark" aria-hidden="true"><i></i></span>
        <span class="brand-copy">
          <strong>Domeye</strong>
          <small>ROUTING ANOMALY CORE</small>
        </span>
      </RouterLink>

      <nav class="primary-nav" aria-label="主导航">
        <RouterLink to="/">核心态势</RouterLink>
        <RouterLink to="/events">异常事件</RouterLink>
        <RouterLink to="/features">路由特征</RouterLink>
      </nav>

      <div class="system-state" :class="{ 'is-offline': !healthy }" role="status">
        <span class="status-dot" aria-hidden="true"></span>
        <span>{{ healthy ? 'API ONLINE' : 'API OFFLINE' }}</span>
        <time v-if="checkedAt">{{ checkedAt }}</time>
      </div>
    </header>

    <div class="context-bar">
      <span>{{ sectionCode }}</span>
      <span>RR / BGP · CORE PROFILE</span>
      <span>UTC+08</span>
    </div>

    <main class="site-main">
      <RouterView v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>

    <footer class="site-footer">
      <span>Domeye Core / Route anomaly evidence console</span>
      <span>入口端口 28471 · API 28473</span>
    </footer>
  </div>
</template>
