<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <article class="not-found">
    <span>404 / 页面不存在</span>
    <strong>未找到这个工作台页面</strong>
    <p>请求路径不属于核心精简版。登录、报告、大屏和数据补全页面已从本项目移除。</p>
    <RouterLink to="/">返回核心态势</RouterLink>
  </article>
</template>

<style scoped>
.not-found {
  min-height: min(560px, 68vh);
  display: grid;
  align-content: center;
  justify-items: center;
  gap: 16px;
  padding: 40px 28px;
  text-align: center;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.not-found span {
  color: var(--signal);
  font-size: 11px;
  font-weight: 750;
  letter-spacing: 0.06em;
}

.not-found strong {
  color: #17212b;
  font-size: clamp(28px, 5vw, 46px);
  line-height: 1.15;
  letter-spacing: -0.04em;
}

.not-found p {
  max-width: 580px;
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.65;
}

.not-found a {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  padding: 0 16px;
  color: #fff;
  background: var(--primary);
  border-radius: 6px;
  font-size: 12px;
  font-weight: 650;
  text-decoration: none;
}
</style>
