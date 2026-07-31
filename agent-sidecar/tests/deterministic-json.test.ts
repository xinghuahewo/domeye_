import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  canonicalJsonSha256,
  canonicalJsonStringify,
  compareUnicodeCodePoints,
} from '../src/shared/deterministic-json.js'

test('code-point comparator 对私用区与 astral 字符保持确定顺序', () => {
  const values = ['😀', '\uE000', 'A', '𐀀', 'a']
  assert.deepEqual(values.sort(compareUnicodeCodePoints), [
    'A',
    'a',
    '\uE000',
    '𐀀',
    '😀',
  ])
})

test('canonical JSON 不受嵌套对象键插入顺序影响', () => {
  const left = {
    '😀': { z: 1, '\uE000': 2 },
    A: { second: 2, first: 1 },
  }
  const right = {
    A: { first: 1, second: 2 },
    '😀': { '\uE000': 2, z: 1 },
  }
  assert.equal(canonicalJsonStringify(left), canonicalJsonStringify(right))
  assert.equal(canonicalJsonSha256(left), canonicalJsonSha256(right))
  assert.equal(
    canonicalJsonStringify(left),
    '{"A":{"first":1,"second":2},"😀":{"z":1,"":2}}',
  )
})

test('canonical JSON 保留有业务语义的数组顺序', () => {
  assert.notEqual(
    canonicalJsonSha256({ evidence: ['first', 'second'] }),
    canonicalJsonSha256({ evidence: ['second', 'first'] }),
  )
})

test('价格证明、模型认证和活动账本禁止退回 locale 相关排序', () => {
  for (const relativePath of [
    '../src/pi/provider-price-attestation.js',
    '../src/pi/model-certification.js',
    '../src/pi/candidate-activity-ledger.js',
  ]) {
    const source = readFileSync(
      new URL(relativePath, import.meta.url),
      'utf8',
    )
    assert.doesNotMatch(source, /\.localeCompare\s*\(/)
    assert.match(source, /compareUnicodeCodePoints/)
  }
})
