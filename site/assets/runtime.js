export function collectorRuntime(collector, generatedAt, now = Date.now() / 1000) {
  const end = collector?.running === true ? generatedAt : collector?.stoppedAt;
  const started = collector?.startedAt;
  const uptime = Number.isFinite(started) && started > 0 && Number.isFinite(end) && end >= started ? end - started : null;
  const result = (status, label) => ({ status, label, uptime });
  if (!collector || !Number.isFinite(generatedAt) || now - generatedAt > 2100) return result('unknown', '状态待确认');
  if (collector.running === false) return result('bad', '已停止');
  if (collector.running !== true) return result('unknown', '状态待确认');
  if (collector.problems?.length || !Number.isFinite(collector.lastSample) ||
      generatedAt - collector.lastSample > Math.max(30, 3 * (collector.intervalSeconds || 0))) return result('warn', '采样异常');
  return result('good', '运行正常');
}

export function formatUptime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  seconds = Math.floor(seconds);
  if (seconds < 60) return `${seconds} 秒`;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return `${days ? days + ' 天 ' : ''}${hours} 时 ${minutes} 分`;
}
