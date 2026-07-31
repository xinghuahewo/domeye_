import assert from 'node:assert/strict'
import {
  chmodSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import {
  createFormalPiAuditLog,
  FORMAL_PI_AUDIT_FILE_PREFIX,
  FormalPiAuditLogError,
  removeExpiredFormalPiAuditLogs,
} from '../src/cli/formal-pi-audit-log.js'

function temporaryAuditDirectory(prefix: string): {
  root: string
  directory: string
} {
  const root = realpathSync(mkdtempSync(join(tmpdir(), prefix)))
  const directory = join(root, 'audit')
  mkdirSync(directory, { mode: 0o700 })
  chmodSync(directory, 0o700)
  return { root, directory }
}

function componentPath(directory: string, dateKey: string): string {
  return join(
    directory,
    `${FORMAL_PI_AUDIT_FILE_PREFIX}${dateKey}.jsonl`,
  )
}

function writeComponentFile(
  directory: string,
  dateKey: string,
  content = '{}\n',
): string {
  const path = componentPath(directory, dateKey)
  writeFileSync(path, content, { mode: 0o600 })
  chmodSync(path, 0o600)
  return path
}

test('正式 Pi 审计日志按 UTC 日写入 0600 JSONL', async () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-write-',
  )
  try {
    const auditLog = createFormalPiAuditLog({
      directory,
      now: () => new Date('2026-07-29T23:59:59Z'),
    })
    const line =
      '{"event":"country_outage_pi_run_audit","audit":{"outcome":"accepted"}}\n'
    await auditLog.writeLine(line)

    const path = componentPath(directory, '2026-07-29')
    assert.equal(readFileSync(path, 'utf8'), line)
    assert.equal(lstatSync(path).mode & 0o777, 0o600)
    assert.equal(auditLog.retentionDays, 30)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('30 天留存包含当前 UTC 日及前 29 日，边界日保留且更早文件清理', () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-retention-',
  )
  try {
    writeComponentFile(directory, '2026-06-29')
    writeComponentFile(directory, '2026-06-30')
    writeComponentFile(directory, '2026-07-29')
    const foreign = join(directory, 'operator-note.txt')
    writeFileSync(foreign, '不得由组件清理\n', { mode: 0o600 })

    const removed = removeExpiredFormalPiAuditLogs(
      directory,
      new Date('2026-07-29T12:00:00Z'),
    )

    assert.deepEqual(removed, [
      `${FORMAL_PI_AUDIT_FILE_PREFIX}2026-06-29.jsonl`,
    ])
    assert.deepEqual(readdirSync(directory).sort(), [
      `${FORMAL_PI_AUDIT_FILE_PREFIX}2026-06-30.jsonl`,
      `${FORMAL_PI_AUDIT_FILE_PREFIX}2026-07-29.jsonl`,
      'operator-note.txt',
    ])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('长驻进程跨 UTC 日后重新执行 30 天清理', async () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-rollover-',
  )
  try {
    writeComponentFile(directory, '2026-06-30')
    let current = new Date('2026-07-29T23:59:59Z')
    const auditLog = createFormalPiAuditLog({
      directory,
      now: () => current,
    })
    assert.equal(
      readdirSync(directory).includes(
        `${FORMAL_PI_AUDIT_FILE_PREFIX}2026-06-30.jsonl`,
      ),
      true,
    )

    current = new Date('2026-07-30T00:00:01Z')
    await auditLog.writeLine('{"sequence":1}\n')

    assert.equal(
      readdirSync(directory).includes(
        `${FORMAL_PI_AUDIT_FILE_PREFIX}2026-06-30.jsonl`,
      ),
      false,
    )
    assert.equal(
      readFileSync(componentPath(directory, '2026-07-30'), 'utf8'),
      '{"sequence":1}\n',
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('同一进程并发追加保持每条 JSONL 完整且不丢失', async () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-concurrent-',
  )
  try {
    const auditLog = createFormalPiAuditLog({
      directory,
      now: () => new Date('2026-07-29T10:00:00Z'),
    })
    await Promise.all(
      Array.from({ length: 80 }, (_, sequence) =>
        auditLog.writeLine(`${JSON.stringify({ sequence })}\n`),
      ),
    )

    const records = readFileSync(
      componentPath(directory, '2026-07-29'),
      'utf8',
    )
      .trimEnd()
      .split('\n')
      .map((line) => JSON.parse(line) as { sequence: number })
    assert.equal(records.length, 80)
    assert.deepEqual(
      records.map((record) => record.sequence),
      Array.from({ length: 80 }, (_, sequence) => sequence),
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('缺失、相对、宽权限、符号链接和祖先路径漂移的目录均失败关闭', () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-directory-',
  )
  try {
    assert.throws(
      () => createFormalPiAuditLog({ directory: 'relative/audit' }),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_directory_not_absolute',
    )
    assert.throws(
      () =>
        createFormalPiAuditLog({
          directory: join(root, 'missing'),
        }),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_directory_not_found',
    )
    const ordinaryFile = join(root, 'ordinary-file')
    writeFileSync(ordinaryFile, '{}\n', { mode: 0o600 })
    assert.throws(
      () => createFormalPiAuditLog({ directory: ordinaryFile }),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_directory_not_directory',
    )

    chmodSync(directory, 0o750)
    assert.throws(
      () => createFormalPiAuditLog({ directory }),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_directory_permissions_invalid',
    )
    chmodSync(directory, 0o700)

    const linkedDirectory = join(root, 'audit-link')
    symlinkSync(directory, linkedDirectory)
    assert.throws(
      () => createFormalPiAuditLog({ directory: linkedDirectory }),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_directory_symlink',
    )

    const linkedParent = join(root, 'parent-link')
    symlinkSync(root, linkedParent)
    assert.throws(
      () =>
        createFormalPiAuditLog({
          directory: join(linkedParent, 'audit'),
        }),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_directory_path_drift',
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('创建后审计目录被替换为符号链接时拒绝路径漂移', async () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-runtime-drift-',
  )
  try {
    const auditLog = createFormalPiAuditLog({
      directory,
      now: () => new Date('2026-07-29T12:00:00Z'),
    })
    const originalDirectory = join(root, 'audit-original')
    const replacementDirectory = join(root, 'audit-replacement')
    mkdirSync(replacementDirectory, { mode: 0o700 })
    chmodSync(replacementDirectory, 0o700)
    renameSync(directory, originalDirectory)
    symlinkSync(replacementDirectory, directory)

    await assert.rejects(
      auditLog.writeLine('{"sequence":1}\n'),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_directory_symlink',
    )
    assert.deepEqual(readdirSync(replacementDirectory), [])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('本组件精确文件名的符号链接或宽权限文件不会被打开或清理', () => {
  const first = temporaryAuditDirectory(
    'domeye-formal-audit-file-link-',
  )
  try {
    const victim = join(first.root, 'victim.jsonl')
    writeFileSync(victim, '不得修改\n', { mode: 0o600 })
    symlinkSync(victim, componentPath(first.directory, '2026-07-29'))

    assert.throws(
      () =>
        createFormalPiAuditLog({
          directory: first.directory,
          now: () => new Date('2026-07-29T12:00:00Z'),
        }),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_component_file_unsafe',
    )
    assert.equal(readFileSync(victim, 'utf8'), '不得修改\n')
  } finally {
    rmSync(first.root, { recursive: true, force: true })
  }

  const second = temporaryAuditDirectory(
    'domeye-formal-audit-file-mode-',
  )
  try {
    const path = writeComponentFile(
      second.directory,
      '2026-07-29',
    )
    chmodSync(path, 0o640)
    assert.throws(
      () =>
        createFormalPiAuditLog({
          directory: second.directory,
          now: () => new Date('2026-07-29T12:00:00Z'),
        }),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_component_file_unsafe',
    )
  } finally {
    rmSync(second.root, { recursive: true, force: true })
  }
})

test('危险的本组件近似名字失败关闭，普通非组件文件不删除', () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-name-',
  )
  try {
    const dangerous = join(
      directory,
      `${FORMAL_PI_AUDIT_FILE_PREFIX}2026-07-29.jsonl.bak`,
    )
    writeFileSync(dangerous, '{}\n', { mode: 0o600 })
    const foreign = join(directory, 'foreign.jsonl')
    writeFileSync(foreign, '{}\n', { mode: 0o600 })

    assert.throws(
      () =>
        removeExpiredFormalPiAuditLogs(
          directory,
          new Date('2026-07-29T12:00:00Z'),
        ),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_component_name_invalid',
    )
    assert.equal(readFileSync(dangerous, 'utf8'), '{}\n')
    assert.equal(readFileSync(foreign, 'utf8'), '{}\n')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('本组件精确格式中的无效 UTC 日期失败关闭', () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-invalid-date-',
  )
  try {
    writeComponentFile(directory, '2026-02-30')
    assert.throws(
      () =>
        removeExpiredFormalPiAuditLogs(
          directory,
          new Date('2026-07-29T12:00:00Z'),
        ),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_component_name_invalid',
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('审计写入拒绝多行注入、CR 和超限单行', async () => {
  const { root, directory } = temporaryAuditDirectory(
    'domeye-formal-audit-line-',
  )
  try {
    const auditLog = createFormalPiAuditLog({
      directory,
      now: () => new Date('2026-07-29T12:00:00Z'),
    })
    await assert.rejects(
      auditLog.writeLine('{"ok":true}\n{"injected":true}\n'),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_line_invalid',
    )
    await assert.rejects(
      auditLog.writeLine('{"value":"carriage\\r"}\r\n'),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_line_invalid',
    )
    await assert.rejects(
      auditLog.writeLine(`${'x'.repeat(64 * 1024)}\n`),
      (error: unknown) =>
        error instanceof FormalPiAuditLogError &&
        error.code === 'audit_line_too_large',
    )
    assert.deepEqual(readdirSync(directory), [])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
