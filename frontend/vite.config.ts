import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'

type FixedDataProfile = {
  id?: unknown
  mode?: unknown
  window_start?: unknown
  snapshot_time?: unknown
}

const FIXED_TIME_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$/

function loadFixedDataWindow() {
  const profilePath = fileURLToPath(new URL('../config/data-profile.json', import.meta.url))
  const profile = JSON.parse(readFileSync(profilePath, 'utf8')) as FixedDataProfile
  if (
    profile.id !== 'feb-mar-2026'
    || profile.mode !== 'fixed'
    || typeof profile.window_start !== 'string'
    || typeof profile.snapshot_time !== 'string'
    || !FIXED_TIME_PATTERN.test(profile.window_start)
    || !FIXED_TIME_PATTERN.test(profile.snapshot_time)
  ) {
    throw new Error(`固定数据档无效，拒绝构建前端：${profilePath}`)
  }
  const start = profile.window_start.slice(0, 19)
  const end = profile.snapshot_time.slice(0, 19)
  if (start >= end) {
    throw new Error(`固定数据档时间顺序无效，拒绝构建前端：${profilePath}`)
  }
  return { start, end }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  const fixedDataWindow = loadFixedDataWindow()
  const dataWindowStart = process.env.VITE_DATA_WINDOW_START
    || env.VITE_DATA_WINDOW_START
    || fixedDataWindow.start
  const dataWindowEnd = process.env.VITE_DATA_WINDOW_END
    || env.VITE_DATA_WINDOW_END
    || fixedDataWindow.end
  const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:28473'
  const apiV2ProxyTarget = process.env.VITE_API_V2_PROXY_TARGET
    || env.VITE_API_V2_PROXY_TARGET
    || apiProxyTarget
  const agentControlProxyTarget = process.env.VITE_AGENT_CONTROL_PROXY_TARGET
    || env.VITE_AGENT_CONTROL_PROXY_TARGET
    || apiProxyTarget
  const proxy = {
    '^/api/v2/country-outage(?:/|$)': {
      target: agentControlProxyTarget,
      changeOrigin: true,
    },
    '/api/v1': {
      target: apiProxyTarget,
      changeOrigin: true,
    },
    '/api/v2': {
      target: apiV2ProxyTarget,
      changeOrigin: true,
    },
  }

  return {
    plugins: [vue()],
    define: {
      'import.meta.env.VITE_DATA_WINDOW_START': JSON.stringify(dataWindowStart),
      'import.meta.env.VITE_DATA_WINDOW_END': JSON.stringify(dataWindowEnd),
    },
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: '127.0.0.1',
      port: Number(env.VITE_PORT || 28471),
      open: env.VITE_OPEN === 'true',
      proxy,
    },
    preview: {
      host: '127.0.0.1',
      port: Number(env.VITE_PORT || 28471),
      proxy,
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      chunkSizeWarningLimit: 1200,
    },
  }
})
