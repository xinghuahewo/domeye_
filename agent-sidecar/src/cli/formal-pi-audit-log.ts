import {
  accessSync,
  closeSync,
  constants,
  fstatSync,
  fsyncSync,
  lstatSync,
  openSync,
  readdirSync,
  realpathSync,
  unlinkSync,
  writeSync,
  type Stats,
} from 'node:fs'
import { isAbsolute, join, resolve } from 'node:path'

export const FORMAL_PI_AUDIT_RETENTION_DAYS = 30 as const
export const FORMAL_PI_AUDIT_FILE_PREFIX =
  'country-outage-pi-run-audit-v1-' as const
export const FORMAL_PI_AUDIT_MAX_LINE_BYTES = 64 * 1024

const MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000
const COMPONENT_FILENAME = new RegExp(
  `^${FORMAL_PI_AUDIT_FILE_PREFIX}(\\d{4}-\\d{2}-\\d{2})\\.jsonl$`,
)

export type FormalPiAuditLogErrorCode =
  | 'audit_directory_not_absolute'
  | 'audit_directory_not_normalized'
  | 'audit_directory_not_found'
  | 'audit_directory_symlink'
  | 'audit_directory_not_directory'
  | 'audit_directory_owner_mismatch'
  | 'audit_directory_permissions_invalid'
  | 'audit_directory_path_drift'
  | 'audit_nofollow_unavailable'
  | 'audit_component_name_invalid'
  | 'audit_component_file_unsafe'
  | 'audit_line_invalid'
  | 'audit_line_too_large'
  | 'audit_write_incomplete'

export class FormalPiAuditLogError extends Error {
  readonly code: FormalPiAuditLogErrorCode

  constructor(code: FormalPiAuditLogErrorCode, message: string) {
    super(message)
    this.name = 'FormalPiAuditLogError'
    this.code = code
  }
}

export interface FormalPiAuditLogOptions {
  directory: string
  now?: () => Date
}

export interface FormalPiAuditLog {
  readonly directory: string
  readonly retentionDays: typeof FORMAL_PI_AUDIT_RETENTION_DAYS
  writeLine(line: string): Promise<void>
}

function currentUserId(): number {
  if (typeof process.getuid !== 'function') {
    throw new FormalPiAuditLogError(
      'audit_directory_owner_mismatch',
      '正式 Pi 审计日志要求可校验当前运行用户的 POSIX uid',
    )
  }
  return process.getuid()
}

function permissionBits(stat: Stats): number {
  return stat.mode & 0o777
}

function assertOwnedByCurrentUser(
  stat: Stats,
  code:
    | 'audit_directory_owner_mismatch'
    | 'audit_component_file_unsafe',
  subject: string,
): void {
  if (stat.uid !== currentUserId()) {
    throw new FormalPiAuditLogError(
      code,
      `${subject}必须由当前 Sidecar 运行用户所有`,
    )
  }
}

function assertDirectory(directory: string): string {
  if (!isAbsolute(directory)) {
    throw new FormalPiAuditLogError(
      'audit_directory_not_absolute',
      'COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY 必须是绝对路径',
    )
  }
  if (resolve(directory) !== directory) {
    throw new FormalPiAuditLogError(
      'audit_directory_not_normalized',
      'COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY 必须是无 ..、无尾随分隔符的规范路径',
    )
  }

  let stat: Stats
  try {
    stat = lstatSync(directory)
  } catch (error) {
    if (
      error instanceof Error &&
      'code' in error &&
      error.code === 'ENOENT'
    ) {
      throw new FormalPiAuditLogError(
        'audit_directory_not_found',
        'COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY 必须由运维预先创建',
      )
    }
    throw error
  }
  if (stat.isSymbolicLink()) {
    throw new FormalPiAuditLogError(
      'audit_directory_symlink',
      '正式 Pi 审计目录禁止使用符号链接',
    )
  }
  if (!stat.isDirectory()) {
    throw new FormalPiAuditLogError(
      'audit_directory_not_directory',
      '正式 Pi 审计路径必须是普通目录',
    )
  }
  assertOwnedByCurrentUser(
    stat,
    'audit_directory_owner_mismatch',
    '正式 Pi 审计目录',
  )
  if (permissionBits(stat) !== 0o700) {
    throw new FormalPiAuditLogError(
      'audit_directory_permissions_invalid',
      '正式 Pi 审计目录权限必须是 0700',
    )
  }

  const canonicalDirectory = realpathSync.native(directory)
  if (canonicalDirectory !== directory) {
    throw new FormalPiAuditLogError(
      'audit_directory_path_drift',
      '正式 Pi 审计目录或其祖先路径包含别名/符号链接',
    )
  }
  try {
    accessSync(
      canonicalDirectory,
      constants.R_OK | constants.W_OK | constants.X_OK,
    )
  } catch {
    throw new FormalPiAuditLogError(
      'audit_directory_permissions_invalid',
      '正式 Pi 审计目录必须允许当前运行用户读、写和进入',
    )
  }
  return canonicalDirectory
}

function utcDateKey(now: Date): string {
  if (!Number.isFinite(now.getTime())) {
    throw new Error('正式 Pi 审计日志时钟无效')
  }
  return now.toISOString().slice(0, 10)
}

function dateKeyToUtcMilliseconds(dateKey: string): number {
  const [yearRaw, monthRaw, dayRaw] = dateKey.split('-')
  const year = Number(yearRaw)
  const month = Number(monthRaw)
  const day = Number(dayRaw)
  const milliseconds = Date.UTC(year, month - 1, day)
  if (
    !Number.isFinite(milliseconds) ||
    new Date(milliseconds).toISOString().slice(0, 10) !== dateKey
  ) {
    throw new FormalPiAuditLogError(
      'audit_component_name_invalid',
      `正式 Pi 审计目录包含无效日期文件名：${dateKey}`,
    )
  }
  return milliseconds
}

function assertSafeComponentFile(path: string): Stats {
  const stat = lstatSync(path)
  if (stat.isSymbolicLink() || !stat.isFile()) {
    throw new FormalPiAuditLogError(
      'audit_component_file_unsafe',
      '正式 Pi 审计组件文件必须是普通文件且不能是符号链接',
    )
  }
  assertOwnedByCurrentUser(
    stat,
    'audit_component_file_unsafe',
    '正式 Pi 审计组件文件',
  )
  if (permissionBits(stat) !== 0o600 || stat.nlink !== 1) {
    throw new FormalPiAuditLogError(
      'audit_component_file_unsafe',
      '正式 Pi 审计组件文件必须是 0600 且只能有一个硬链接',
    )
  }
  return stat
}

function retentionBoundaryUtcMilliseconds(now: Date): number {
  const today = dateKeyToUtcMilliseconds(utcDateKey(now))
  return (
    today -
    (FORMAL_PI_AUDIT_RETENTION_DAYS - 1) * MILLISECONDS_PER_DAY
  )
}

/**
 * 只清理本组件精确命名且早于“当前 UTC 日及前 29 日”的普通文件。
 * 非本组件文件永不删除；使用本组件前缀但不符合合同的近似名字会失败关闭。
 */
export function removeExpiredFormalPiAuditLogs(
  directory: string,
  now: Date,
): string[] {
  const canonicalDirectory = assertDirectory(directory)
  const boundary = retentionBoundaryUtcMilliseconds(now)
  const removed: string[] = []

  for (const entry of readdirSync(canonicalDirectory, {
    withFileTypes: true,
  })) {
    const match = entry.name.match(COMPONENT_FILENAME)
    if (!match) {
      if (entry.name.startsWith(FORMAL_PI_AUDIT_FILE_PREFIX)) {
        throw new FormalPiAuditLogError(
          'audit_component_name_invalid',
          `正式 Pi 审计目录包含危险的本组件近似文件名：${entry.name}`,
        )
      }
      continue
    }

    const dateKey = match[1]!
    const fileDate = dateKeyToUtcMilliseconds(dateKey)
    const filePath = join(canonicalDirectory, entry.name)
    assertSafeComponentFile(filePath)
    if (fileDate >= boundary) continue

    try {
      unlinkSync(filePath)
      removed.push(entry.name)
    } catch (error) {
      if (
        error instanceof Error &&
        'code' in error &&
        error.code === 'ENOENT'
      ) {
        continue
      }
      throw error
    }
  }
  return removed
}

function validateAuditLine(line: string): Buffer {
  if (
    !line.endsWith('\n') ||
    line.slice(0, -1).includes('\n') ||
    line.includes('\r')
  ) {
    throw new FormalPiAuditLogError(
      'audit_line_invalid',
      '正式 Pi 审计记录必须恰好占用一行并以 LF 结束',
    )
  }
  const buffer = Buffer.from(line, 'utf8')
  if (buffer.byteLength > FORMAL_PI_AUDIT_MAX_LINE_BYTES) {
    throw new FormalPiAuditLogError(
      'audit_line_too_large',
      `正式 Pi 审计单行不得超过 ${FORMAL_PI_AUDIT_MAX_LINE_BYTES} 字节`,
    )
  }
  return buffer
}

function writeAuditLine(
  directory: string,
  dateKey: string,
  line: string,
): void {
  const canonicalDirectory = assertDirectory(directory)
  const filename = `${FORMAL_PI_AUDIT_FILE_PREFIX}${dateKey}.jsonl`
  if (!COMPONENT_FILENAME.test(filename)) {
    throw new FormalPiAuditLogError(
      'audit_component_name_invalid',
      '正式 Pi 审计文件名不符合固定组件合同',
    )
  }
  const path = join(canonicalDirectory, filename)
  const buffer = validateAuditLine(line)

  try {
    assertSafeComponentFile(path)
  } catch (error) {
    if (
      !(
        error instanceof Error &&
        'code' in error &&
        error.code === 'ENOENT'
      )
    ) {
      throw error
    }
  }

  const noFollow = constants.O_NOFOLLOW
  if (!noFollow) {
    throw new FormalPiAuditLogError(
      'audit_nofollow_unavailable',
      '当前平台不支持 O_NOFOLLOW，正式 Pi 审计日志拒绝启动',
    )
  }
  const flags =
    constants.O_WRONLY |
    constants.O_CREAT |
    constants.O_APPEND |
    noFollow
  let descriptor: number | undefined
  try {
    descriptor = openSync(path, flags, 0o600)
    const opened = fstatSync(descriptor)
    const named = assertSafeComponentFile(path)
    if (opened.dev !== named.dev || opened.ino !== named.ino) {
      throw new FormalPiAuditLogError(
        'audit_directory_path_drift',
        '正式 Pi 审计文件在打开期间发生路径漂移',
      )
    }
    const written = writeSync(
      descriptor,
      buffer,
      0,
      buffer.byteLength,
      null,
    )
    if (written !== buffer.byteLength) {
      throw new FormalPiAuditLogError(
        'audit_write_incomplete',
        '正式 Pi 审计记录未能以单次追加完整写入',
      )
    }
    fsyncSync(descriptor)
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

export function createFormalPiAuditLog(
  options: FormalPiAuditLogOptions,
): FormalPiAuditLog {
  const directory = assertDirectory(options.directory)
  const now = options.now ?? (() => new Date())
  let lastRetentionSweep = utcDateKey(now())
  removeExpiredFormalPiAuditLogs(
    directory,
    new Date(`${lastRetentionSweep}T00:00:00Z`),
  )
  let writeQueue: Promise<void> = Promise.resolve()

  return {
    directory,
    retentionDays: FORMAL_PI_AUDIT_RETENTION_DAYS,
    writeLine(line: string): Promise<void> {
      const operation = writeQueue.then(() => {
        const current = now()
        const dateKey = utcDateKey(current)
        if (dateKey !== lastRetentionSweep) {
          removeExpiredFormalPiAuditLogs(directory, current)
          lastRetentionSweep = dateKey
        }
        writeAuditLine(directory, dateKey, line)
      })
      writeQueue = operation.catch(() => undefined)
      return operation
    },
  }
}
