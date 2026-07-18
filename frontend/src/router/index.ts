import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/pages/HomePage.vue'),
      meta: { title: '核心态势', section: '监测' },
    },
    {
      path: '/events',
      name: 'events',
      component: () => import('@/pages/EventsPage.vue'),
      meta: { title: '异常事件', section: '异常监测' },
    },
    {
      path: '/events/detail',
      name: 'event-detail',
      component: () => import('@/pages/EventDetailPage.vue'),
      meta: { title: '事件证据', section: '异常监测' },
    },
    {
      path: '/features',
      name: 'features',
      component: () => import('@/pages/FeaturesPage.vue'),
      meta: { title: '综合特征', section: '时序特征' },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/pages/NotFoundPage.vue'),
      meta: { title: '页面不存在', section: 'Domeye Core' },
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  document.title = `${String(to.meta.title || '工作台')} · Domeye Core`
})

export default router
