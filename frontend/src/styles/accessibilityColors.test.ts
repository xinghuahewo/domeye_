import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const globalCss = readFileSync(new URL('./main.css', import.meta.url), 'utf8')
const reportWorkbench = readFileSync(
  new URL('../components/CountryOutageReportWorkbench.vue', import.meta.url),
  'utf8',
)

function rule(
  source: string,
  selector: string,
  occurrence: 'first' | 'last' = 'first',
): string {
  const selectorIndex = occurrence === 'last'
    ? source.lastIndexOf(selector)
    : source.indexOf(selector)
  expect(selectorIndex, `缺少样式选择器 ${selector}`).toBeGreaterThanOrEqual(0)
  const openBrace = source.indexOf('{', selectorIndex)
  const closeBrace = source.indexOf('}', openBrace)
  expect(openBrace, `${selector} 缺少左花括号`).toBeGreaterThan(selectorIndex)
  expect(closeBrace, `${selector} 缺少右花括号`).toBeGreaterThan(openBrace)
  return source.slice(openBrace + 1, closeBrace)
}

function property(block: string, name: string): string {
  const match = block.match(new RegExp(`(?:^|\\n)\\s*${name}:\\s*([^;]+);`))
  expect(match, `缺少样式属性 ${name}`).not.toBeNull()
  return match![1]!.trim()
}

function hexColor(value: string): string {
  const match = value.match(/#[0-9a-f]{6}\b/i)
  expect(match, `缺少六位十六进制颜色：${value}`).not.toBeNull()
  return match![0]!.toLowerCase()
}

function relativeLuminance(color: string): number {
  const channels = color
    .slice(1)
    .match(/.{2}/g)!
    .map((part) => Number.parseInt(part, 16) / 255)
    .map((channel) => (
      channel <= 0.04045
        ? channel / 12.92
        : ((channel + 0.055) / 1.055) ** 2.4
    ))
  return (
    0.2126 * channels[0]!
    + 0.7152 * channels[1]!
    + 0.0722 * channels[2]!
  )
}

function contrastRatio(foreground: string, background: string): number {
  const foregroundLuminance = relativeLuminance(foreground)
  const backgroundLuminance = relativeLuminance(background)
  const lighter = Math.max(foregroundLuminance, backgroundLuminance)
  const darker = Math.min(foregroundLuminance, backgroundLuminance)
  return (lighter + 0.05) / (darker + 0.05)
}

function expectContrast(
  foreground: string,
  background: string,
  minimum: number,
): void {
  expect(
    contrastRatio(foreground, background),
    `${foreground} 对 ${background} 的对比度必须至少为 ${minimum}:1`,
  ).toBeGreaterThanOrEqual(minimum)
}

describe('国家中断报告工作台无障碍颜色合同', () => {
  it('全局键盘焦点使用明暗双环，在浅色和深色表面均达到 3:1', () => {
    const block = rule(globalCss, 'button:focus-visible,')
    const lightRing = hexColor(property(block, 'outline'))
    const darkRing = hexColor(property(block, 'box-shadow'))

    expect(globalCss).toMatch(
      /input:focus-visible,\s*select:focus-visible,\s*textarea:focus-visible,\s*summary:focus-visible/,
    )
    expect(property(block, 'outline')).toMatch(/^3px\s+solid\b/)
    expectContrast(darkRing, '#ffffff', 3)
    expectContrast(lightRing, '#17212a', 3)
  })

  it('程序聚焦的深色页头和浅色报告标题分别使用高对比轮廓', () => {
    const publishedHeaderFocus = rule(
      reportWorkbench,
      '.published-header:focus {',
    )
    const reportTitleFocus = rule(
      reportWorkbench,
      '.report-title-block:focus {',
    )

    expectContrast(
      hexColor(property(publishedHeaderFocus, 'outline')),
      '#17212a',
      3,
    )
    expectContrast(
      hexColor(property(reportTitleFocus, 'outline')),
      '#fffdf8',
      3,
    )
  })

  it('外部来源登记和 URL 要求小字达到普通文本 4.5:1', () => {
    const sourceLabel = rule(
      reportWorkbench,
      '.external-request-ledger dt,',
    )
    const urlRequirement = rule(
      reportWorkbench,
      '.external-url-register-heading small {',
      'last',
    )

    expectContrast(
      hexColor(property(sourceLabel, 'color')),
      '#fffdf8',
      4.5,
    )
    expect(property(sourceLabel, 'font')).toMatch(/\b9px\//)
    expectContrast(
      hexColor(property(urlRequirement, 'color')),
      '#eee9df',
      4.5,
    )
    expect(property(urlRequirement, 'font-size')).toBe('9px')
  })
})
