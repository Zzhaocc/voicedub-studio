/* VoiceDub Studio · 配音工作台
 * 零依赖单页应用：拉数据 → 渲染 → 轮询。所有服务端文本经 esc() 转义。
 */
'use strict';

const state = {
  health: null,
  catalog: null,
  voices: [],
  media: [],
  jobs: [],
  job: null,
  jobId: null,
  mode: 'dialogue',
  view: 'studio',
  busy: false,
  pollTimer: null,
};

const $ = (sel) => document.querySelector(sel);
const TERMINAL = ['succeeded', 'failed', 'cancelled'];

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

const fmt = {
  dt(v) {
    if (!v) return '—';
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false });
  },
  ms(v) {
    if (v == null) return '—';
    return v >= 1000 ? (v / 1000).toFixed(1) + 's' : v + 'ms';
  },
  dur(v) {
    if (v == null) return '—';
    const total = Math.round(Number(v));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m ? `${m}:${String(s).padStart(2, '0')}` : `0:${String(s).padStart(2, '0')}`;
  },
  bytes(v) {
    if (v == null) return '—';
    if (v >= 1048576) return (v / 1048576).toFixed(2) + ' MB';
    if (v >= 1024) return (v / 1024).toFixed(1) + ' KB';
    return v + ' B';
  },
};

function toast(message, kind = 'info') {
  const stack = $('#toast-stack');
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.innerHTML = `<span>${kind === 'error' ? '✕' : kind === 'success' ? '✓' : 'i'}</span>
    <span>${esc(message)}</span><button class="toast-close" type="button">×</button>`;
  node.querySelector('.toast-close').addEventListener('click', () => node.remove());
  stack.appendChild(node);
  setTimeout(() => node.remove(), 5000);
}

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}) } };
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const resp = await fetch(path, init);
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!resp.ok) {
    const detail = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : resp.statusText;
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return data;
}

function pill(status) {
  const label = { queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }[status] || status;
  return `<span class="pill ${status}">${esc(label)}</span>`;
}

/* ------------------------------------------------------------------ */
async function boot() {
  try {
    const [health, catalog] = await Promise.all([api('/api/health'), api('/api/catalog')]);
    state.health = health;
    state.catalog = catalog;
    state.voices = catalog.voices || [];
    state.media = catalog.media || [];
    $('#health').innerHTML = `<span class="dot succeeded"></span>
      <div><div class="health-title">服务正常</div><div class="health-sub">v${esc(health.version)} · ${esc(health.workers)} worker</div></div>`;
    $('#engine').textContent = `${health.engine} · ${health.ffmpeg}`;
    fillForms();
    renderVoiceSelects();
    renderVoices();
    await loadJobs();
    const latest = state.jobs.find((j) => j.status === 'succeeded') || state.jobs[0];
    if (latest) await openJob(latest.id, true);
    switchView('studio');
    startPolling();
  } catch (err) {
    $('#job-body').innerHTML = `<div class="empty"><div class="empty-mark">!</div>
      <div class="empty-title">加载失败</div><div class="empty-desc">${esc(err.message)}</div></div>`;
  }
}

function fillForms() {
  const demo = state.catalog.demo || {};
  $('#dlg-script').value = demo.dialogue || '';
  $('#dub-script').value = demo.dubbing || '';
  const book = demo.audiobook || { chapters: [] };
  $('#book-title').value = book.title || '有声书';
  $('#book-chapters').value = (book.chapters || []).map((c) => `## ${c.title}\n${c.text}`).join('\n\n');

  const media = state.media || [];
  $('#dub-media').innerHTML = media.map((m) => `<option value="${esc(m.id)}">${esc(m.filename)} · ${fmt.dur(m.duration)}</option>`).join('')
    || '<option value="">没有可用视频</option>';

  $('#mode-tabs').querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchMode(tab.dataset.mode));
  });
}

function renderVoiceSelects() {
  const options = state.voices.map((v) => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');
  ['#dlg-v1', '#dlg-v2', '#dlg-v3', '#dub-voice', '#book-voice'].forEach((sel) => {
    const el = $(sel);
    if (el) el.innerHTML = options;
  });
  // 对白默认：林夕、周野、旁白 各用一个不同音色
  const byName = (keyword) => state.voices.find((v) => v.name.includes(keyword));
  if (byName('林夕')) $('#dlg-v1').value = byName('林夕').id;
  if (byName('周野')) $('#dlg-v2').value = byName('周野').id;
  if (byName('林夕')) $('#dlg-v3').value = (byName('老陈') || byName('林夕')).id;
  if (byName('周野')) $('#dub-voice').value = byName('周野').id;
  if (byName('老陈')) $('#book-voice').value = byName('老陈').id;
}

function switchMode(mode) {
  state.mode = mode;
  $('#mode-tabs').querySelectorAll('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.mode === mode));
  ['dialogue', 'dubbing', 'audiobook'].forEach((name) => {
    $(`#form-${name}`).classList.toggle('hidden', name !== mode);
  });
  const titles = { dialogue: '对白剧本', dubbing: '视频配音', audiobook: '有声书章节' };
  const hints = {
    dialogue: '用 [角色] 或 角色： 开头',
    dubbing: '合成后自动对齐原视频时长',
    audiobook: '用 ## 标题 分隔章节',
  };
  $('#form-title').textContent = titles[mode];
  $('#form-hint').textContent = hints[mode];
}

/* ------------------------------------------------------------------ */
async function loadJobs() {
  const data = await api('/api/jobs?limit=30');
  state.jobs = data.items || [];
  renderRecent();
  if (state.view === 'jobs') renderJobsTable();
}

async function openJob(jobId, quiet = false) {
  state.jobId = jobId;
  try {
    state.job = await api(`/api/jobs/${jobId}`);
    renderJob();
    if (!quiet) switchView('studio');
  } catch (err) {
    if (!quiet) toast(err.message, 'error');
  }
}

async function submitJob(endpoint, body) {
  if (state.busy) return;
  state.busy = true;
  try {
    const created = await api(endpoint, { method: 'POST', body });
    toast('任务已入队 ' + created.job_id, 'success');
    await openJob(created.job_id, true);
    await loadJobs();
    switchView('studio');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    state.busy = false;
  }
}

function parseChapters(text) {
  const chapters = [];
  let current = null;
  (text || '').split('\n').forEach((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith('## ')) {
      if (current) chapters.push(current);
      current = { title: trimmed.slice(3).trim(), lines: [] };
    } else if (trimmed) {
      if (!current) current = { title: '第 1 章', lines: [] };
      current.lines.push(trimmed);
    }
  });
  if (current) chapters.push(current);
  return chapters.map((c) => ({ title: c.title, text: c.lines.join('\n') })).filter((c) => c.text);
}

/* ------------------------------------------------------------------ */
function renderJob() {
  const job = state.job;
  const body = $('#job-body');
  const status = $('#job-status');
  if (!job) {
    status.className = 'pill';
    status.textContent = '空闲';
    body.innerHTML = `<div class="empty"><div class="empty-mark">◎</div><div class="empty-title">还没有任务</div>
      <div class="empty-desc">选一个模式，填入文案，点生成。</div></div>`;
    return;
  }
  status.className = `pill ${job.status}`;
  status.textContent = { queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }[job.status] || job.status;

  const fill = job.status === 'succeeded' ? 'done' : job.status === 'failed' ? 'fail' : '';
  const steps = (job.steps || []).map((s) => {
    const icon = s.status === 'succeeded' ? '✓' : s.status === 'failed' ? '✗' : s.status === 'cancelled' ? '–' : '·';
    const color = s.status === 'succeeded' ? '#34d399' : s.status === 'failed' ? '#f87171' : s.status === 'cancelled' ? '#66708a' : '#fbbf24';
    return `<div class="step-row"><span class="step-no">${String(s.seq).padStart(2, '0')}</span>
      <div><div class="step-name" style="color:${color}">${icon} ${esc(s.name)}</div>
      <div class="step-detail">${esc(s.detail || '')}</div></div>
      <span class="step-lat">${s.latency_ms ? fmt.ms(s.latency_ms) : ''}</span></div>`;
  }).join('');

  const audio = (job.assets || []).find((a) => a.kind === 'audio');
  const video = (job.assets || []).find((a) => a.kind === 'video');
  const assets = (job.assets || []).map((a) => `<a class="asset" href="${esc(a.url)}" target="_blank" rel="noopener">
      <b>${esc(a.kind)}</b> ${esc(a.filename)} · ${fmt.bytes(a.size_bytes)}</a>`).join('');

  const timeline = (job.output && job.output.timeline) || [];
  const chapters = (job.output && job.output.chapters) || [];
  const extraList = timeline.length
    ? `<div class="steps" style="margin-top:12px">${timeline.slice(0, 8).map((item) => `
        <div class="step-row"><span class="step-no">${fmt.dur(item.start)}</span>
        <div><div class="step-name">${esc(item.speaker || '旁白')}</div>
        <div class="step-detail">${esc(item.text)}</div></div></div>`).join('')}</div>`
    : chapters.length
      ? `<div class="steps" style="margin-top:12px">${chapters.map((item) => `
          <div class="step-row"><span class="step-no">${String(item.index).padStart(2, '0')}</span>
          <div><div class="step-name">${esc(item.title)}</div>
          <div class="step-detail">${fmt.dur(item.start)} – ${fmt.dur(item.end)} · ${item.sentences} 句</div></div></div>`).join('')}</div>`
      : '';

  body.innerHTML = `
    <div style="font-size:13px;font-weight:700">${esc(job.title || '未命名')}</div>
    <div class="job-sub">${esc(job.id)} · ${fmt.dt(job.created_at)}${job.duration_ms ? ' · 耗时 ' + fmt.ms(job.duration_ms) : ''}</div>
    <div class="progress"><div class="progress-fill ${fill}" style="width:${job.progress}%"></div></div>
    <div class="small muted">${job.progress}%${job.error ? ' · <span style="color:#f87171">' + esc(job.error) + '</span>' : ''}</div>
    ${video ? `<video controls preload="metadata" src="${esc(video.preview_url || video.url)}"></video>` : ''}
    ${audio ? `<audio controls preload="metadata" src="${esc(audio.preview_url || audio.url)}"></audio>` : ''}
    <div class="steps">${steps}</div>
    ${extraList}
    ${assets ? `<div class="asset-row">${assets}</div>` : ''}
    <div class="asset-row">
      ${job.status === 'running' || job.status === 'queued' ? '<button class="btn btn-sm btn-danger" id="btn-cancel" type="button">取消任务</button>' : ''}
      ${job.status === 'failed' || job.status === 'cancelled' ? '<button class="btn btn-sm" id="btn-retry" type="button">重试</button>' : ''}
    </div>`;
  const cancel = $('#btn-cancel');
  const retry = $('#btn-retry');
  if (cancel) cancel.addEventListener('click', async () => {
    try { await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' }); toast('已请求取消', 'success'); await openJob(job.id, true); }
    catch (err) { toast(err.message, 'error'); }
  });
  if (retry) retry.addEventListener('click', async () => {
    try {
      const data = await api(`/api/jobs/${job.id}/retry`, { method: 'POST' });
      await openJob(data.job_id, true); await loadJobs(); toast('已重新入队', 'success');
    } catch (err) { toast(err.message, 'error'); }
  });
}

function renderRecent() {
  const box = $('#recent');
  if (!state.jobs.length) {
    box.innerHTML = '<div class="empty"><div class="empty-title">暂无任务</div><div class="empty-desc">生成第一条配音后会出现在这里。</div></div>';
    return;
  }
  const kindLabel = { dialogue: '对白', dubbing: '视频配音', audiobook: '有声书' };
  box.innerHTML = state.jobs.map((j) => `<div class="job-item" data-job="${esc(j.id)}">
      <div><div class="job-title">${esc(j.title || '未命名')}</div>
      <div class="job-sub">${esc(kindLabel[j.kind] || j.kind)} · ${esc(j.id)}</div></div>
      <div class="job-meta">${j.output && j.output.duration ? fmt.dur(j.output.duration) : '—'}</div>
      <div class="job-meta">${j.duration_ms ? fmt.ms(j.duration_ms) : '—'}</div>
      ${pill(j.status)}</div>`).join('');
  box.querySelectorAll('[data-job]').forEach((el) => el.addEventListener('click', () => openJob(el.dataset.job)));
}

/* ------------------------------------------------------------------ */
function renderVoices() {
  const kindLabel = { preset: '预设', designed: '设计', cloned: '克隆', custom: '自定义' };
  $('#voice-grid').innerHTML = state.voices.map((v) => `
    <div class="voice-card">
      <div class="voice-top"><div><div class="voice-name">${esc(v.name)}</div>
        <div class="kv"><span>${esc(kindLabel[v.kind] || v.kind)}</span></div></div>
        <span class="pill">${esc(v.timbre)}</span></div>
      <div class="voice-desc">${esc(v.description || '')}</div>
      <div class="kv"><span>pitch <b>${Number(v.pitch).toFixed(2)}</b></span><span>speed <b>${Number(v.speed).toFixed(2)}</b></span>
        ${v.reference_duration ? `<span>ref <b>${fmt.dur(v.reference_duration)}</b></span>` : ''}</div>
      <div class="voice-actions">
        <button class="btn btn-sm" data-play="${esc(v.id)}" type="button">试听</button>
        ${v.kind !== 'preset' ? `<button class="btn btn-sm btn-danger" data-del="${esc(v.id)}" type="button">删除</button>` : ''}
      </div>
    </div>`).join('');

  $('#voice-grid').querySelectorAll('[data-play]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const player = $('#player');
      player.src = `/api/voices/${btn.dataset.play}/preview?t=${Date.now()}`;
      player.play().catch(() => toast('浏览器阻止了自动播放，请再点一次', 'error'));
    });
  });
  $('#voice-grid').querySelectorAll('[data-del]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try { await api(`/api/voices/${btn.dataset.del}`, { method: 'DELETE' }); toast('已删除', 'success'); }
      catch (err) { toast(err.message, 'error'); }
      await refreshVoices();
    });
  });
}

async function refreshVoices() {
  const data = await api('/api/voices');
  state.voices = data.items || [];
  renderVoices();
  renderVoiceSelects();
}

async function designVoice() {
  const description = $('#design-desc').value.trim();
  if (!description) { toast('先描述你想要的音色', 'error'); return; }
  try {
    const data = await api('/api/voices/design', { method: 'POST', body: { description } });
    $('#design-result').innerHTML = `已生成：<b>${esc(data.name)}</b> · pitch ${Number(data.pitch).toFixed(2)} · speed ${Number(data.speed).toFixed(2)} · ${esc(data.timbre)}
      <div style="margin-top:6px">${(data.design.matched_keywords || []).map((k) => `<span class="kw">${esc(k)}</span>`).join('')}</div>`;
    await refreshVoices();
    const player = $('#player');
    player.src = `/api/voices/${data.id}/preview?t=${Date.now()}`;
    player.play().catch(() => {});
    toast('音色已加入音色库', 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* ------------------------------------------------------------------ */
function renderJobsTable() {
  if (!state.jobs.length) {
    $('#jobs-table').innerHTML = '<div class="empty"><div class="empty-title">队列为空</div></div>';
    return;
  }
  const kindLabel = { dialogue: '对白', dubbing: '视频配音', audiobook: '有声书' };
  $('#jobs-table').innerHTML = `<table><thead><tr><th>任务</th><th>类型</th><th>状态</th><th>进度</th><th>时长</th><th>耗时</th><th></th></tr></thead><tbody>
    ${state.jobs.map((j) => `<tr>
      <td><div style="font-weight:600">${esc(j.title || '未命名')}</div><div class="mono muted">${esc(j.id)}</div></td>
      <td>${esc(kindLabel[j.kind] || j.kind)}</td>
      <td>${pill(j.status)}</td>
      <td>${j.progress}%</td>
      <td>${j.output && j.output.duration ? fmt.dur(j.output.duration) : '—'}</td>
      <td>${j.duration_ms ? fmt.ms(j.duration_ms) : '—'}</td>
      <td><button class="btn btn-sm" data-open="${esc(j.id)}" type="button">查看</button></td></tr>`).join('')}
    </tbody></table>`;
  $('#jobs-table').querySelectorAll('[data-open]').forEach((btn) => {
    btn.addEventListener('click', () => openJob(btn.dataset.open));
  });
}

async function renderMetrics() {
  const data = await api('/api/metrics');
  const j = data.jobs, a = data.audio, l = data.library;
  const cards = [
    ['任务总数', j.total, `成功 ${j.succeeded} · 失败 ${j.failed}`],
    ['成功率', j.success_rate == null ? '—' : (j.success_rate * 100).toFixed(1) + '%', '按已结束任务'],
    ['平均耗时', j.avg_duration_ms ? fmt.ms(j.avg_duration_ms) : '—', '端到端'],
    ['音频总时长', fmt.dur(a.seconds), `${a.outputs} 条音频`],
    ['产物体积', fmt.bytes(a.total_bytes), `视频 ${a.videos} 条`],
    ['音色库', l.voices, `克隆 ${l.cloned} · 素材 ${l.media}`],
  ];
  $('#metric-grid').innerHTML = cards.map(([label, value, foot]) => `
    <div class="metric-card"><div class="metric-label">${esc(label)}</div>
    <div class="metric-value">${esc(value)}</div><div class="metric-foot">${esc(foot)}</div></div>`).join('');
  $('#metrics-recent').innerHTML = `<div class="panel-title" style="margin-bottom:8px">最近任务</div>
    <table><thead><tr><th>任务</th><th>类型</th><th>状态</th><th>时长</th><th>耗时</th></tr></thead><tbody>
    ${(data.recent || []).map((r) => `<tr><td>${esc(r.title || r.id)}</td><td>${esc(r.kind)}</td>
      <td>${pill(r.status)}</td><td>${r.output && r.output.duration ? fmt.dur(r.output.duration) : '—'}</td>
      <td>${r.duration_ms ? fmt.ms(r.duration_ms) : '—'}</td></tr>`).join('')}
    </tbody></table>`;
}

/* ------------------------------------------------------------------ */
function switchView(view) {
  state.view = view;
  document.querySelectorAll('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.view === view));
  document.querySelectorAll('.view').forEach((el) => el.classList.toggle('hidden', el.id !== `view-${view}`));
  if (view === 'voices') refreshVoices();
  if (view === 'jobs') { loadJobs(); renderJobsTable(); }
  if (view === 'metrics') renderMetrics();
}

/* ------------------------------------------------------------------ */
function showModal({ title, desc, fields, confirmText, onConfirm }) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-backdrop"><div class="modal">
    <div class="modal-title">${esc(title)}</div>
    ${desc ? `<div class="modal-desc">${esc(desc)}</div>` : ''}
    <div class="modal-body" id="modal-body"></div>
    <div class="modal-foot"><button class="btn btn-ghost" id="modal-cancel" type="button">取消</button>
      <button class="btn btn-primary" id="modal-ok" type="button">${esc(confirmText || '确认')}</button></div>
  </div></div>`;
  const body = $('#modal-body');
  (fields || []).forEach((f) => {
    const label = document.createElement('label');
    label.className = 'modal-label';
    label.innerHTML = `<span>${esc(f.label)}</span>`;
    let input;
    if (f.type === 'file') {
      input = document.createElement('input');
      input.type = 'file';
      input.accept = 'audio/wav,audio/*';
    } else {
      input = document.createElement('input');
      input.type = 'text';
      input.placeholder = f.placeholder || '';
    }
    input.className = 'modal-input';
    input.name = f.name;
    label.appendChild(input);
    body.appendChild(label);
  });
  const close = () => { root.innerHTML = ''; };
  $('#modal-cancel').addEventListener('click', close);
  $('#modal-ok').addEventListener('click', async () => {
    const values = {};
    body.querySelectorAll('input').forEach((el) => {
      values[el.name] = el.type === 'file' ? (el.files && el.files[0]) : el.value;
    });
    close();
    try { await onConfirm(values); } catch (err) { toast(err.message, 'error'); }
  });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/* ------------------------------------------------------------------ */
function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (state.job && (state.job.status === 'running' || state.job.status === 'queued')) {
      await openJob(state.job.id, true);
    }
    if (state.jobs.some((j) => j.status === 'running' || j.status === 'queued')) {
      await loadJobs();
    }
  }, 1200);
}

/* ------------------------------------------------------------------ */
document.querySelectorAll('.nav-item').forEach((el) => el.addEventListener('click', () => switchView(el.dataset.view)));
$('#btn-refresh').addEventListener('click', loadJobs);
$('#btn-jobs-refresh').addEventListener('click', async () => { await loadJobs(); renderJobsTable(); });
$('#btn-metrics-refresh').addEventListener('click', renderMetrics);
$('#btn-design').addEventListener('click', designVoice);
$('#design-desc').addEventListener('keydown', (event) => { if (event.key === 'Enter') designVoice(); });

$('#btn-dialogue').addEventListener('click', () => {
  const script = $('#dlg-script').value.trim();
  if (!script) { toast('剧本不能为空', 'error'); return; }
  submitJob('/api/jobs/dialogue', {
    title: $('#dlg-title').value.trim() || '多角色对白',
    script,
    voice_map: {
      '林夕': $('#dlg-v1').value,
      '周野': $('#dlg-v2').value,
      '旁白': $('#dlg-v3').value,
    },
    default_voice_id: $('#dlg-v3').value,
  });
});

$('#btn-dubbing').addEventListener('click', () => {
  const mediaId = $('#dub-media').value;
  const script = $('#dub-script').value.trim();
  if (!mediaId) { toast('没有可用的源视频', 'error'); return; }
  if (!script) { toast('配音文案不能为空', 'error'); return; }
  submitJob('/api/jobs/dubbing', {
    title: $('#dub-title').value.trim() || '视频配音',
    media_id: mediaId,
    script,
    voice_id: $('#dub-voice').value,
  });
});

$('#btn-audiobook').addEventListener('click', () => {
  const chapters = parseChapters($('#book-chapters').value);
  if (!chapters.length) { toast('至少需要一章内容', 'error'); return; }
  submitJob('/api/jobs/audiobook', {
    title: $('#book-title').value.trim() || '有声书',
    chapters,
    voice_id: $('#book-voice').value,
  });
});

$('#btn-clone').addEventListener('click', () => {
  showModal({
    title: '克隆音色',
    desc: '上传 3–10 秒 WAV 参考音频；不上传则用内置样本演示分析流程。',
    confirmText: '开始克隆',
    fields: [
      { name: 'name', label: '名称', placeholder: '例如：客户 A · 女声' },
      { name: 'audio', label: '参考音频（可选）', type: 'file' },
    ],
    onConfirm: async (values) => {
      if (!values.name || !values.name.trim()) throw new Error('名称不能为空');
      let audio_b64 = null;
      if (values.audio) audio_b64 = await fileToBase64(values.audio);
      const data = await api('/api/voices/clone', { method: 'POST', body: { name: values.name.trim(), audio_b64 } });
      toast(`已克隆：基频 ${data.analysis ? data.analysis.f0 : '?'}Hz`, 'success');
      await refreshVoices();
    },
  });
});

boot();
