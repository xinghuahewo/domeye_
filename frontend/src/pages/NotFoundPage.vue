<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <article class="not-found">
    <span>404 / ROUTE NOT FOUND</span>
    <strong>这里没有<br />路由记录</strong>
    <p>请求路径不属于核心精简版。登录、报告、大屏和数据补全页面已从本项目移除。</p>
    <RouterLink to="/">返回核心态势 →</RouterLink>
  </article>
</template>

<style scoped>
.not-found {
  min-height: 62vh;
  display: grid;
  align-content: center;
  justify-items: start;
  gap: 22px;
  padding: 32px 0;
}

.not-found span {
  color: var(--signal);
  font: 700 11px/1 var(--mono);
  letter-spacing: 0.1em;
}

.not-found strong {
  font-family: "Arial Narrow", "DIN Alternate", sans-serif;
  font-size: clamp(64px, 12vw, 150px);
  line-height: 0.82;
  letter-spacing: -0.06em;
}

.not-found p {
  max-width: 580px;
  margin: 0;
  color: var(--muted);
  line-height: 1.7;
}

.not-found a {
  padding-bottom: 6px;
  border-bottom: 1px solid var(--ink);
  font: 12px/1 var(--mono);
}
</style>
