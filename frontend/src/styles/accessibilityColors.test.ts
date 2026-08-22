import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const globalCss = readFileSync(new URL('./main.css', import.meta.url), 'utf8')

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

describe('全局无障碍颜色合同', () => {
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

})
