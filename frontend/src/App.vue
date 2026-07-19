<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { getHealth } from '@/api/health'
import { resolveDataWindow } from '@/utils/time'

const route = useRoute()
const healthy = ref(false)
const healthChecked = ref(false)
const checkedAt = ref('')
const sidebarOpen = ref(false)
const isCompactViewport = ref(false)
const sidebarElement = ref<HTMLElement | null>(null)
const sidebarClose = ref<HTMLButtonElement | null>(null)
const menuTrigger = ref<HTMLButtonElement | null>(null)
const dataWindow = resolveDataWindow(import.meta.env)
const dataWindowLabel = dataWindow
  ? `${dataWindow.start.slice(0, 10)} 至 ${dataWindow.end.slice(0, 10)}`
  : '2026-02-01 至制品快照'
let timer: number | undefined
let compactViewportQuery: MediaQueryList | undefined

const currentPage = computed(() => String(route.meta.title || '系统状态'))
const currentSection = computed(() => String(route.meta.section || 'Domeye Core'))
const healthLabel = computed(() => {
  if (!healthChecked.value) return '正在检查'
  return healthy.value ? 'API 在线' : 'API 离线'
})
const healthShortLabel = computed(() => {
  if (!healthChecked.value) return 'API 检查中'
  return healthy.value ? 'API 正常' : 'API 异常'
})

function isRouteActive(...names: string[]) {
  return names.includes(String(route.name))
}

async function checkHealth() {
  try {
    const payload = await getHealth()
    healthy.value = payload.status === 'ok'
    checkedAt.value = new Date(payload.time).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    healthy.value = false
    checkedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } finally {
    healthChecked.value = true
  }
}

function openSidebar() {
  sidebarOpen.value = true
  void nextTick(() => sidebarClose.value?.focus())
}

function closeSidebar(restoreFocus = true) {
  const wasOpen = sidebarOpen.value
  sidebarOpen.value = false
  if (wasOpen && restoreFocus && isCompactViewport.value) {
    void nextTick(() => menuTrigger.value?.focus())
  }
}

function onKeydown(event: KeyboardEvent) {
  if (!sidebarOpen.value || !isCompactViewport.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closeSidebar()
    return
  }
  if (event.key !== 'Tab' || !sidebarElement.value) return

  const focusable = Array.from(
    sidebarElement.value.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => element.getClientRects().length > 0)
  if (focusable.length === 0) return

  const first = focusable[0]!
  const last = focusable[focusable.length - 1]!
  const active = document.activeElement
  if (event.shiftKey && (active === first || !sidebarElement.value.contains(active))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || !sidebarElement.value.contains(active))) {
    event.preventDefault()
    first.focus()
  }
}

function syncCompactViewport(event?: MediaQueryListEvent) {
  const compact = event?.matches ?? compactViewportQuery?.matches ?? false
  isCompactViewport.value = compact
  if (!compact && sidebarOpen.value) closeSidebar(false)
}

watch(() => route.fullPath, () => closeSidebar())
watch([sidebarOpen, isCompactViewport], ([open, compact]) => {
  document.body.classList.toggle('has-open-drawer', open && compact)
})

onMounted(() => {
  void checkHealth()
  timer = window.setInterval(checkHealth, 30_000)
  compactViewportQuery = window.matchMedia('(max-width: 900px)')
  syncCompactViewport()
  compactViewportQuery.addEventListener('change', syncCompactViewport)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
  compactViewportQuery?.removeEventListener('change', syncCompactViewport)
  window.removeEventListener('keydown', onKeydown)
  document.body.classList.remove('has-open-drawer')
})
</script>

<template>
  <div class="app-shell">
    <button
      v-if="sidebarOpen"
      class="sidebar-scrim"
      type="button"
      aria-label="关闭导航菜单"
      @click="closeSidebar()"
    ></button>

    <aside
      id="app-sidebar"
      ref="sidebarElement"
      class="sidebar"
      :class="{ 'is-open': sidebarOpen }"
      :aria-hidden="isCompactViewport && !sidebarOpen ? 'true' : undefined"
      :inert="isCompactViewport && !sidebarOpen"
    >
      <div class="sidebar-brand-row">
        <RouterLink class="brand" to="/" aria-label="返回 Domeye Core 首页" @click="closeSidebar()">
          <svg class="brand-mark" viewBox="0 0 40 40" aria-hidden="true">
            <circle cx="20" cy="20" r="17" fill="#fff4e8" />
            <path d="M9 23.5h22M13 16l7-4 7 4v8l-7 4-7-4z" fill="none" stroke="#f48120" stroke-width="2.3" stroke-linejoin="round" />
            <circle cx="13" cy="16" r="2.3" fill="#175cd3" />
            <circle cx="27" cy="16" r="2.3" fill="#35b6d4" />
            <circle cx="20" cy="28" r="2.3" fill="#0b57b7" />
          </svg>
          <span class="brand-copy">
            <strong>Domeye Core</strong>
            <small>Routing observatory</small>
          </span>
        </RouterLink>
        <button
          ref="sidebarClose"
          class="sidebar-close"
          type="button"
          aria-label="关闭导航菜单"
          @click="closeSidebar()"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <section class="nav-group">
          <p class="nav-group-label">监测</p>
          <RouterLink
            class="nav-link"
            :class="{ 'is-active': isRouteActive('home') }"
            to="/"
            @click="closeSidebar()"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 19V9m5 10V5m5 14v-7m5 7V8" />
            </svg>
            <span>核心态势</span>
          </RouterLink>
          <RouterLink
            class="nav-link"
            :class="{ 'is-active': isRouteActive('events', 'event-detail') }"
            to="/events"
            @click="closeSidebar()"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 3 3.7 18.2A1.2 1.2 0 0 0 4.8 20h14.4a1.2 1.2 0 0 0 1.1-1.8zM12 9v4m0 3.4v.1" />
            </svg>
            <span>异常事件</span>
          </RouterLink>
        </section>

        <section class="nav-group">
          <p class="nav-group-label">时序特征</p>
          <RouterLink
            class="nav-link"
            :class="{ 'is-active': isRouteActive('features') }"
            to="/features"
            @click="closeSidebar()"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 17 9 12l3 3 7-8M4 20h16" />
            </svg>
            <span>综合特征</span>
          </RouterLink>
          <span class="nav-link is-disabled" aria-disabled="true">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="8.5" /><path d="M3.8 12h16.4M12 3.5c2.2 2.3 3.3 5.2 3.3 8.5S14.2 18.2 12 20.5C9.8 18.2 8.7 15.3 8.7 12S9.8 5.8 12 3.5" />
            </svg>
            <span>国家特征</span>
            <small>下一阶段</small>
          </span>
          <span class="nav-link is-disabled" aria-disabled="true">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="6" cy="12" r="2.3" /><circle cx="18" cy="6" r="2.3" /><circle cx="18" cy="18" r="2.3" /><path d="m8.1 11 7.8-4m-7.8 6 7.8 4" />
            </svg>
            <span>AS 特征</span>
            <small>下一阶段</small>
          </span>
        </section>
      </nav>

      <div
        class="sidebar-status"
        :class="{ 'is-offline': healthChecked && !healthy, 'is-checking': !healthChecked }"
        role="status"
      >
        <div class="status-heading">
          <span class="status-dot" aria-hidden="true"></span>
          <strong>{{ healthLabel }}</strong>
        </div>
        <p>{{ checkedAt ? `最近检查 ${checkedAt}` : '正在检查服务状态' }}</p>
        <div class="data-baseline">
          <span>数据历史</span>
          <b>2026-02-01 起</b>
        </div>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="topbar-leading">
          <button
            ref="menuTrigger"
            class="menu-trigger"
            type="button"
            aria-label="打开导航菜单"
            aria-controls="app-sidebar"
            :aria-expanded="sidebarOpen"
            @click="openSidebar"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
          </button>
          <div class="breadcrumbs" aria-label="当前位置">
            <span>{{ currentSection }}</span>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" /></svg>
            <strong>{{ currentPage }}</strong>
          </div>
        </div>

        <div class="topbar-meta">
          <div class="data-window">
            <span>数据范围</span>
            <strong>{{ dataWindowLabel }}</strong>
          </div>
          <div
            class="topbar-health"
            :class="{ 'is-offline': healthChecked && !healthy, 'is-checking': !healthChecked }"
          >
            <span class="status-dot" aria-hidden="true"></span>
            <strong>{{ healthShortLabel }}</strong>
          </div>
        </div>
      </header>

      <main class="site-main">
        <RouterView v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" />
          </Transition>
        </RouterView>
      </main>

      <footer class="site-footer">
        <span>Domeye Core · 路由异常检测核心工作台</span>
        <span>Asia/Shanghai · UTC+08</span>
      </footer>
    </div>
  </div>
</template>
