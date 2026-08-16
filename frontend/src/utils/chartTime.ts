export type TimeInput = string | number | Date

function timeParts(value: TimeInput, timezone: string) {
  const parsed = value instanceof Date ? value : new Date(value)
  if (!Number.isFinite(parsed.getTime())) return null
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(parsed)
  return Object.fromEntries(parts.map((part) => [part.type, part.value]))
}

export function formatChartTime(
  value: TimeInput,
  timezone = 'Asia/Shanghai',
  includeDate = false,
): string {
  const parts = timeParts(value, timezone)
  if (!parts) return String(value)
  const clock = `${parts.hour}:${parts.minute}`
  return includeDate
    ? `${parts.year}-${parts.month}-${parts.day} ${clock}`
    : `${parts.month}-${parts.day} ${clock}`
}
