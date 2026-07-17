const pad = (value: number) => String(value).padStart(2, '0')

export const toBackendTime = (value: string): string => {
  const normalized = value.trim().replace('T', ' ')
  return normalized.length === 16 ? `${normalized}:00` : normalized
}

export const toInputTime = (date: Date): string =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`

export const recentRange = (hours = 24) => {
  const end = new Date()
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000)
  return {
    start: toInputTime(start),
    end: toInputTime(end),
  }
}

export const recentDateRange = (days = 7) => {
  const end = new Date()
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000)
  const dateOnly = (value: Date) => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
  return { start: dateOnly(start), end: dateOnly(end) }
}
