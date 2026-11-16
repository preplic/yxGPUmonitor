export const TIME_ZONE = 'Asia/Shanghai';
export const TIME_ZONE_LABEL = '北京时间 (UTC+08:00)';

export function formatTime(value, options = {}) {
  if (!Number.isFinite(value)) return '—';
  return new Intl.DateTimeFormat('zh-CN', { timeZone: TIME_ZONE, month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false, ...options }).format(new Date(value * 1000));
}

export function formatFileDate(value) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE,
    year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value * 1000))
    .map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
