import { createHash } from 'node:crypto'

/**
 * 按 Unicode code point 比较字符串，避免宿主 locale/ICU 改变内容身份。
 */
export function compareUnicodeCodePoints(
  left: string,
  right: string,
): number {
  const leftIterator = left[Symbol.iterator]()
  const rightIterator = right[Symbol.iterator]()
  while (true) {
    const leftValue = leftIterator.next()
    const rightValue = rightIterator.next()
    if (leftValue.done || rightValue.done) {
      if (leftValue.done && rightValue.done) return 0
      return leftValue.done ? -1 : 1
    }
    const difference =
      leftValue.value.codePointAt(0)! - rightValue.value.codePointAt(0)!
    if (difference !== 0) return difference
  }
}

export function canonicalizeJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalizeJson)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) =>
          compareUnicodeCodePoints(left, right),
        )
        .map(([key, item]) => [key, canonicalizeJson(item)]),
    )
  }
  return value
}

export function canonicalJsonStringify(
  value: unknown,
  space?: string | number,
): string {
  const serialized = JSON.stringify(canonicalizeJson(value), null, space)
  if (serialized === undefined) {
    throw new TypeError('内容身份只接受可序列化的 JSON 值')
  }
  return serialized
}

export function canonicalJsonSha256(value: unknown): string {
  return createHash('sha256')
    .update(canonicalJsonStringify(value))
    .digest('hex')
}
