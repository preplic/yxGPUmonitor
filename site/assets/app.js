import { deriveKey, decrypt } from './crypto.js';
import { formatTime as date, formatFileDate, TIME_ZONE_LABEL } from './time.js';
import { SESSION_LIFETIME, loadSession, saveSession, clearSession } from './session.js';
import { collectorRuntime, formatUptime } from './runtime.js';
import { fetchSnapshot as fetchEnvelope } from './snapshot.js';

const $ = (query, root = document) => root.querySelector(query);
const icons = () => window.lucide?.createIcons({ attrs: { 'aria-hidden': 'true' } });
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = name => `<i data-lucide="${name}"></i>`;
const palette = ['#55d6aa', '#e9b96b', '#a899ef', '#6da8f5', '#f58d9a', '#7ac8d6'];
const views = { overview: '监控总览', gpus: 'GPU 设备', events: '运行记录' };
const gpuPeriods = [['15m', '15 分钟'], ['1h', '1 小时'], ['6h', '6 小时'], ['24h', '24 小时']];
const modelName = value => value.replace('NVIDIA ', '').replace('-SXM4-40GB', '');
const state = { data: null, key: null, envelope: null, user: '', expiresAt: 0, hours: 24,
  gpuWindow: '15m', eventFilter: 'all', charts: [] };
let refreshing = false;
let toastTimer;
let chartObserver;
let authGeneration = 0;
let authBusy = false;
let expiryTimer;

function fmt(value, digits = 1) {
  return Number.isFinite(Number(value)) && value !== null ? Number(value).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: digits }) : '—';
}

const gib = value => Number.isFinite(value) ? value / 1024 : null;

function gpuWindow() {
  return state.data.node.metricWindows?.[state.gpuWindow] || state.data.node.metricWindow;
}

function gpuCards() {
  const metrics = gpuWindow()?.metrics;
  return state.data.node.gpus.map(gpu => ({ ...gpu, ...metrics?.[gpu.uuid] }));
}

function metricPeriod() {
  const window = gpuWindow();
  return window ? `区间平均 · ${date(window.start)} - ${date(window.end)} · 北京时间` : '';
}

function duration(seconds) {
  if (seconds < 60) return `${fmt(Math.max(0, seconds), 0)} 秒`;
  if (seconds < 3600) return `${fmt(seconds / 60, 1)} 分钟`;
  return `${fmt(seconds / 3600, 1)} 小时`;
}

function currentView() { return Object.hasOwn(views, location.hash.slice(1)) ? location.hash.slice(1) : 'overview'; }
function theme(name) { return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim(); }
function availablePoints() { return state.data.history.filter(point => point.t <= state.data.node.lastSample + 1); }

function bounds() {
  const points = availablePoints();
  const last = points.at(-1);
  const requested = last.t - state.hours * 3600;
  const first = points.findLast(point => point.t <= requested) || points[0];
  return { first, last, start: Math.max(requested, first.t), points: points.filter(point => point.t >= first.t) };
}

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}

function rangeControls() {
  return `<div class="segmented" aria-label="统计时段">${[[1, '1 小时'], [6, '6 小时'], [24, '24 小时'], [168, '7 天']].map(([hours, label]) =>
    `<button data-range="${hours}" class="${state.hours === hours ? 'selected' : ''}" aria-pressed="${state.hours === hours}">${label}</button>`).join('')}</div>`;
}

function heading(title) {
  const data = state.data;
  const good = !data.node.problems.length;
  return `<div class="page-heading"><div><h1>${title}</h1><div class="page-subtitle"><span class="status-dot ${good ? '' : 'warn'}"></span><span>${esc(data.node.name)}</span><span class="separator">/</span><span>${esc(data.node.model)}</span></div></div>
    <div class="heading-controls">${rangeControls()}<button class="icon-button" data-export aria-label="导出 CSV" data-tooltip="导出 CSV">${icon('download')}</button></div></div>${notices()}`;
}

function notices() {
  const data = state.data;
  const old = Date.now() / 1000 - data.generatedAt > 2100;
  const issues = data.node.problems;
  if (old) return `<div class="notice">${icon('clock-alert')}数据已过期，最近发布于 ${date(data.generatedAt)}。</div>`;
  if (issues.length) return `<div class="notice">${icon('triangle-alert')}节点状态异常：${esc([...new Set(issues)].join(' · '))}</div>`;
  return '';
}

function metrics() {
  const data = state.data, gpus = gpuCards();
  const used = gpus.filter(gpu => gpu.occupied).length;
  const valid = gpus.filter(gpu => Number.isFinite(gpu.utilization));
  const avg = valid.length ? valid.reduce((sum, gpu) => sum + gpu.utilization, 0) / valid.length : null;
  const memoryCards = gpus.filter(gpu => Number.isFinite(gpu.memoryUsed));
  const memory = memoryCards.length ? memoryCards.reduce((sum, gpu) => sum + gpu.memoryUsed, 0) / 1024 : null;
  const total = gpus.reduce((sum, gpu) => sum + (gpu.memoryTotal || 0), 0) / 1024;
  const { first, last } = bounds();
  const hours = Number.isFinite(first.busySeconds) && Number.isFinite(last.busySeconds)
    ? Math.max(0, last.busySeconds - first.busySeconds) / 3600 : null;
  const rows = [
    ['microchip', '当前占卡', `${used}<small>/ ${gpus.length}</small>`, `${gpus.length - used} 张空闲 · ${gpus.filter(gpu => gpu.owners.length > 1).length} 张共享`],
    ['gauge', '平均 GPU 利用率', `${fmt(avg, 0)}<small>%</small>`, `平均显存 ${fmt(memory, 1)} / ${fmt(total, 0)} GiB`],
    ['chart-no-axes-combined', '区间占用卡时', `${fmt(hours, 2)}<small>GPU·h</small>`, `已观测 ${duration(last.t - first.t)}`],
    ['activity', '累计成功采样', `${fmt(data.node.sampleCount, 0)}<small>次</small>`, `节点采样周期 ${data.node.gpuInterval} 秒`]
  ];
  return `<div class="metrics">${rows.map(([symbol, label, value, meta]) => `<div class="metric"><div class="metric-label">${icon(symbol)}${label}</div><div class="metric-value">${value}</div><div class="metric-meta">${meta}</div></div>`).join('')}</div>`;
}

function chartPanel() {
  return `<section class="chart-tool full-chart"><div class="tool-heading"><h2>GPU 占用趋势 <span class="heading-note">/ 卡</span></h2></div>
    <div class="chart-legend"><span><i class="legend-dot" style="background:${palette[0]}"></i>物理卡占用</span></div><div id="occupancyChart" class="occupancy-chart" role="img" aria-label="GPU 占卡时间趋势"></div>
    <div class="plot-foot"><span>${date(bounds().first.t)} — ${date(bounds().last.t)}</span><span>${TIME_ZONE_LABEL}</span></div></section>`;
}

function gpuCard(gpu) {
  const util = Math.min(100, Math.max(0, gpu.utilization || 0));
  const memory = Math.min(100, Math.max(0, gpu.memoryUtilization || 0));
  const owners = gpu.intervalOwners || [];
  const emptyOwners = !gpu.intervalOwners ? '暂无区间记录' : gpu.intervalOccupied ? '归属待确认' : '无';
  return `<article class="gpu-card" data-gpu="${gpu.index}" tabindex="0" role="button" aria-label="查看 GPU ${gpu.index} 详情"><div class="gpu-card-top">${icon('microchip')}<strong>GPU ${gpu.index}</strong><span class="badge ${gpu.occupied ? 'good' : ''}">${gpu.owners.length > 1 ? '当前共享' : gpu.occupied ? '当前占用' : '当前空闲'}</span></div>
    <div class="gpu-model">${esc(modelName(gpu.model))} · ${fmt(gpu.memoryTotal / 1024, 0)} GiB</div>
    <div class="gpu-meter"><div class="meter-label"><span>平均利用率</span><b>${fmt(gpu.utilization, 0)}<small> %</small></b></div><div class="meter-track"><span style="width:${util}%"></span></div></div>
    <div class="gpu-meter"><div class="meter-label"><span>平均显存</span><b title="${fmt(gib(gpu.memoryUsed), 1)} / ${fmt(gib(gpu.memoryTotal), 0)} GiB">${fmt(gpu.memoryUtilization, 0)}<small> %</small></b></div><div class="meter-track memory"><span style="width:${memory}%"></span></div></div>
    <div class="gpu-owner-label">区间占用用户</div><div class="gpu-owners">${owners.length ? owners.map(owner => `<span class="user-tag">${icon('user-round')}${esc(owner.name)}</span>`).join('') : `<span>${emptyOwners}</span>`}</div>
    <div class="gpu-card-bottom"><span>${icon('thermometer')}${fmt(gpu.temperature, 0)} °C</span><span>${icon('zap')}${fmt(gpu.power, 0)} / ${fmt(gpu.powerLimit, 0)} W</span></div></article>`;
}

function gpuSection(all = false) {
  const controls = `<div class="segmented" aria-label="GPU 统计时段">${gpuPeriods.map(([key, label]) =>
    `<button data-gpu-window="${key}" class="${state.gpuWindow === key ? 'selected' : ''}" aria-pressed="${state.gpuWindow === key}" ${key !== '15m' && !state.data.node.metricWindows?.[key] ? 'disabled' : ''}>${label}</button>`).join('')}</div>`;
  return `<section><div class="section-heading"><h2>GPU 设备<span class="count">${state.data.node.gpus.length} 张</span></h2>${all ? '' : `<a href="#gpus" class="text-link">全部设备${icon('arrow-up-right')}</a>`}</div><div class="gpu-window-bar">${controls}<div class="gpu-period heading-note">${metricPeriod()}</div></div><div class="gpu-grid">${gpuCards().map(gpuCard).join('')}</div></section>`;
}

function empty(symbol, title, text = '') { return `<div class="empty-state">${icon(symbol)}<h3>${title}</h3>${text ? `<p>${text}</p>` : ''}</div>`; }

function overview() {
  return heading('集群概览') + metrics() + chartPanel() + gpuSection();
}

const eventTypes = { start: ['play', '启动', ''], stop: ['square', '停止', ''], gap: ['unplug', '采样缺口', 'text-warning'], sample_error: ['triangle-alert', '采样异常', 'text-danger'] };
function eventsTable() {
  const since = state.data.node.lastSample - state.hours * 3600;
  const rows = state.data.events.filter(row => row.source === 'gpu' && row.t >= since && (state.eventFilter === 'all' || row.type === state.eventFilter));
  return rows.length ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>事件</th><th>来源</th><th>详情</th><th class="number">时长</th></tr></thead><tbody>${rows.map(row => {
    const [symbol, label, color] = eventTypes[row.type] || ['info', row.type, ''];
    const messages = { collector_restart: '采集器重启', sampling_interrupted: '采样间隔中断', sampling_interval_exceeded: '超过有效采样间隔' };
    return `<tr><td class="muted">${date(row.t, { second: '2-digit' })}</td><td><span class="event-type ${color}">${icon(symbol)}${label}</span></td><td><span class="badge">GPU 采集</span></td><td class="event-message">${esc(messages[row.message] || row.message || (row.type === 'start' ? '采集服务启动' : '采集服务停止'))}</td><td class="number">${row.seconds ? duration(row.seconds) : '—'}</td></tr>`;
  }).join('')}</tbody></table></div><div class="table-foot"><span>${rows.length} 条记录</span><span>${TIME_ZONE_LABEL}</span></div>` : empty('circle-check', '该时段暂无运行事件');
}

function eventsPage() {
  return heading('运行记录') + `<div id="runtimeStatus">${runtimeSection()}</div><div class="section-heading"><h2>历史事件</h2><select id="eventFilter" aria-label="筛选事件"><option value="all">全部事件</option>${Object.entries(eventTypes).map(([key, [, label]]) => `<option value="${key}" ${state.eventFilter === key ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div id="tableContent">${eventsTable()}</div>`;
}

function runtimeSection() {
  return `<section class="runtime-section" aria-label="当前运行状况"><div class="section-heading"><h2>当前运行状况</h2><span class="heading-note">快照时间 ${date(state.data.generatedAt, { second: '2-digit' })} · 北京时间</span></div><div class="runtime-schedule"><span>节点采样 ${state.data.node.gpuInterval} 秒 / 次</span><span>网站数据更新 ${fmt((state.data.publishInterval || 900) / 60, 0)} 分钟 / 次</span></div>${[['gpu', 'microchip', 'GPU 采集']].map(([source, symbol, name]) => {
    const collector = state.data.collectors?.[source];
    const runtime = collectorRuntime(collector, state.data.generatedAt);
    return `<div class="runtime-row" data-collector="${source}" data-status="${runtime.status}"><div class="runtime-service"><div class="runtime-name">${icon(symbol)}<h3>${name}</h3><span class="runtime-state ${runtime.status}">${runtime.label}</span></div><span class="heading-note">${collector?.intervalSeconds ? `每 ${Number(collector.intervalSeconds).toLocaleString('en-US', { maximumFractionDigits: 3 })} 秒采样` : '采样周期未知'}</span></div>
      <div class="runtime-stat"><span class="runtime-label">本次运行时长</span><strong class="runtime-uptime">${formatUptime(runtime.uptime)}</strong><span class="runtime-meta">启动 ${date(collector?.startedAt, { second: '2-digit' })}</span></div>
      <div class="runtime-stat"><span class="runtime-label">快照内最近成功采样</span><strong class="runtime-sample">${date(collector?.lastSample, { second: '2-digit' })}</strong><span class="runtime-meta">北京时间</span></div>
      <div class="runtime-stat"><span class="runtime-label">累计成功采样</span><strong class="runtime-count">${collector ? fmt(collector.sampleCount, 0) : '—'}<small>次</small></strong><span class="runtime-meta">${collector?.stoppedAt ? `停止 ${date(collector.stoppedAt, { second: '2-digit' })}` : ''}</span></div></div>`;
  }).join('')}</section>`;
}

function updateRuntimeStatus() {
  const element = $('#runtimeStatus');
  if (state.data && element) { element.innerHTML = runtimeSection(); icons(); }
}

function disposeCharts() {
  chartObserver?.disconnect();
  state.charts.forEach(chart => chart.dispose());
  state.charts = [];
}

function plot(element, series, maximum) {
  if (!element) return;
  const chart = echarts.init(element, null, { renderer: 'canvas' });
  const width = element.clientWidth;
  chart.setOption({ animationDuration: 350, textStyle: { fontFamily: 'YX Sans, sans-serif' },
    color: palette, grid: { top: 20, right: 19, bottom: 31, left: width < 420 ? 32 : 39 },
    tooltip: { trigger: 'axis', backgroundColor: theme('tooltip'), borderColor: theme('border-strong'),
      textStyle: { color: theme('tooltip-text'), fontSize: 11 }, padding: [10, 13],
      formatter: params => `${date(params[0].value[0] / 1000)}<br>${params.map(item => `<span style="display:inline-block;width:6px;height:6px;background:${item.color};margin-right:6px"></span>${esc(item.seriesName)}<b style="margin-left:20px">${item.value[1]}</b>`).join('<br>')}` },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: theme('border') } }, axisTick: { show: false },
      axisLabel: { color: theme('muted'), fontSize: 9, hideOverlap: true, formatter: value => date(value / 1000, { month: undefined, day: undefined }) }, splitLine: { show: false }, min: bounds().first.t * 1000, max: bounds().last.t * 1000 },
    yAxis: { type: 'value', min: 0, max: maximum, minInterval: 1, axisLine: { show: false },
      axisLabel: { color: theme('muted'), fontSize: 9 }, splitLine: { lineStyle: { color: theme('border'), type: 'dashed' } } },
    series: series.map(item => ({ type: 'line', step: 'end', showSymbol: false, symbolSize: 4,
      emphasis: { focus: 'series' }, lineStyle: { width: 1.8 }, ...item })) });
  state.charts.push(chart);
  chartObserver ||= new ResizeObserver(entries => { for (const entry of entries) echarts.getInstanceByDom(entry.target)?.resize(); });
  chartObserver.observe(element);
}

function drawMainChart() {
  const element = $('#occupancyChart');
  if (!element) return;
  const points = bounds().points;
  const series = [{ name: '物理卡占用', areaStyle: { opacity: 0.13 }, itemStyle: { color: palette[0] }, data: points.map(point => [point.t * 1000, point.busy]) }];
  const max = Math.max(state.data.node.expectedGpus, ...points.map(point => point.busy));
  plot(element, series, max);
}

function render() {
  if (!state.data) return;
  disposeCharts();
  const view = currentView();
  document.title = `${views[view]} · YX GPU Monitor`;
  $('#breadcrumbView').textContent = views[view];
  document.querySelectorAll('[data-view]').forEach(link => {
    link.classList.toggle('active', link.dataset.view === view);
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  $('#main').innerHTML = view === 'overview' ? overview() : view === 'gpus' ? heading('GPU 设备') + metrics() + gpuSection(true) : eventsPage();
  $('#sidebarNode').textContent = state.data.node.name;
  $('#sidebarModel').textContent = `${modelName(state.data.node.model)} × ${state.data.node.gpus.length}`;
  $('#navGpuCount').textContent = state.data.node.gpus.length;
  const bad = state.data.node.problems.length;
  $('#sidebarStatus').classList.toggle('warn', Boolean(bad));
  $('#eventDot').hidden = !state.data.events.some(event => event.source === 'gpu' && event.type === 'sample_error');
  $('#footerStatus').textContent = `${bad ? '采样异常' : '采样正常'} · ${fmt(state.data.node.sampleCount, 0)} 次 GPU 采样`;
  $('#footerTime').textContent = `观测起点 ${date(state.data.node.startedAt)} · ${TIME_ZONE_LABEL}`;
  $('#freshness').textContent = `更新时间 ${date(state.data.generatedAt, { month: undefined, day: undefined })} · 北京时间`;
  icons();
  drawMainChart();
}

function closeMenu() { $('#sidebar').classList.remove('open'); $('#sidebarScrim').hidden = true; }

function gpuDetail(index) {
  const gpu = gpuCards().find(item => item.index === Number(index));
  if (!gpu) return;
  const owners = gpu.intervalOwners || [];
  const emptyOwners = !gpu.intervalOwners ? '暂无区间记录' : gpu.intervalOccupied ? '归属待确认' : '无';
  $('#detailContent').innerHTML = `<div class="dialog-heading"><h2>GPU ${gpu.index} <span class="heading-note">${esc(gpu.model)}</span></h2><button class="icon-button" data-close-dialog aria-label="关闭详情" data-tooltip="关闭">${icon('x')}</button></div><p class="heading-note">${metricPeriod()}</p><dl class="detail-values"><div><dt>平均利用率</dt><dd>${fmt(gpu.utilization, 0)} %</dd></div><div><dt>平均显存占用</dt><dd>${fmt(gib(gpu.memoryUsed), 1)} GiB</dd></div><div><dt>平均显存占用率</dt><dd>${fmt(gpu.memoryUtilization, 0)} %</dd></div><div><dt>有效采样</dt><dd>${fmt(gpu.metricSamples, 0)} 次</dd></div><div><dt>当前计算进程</dt><dd>${gpu.processes}</dd></div></dl><div class="section-heading"><h2>区间占用用户</h2></div><div class="scope-tags">${owners.map(owner => `<span class="badge">${esc(owner.name)} · UID ${owner.uid}</span>`).join('') || `<span class="heading-note">${emptyOwners}</span>`}</div><dl class="detail-values"><div><dt>温度</dt><dd>${fmt(gpu.temperature, 0)} °C</dd></div><div><dt>功耗</dt><dd>${fmt(gpu.power, 0)} W</dd></div><div><dt>功率上限</dt><dd>${fmt(gpu.powerLimit, 0)} W</dd></div></dl><p class="detail-uuid">${esc(gpu.uuid)}</p>`;
  $('#detailDialog').showModal();
  icons();
}

function exportCsv() {
  let rows;
  if (currentView() === 'gpus') rows = [['GPU', '型号', '平均利用率%', '平均显存占用率%', '平均显存MiB', '区间开始（北京时间）', '区间结束（北京时间）', '温度°C', '功耗W', '区间占用账号'], ...gpuCards().map(gpu => [gpu.index, gpu.model, gpu.utilization, gpu.memoryUtilization, gpu.memoryUsed, date(gpuWindow()?.start, { second: '2-digit' }), date(gpuWindow()?.end, { second: '2-digit' }), gpu.temperature, gpu.power, (gpu.intervalOwners || []).map(owner => owner.name).join(';')])];
  else if (currentView() === 'events') rows = [['时间（北京时间）', '来源', '类型', '详情', '秒'], ...state.data.events.filter(row => row.source === 'gpu').map(row => [date(row.t), row.source, row.type, row.message || '', row.seconds || ''])];
  else rows = [['时间（北京时间）', '占用GPU数', '空闲GPU数', '累计占用卡时/GPU·h'], ...bounds().points.map(point => [date(point.t, { second: '2-digit' }), point.busy, Math.max(0, state.data.node.gpus.length - point.busy), Number.isFinite(point.busySeconds) ? point.busySeconds / 3600 : null])];
  const csv = '\ufeff' + rows.map(row => row.map(value => `"${String(value ?? '').replace(/"/g, '""')}"`).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `yxgpu-${currentView()}-${formatFileDate(state.data.generatedAt)}.csv`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function logout(message = '') {
  authGeneration++;
  clearTimeout(expiryTimer);
  void clearSession();
  closeMenu();
  disposeCharts();
  if ($('#detailDialog').open) $('#detailDialog').close();
  $('#detailContent').replaceChildren();
  state.data = null;
  state.key = null;
  state.envelope = null;
  state.user = '';
  state.expiresAt = 0;
  state.gpuWindow = '15m';
  $('#main').replaceChildren();
  for (const id of ['sessionUser', 'sidebarNode', 'sidebarModel', 'footerStatus', 'footerTime', 'freshness']) $(`#${id}`).textContent = '';
  $('#avatar').textContent = 'U';
  clearTimeout(toastTimer);
  $('#toast').hidden = true;
  $('#toast').textContent = '';
  $('#app').hidden = true;
  $('#authScreen').hidden = false;
  $('#loginForm').reset();
  $('#password').type = 'password';
  $('#showPassword').setAttribute('aria-label', '显示密码');
  $('#showPassword').dataset.tooltip = '显示密码';
  $('#showPassword').innerHTML = icon('eye');
  $('#loginError').textContent = message;
  document.title = 'YX GPU Monitor';
  icons();
  $('#username').focus();
}

async function refresh() {
  if (!state.key || refreshing) return;
  if (Date.now() >= state.expiresAt) { logout('会话已到期，请重新登录'); return; }
  const generation = authGeneration;
  const { key, user, envelope: previous } = state;
  refreshing = true;
  $('#refresh').classList.add('spin');
  try {
    const envelope = await fetchEnvelope();
    if (generation !== authGeneration) return;
    if (envelope.salt !== previous.salt) { logout('访问凭据已更新，请重新登录'); return; }
    if (envelope.ciphertext !== previous.ciphertext) {
      const data = await decrypt(envelope, key);
      if (generation !== authGeneration) return;
      if (!data.allowedUsers.includes(user)) { logout('当前账号已无访问权限'); return; }
      if ($('#detailDialog').open) $('#detailDialog').close();
      state.data = data;
      state.envelope = envelope;
      render();
    }
  } catch (error) {
    if (generation !== authGeneration) return;
    if (error.name === 'OperationError') logout('访问凭据已变更，请重新登录');
    else toast(error.message);
  }
  finally { refreshing = false; $('#refresh').classList.remove('spin'); }
}

function loginBusy(busy, label = '正在验证') {
  authBusy = busy;
  $('#loginForm').setAttribute('aria-busy', String(busy));
  const button = $('#loginForm button[type=submit]');
  button.disabled = busy;
  button.innerHTML = `${icon(busy ? 'loader-circle' : 'arrow-right')}<span>${busy ? label : '进入控制台'}</span>`;
  icons();
}

function unlock(data, envelope, session) {
  if (Date.now() >= session.expiresAt) { logout('会话已到期，请重新登录'); return; }
  state.key = session.key;
  state.user = session.user;
  state.expiresAt = session.expiresAt;
  state.data = data;
  state.envelope = envelope;
  clearTimeout(expiryTimer);
  expiryTimer = setTimeout(() => logout('会话已到期，请重新登录'), session.expiresAt - Date.now());
  $('#password').value = '';
  $('#sessionUser').textContent = session.user;
  $('#avatar').textContent = session.user.slice(0, 1).toUpperCase();
  $('#authScreen').hidden = true;
  $('#app').hidden = false;
  render();
  $('#main').focus({ preventScroll: true });
}

async function restoreSession() {
  const generation = ++authGeneration;
  loginBusy(true, '正在恢复会话');
  try {
    const session = await loadSession();
    if (!session || generation !== authGeneration) return;
    const envelope = await fetchEnvelope();
    if (generation !== authGeneration) return;
    if (session.salt !== envelope.salt) { logout('访问凭据已更新，请重新登录'); return; }
    const data = await decrypt(envelope, session.key);
    if (generation !== authGeneration) return;
    if (!data.allowedUsers.includes(session.user)) { logout('当前账号已无访问权限'); return; }
    unlock(data, envelope, session);
  } catch (error) {
    if (generation !== authGeneration) return;
    if (error.name === 'OperationError') logout('访问凭据已变更，请重新登录');
    else $('#loginError').textContent = error.message;
  } finally { loginBusy(false); }
}

$('#loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (authBusy) return;
  const generation = ++authGeneration;
  const user = $('#username').value.trim();
  let key;
  loginBusy(true);
  $('#loginError').textContent = '';
  icons();
  try {
    const envelope = await fetchEnvelope();
    key = await deriveKey(envelope, $('#password').value);
    $('#password').value = '';
    const data = await decrypt(envelope, key);
    if (!data.allowedUsers.includes(user)) throw new DOMException('Invalid credentials', 'OperationError');
    if (generation !== authGeneration) return;
    const session = { user, key, salt: envelope.salt, expiresAt: Date.now() + SESSION_LIFETIME };
    const saved = await saveSession(session);
    if (generation !== authGeneration) return;
    unlock(data, envelope, session);
    if (!saved) toast('浏览器未允许保存会话，刷新后需重新登录');
  } catch (error) {
    key = null;
    $('#password').value = '';
    $('#loginError').textContent = error.name === 'OperationError' ? '用户名或密码不正确' : error.message;
  } finally { loginBusy(false); }
});

$('#showPassword').addEventListener('click', () => {
  const show = $('#password').type === 'password';
  $('#password').type = show ? 'text' : 'password';
  $('#showPassword').setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
  $('#showPassword').dataset.tooltip = show ? '隐藏密码' : '显示密码';
  $('#showPassword').innerHTML = icon(show ? 'eye-off' : 'eye');
  icons();
});
$('#logout').addEventListener('click', () => logout());
$('#refresh').addEventListener('click', refresh);
$('#themeToggle').addEventListener('click', () => {
  document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('yxgpu-theme', document.documentElement.dataset.theme); } catch { /* Theme persistence is optional. */ }
  if ($('#detailDialog').open) $('#detailDialog').close();
  render();
});
$('#mobileMenu').addEventListener('click', () => { $('#sidebar').classList.toggle('open'); $('#sidebarScrim').hidden = !$('#sidebar').classList.contains('open'); });
$('#sidebarScrim').addEventListener('click', closeMenu);
$('#main').addEventListener('click', event => {
  const target = event.target.closest('[data-range],[data-gpu-window],[data-gpu],[data-export]');
  if (!target) return;
  if (target.dataset.range) { state.hours = Number(target.dataset.range); render(); }
  else if (target.dataset.gpuWindow) { state.gpuWindow = target.dataset.gpuWindow; if ($('#detailDialog').open) $('#detailDialog').close(); render(); }
  else if (target.dataset.gpu !== undefined) gpuDetail(target.dataset.gpu);
  else if (target.hasAttribute('data-export')) exportCsv();
});
$('#main').addEventListener('keydown', event => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-gpu]')) {
    event.preventDefault(); gpuDetail(event.target.dataset.gpu);
  }
});
$('#main').addEventListener('change', event => {
  if (event.target.id === 'eventFilter') { state.eventFilter = event.target.value; $('#tableContent').innerHTML = eventsTable(); icons(); }
});
$('#detailDialog').addEventListener('click', event => { if (event.target.closest('[data-close-dialog]') || event.target === $('#detailDialog')) $('#detailDialog').close(); });
window.addEventListener('hashchange', () => { closeMenu(); if ($('#detailDialog').open) $('#detailDialog').close(); render(); window.scrollTo({ top: 0 }); });
setInterval(() => { updateRuntimeStatus(); if ($('#autoRefresh').checked && !document.hidden) refresh(); }, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) updateRuntimeStatus(); });
function checkExpiry() { if (state.key && Date.now() >= state.expiresAt) logout('会话已到期，请重新登录'); }
document.addEventListener('visibilitychange', checkExpiry);
window.addEventListener('pageshow', checkExpiry);
try { const saved = localStorage.getItem('yxgpu-theme'); if (saved === 'light') document.documentElement.dataset.theme = saved; } catch { /* Theme persistence is optional. */ }
icons();
void restoreSession();
