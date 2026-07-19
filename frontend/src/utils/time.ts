const pad = (value: number) => String(value).padStart(2, '0')

type DataWindowEnv = {
  VITE_DATA_WINDOW_START?: string
  VITE_DATA_WINDOW_END?: string
}

export interface DataWindow {
  start: string
  end: string
}

const INPUT_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/

export const toBackendTime = (value: string): string => {
  const normalized = value.trim().replace('T', ' ')
  return normalized.length === 16 ? `${normalized}:00` : normalized
}

export const toInputTime = (date: Date): string =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`

export const parseInputTime = (value: string): Date | null => {
  const match = INPUT_TIME_PATTERN.exec(value)
  if (!match) return null

  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6] ?? '0')
  const parsed = new Date(year, month - 1, day, hour, minute, second, 0)
  if (
    parsed.getFullYear() !== year
    || parsed.getMonth() !== month - 1
    || parsed.getDate() !== day
    || parsed.getHours() !== hour
    || parsed.getMinutes() !== minute
    || parsed.getSeconds() !== second
  ) return null
  return parsed
}

export const resolveDataWindow = (env: DataWindowEnv): DataWindow | null => {
  const start = env.VITE_DATA_WINDOW_START?.trim() || ''
  const end = env.VITE_DATA_WINDOW_END?.trim() || ''
  if (start.length !== 19 || end.length !== 19) return null
  const parsedStart = parseInputTime(start)
  const parsedEnd = parseInputTime(end)
  if (!parsedStart || !parsedEnd || parsedStart.getTime() >= parsedEnd.getTime()) return null
  return { start, end }
}

const activeDataWindow = (env?: DataWindowEnv): DataWindow | null =>
  resolveDataWindow(env ?? import.meta.env)

export const recentRange = (hours = 24, env?: DataWindowEnv) => {
  const window = activeDataWindow(env)
  const windowStart = window ? parseInputTime(window.start) : null
  const end = window ? parseInputTime(window.end) as Date : new Date()
  const requestedStart = new Date(end.getTime() - hours * 60 * 60 * 1000)
  const start = windowStart && requestedStart.getTime() < windowStart.getTime()
    ? windowStart
    : requestedStart
  return {
    start: toInputTime(start),
    end: toInputTime(end),
  }
}

export const recentDateRange = (days = 7, env?: DataWindowEnv) => {
  const window = activeDataWindow(env)
  const windowStart = window ? parseInputTime(window.start) : null
  const end = window ? parseInputTime(window.end) as Date : new Date()
  const requestedStart = new Date(end.getTime() - days * 24 * 60 * 60 * 1000)
  const start = windowStart && requestedStart.getTime() < windowStart.getTime()
    ? windowStart
    : requestedStart
  const dateOnly = (value: Date) => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
  return { start: dateOnly(start), end: dateOnly(end) }
}
