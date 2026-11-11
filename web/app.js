/* TenderPilot — SPA без зависимостей */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const money = (v) => {
  const n = Number(v || 0);
  if (!n) return '—';
  if (n >= 1e9) return (n / 1e9).toFixed(2).replace('.', ',') + ' млрд ₽';
  if (n >= 1e6) return (n / 1e6).toFixed(2).replace('.', ',').replace(',00', '') + ' млн ₽';
  if (n >= 1e3) return Math.round(n / 1e3) + ' тыс ₽';
  return Math.round(n) + ' ₽';
};
const moneyFull = (v) => Math.round(Number(v || 0)).toLocaleString('ru-RU') + ' ₽';
const num = (v, d = 0) => Number(v || 0).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
const dt = (s) => { if (!s) return '—'; const d = new Date(s); if (isNaN(d)) return s; return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short', year: '2-digit' }); };
const dtTime = (s) => { if (!s) return ''; const d = new Date(s); const today = new Date(); const diff = (today - d) / 1000; if (diff < 60) return 'только что'; if (diff < 3600) return Math.floor(diff / 60) + ' мин назад'; if (diff < 86400) return Math.floor(diff / 3600) + ' ч назад'; return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' }); };
const VERDICT = { go: ['Идём', 'go'], consider: ['Спорно', 'consider'], no_go: ['Не идём', 'no_go'] };
const STAGE_COLOR = { new: 'plain', review: 'info', go: 'brand', bid: 'info', won: 'go', lost: 'no_go', skip: 'plain' };

function toast(msg, kind = '', ms = 4200) {
  const el = document.createElement('div');
  el.className = 'toast-item ' + kind;
  el.innerHTML = esc(msg);
  $('#toast').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = '.3s'; setTimeout(() => el.remove(), 320); }, ms);
}

const state = {
  user: null, catalog: null, company: null, draft: null, ai: null, presets: {},
  tenders: [], filters: { q: '', verdict: '', platform: '', category: '', law: '', stage: '', sort: 'match' },
  tender: null, tab: 'ai', stats: null, activity: null, busy: false, profileStep: 1,
};

async function api(path, opts = {}) {
  const headers = opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
  const r = await fetch('/api' + path, { credentials: 'same-origin', ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (r.status === 401) { state.user = null; renderLogin(); throw new Error('Требуется вход'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'Ошибка ' + r.status);
  return data;
}

/* ================= LOGIN ================= */
function renderLogin() {
  document.getElementById('root').innerHTML = `
  <div class="login-wrap">
    <div class="login-art">
      <div>
        <div class="logo" style="margin-bottom:38px"><span class="logo-mark">TP</span> TenderPilot</div>
        <h1>Тендеры находит ИИ.<br>Завод решает<i style="color:#60a5fa">, что взять в работу</i>.</h1>
        <p>Закрытая платформа для владельца завода: настраиваете номенклатуру один раз — дальше система сама мониторит ЕИС, Росэлторг, B2B-Center, ЭТП ГПБ и закупки корпораций.</p>
        <div class="login-feats">
          <div class="login-feat"><span class="lico">◎</span><div><b>Мониторинг всех площадок</b><br>44-ФЗ, 223-ФЗ и коммерческие закупки — в одной ленте</div></div>
          <div class="login-feat"><span class="lico">✳</span><div><b>ИИ читает ТЗ и файлы</b><br>Сверяет требования с вашим профилем и находит «заточки»</div></div>
          <div class="login-feat"><span class="lico">₽</span><div><b>Маржа и риски до подачи</b><br>Себестоимость, обеспечение, кассовый разрыв, вероятность победы</div></div>
        </div>
      </div>
      <div style="color:#63799c;font-size:12px;position:relative;z-index:2">Демо-доступ: owner@zavod.ru / demo1234</div>
    </div>
    <div class="login-form-side">
      <div class="login-card">
        <div class="logo" style="margin-bottom:26px"><span class="logo-mark">TP</span> TenderPilot</div>
        <h2 style="margin:0 0 6px;font-size:22px;letter-spacing:-.4px">Вход в систему</h2>
        <p class="muted tiny" style="margin:0 0 22px">Личный кабинет владельца завода</p>
        <form id="loginForm">
          <div class="field"><label>Email</label><input type="email" name="email" value="owner@zavod.ru" required></div>
          <div class="field"><label>Пароль</label><input type="password" name="password" value="demo1234" required></div>
          <button class="btn primary block" type="submit" style="margin-top:6px">Войти</button>
          <div class="hint" style="text-align:center;margin-top:14px">Демо: owner@zavod.ru / demo1234</div>
        </form>
      </div>
    </div>
  </div>`;
  $('#loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const res = await api('/login', { method: 'POST', body: JSON.stringify({ email: f.get('email'), password: f.get('password') }) });
      state.user = res.user;
      await boot(true);
    } catch (err) { toast(err.message, 'err'); }
  });
}

/* ================= SHELL ================= */
const NAV = [
  { id: 'dashboard', name: 'Дашборд', ico: '◧' },
  { id: 'tenders', name: 'Лента тендеров', ico: '☰' },
  { id: 'analytics', name: 'Аналитика', ico: '◔' },
  { id: 'sources', name: 'Источники данных', ico: '⇄' },
  { sep: 'Настройка' },
  { id: 'profile', name: 'Профиль завода', ico: '⚙' },
  { id: 'ai', name: 'Настройки ИИ', ico: '✳' },
];

function renderShell() {
  const c = state.company || {};
  const aiOn = state.ai && state.ai.api_key_set;
  document.getElementById('root').innerHTML = `
  <div class="shell">
    <aside class="side" id="side">
      <div class="side-head">
        <span class="logo-mark">TP</span>
        <div style="line-height:1.2"><div class="logo" style="font-size:15px">TenderPilot</div>
        <div style="font-size:11px;color:#7f92b3">${esc(c.name || 'Профиль не настроен')}</div></div>
      </div>
      <div class="side-nav">
        ${NAV.map((n) => n.sep ? `<div class="side-label">${n.sep}</div>` :
          `<div class="nav-item" data-nav="${n.id}"><span style="width:18px;text-align:center;opacity:.85">${n.ico}</span>${n.name}<span class="badge" data-badge="${n.id}" style="display:none">0</span></div>`).join('')}
      </div>
      <div class="side-foot">
        <div class="ai-status">
          <span class="dot ${aiOn ? 'on' : 'off'}"></span>
          <div style="line-height:1.3">
            <div style="color:#e6edf9;font-weight:600">${aiOn ? 'ИИ подключён' : 'ИИ в базовом режиме'}</div>
            <div style="color:#7f92b3;font-size:11px">${aiOn ? esc(state.ai.model || '') : 'ключ не задан'}</div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:12px;padding:0 4px">
          <div class="avatar" style="background:#24345a;color:#9db4da">${esc((state.user?.name || 'В').slice(0, 1))}</div>
          <div style="line-height:1.2;font-size:12px;min-width:0">
            <div style="color:#dbe5f5;overflow:hidden;text-overflow:ellipsis">${esc(state.user?.name || '')}</div>
            <div style="color:#7f92b3">${esc(state.user?.email || '')}</div>
          </div>
          <button class="btn ghost sm" data-act="logout" style="margin-left:auto;color:#8fa3c4">⎋</button>
        </div>
      </div>
    </aside>
    <div class="main">
      <div class="topbar">
        <button class="btn ghost sm" data-act="menu" style="display:none" id="menuBtn">☰</button>
        <div class="search"><i>⌕</i><input id="globalSearch" placeholder="Поиск по названию, заказчику, номеру или тексту ТЗ…" value="${esc(state.filters.q)}"></div>
        <div class="top-actions">
          <button class="btn sm" data-act="scan">⟳ Сканировать площадки</button>
          <button class="btn primary sm" data-act="analyze-new">✳ Проанализировать новые</button>
        </div>
      </div>
      <div class="content" id="content"></div>
    </div>
  </div>`;
  bindShell();
}

function bindShell() {
  $$('.nav-item').forEach((el) => el.addEventListener('click', () => { location.hash = '#/' + el.dataset.nav; }));
  $('#globalSearch')?.addEventListener('input', (e) => {
    state.filters.q = e.target.value;
    if (!location.hash.startsWith('#/tenders')) location.hash = '#/tenders';
    else renderTendersList();
  });
  $$('[data-act]').forEach((el) => el.addEventListener('click', handleAction));
}

async function handleAction(e) {
  const act = e.currentTarget.dataset.act;
  if (act === 'logout') { await api('/logout', { method: 'POST' }); state.user = null; renderLogin(); }
  if (act === 'menu') $('#side')?.classList.toggle('open');
  if (act === 'scan') await scanPlatforms();
  if (act === 'analyze-new') await analyzeNew();
}

/* ================= DASHBOARD ================= */
async function renderDashboard() {
  const content = $('#content');
  content.innerHTML = `<div class="grid kpis">${Array(4).fill('<div class="kpi"><div class="skel" style="height:52px"></div></div>').join('')}</div>
    <div style="height:14px"></div><div class="card"><div class="card-body"><div class="skel" style="height:180px"></div></div></div>`;
  const [stats, act] = await Promise.all([api('/stats'), api('/activity')]);
  state.stats = stats; state.activity = act;
  const t = stats.totals;
  const needSetup = !(state.company && state.company.onboarding_done);
  content.innerHTML = `
  ${needSetup ? `<div class="card" style="margin-bottom:14px;border-color:#cfe0fb;background:linear-gradient(120deg,#f2f7ff,#fff)">
    <div class="card-body" style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
      <div style="width:38px;height:38px;border-radius:11px;background:var(--brand-soft);display:grid;place-items:center;font-size:18px;color:var(--brand)">✳</div>
      <div style="flex:1;min-width:220px"><b>Завершите настройку профиля завода</b>
        <div class="muted tiny">Укажите номенклатуру, ОКПД2, регионы и экономику — ИИ начнёт точнее отбирать закупки и считать маржу.</div></div>
      <button class="btn primary" data-goto="profile">Настроить профиль</button>
    </div></div>` : ''}
  <div class="page-head">
    <div><h2>Дашборд</h2><p>Состояние тендерной воронки на ${new Date().toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}</p></div>
    <div class="spacer"></div>
    <div class="chips-row">
      <button class="chip-btn" data-goto="tenders">Вся лента →</button>
    </div>
  </div>
  <div class="grid kpis">
    <div class="kpi accent"><div class="lbl">Найдено закупок</div><div class="val">${t.tenders}</div><div class="sub">проанализировано ИИ: ${t.analyzed}</div></div>
    <div class="kpi good"><div class="lbl">Рекомендовано «идём»</div><div class="val">${t.go}</div><div class="sub">сумма ${money(t.sum_go)}</div></div>
    <div class="kpi warn"><div class="lbl">Спорные</div><div class="val">${t.consider}</div><div class="sub">нужно решение владельца</div></div>
    <div class="kpi"><div class="lbl">Средняя маржа по «идём»</div><div class="val">${t.avg_margin_go}%</div><div class="sub">целевая ${state.company?.economics?.target_margin_pct || 26}%</div></div>
    <div class="kpi"><div class="lbl">В работе</div><div class="val">${money(t.pipeline_sum)}</div><div class="sub">сумма активных заявок и подготовки</div></div>
  </div>
  <div style="height:14px"></div>
  <div class="split">
    <div class="stack">
      <div class="card">
        <div class="card-head">✳ Топ рекомендаций ИИ <span class="chip go" style="margin-left:auto">приоритет</span></div>
        <div class="card-body" style="padding:0">
          ${stats.top.length ? `<div class="table-wrap"><table class="t"><tbody>
            ${stats.top.map((x) => tenderRow(x)).join('')}
          </tbody></table></div>` : `<div class="empty"><b>Пока нет рекомендаций</b>Запустите анализ ленты — ИИ отберёт подходящие закупки.</div>`}
        </div>
      </div>
      <div class="card">
        <div class="card-head">⏱ Ближайшие дедлайны</div>
        <div class="card-body" style="padding:0">
          ${stats.deadline_soon.length ? `<div class="table-wrap"><table class="t"><tbody>${stats.deadline_soon.map((x) => tenderRow(x)).join('')}</tbody></table></div>`
      : `<div class="empty"><b>Нет закупок с дедлайном в 7 дней</b>Спокойная неделя.</div>`}
        </div>
      </div>
    </div>
    <div class="stack">
      <div class="card">
        <div class="card-head">Активность</div>
        <div class="card-body">
          <div class="feed">
            ${(act.events || []).slice(0, 12).map((e) => `
              <div class="feed-item" data-goto="tender/${e.tender_id}" style="cursor:pointer">
                <div class="feed-ico ${e.type}">${e.type === 'ai' ? '✳' : e.type === 'found' ? '⌕' : e.type === 'stage' ? '⇄' : '•'}</div>
                <div class="feed-main"><b>${esc(e.title)}</b><small>${esc(e.text)} · ${dtTime(e.created_at)}</small></div>
              </div>`).join('') || '<div class="empty">Событий пока нет</div>'}
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-head">Статус мониторинга</div>
        <div class="card-body stack">
          <div style="display:flex;justify-content:space-between;font-size:13px"><span class="muted">Площадок в мониторинге</span><b>${(state.catalog?.platforms || []).length}</b></div>
          <div style="display:flex;justify-content:space-between;font-size:13px"><span class="muted">Последнее сканирование</span><b>${act.sync?.[0] ? dtTime(act.sync[0].created_at) : 'ещё не было'}</b></div>
          ${Object.entries(stats.by_platform || {}).slice(0, 6).map(([k, v]) => `
            <div><div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px"><span>${esc(k)}</span><span class="muted">${v.count} закупок · ${money(v.sum)}</span></div>
            <div class="bar"><i style="width:${Math.min(100, (v.count / Math.max(1, t.tenders)) * 100)}%"></i></div></div>`).join('')}
        </div>
      </div>
    </div>
  </div>`;
  bindCommon(content);
}

function bindCommon(root) {
  $$('[data-goto]', root).forEach((el) => el.addEventListener('click', (ev) => {
    ev.stopPropagation();
    location.hash = '#/' + el.dataset.goto;
  }));
  $$('tr[data-tid]', root).forEach((tr) => tr.addEventListener('click', () => { location.hash = '#/tender/' + tr.dataset.tid; }));
}

/* ================= TENDERS LIST ================= */
function verdictChip(a) {
  if (!a) return `<span class="chip plain">без анализа</span>`;
  const [label, cls] = VERDICT[a.verdict] || ['—', 'plain'];
  return `<span class="chip ${cls}"><span class="dot ${cls}"></span>${label}</span>`;
}
function ring(score, cls) {
  const color = score >= 75 ? '#047857' : score >= 50 ? '#b45309' : '#b91c1c';
  return `<div class="ring ${cls || ''}" style="--v:${score};--rc:${color}"><b>${score}</b></div>`;
}
function marginChip(m) {
  const cls = m.verdict === 'good' ? 'go' : m.verdict === 'ok' ? 'info' : m.verdict === 'thin' ? 'consider' : 'no_go';
  return `<span class="chip ${cls}">${m.margin_pct}%</span>`;
}
function tenderRow(t) {
  const dl = t.days_left;
  const dlCls = dl < 0 ? 'no_go' : dl <= 3 ? 'no_go' : dl <= 7 ? 'consider' : 'plain';
  return `<tr data-tid="${t.id}">
    <td style="width:48px">${ring(t.match.score, 'sm')}</td>
    <td>
      <div class="t-title">${esc(t.title)}</div>
      <div class="t-sub">${esc(t.platform_short)} · ${esc(t.law)} · ${esc(t.customer)}${t.manual ? ' · <span class="chip brand" style="padding:1px 6px;font-size:10px">вручную</span>' : ''}</div>
    </td>
    <td class="nowrap num">${money(t.sum)}</td>
    <td class="nowrap">${marginChip(t.margin)}</td>
    <td class="nowrap">${verdictChip(t.analysis)}</td>
    <td class="nowrap"><span class="chip ${dlCls}">${dl < 0 ? 'истёк' : dl + ' дн'}</span></td>
  </tr>`;
}

async function renderTenders() {
  const content = $('#content');
  content.innerHTML = `<div class="page-head"><div><h2>Лента тендеров</h2><p>Закупки, найденные мониторингом и добавленные вручную</p></div>
    <div class="spacer"></div><button class="btn primary" data-act="parser">+ Добавить закупку вручную</button></div>
    <div class="card"><div class="card-body"><div class="skel" style="height:220px"></div></div></div>`;
  await loadTenders();
  renderTendersList();
  content.querySelector('[data-act="parser"]')?.addEventListener('click', openParserModal);
}

async function loadTenders() {
  const q = new URLSearchParams();
  if (state.filters.q) q.set('q', state.filters.q);
  if (state.filters.sort) q.set('sort', state.filters.sort);
  const res = await api('/tenders?' + q.toString());
  state.tenders = res.items;
  updateBadges();
}

function updateBadges() {
  const un = state.tenders.filter((t) => !t.analysis).length;
  const go = state.tenders.filter((t) => t.analysis?.verdict === 'go').length;
  const set = (id, v) => { const el = document.querySelector(`[data-badge="${id}"]`); if (el) { el.textContent = v; el.style.display = v ? '' : 'none'; } };
  set('tenders', state.tenders.length);
  set('dashboard', un || '');
  const nav = document.querySelector('[data-nav="dashboard"] .badge');
  if (nav && go) { nav.textContent = go; nav.style.display = ''; }
}

function renderTendersList() {
  const f = state.filters;
  const items = state.tenders.filter((t) => {
    if (f.q) {
      const q = f.q.toLowerCase();
      const hay = [t.title, t.customer, t.ext_id, t.tz_text, t.region].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (f.platform && t.platform !== f.platform) return false;
    if (f.category && t.category !== f.category) return false;
    if (f.law && t.law !== f.law) return false;
    if (f.stage && t.stage !== f.stage) return false;
    if (f.verdict === 'unanalyzed' && t.analysis) return false;
    if (f.verdict && f.verdict !== 'unanalyzed' && t.analysis?.verdict !== f.verdict) return false;
    return true;
  });
  const content = $('#content');
  const cats = state.catalog?.categories || [];
  const plats = state.catalog?.platforms || [];
  const stages = state.catalog?.stages || [];
  const vBtn = (id, label, count) => `<button class="chip-btn ${f.verdict === id ? 'active' : ''}" data-v="${id}">${label}${count != null ? ` <span style="opacity:.65">${count}</span>` : ''}</button>`;
  const cnt = (fn) => state.tenders.filter(fn).length;

  content.innerHTML = `
  <div class="page-head"><div><h2>Лента тендеров</h2><p>${state.tenders.length} закупок в базе · показано ${items.length}</p></div>
    <div class="spacer"></div>
    <button class="btn" data-act="parser">+ Добавить по ссылке/ТЗ</button>
  </div>
  <div class="card">
    <div class="filters">
      <div class="chips-row">
        ${vBtn('', 'Все', state.tenders.length)}
        ${vBtn('go', 'Идём', cnt((t) => t.analysis?.verdict === 'go'))}
        ${vBtn('consider', 'Спорно', cnt((t) => t.analysis?.verdict === 'consider'))}
        ${vBtn('no_go', 'Не идём', cnt((t) => t.analysis?.verdict === 'no_go'))}
        ${vBtn('unanalyzed', 'Без анализа', cnt((t) => !t.analysis))}
      </div>
      <div style="flex:1"></div>
      <select data-f="platform"><option value="">Все площадки</option>${plats.map((p) => `<option value="${p.id}" ${f.platform === p.id ? 'selected' : ''}>${esc(p.short)}</option>`).join('')}</select>
      <select data-f="category"><option value="">Все категории</option>${cats.map((c) => `<option value="${c.id}" ${f.category === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
      <select data-f="law"><option value="">Все законы</option><option ${f.law === '44-ФЗ' ? 'selected' : ''}>44-ФЗ</option><option ${f.law === '223-ФЗ' ? 'selected' : ''}>223-ФЗ</option><option value="коммерч." ${f.law === 'коммерч.' ? 'selected' : ''}>Коммерческие</option></select>
      <select data-f="stage"><option value="">Все этапы</option>${stages.map((s) => `<option value="${s.id}" ${f.stage === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
      <select data-f="sort"><option value="match" ${f.sort === 'match' ? 'selected' : ''}>Сортировка: соответствие</option><option value="deadline" ${f.sort === 'deadline' ? 'selected' : ''}>Дедлайн</option><option value="sum" ${f.sort === 'sum' ? 'selected' : ''}>Сумма</option><option value="margin" ${f.sort === 'margin' ? 'selected' : ''}>Маржа</option></select>
    </div>
    ${items.length ? `<div class="table-wrap"><table class="t">
      <thead><tr><th></th><th>Закупка</th><th>Сумма</th><th>Маржа</th><th>Вердикт ИИ</th><th>Дедлайн</th></tr></thead>
      <tbody>${items.map(tenderRow).join('')}</tbody></table></div>`
      : `<div class="empty"><b>Ничего не найдено</b>Измените фильтры или запустите сканирование площадок.</div>`}
  </div>`;
  $$('[data-v]', content).forEach((b) => b.addEventListener('click', () => { state.filters.verdict = b.dataset.v; renderTendersList(); }));
  $$('[data-f]', content).forEach((s) => s.addEventListener('change', () => {
    state.filters[s.dataset.f] = s.value;
    if (s.dataset.f === 'sort') loadTenders().then(renderTendersList); else renderTendersList();
  }));
  content.querySelector('[data-act="parser"]')?.addEventListener('click', openParserModal);
  bindCommon(content);
}

/* ================= TENDER DETAIL ================= */
async function renderTender(id) {
  const content = $('#content');
  content.innerHTML = `<div class="card"><div class="card-body"><div class="skel" style="height:260px"></div></div></div>`;
  try {
    const { tender } = await api('/tenders/' + id);
    state.tender = tender;
  } catch (e) { content.innerHTML = `<div class="empty"><b>Не удалось открыть закупку</b>${esc(e.message)}</div>`; return; }
  const t = state.tender;
  const a = t.analysis;
  const [vLabel, vCls] = a ? (VERDICT[a.verdict] || ['—', 'plain']) : ['анализ не запущен', 'plain'];
  content.innerHTML = `
  <div class="no-print" style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
    <button class="btn ghost sm" onclick="history.back()">←</button>
    <span class="muted tiny">Лента / ${esc(t.platform_short)} / ${esc(t.ext_id || '')}</span>
    <div style="flex:1"></div>
    <button class="btn sm" data-act="print">🖨 Заключение</button>
    <select class="btn sm" data-stage style="padding:8px 10px">${(state.catalog?.stages || []).map((s) => `<option value="${s.id}" ${t.stage === s.id ? 'selected' : ''}>Этап: ${esc(s.name)}</option>`).join('')}</select>
  </div>
  <div class="card" style="margin-bottom:14px">
    <div class="card-body">
      <div class="detail-head">
        <div style="flex:1;min-width:280px">
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
            <span class="chip brand">${esc(t.platform_name)}</span>
            <span class="chip">${esc(t.law)}</span>
            <span class="chip plain">${esc(t.category_name)}</span>
            <span class="chip ${STAGE_COLOR[t.stage] || 'plain'}">${esc(t.stage_name || '')}</span>
            ${a ? `<span class="chip ${vCls}">${vLabel}</span>` : ''}
          </div>
          <h1 class="detail-title">${esc(t.title)}</h1>
          <div class="meta-row">
            <span>🏭 ${esc(t.customer)}</span>
            <span>📍 ${esc(t.region)}</span>
            <span>📅 опубликовано ${dt(t.published)}</span>
            <span>⏱ дедлайн ${dt(t.deadline)} ${t.days_left >= 0 ? `(${t.days_left} дн.)` : '(истёк)'}</span>
            ${t.source_url ? `<a href="${esc(t.source_url)}" target="_blank" rel="noopener">Открыть на площадке ↗</a>` : ''}
          </div>
        </div>
        <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">
          <div style="text-align:center"><div style="font-size:11px;color:var(--ink-3);font-weight:700;text-transform:uppercase">сумма</div><div style="font-size:19px;font-weight:750;letter-spacing:-.5px">${money(t.sum)}</div><div class="tiny muted">${esc(t.qty + ' ' + t.unit)}</div></div>
          <div style="text-align:center"><div style="font-size:11px;color:var(--ink-3);font-weight:700;text-transform:uppercase">соответствие</div>${ring(t.match.score, 'lg')}</div>
          <div style="text-align:center"><div style="font-size:11px;color:var(--ink-3);font-weight:700;text-transform:uppercase">маржа</div><div style="font-size:19px;font-weight:750;color:${t.margin.margin_pct >= t.margin.min_margin_pct ? 'var(--ok)' : 'var(--bad)'}">${t.margin.margin_pct}%</div><div class="tiny muted">${money(t.margin.margin)}</div></div>
        </div>
      </div>
    </div>
  </div>
  <div class="split">
    <div class="card">
      <div class="tabs" id="tabs">
        ${[['ai', 'ИИ-заключение'], ['tz', 'ТЗ и требования'], ['money', 'Экономика'], ['docs', 'Документы'], ['log', 'История']]
      .map(([k, n]) => `<div class="tab ${state.tab === k ? 'active' : ''}" data-tab="${k}">${n}</div>`).join('')}
      </div>
      <div class="card-body" id="tabBody"></div>
    </div>
    <div class="stack">
      <div class="card">
        <div class="card-head">Соответствие профилю</div>
        <div class="card-body bars">
          ${t.match.parts.map((p) => `<div>
            <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px"><span>${esc(p.name)} <span class="muted">(${p.weight}%)</span></span><b>${p.score}</b></div>
            <div class="bar ${p.score >= 75 ? 'good' : p.score >= 45 ? 'warn' : 'bad'}"><i style="width:${p.score}%"></i></div>
            <div class="tiny muted" style="margin-top:3px">${esc(p.comment)}</div></div>`).join('')}
        </div>
      </div>
      <div class="card">
        <div class="card-head">Ключевые условия</div>
        <div class="card-body">
          <dl class="kv" style="grid-template-columns:130px 1fr">
            <dt>ОКПД2</dt><dd>${esc(t.okpd || '—')}</dd>
            <dt>Срок поставки</dt><dd>${t.delivery_days} дн.</dd>
            <dt>Оплата</dt><dd>${esc(t.payment_terms || '—')}</dd>
            <dt>Обеспечение заявки</dt><dd>${t.bid_security_pct}%</dd>
            <dt>Обеспечение контракта</dt><dd>${t.contract_security_pct}%</dd>
            <dt>Ответственность</dt><dd>${esc(t.penalty || '—')}</dd>
          </dl>
        </div>
      </div>
    </div>
  </div>`;

  $$('#tabs .tab').forEach((el) => el.addEventListener('click', () => { state.tab = el.dataset.tab; renderTender(id); }));
  $$('#tabs .tab').forEach((el) => { if (el.dataset.tab === state.tab) el.classList.add('active'); });
  $('[data-act="print"]')?.addEventListener('click', () => window.print());
  $('[data-stage]')?.addEventListener('change', async (e) => {
    await api('/tenders/' + t.id, { method: 'PATCH', body: JSON.stringify({ stage: e.target.value }) });
    toast('Этап обновлён', 'ok');
    renderTender(id);
  });
  renderTab(id);
  if (!t.analysis) runAnalyze(id, { silent: true });
}

function renderTab(id) {
  const t = state.tender;
  const a = t.analysis;
  const body = $('#tabBody');
  if (!body) return;

  if (state.tab === 'ai') {
    body.innerHTML = `
      <div class="no-print" style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
        <button class="btn primary sm" data-analyze>${a ? '✳ Обновить заключение ИИ' : '✳ Запустить анализ ИИ'}</button>
        <input id="aiQuestion" placeholder="Свой вопрос к ИИ по этой закупке…" style="flex:1;min-width:200px;padding:7px 11px;border:1px solid var(--line);border-radius:9px;outline:none">
        <button class="btn sm" data-ask>Спросить</button>
      </div>
      ${!a ? `<div class="empty"><b>Заключения пока нет</b>ИИ читает ТЗ, сверяет с профилем и считает экономику.</div>` : `
      <div class="verdict-box ${a.verdict}">
        <h3>${a.verdict === 'go' ? '✓' : a.verdict === 'consider' ? '≈' : '✕'} ${a.verdict === 'go' ? 'Рекомендуется к участию' : a.verdict === 'consider' ? 'Требует решения владельца' : 'Участие не рекомендуется'}
          <span class="ai-badge" style="margin-left:auto">${a.source === 'llm' ? '✳ ' + esc(a.model || 'LLM') : '⚙ встроенный расчёт'} · уверенность ${a.confidence}%</span></h3>
        <p>${esc(a.summary)}</p>
      </div>
      <div class="divider"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <div><b style="font-size:13px">Почему подходит</b><div class="stack" style="margin-top:7px">${(a.match_notes || []).slice(0, 6).map((n) => `<div class="tiny" style="display:flex;gap:7px"><span style="color:var(--ok)">✓</span><span>${esc(n)}</span></div>`).join('')}</div></div>
        <div><b style="font-size:13px">Конкуренция и цена</b>
          <div class="tiny" style="margin-top:7px;display:grid;gap:6px">
            <div style="display:flex;justify-content:space-between"><span class="muted">Вероятность победы</span><b>${a.competitive?.win_probability || 0}%</b></div>
            <div style="display:flex;justify-content:space-between"><span class="muted">Ожидаемых участников</span><b>${a.competitive?.expected_bidders || '—'}</b></div>
            <div style="display:flex;justify-content:space-between"><span class="muted">Рекомендуемая цена</span><b>${money(a.pricing?.recommended_price)}</b></div>
            <div style="display:flex;justify-content:space-between"><span class="muted">Запас на торг</span><b>${a.pricing?.max_discount_pct || 0}%</b></div>
            <div class="muted" style="margin-top:2px">${esc(a.competitive?.comment || '')}</div>
            <div class="muted">${esc(a.pricing?.comment || '')}</div>
          </div>
        </div>
      </div>
      <div class="divider"></div>
      <b style="font-size:13px">Проверка требований ТЗ</b>
      <div style="margin-top:6px">${(a.requirements || []).map((r) => `
        <div class="req"><div class="req-ico ${r.status}">${r.status === 'met' ? '✓' : r.status === 'partial' ? '≈' : r.status === 'missing' ? '✕' : '?'}</div>
        <div><b>${esc(r.text)}</b><span>${esc(r.comment)}</span></div></div>`).join('')}</div>
      ${(a.risks || []).length ? `<div class="divider"></div><b style="font-size:13px">Риски</b><div style="margin-top:8px">${a.risks.map((r) => `<div class="risk ${r.level}"><span class="risk-lvl">${r.level === 'high' ? 'критично' : r.level === 'medium' ? 'средне' : 'низко'}</span><span>${esc(r.text)}</span></div>`).join('')}</div>` : ''}
      ${(a.red_flags || []).length ? `<div class="divider"></div><b style="font-size:13px">Признаки ограничения конкуренции</b><div style="margin-top:8px" class="stack">${a.red_flags.map((x) => `<div class="risk high"><span class="risk-lvl">flag</span><span>${esc(x)}</span></div>`).join('')}</div>` : ''}
      <div class="divider"></div>
      <div class="split" style="grid-template-columns:1fr 1fr;gap:14px">
        <div><b style="font-size:13px">Что делать дальше</b><ol style="margin:8px 0 0;padding-left:18px;font-size:12.8px;color:var(--ink-2);display:grid;gap:5px">${(a.next_steps || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ol></div>
        <div><b style="font-size:13px">Уточнить</b><ul style="margin:8px 0 0;padding-left:18px;font-size:12.8px;color:var(--ink-2);display:grid;gap:5px">${(a.questions || []).length ? (a.questions || []).map((s) => `<li>${esc(s)}</li>`).join('') : '<li>Вопросов нет</li>'}</ul>
        <div class="tiny muted" style="margin-top:8px">${esc(a.margin_comment || '')}</div></div>
      </div>`}`;
    body.querySelector('[data-analyze]')?.addEventListener('click', () => runAnalyze(id));
    body.querySelector('[data-ask]')?.addEventListener('click', () => runAnalyze(id, { question: $('#aiQuestion').value.trim() }));
  }

  if (state.tab === 'tz') {
    body.innerHTML = `
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
        <button class="btn sm" data-toggle-tz>Показать полный текст ТЗ</button>
        <span class="chip plain">${(t.files || []).length} файлов в пакете</span>
      </div>
      <b style="font-size:13px">Формализованные требования</b>
      <div style="margin-top:6px">${(t.requirements || []).length ? t.requirements.map((r) => {
      const found = (t.analysis?.requirements || []).find((x) => x.text === r.text);
      return `<div class="req"><div class="req-ico ${found?.status || 'unknown'}">${found?.status === 'met' ? '✓' : found?.status === 'partial' ? '≈' : found?.status === 'missing' ? '✕' : '?'}</div>
            <div><b>${esc(r.text)}</b><span>${esc(found?.comment || 'без оценки — запустите ИИ')}</span></div></div>`;
    }).join('') : '<div class="empty">Требования не выделены</div>'}</div>
      <div class="divider"></div>
      <b style="font-size:13px">Описание закупки</b>
      <p class="tiny muted">${esc(t.description || '—')}</p>
      <div id="tzFull" style="display:none;margin-top:10px">
        <b style="font-size:13px">Текст технического задания</b>
        <pre style="white-space:pre-wrap;font-family:inherit;font-size:12.8px;background:#fbfcfe;border:1px solid var(--line-2);border-radius:10px;padding:12px;line-height:1.55;max-height:420px;overflow:auto">${esc(t.tz_text || 'Текст ТЗ не распознан')}</pre>
      </div>`;
    body.querySelector('[data-toggle-tz]')?.addEventListener('click', (e) => {
      const el = $('#tzFull'); el.style.display = el.style.display === 'none' ? 'block' : 'none';
      e.currentTarget.textContent = el.style.display === 'none' ? 'Показать полный текст ТЗ' : 'Скрыть текст ТЗ';
    });
  }

  if (state.tab === 'money') {
    const m = t.margin;
    const e = state.company.economics;
    body.innerHTML = `
      <div class="split" style="grid-template-columns:1fr 1fr;gap:18px">
        <div>
          <div class="margin-big"><b style="color:${m.margin_pct >= m.min_margin_pct ? 'var(--ok)' : 'var(--bad)'}">${m.margin_pct}%</b><span>маржа · ${moneyFull(m.margin)}</span></div>
          <div class="tiny muted" style="margin:2px 0 12px">${esc(m.comment)}</div>
          ${m.lines.map((l) => `<div class="mline"><span>${esc(l.name)}${l.region_factor && l.region_factor !== 1 ? ` <span class="chip plain" style="font-size:10px">×${l.region_factor}</span>` : ''}</span><span class="v">${m.region_factor && l.pct ? '' : ''}${moneyFull(l.value)}</span></div>`).join('')}
          <div class="mline"><span>Итого себестоимость</span><span class="v">${moneyFull(m.cost_total)}</span></div>
          <div class="mline total"><span>Маржа</span><span class="v" style="color:${m.margin >= 0 ? 'var(--ok)' : 'var(--bad)'}">${moneyFull(m.margin)}</span></div>
          <div class="divider"></div>
          <dl class="kv" style="grid-template-columns:1fr auto;font-size:12.8px">
            <dt>Оборотный капитал</dt><dd>${moneyFull(m.working_capital)}</dd>
            <dt>Безубыточная цена (с НДС)</dt><dd>${moneyFull(m.breakeven_price)}</dd>
            <dt>Цена контракта</dt><dd>${moneyFull(m.price)}${m.vat ? ` <span class="muted tiny">(в т.ч. НДС ${money(m.vat)})</span>` : ''}</dd>
          </dl>
        </div>
        <div>
          <b style="font-size:13px">Калькулятор «а если»</b>
          <p class="tiny muted" style="margin:4px 0 12px">Меняйте параметры — маржа пересчитывается запросом к серверу.</p>
          <div class="stack">
            <div><div style="display:flex;justify-content:space-between;font-size:12.5px"><span>Цена контракта</span><b id="pPrice">${moneyFull(m.price)}</b></div>
              <input type="range" id="sPrice" min="${Math.round(m.cost_total * 0.9)}" max="${Math.round(m.price * 1.15)}" step="10000" value="${Math.round(m.price)}"></div>
            <div><div style="display:flex;justify-content:space-between;font-size:12.5px"><span>Материалы, %</span><b id="pMat">${e.material_pct}%</b></div>
              <input type="range" id="sMat" min="20" max="80" value="${e.material_pct}"></div>
            <div><div style="display:flex;justify-content:space-between;font-size:12.5px"><span>Оплата труда, %</span><b id="pLab">${e.labor_pct}%</b></div>
              <input type="range" id="sLab" min="3" max="40" value="${e.labor_pct}"></div>
            <div><div style="display:flex;justify-content:space-between;font-size:12.5px"><span>Накладные, %</span><b id="pOh">${e.overhead_pct}%</b></div>
              <input type="range" id="sOh" min="5" max="35" value="${e.overhead_pct}"></div>
            <div><div style="display:flex;justify-content:space-between;font-size:12.5px"><span>Логистика, %</span><b id="pLog">${e.logistics_pct}%</b></div>
              <input type="range" id="sLog" min="0" max="25" value="${e.logistics_pct}"></div>
          </div>
          <div class="divider"></div>
          <div id="calcOut" class="stack tiny"></div>
        </div>
      </div>`;
    const recalc = async () => {
      const ov = {
        price: +$('#sPrice').value, material_pct: +$('#sMat').value, labor_pct: +$('#sLab').value,
        overhead_pct: +$('#sOh').value, logistics_pct: +$('#sLog').value,
      };
      $('#pPrice').textContent = moneyFull(ov.price); $('#pMat').textContent = ov.material_pct + '%';
      $('#pLab').textContent = ov.labor_pct + '%'; $('#pOh').textContent = ov.overhead_pct + '%'; $('#pLog').textContent = ov.logistics_pct + '%';
      const { margin: nm } = await api('/tenders/' + id + '/margin', { method: 'POST', body: JSON.stringify({ overrides: ov }) });
      $('#calcOut').innerHTML = `
        <div style="display:flex;justify-content:space-between"><span class="muted">Маржа</span><b style="color:${nm.margin_pct >= nm.min_margin_pct ? 'var(--ok)' : 'var(--bad)'}">${nm.margin_pct}% · ${money(nm.margin)}</b></div>
        <div style="display:flex;justify-content:space-between"><span class="muted">Себестоимость</span><b>${money(nm.cost_total)}</b></div>
        <div style="display:flex;justify-content:space-between"><span class="muted">Оборотные средства</span><b>${money(nm.working_capital)}</b></div>
        <div class="muted">${esc(nm.comment)}</div>`;
    };
    ['sPrice', 'sMat', 'sLab', 'sOh', 'sLog'].forEach((sid) => $('#' + sid)?.addEventListener('input', recalc));
    recalc();
  }

  if (state.tab === 'docs') {
    body.innerHTML = `
      <b style="font-size:13px">Файлы закупки</b>
      <div class="stack" style="margin-top:8px">
        ${(t.files || []).length ? t.files.map((f) => `<div style="display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:10px">
          <div style="width:32px;height:32px;border-radius:8px;background:#eef2f7;display:grid;place-items:center;font-size:11px;font-weight:700;color:var(--ink-2)">${esc((f.name.split('.').pop() || '').toUpperCase())}</div>
          <div style="flex:1"><b style="font-size:12.8px">${esc(f.name)}</b><div class="tiny muted">${esc(f.kind || 'документ')} · ${Math.round((f.size || 0) / 1024)} КБ</div></div>
          <span class="chip plain" style="cursor:pointer" onclick="toast('В демо файл не скачивается — в боевой версии отдаётся с площадки')">Скачать</span>
        </div>`).join('') : '<div class="empty">Файлы не приложены</div>'}
      </div>
      <div class="divider"></div>
      <b style="font-size:13px">Что готовить в заявку</b>
      <ul style="margin:8px 0 0;padding-left:18px;font-size:12.8px;color:var(--ink-2);display:grid;gap:5px">
        ${((t.analysis?.documents_needed) || ['Заявка по форме площадки', 'Сертификаты на продукцию', 'Справка об опыте', 'Выписка ЕГРЮЛ']).map((d) => `<li>${esc(d)}</li>`).join('')}
      </ul>`;
  }

  if (state.tab === 'log') {
    body.innerHTML = `
      <b style="font-size:13px">Заметка по закупке</b>
      <textarea id="note" rows="4" style="width:100%;margin-top:8px;padding:10px;border:1px solid var(--line);border-radius:10px;outline:none" placeholder="Комментарий для команды: что уточнить, кто отвечает, какие риски…">${esc(t.note || '')}</textarea>
      <div style="display:flex;gap:8px;margin-top:8px"><button class="btn primary sm" data-save-note>Сохранить заметку</button></div>
      <div class="divider"></div>
      <b style="font-size:13px">История событий</b>
      <div class="feed" style="margin-top:8px">
        ${(t.events || []).map((e) => `<div class="feed-item"><div class="feed-ico ${e.type}">${e.type === 'ai' ? '✳' : e.type === 'found' ? '⌕' : '•'}</div>
          <div class="feed-main"><b>${esc(e.text)}</b><small>${esc(e.created_at.replace('T', ' ').slice(0, 16))}</small></div></div>`).join('') || '<div class="empty">Событий нет</div>'}
      </div>`;
    body.querySelector('[data-save-note]')?.addEventListener('click', async () => {
      await api('/tenders/' + id, { method: 'PATCH', body: JSON.stringify({ note: $('#note').value }) });
      toast('Заметка сохранена', 'ok');
    });
  }
}

async function runAnalyze(id, opts = {}) {
  const btn = document.querySelector('[data-analyze]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> ИИ работает…'; }
  try {
    const res = await api('/tenders/' + id + '/analyze', { method: 'POST', body: JSON.stringify({ question: opts.question || null, force_llm: true }) });
    if (res.warning) toast(res.warning, res.analysis?.source === 'llm' ? '' : 'err', 7000);
    else if (!opts.silent) toast('Заключение готово', 'ok');
    await renderTender(id);
    state.tab = 'ai';
    renderTab(id);
  } catch (e) {
    toast('Ошибка анализа: ' + e.message, 'err');
    if (btn) { btn.disabled = false; btn.textContent = '✳ Повторить анализ ИИ'; }
  }
}

/* ================= PARSE MODAL ================= */
function openParserModal() {
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.innerHTML = `
  <div class="modal">
    <div class="modal-head"><h3>Добавить закупку</h3><div style="flex:1"></div><button class="btn ghost sm" data-close>✕</button></div>
    <div class="modal-body">
      <p class="tiny muted" style="margin-top:0">Вставьте ссылку на закупку или текст извещения/ТЗ — парсер вытащит сумму, ОКПД2, сроки и требования, после чего ИИ сразу даст заключение.</p>
      <div class="field"><label>Ссылка на закупку (ЕИС, ЭТП, корпоративный портал)</label>
        <input id="pUrl" placeholder="https://zakupki.gov.ru/epz/order/notice/…"></div>
      <div class="field"><label>или текст извещения / технического задания</label>
        <textarea id="pText" rows="8" placeholder="Вставьте текст закупки: наименование, НМЦК, сроки, требования к сертификатам, условия поставки…"></textarea></div>
      <div class="field"><label>или файл ТЗ (.docx, .pdf, .txt)</label>
        <input type="file" id="pFile" accept=".docx,.pdf,.txt"></div>
      <div class="tiny muted">Демо-режим: ссылки на реальные ЭТП могут требовать авторизации, поэтому надёжнее вставлять текст.</div>
    </div>
    <div class="modal-foot"><button class="btn" data-close>Отмена</button><button class="btn primary" data-run>Распознать и проанализировать</button></div>
  </div>`;
  document.body.appendChild(bg);
  const close = () => bg.remove();
  $$('[data-close]', bg).forEach((b) => b.addEventListener('click', close));
  bg.addEventListener('click', (e) => { if (e.target === bg) close(); });
  $('[data-run]', bg).addEventListener('click', async (e) => {
    const url = $('#pUrl').value.trim();
    const text = $('#pText').value.trim();
    const file = $('#pFile').files[0];
    const bt = e.currentTarget;
    bt.disabled = true; bt.innerHTML = '<span class="spinner"></span> Распознаём…';
    try {
      let res;
      if (file) {
        const fd = new FormData(); fd.append('file', file);
        res = await api('/tenders/parse-file', { method: 'POST', body: fd });
      } else {
        res = await api('/tenders/parse', { method: 'POST', body: JSON.stringify({ url, text }) });
      }
      toast('Закупка распознана: ' + res.tender.title.slice(0, 60), 'ok');
      if (res.note) toast(res.note, '', 6000);
      close();
      await loadTenders();
      location.hash = '#/tender/' + res.tender.id;
    } catch (err) {
      toast(err.message, 'err', 6000);
      bt.disabled = false; bt.textContent = 'Распознать и проанализировать';
    }
  });
}

/* ================= PROFILE ================= */
async function renderProfile() {
  state.draft = JSON.parse(JSON.stringify(state.company || {}));
  const content = $('#content');
  const steps = ['Производство', 'Номенклатура', 'География и опыт', 'Экономика', 'Скоринг ИИ'];
  content.innerHTML = `
  <div class="page-head"><div><h2>Профиль завода</h2><p>Чем точнее профиль, тем точнее ИИ отбирает закупки и считает маржу</p></div>
    <div class="spacer"></div>
    <button class="btn primary" data-save>Сохранить профиль</button></div>
  <div class="steps">${steps.map((s, i) => `<div class="step ${state.profileStep === i + 1 ? 'active' : ''} ${state.company?.onboarding_done ? 'done' : ''}" data-step="${i + 1}"><i>${i + 1}</i>${s}</div>`).join('')}</div>
  <div id="stepBody"></div>`;
  $$('.step').forEach((el) => el.addEventListener('click', () => { state.profileStep = +el.dataset.step; renderProfileStep(); $$('.step').forEach((s) => s.classList.toggle('active', +s.dataset.step === state.profileStep)); }));
  $('[data-save]').addEventListener('click', saveProfile);
  renderProfileStep();
}

function f(path, label, opts = {}) {
  const val = path.split('.').reduce((o, k) => (o || {})[k], state.draft) ?? '';
  const type = opts.type || 'text';
  if (type === 'select') {
    return `<div class="field ${opts.full ? 'full' : ''}"><label>${label}</label><select data-f="${path}">${opts.options.map((o) => `<option value="${esc(o.value ?? o)}" ${String(val) === String(o.value ?? o) ? 'selected' : ''}>${esc(o.label ?? o)}</option>`).join('')}</select>${opts.hint ? `<div class="hint">${opts.hint}</div>` : ''}</div>`;
  }
  if (type === 'textarea') {
    return `<div class="field full"><label>${label}</label><textarea data-f="${path}" rows="${opts.rows || 3}" placeholder="${esc(opts.placeholder || '')}">${esc(val)}</textarea>${opts.hint ? `<div class="hint">${opts.hint}</div>` : ''}</div>`;
  }
  return `<div class="field ${opts.full ? 'full' : ''}"><label>${label}</label><input type="${type}" data-f="${path}" value="${esc(val)}" placeholder="${esc(opts.placeholder || '')}" ${opts.step ? `step="${opts.step}"` : ''}>${opts.hint ? `<div class="hint">${opts.hint}</div>` : ''}</div>`;
}

function renderProfileStep() {
  const d = state.draft;
  const body = $('#stepBody');
  const step = state.profileStep;
  const regions = state.catalog?.regions || [];
  const cats = state.catalog?.categories || [];

  if (step === 1) {
    body.innerHTML = `<div class="card"><div class="card-body">
      <b>Основные данные</b><p class="tiny muted" style="margin:4px 0 14px">Эти данные попадают в оценку опыта и в заявки.</p>
      <div class="form-grid">
        ${f('name', 'Название завода', { placeholder: 'ООО «ПромАрматура»' })}
        ${f('inn', 'ИНН', { placeholder: '5260000000' })}
        ${f('region', 'Регион базы', { type: 'select', options: [{ value: '', label: '— выберите —' }, ...regions] })}
        ${f('site', 'Сайт', { placeholder: 'zavod.ru' })}
        ${f('contact', 'Контакт для закупок', { placeholder: 'Иванов И.И., +7…' })}
        ${f('experience_years', 'Опыт работы, лет', { type: 'number' })}
        ${f('lead_days', 'Производственный цикл, дней', { type: 'number', hint: 'Учитывается при оценке сроков поставки' })}
        ${f('max_order_sum', 'Максимальная сумма заказа, ₽', { type: 'number', hint: 'Выше этой суммы закупки помечаются как рискованные' })}
        ${f('min_order_sum', 'Минимальная сумма заказа, ₽', { type: 'number' })}
        ${f('capacity_note', 'Мощности и ограничения', { full: true, type: 'textarea', placeholder: '2 линии мехобработки, литьё ЧПУ, покраска…' })}
        ${f('references', 'Ключевые референсы', { full: true, type: 'textarea', placeholder: 'Газпром, Т Плюс, Водоканалы — 12 контрактов за 3 года' })}
      </div></div></div>`;
  }

  if (step === 2) {
    body.innerHTML = `<div class="card"><div class="card-body">
      <b>Направления производства</b><p class="tiny muted" style="margin:4px 0 12px">Отметьте, что производите. ИИ использует это для отбора по ОКПД2 и ключевым словам в ТЗ.</p>
      <div class="checks">
        ${cats.map((c) => `<div class="check-card ${(d.categories || []).includes(c.id) ? 'on' : ''}" data-cat="${c.id}">
          <input type="checkbox" ${(d.categories || []).includes(c.id) ? 'checked' : ''} style="margin-top:2px">
          <div><b>${esc(c.name)}</b><small>ОКПД2: ${c.okpd.slice(0, 3).join(', ')}</small></div></div>`).join('')}
      </div>
      <div class="divider"></div>
      ${f('products', 'Конкретная продукция (через запятую)', { full: true, type: 'textarea', placeholder: 'задвижки чугунные Ду 50–300, затворы дисковые, краны шаровые, фланцы' })}
      <div class="divider"></div>
      <b style="font-size:13px">Коды ОКПД2</b>
      <p class="tiny muted" style="margin:3px 0 6px">Добавляйте свои коды — совпадение ОКПД2 даёт максимальный вес в скоринге.</p>
      <div style="display:flex;gap:8px"><input id="okpdIn" placeholder="28.14.13" style="flex:1;padding:9px 11px;border:1px solid var(--line);border-radius:9px;outline:none"><button class="btn sm" data-add="okpd">Добавить</button></div>
      <div class="tags">${(d.okpd || []).map((x) => `<span class="tag"><b>${esc(x)}</b><span class="x" data-del="okpd" data-val="${esc(x)}">✕</span></span>`).join('') || '<span class="tiny muted">Пока пусто</span>'}</div>
      <div class="divider"></div>
      <b style="font-size:13px">Сертификаты и допуски</b>
      <div style="display:flex;gap:8px;margin-top:6px"><input id="certIn" placeholder="ТР ТС 010/2011" style="flex:1;padding:9px 11px;border:1px solid var(--line);border-radius:9px;outline:none"><button class="btn sm" data-add="certificates">Добавить</button></div>
      <div class="tags">${(d.certificates || []).map((x) => `<span class="tag"><b>${esc(x)}</b><span class="x" data-del="certificates" data-val="${esc(x)}">✕</span></span>`).join('') || '<span class="tiny muted">Пока пусто</span>'}</div>
      <div class="divider"></div>
      <b style="font-size:13px">Лицензии, СРО, аккредитации</b>
      <div style="display:flex;gap:8px;margin-top:6px"><input id="licIn" placeholder="СРО на монтаж" style="flex:1;padding:9px 11px;border:1px solid var(--line);border-radius:9px;outline:none"><button class="btn sm" data-add="licenses">Добавить</button></div>
      <div class="tags">${(d.licenses || []).map((x) => `<span class="tag"><b>${esc(x)}</b><span class="x" data-del="licenses" data-val="${esc(x)}">✕</span></span>`).join('') || '<span class="tiny muted">Пока пусто</span>'}</div>
    </div></div>`;
    $$('[data-cat]').forEach((el) => el.addEventListener('click', (ev) => {
      ev.preventDefault();
      const id = el.dataset.cat;
      const arr = state.draft.categories || (state.draft.categories = []);
      const i = arr.indexOf(id);
      if (i >= 0) arr.splice(i, 1); else arr.push(id);
      el.classList.toggle('on');
      const cb = el.querySelector('input'); if (cb) cb.checked = arr.includes(id);
    }));
    [['okpd', 'okpdIn'], ['certificates', 'certIn'], ['licenses', 'licIn']].forEach(([key, inputId]) => {
      const add = () => {
        const el = $('#' + inputId); const v = el.value.trim(); if (!v) return;
        state.draft[key] = state.draft[key] || [];
        if (!state.draft[key].includes(v)) state.draft[key].push(v);
        el.value = ''; renderProfileStep();
      };
      body.querySelector(`[data-add="${key}"]`)?.addEventListener('click', add);
      $('#' + inputId)?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
    });
    $$('[data-del]', body).forEach((el) => el.addEventListener('click', () => {
      const k = el.dataset.del; state.draft[k] = (state.draft[k] || []).filter((x) => x !== el.dataset.val); renderProfileStep();
    }));
  }

  if (step === 3) {
    body.innerHTML = `<div class="card"><div class="card-body">
      <b>География поставок</b><p class="tiny muted" style="margin:4px 0 12px">Отметьте регионы, куда реально возите. Остальные закупки получат меньший вес и удорожание логистики.</p>
      <div class="checks" style="max-height:340px;overflow:auto;padding-right:4px">
        ${regions.map((r) => `<div class="check-card ${(d.delivery_regions || []).includes(r) ? 'on' : ''}" data-reg="${esc(r)}">
          <input type="checkbox" ${(d.delivery_regions || []).includes(r) ? 'checked' : ''} style="margin-top:2px"><div><b>${esc(r)}</b></div></div>`).join('')}
      </div>
      <div style="display:flex;gap:8px;margin-top:10px"><button class="btn sm" data-selall>Выбрать все</button><button class="btn sm" data-clear>Снять все</button></div>
    </div></div>`;
    $$('[data-reg]').forEach((el) => el.addEventListener('click', (ev) => {
      ev.preventDefault();
      const r = el.dataset.reg;
      const arr = state.draft.delivery_regions || (state.draft.delivery_regions = []);
      const i = arr.indexOf(r); if (i >= 0) arr.splice(i, 1); else arr.push(r);
      el.classList.toggle('on'); const cb = el.querySelector('input'); if (cb) cb.checked = arr.includes(r);
    }));
    body.querySelector('[data-selall]')?.addEventListener('click', () => { state.draft.delivery_regions = [...regions]; renderProfileStep(); });
    body.querySelector('[data-clear]')?.addEventListener('click', () => { state.draft.delivery_regions = []; renderProfileStep(); });
  }

  if (step === 4) {
    const e = d.economics || {};
    body.innerHTML = `<div class="card"><div class="card-body">
      <b>Экономика сделки</b><p class="tiny muted" style="margin:4px 0 14px">Эти проценты ИИ применяет к цене каждой закупки, чтобы посчитать маржу и безубыточную цену.</p>
      <div class="form-grid">
        ${f('economics.tax_mode', 'Налоговый режим', { type: 'select', options: [{ value: 'vat', label: 'ОСНО (с НДС 20%)' }, { value: 'usn', label: 'УСН (без НДС, 6% с оборота)' }] })}
        ${f('economics.material_pct', 'Доля материалов, %', { type: 'number' })}
        ${f('economics.labor_pct', 'Оплата труда, %', { type: 'number' })}
        ${f('economics.overhead_pct', 'Накладные расходы, %', { type: 'number' })}
        ${f('economics.logistics_pct', 'Логистика (база), %', { type: 'number', hint: 'Умножается на коэффициент удалённости региона' })}
        ${f('economics.bg_rate_pct', 'Стоимость банковской гарантии, % годовых', { type: 'number', step: '0.1' })}
        ${f('economics.risk_reserve_pct', 'Резерв на риски и штрафы, %', { type: 'number' })}
        ${f('economics.min_margin_pct', 'Минимальная маржа, %', { type: 'number', hint: 'Ниже — ИИ рекомендует отказаться' })}
        ${f('economics.target_margin_pct', 'Целевая маржа, %', { type: 'number' })}
      </div>
      <div class="divider"></div>
      <div class="tiny muted">Проверка: при цене 10 млн ₽ и ваших настройках себестоимость ≈ <b id="simCost">—</b>, маржа ≈ <b id="simMargin">—</b>.</div>
    </div></div>`;
    const sim = () => {
      const price = 10_000_000;
      const net = (d.economics?.tax_mode === 'usn') ? price : price / 1.2;
      const sumPct = ['material_pct', 'labor_pct', 'overhead_pct', 'logistics_pct'].reduce((s, k) => s + Number(d.economics?.[k] || 0), 0);
      const cost = net * sumPct / 100 + net * Number(d.economics?.risk_reserve_pct || 0) / 100;
      const m = net - cost; const pct = net ? (m / net * 100) : 0;
      $('#simCost').textContent = money(cost); $('#simMargin').textContent = money(m) + ' (' + pct.toFixed(1) + '%)';
    };
    sim();
    $$('[data-f]', body).forEach((el) => el.addEventListener('input', sim));
  }

  if (step === 5) {
    const s = d.scoring || {};
    body.innerHTML = `<div class="card"><div class="card-body">
      <b>Веса скоринга</b><p class="tiny muted" style="margin:4px 0 14px">Как ИИ взвешивает факторы при оценке соответствия закупки вашему заводу.</p>
      <div class="stack" style="max-width:520px">
        ${[['w_okpd', 'Совпадение ОКПД2'], ['w_keywords', 'Совпадение по ТЗ'], ['w_sum', 'Объём к мощности'], ['w_region', 'География'], ['w_deadline', 'Сроки'], ['w_certificates', 'Сертификаты']].map(([k, n]) => `
          <div><div style="display:flex;justify-content:space-between;font-size:12.5px"><span>${n}</span><b id="w_${k}">${s[k] ?? 10}</b></div>
          <input type="range" min="0" max="50" value="${s[k] ?? 10}" data-w="${k}"></div>`).join('')}
      </div>
      <div class="divider"></div>
      <b style="font-size:13px">Порог автозапуска ИИ</b>
      <p class="tiny muted" style="margin:3px 0 6px">Закупки с соответствием ниже порога не отправляются в LLM (экономия токенов) — им даётся быстрый расчёт.</p>
      <div style="display:flex;gap:10px;align-items:center;max-width:420px">
        <input type="range" min="20" max="90" value="${state.ai?.match_threshold || 55}" id="thr" style="flex:1"><b id="thrV">${state.ai?.match_threshold || 55}</b>
      </div>
    </div></div>`;
    $$('[data-w]', body).forEach((el) => el.addEventListener('input', () => {
      const k = el.dataset.w; state.draft.scoring = state.draft.scoring || {}; state.draft.scoring[k] = +el.value;
      $('#w_' + k).textContent = el.value;
    }));
    $('#thr')?.addEventListener('input', (e) => { $('#thrV').textContent = e.target.value; state.pendingThreshold = +e.target.value; });
  }
}

function collectDraft() {
  $$('[data-f]').forEach((el) => {
    const path = el.dataset.f.split('.');
    let o = state.draft;
    for (let i = 0; i < path.length - 1; i++) { o[path[i]] = o[path[i]] || {}; o = o[path[i]]; }
    const key = path[path.length - 1];
    let v = el.value;
    if (el.type === 'number') v = v === '' ? 0 : Number(v);
    o[key] = v;
  });
  return state.draft;
}

async function saveProfile() {
  const draft = collectDraft();
  draft.onboarding_done = true;
  try {
    if (state.pendingThreshold) {
      await api('/ai/settings', { method: 'PUT', body: JSON.stringify({ match_threshold: state.pendingThreshold }) });
      state.ai = (await api('/ai/settings')).settings;
      state.pendingThreshold = null;
    }
    const res = await api('/company', { method: 'PUT', body: JSON.stringify(draft) });
    state.company = res.company;
    toast('Профиль сохранён — пересчитываю оценки закупок', 'ok');
    await loadTenders();
    renderShell();
    location.hash = '#/tenders';
  } catch (e) { toast('Ошибка сохранения: ' + e.message, 'err'); }
}

/* ================= AI SETTINGS ================= */
async function renderAiSettings() {
  const { settings } = await api('/ai/settings');
  state.ai = settings;
  const presets = state.presets;
  const content = $('#content');
  content.innerHTML = `
  <div class="page-head"><div><h2>Настройки ИИ</h2><p>Подключите модель — и анализ ТЗ станет полноценным заключением аналитика, а не шаблоном</p></div>
    <div class="spacer"></div><button class="btn" data-test>Проверить подключение</button><button class="btn primary" data-save>Сохранить</button></div>
  <div class="split">
    <div class="card"><div class="card-body">
      <b>Модель</b>
      <div class="form-grid" style="margin-top:12px">
        <div class="field"><label>Провайдер</label>
          <select id="aProvider">${Object.entries(presets).map(([k, v]) => `<option value="${k}" ${settings.provider === k ? 'selected' : ''}>${esc({ openai: 'OpenAI', deepseek: 'DeepSeek', openrouter: 'OpenRouter (агрегатор)', anthropic: 'Anthropic', gigachat: 'GigaChat (Сбер)', custom: 'Свой / self-hosted' }[k] || k)}</option>`).join('')}</select>
          <div class="hint">OpenRouter удобен: один ключ даёт доступ к Claude, GPT, Gemini, Qwen.</div>
        </div>
        <div class="field"><label>Модель</label><input id="aModel" list="modelList" value="${esc(settings.model || '')}">
          <datalist id="modelList">${(presets[settings.provider]?.models || []).map((m) => `<option value="${esc(m)}">`).join('')}</datalist></div>
        <div class="field full"><label>Base URL</label><input id="aBase" value="${esc(settings.base_url || '')}" placeholder="https://api.openai.com/v1">
          <div class="hint">Для self-hosted (vLLM, Ollama, LM Studio) укажите свой адрес — используется OpenAI-совместимый протокол.</div></div>
        <div class="field full"><label>API-ключ</label><input id="aKey" type="password" placeholder="${settings.api_key_set ? 'ключ сохранён (' + esc(settings.api_key_hint) + ') — оставьте пустым, чтобы не менять' : 'вставьте ключ'}">
          <div class="hint">Ключ хранится в базе на сервере и никогда не отдаётся в браузер.</div></div>
        <div class="field"><label>Температура</label><input id="aTemp" type="number" step="0.1" min="0" max="1" value="${settings.temperature}"></div>
        <div class="field"><label>Порог автозапуска ИИ (%)</label><input id="aThr" type="number" min="0" max="100" value="${settings.match_threshold}"></div>
        <div class="field full"><label style="display:flex;align-items:center;gap:8px"><input type="checkbox" id="aAuto" ${settings.auto_analyze ? 'checked' : ''} style="width:auto"> Автоматически анализировать новые закупки</label></div>
      </div>
    </div></div>
    <div class="stack">
      <div class="card"><div class="card-head">Статус</div><div class="card-body stack">
        <div style="display:flex;gap:9px;align-items:center"><span class="dot ${settings.api_key_set ? 'on' : 'off'}"></span>
          <b>${settings.api_key_set ? 'Ключ задан' : 'Ключ не задан'}</b></div>
        <div class="tiny muted">Без ключа система работает в базовом режиме: скоринг, проверка требований и расчёт маржи считаются встроенным движком. С ключом добавляется чтение ТЗ, поиск «заточек», оценка конкуренции и рекомендации.</div>
        <div id="testOut" class="tiny"></div>
      </div></div>
      <div class="card"><div class="card-head">Где взять ключ</div><div class="card-body tiny muted" style="display:grid;gap:7px">
        <div><b style="color:var(--ink)">OpenAI</b> — platform.openai.com → API keys</div>
        <div><b style="color:var(--ink)">DeepSeek</b> — platform.deepseek.com (самое дешёвое для объёмов)</div>
        <div><b style="color:var(--ink)">OpenRouter</b> — openrouter.ai/keys (Claude/GPT/Gemini одним ключом)</div>
        <div><b style="color:var(--ink)">Anthropic</b> — console.anthropic.com</div>
        <div><b style="color:var(--ink)">GigaChat</b> — требует OAuth-авторизацию, для РФ-контура</div>
        <div style="margin-top:4px">Если данные не должны уходить за границу — поднимите локальную модель (Ollama/vLLM) и укажите её Base URL.</div>
      </div></div>
    </div>
  </div>`;
  $('#aProvider').addEventListener('change', (e) => {
    const p = presets[e.target.value];
    if (p) { $('#aBase').value = p.base_url; $('#aModel').value = p.model; $('#modelList').innerHTML = (p.models || []).map((m) => `<option value="${esc(m)}">`).join(''); }
  });
  $('[data-save]').addEventListener('click', async () => {
    const payload = {
      provider: $('#aProvider').value, model: $('#aModel').value, base_url: $('#aBase').value,
      temperature: +$('#aTemp').value, match_threshold: +$('#aThr').value, auto_analyze: $('#aAuto').checked,
    };
    const key = $('#aKey').value.trim();
    if (key) payload.api_key = key;
    await api('/ai/settings', { method: 'PUT', body: JSON.stringify(payload) });
    toast('Настройки ИИ сохранены', 'ok');
    state.ai = (await api('/ai/settings')).settings;
    renderShell(); location.hash = '#/ai';
  });
  $('[data-test]').addEventListener('click', async (e) => {
    const b = e.currentTarget; b.disabled = true; b.innerHTML = '<span class="spinner dark"></span> Проверяем…';
    const out = $('#testOut');
    try {
      const r = await api('/ai/test', { method: 'POST' });
      out.innerHTML = r.ok ? `<span style="color:var(--ok)">✓ Модель ответила: «${esc(r.answer)}» (${esc(r.model)})</span>`
        : `<span style="color:var(--bad)">✕ ${esc(r.error)}</span>`;
    } catch (err) { out.innerHTML = `<span style="color:var(--bad)">✕ ${esc(err.message)}</span>`; }
    b.disabled = false; b.textContent = 'Проверить подключение';
  });
}

/* ================= ANALYTICS ================= */
async function renderAnalytics() {
  const stats = await api('/stats');
  const content = $('#content');
  const t = stats.totals;
  const platforms = Object.entries(stats.by_platform).sort((a, b) => b[1].count - a[1].count);
  const maxSum = Math.max(1, ...platforms.map(([, v]) => v.sum));
  const stages = state.catalog?.stages || [];
  const stageCount = {};
  state.tenders.forEach((x) => { stageCount[x.stage] = (stageCount[x.stage] || 0) + 1; });
  content.innerHTML = `
  <div class="page-head"><div><h2>Аналитика</h2><p>Эффективность мониторинга и работы с воронкой</p></div></div>
  <div class="grid kpis">
    <div class="kpi accent"><div class="lbl">Всего закупок</div><div class="val">${t.tenders}</div><div class="sub">в мониторинге ${Object.keys(stats.by_platform).length} площадок</div></div>
    <div class="kpi good"><div class="lbl">Сумма по «идём»</div><div class="val">${money(t.sum_go)}</div><div class="sub">${t.go} закупок</div></div>
    <div class="kpi"><div class="lbl">В работе</div><div class="val">${money(t.pipeline_sum)}</div><div class="sub">подготовка и поданные заявки</div></div>
    <div class="kpi"><div class="lbl">Выиграно</div><div class="val">${money(t.won_sum)}</div><div class="sub">win rate ${t.win_rate}%</div></div>
  </div>
  <div style="height:14px"></div>
  <div class="split">
    <div class="card"><div class="card-head">Закупки по площадкам</div><div class="card-body bars">
      ${platforms.map(([k, v]) => `<div>
        <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px"><span>${esc(k)}</span><span class="muted">${v.count} · ${money(v.sum)} · «идём» ${v.go}</span></div>
        <div class="bar"><i style="width:${(v.sum / maxSum) * 100}%"></i></div></div>`).join('')}
    </div></div>
    <div class="stack">
      <div class="card"><div class="card-head">Воронка по этапам</div><div class="card-body">
        ${stages.map((s) => `<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <span style="width:110px;font-size:12.5px">${esc(s.name)}</span>
          <div class="progress" style="flex:1"><i style="width:${((stageCount[s.id] || 0) / Math.max(1, state.tenders.length)) * 100}%"></i></div>
          <b style="width:24px;text-align:right;font-size:12.5px">${stageCount[s.id] || 0}</b></div>`).join('')}
      </div></div>
      <div class="card"><div class="card-head">Распределение вердиктов</div><div class="card-body">
        <div style="display:flex;height:26px;border-radius:8px;overflow:hidden;margin-bottom:10px">
          ${[['go', t.go, '#047857'], ['consider', t.consider, '#b45309'], ['no_go', t.no_go, '#b91c1c'], ['none', t.tenders - t.analyzed, '#cbd5e1']].map(([k, v, c]) => `<div style="width:${(v / Math.max(1, t.tenders)) * 100}%;background:${c}" title="${k}: ${v}"></div>`).join('')}
        </div>
        <div class="tiny" style="display:grid;gap:5px">
          <div style="display:flex;justify-content:space-between"><span>✓ Идём</span><b>${t.go}</b></div>
          <div style="display:flex;justify-content:space-between"><span>≈ Спорно</span><b>${t.consider}</b></div>
          <div style="display:flex;justify-content:space-between"><span>✕ Не идём</span><b>${t.no_go}</b></div>
          <div style="display:flex;justify-content:space-between"><span>— Без анализа</span><b>${t.tenders - t.analyzed}</b></div>
        </div>
      </div></div>
    </div>
  </div>
  <div style="height:14px"></div>
  <div class="card"><div class="card-head">Топ возможностей</div><div class="card-body" style="padding:0">
    <div class="table-wrap"><table class="t"><thead><tr><th></th><th>Закупка</th><th>Сумма</th><th>Маржа</th><th>Вердикт</th><th>Дедлайн</th></tr></thead>
    <tbody>${stats.top.map(tenderRow).join('') || '<tr><td colspan="6"><div class="empty">Нет данных</div></td></tr>'}</tbody></table></div>
  </div></div>`;
  bindCommon(content);
}

/* ================= SYNC / MASS ANALYZE ================= */
async function scanPlatforms() {
  const btn = $('[data-act="scan"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner dark"></span> Сканируем…'; }
  try {
    const r = await api('/sync', { method: 'POST', body: JSON.stringify({}) });
    toast(r.count ? `Мониторинг: найдено ${r.count} новых закупок` : 'Новых закупок не найдено — лента актуальна', r.count ? 'ok' : '', 5000);
    await loadTenders();
    route();
  } catch (e) { toast('Ошибка сканирования: ' + e.message, 'err'); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = '⟳ Сканировать площадки'; } }
}

async function analyzeNew() {
  const btn = $('[data-act="analyze-new"]');
  await loadTenders();
  const un = state.tenders.filter((t) => !t.analysis);
  if (!un.length) { toast('Все закупки уже проанализированы'); return; }
  const limit = Math.min(un.length, 6);
  if (btn) { btn.disabled = true; }
  let done = 0;
  for (const t of un.slice(0, limit)) {
    if (btn) btn.innerHTML = `<span class="spinner"></span> Анализ ${done + 1}/${limit}…`;
    try { await api('/tenders/' + t.id + '/analyze', { method: 'POST', body: JSON.stringify({ force_llm: true }) }); } catch (e) { }
    done++;
  }
  toast(`ИИ обработал ${done} закупок`, 'ok');
  if (btn) { btn.disabled = false; btn.innerHTML = '✳ Проанализировать новые'; }
  await loadTenders();
  route();
}


/* ================= SOURCES (реальный сбор данных) ================= */
async function renderSources() {
  const content = $('#content');
  content.innerHTML = `<div class="page-head"><div><h2>Источники данных</h2><p>Реальный сбор закупок: ЕИС, выгрузки ЭТП, API площадок</p></div></div>
    <div class="card"><div class="card-body"><div class="skel" style="height:220px"></div></div></div>`;
  const [{ sources, schedule }, logsRes, notifyRes] = await Promise.all([
    api('/sources'), api('/logs'), api('/notify'),
  ]);
  state.sources = sources; state.schedule = schedule; state.notify = notifyRes.notify;
  content.innerHTML = `
  <div class="page-head">
    <div><h2>Источники данных</h2><p>Сбор закупок из ЕИС и выгрузок площадок, автоотсев ИИ и уведомления</p></div>
    <div class="spacer"></div>
    <button class="btn" data-check>Проверить доступность площадок</button>
    <button class="btn primary" data-run>⟳ Собрать сейчас</button>
  </div>

  <div class="split">
    <div class="stack">
      <div class="card">
        <div class="card-head">Источники</div>
        <div class="card-body stack" id="srcList">
          ${(sources || []).map((src) => `
            <div class="card" style="box-shadow:none;border-color:${src.enabled ? 'var(--brand-2)' : 'var(--line)'};background:${src.enabled ? 'var(--brand-soft)' : '#fff'}">
              <div class="card-body" style="padding:13px 14px">
                <div style="display:flex;gap:10px;align-items:flex-start">
                  <input type="checkbox" data-src-toggle="${src.id}" ${src.enabled ? 'checked' : ''} style="margin-top:3px;width:auto">
                  <div style="flex:1;min-width:0">
                    <b style="font-size:13.5px">${esc(src.name)}</b>
                    <div class="tiny muted" style="margin-top:2px">${esc(src.note || '')}</div>
                    ${src.type === 'eis_search' ? `
                      <div style="margin-top:8px;display:grid;gap:6px">
                        <input data-src-field="${src.id}:keywords" value="${esc((src.params.keywords || []).join(', '))}" placeholder="Ключевые слова через запятую: задвижка, затвор, арматура" style="width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-size:12.5px">
                        <div style="display:flex;gap:6px;align-items:center">
                          <span class="tiny muted" style="white-space:nowrap">Страниц</span>
                          <input type="number" data-src-field="${src.id}:pages" value="${src.params.pages || 2}" min="1" max="5" style="width:80px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-size:12.5px" title="Страниц выдачи">
                          <span class="tiny muted" style="white-space:nowrap">Мин. сумма, ₽</span>
                          <input type="number" data-src-field="${src.id}:min_sum" value="${src.params.min_sum || 0}" style="flex:1;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-size:12.5px" title="Мин. сумма закупки, ₽">
                        </div>
                      </div>` : ''}
                    ${src.type === 'etp_api' ? `
                      <div style="margin-top:8px;display:grid;gap:6px">
                        <input data-src-field="${src.id}:url" value="${esc(src.params.url || '')}" placeholder="https://api.etp.ru/v1/purchases?status=active" style="width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-size:12.5px">
                        <input data-src-field="${src.id}:token" value="${esc(src.params.token || '')}" placeholder="Bearer-токен площадки" style="width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-size:12.5px">
                      </div>` : ''}
                    ${src.type === 'files_watch' ? `
                      <div class="tiny" style="margin-top:8px">Папка <code>${esc(state.logs?.inbox_path || 'inbox')}</code> — файлы обрабатываются автоматически. Можно загрузить прямо здесь:
                        <div style="margin-top:7px;display:flex;gap:7px;align-items:center;flex-wrap:wrap">
                          <input type="file" id="ingestFile" accept=".xml,.csv,.txt,.zip" style="font-size:12px">
                          <button class="btn sm" data-upload>Загрузить и разобрать</button>
                        </div>
                      </div>` : ''}
                  </div>
                </div>
              </div>
            </div>`).join('')}
        </div>
      </div>

      <div class="card">
        <div class="card-head">Расписание и уведомления</div>
        <div class="card-body">
          <div class="form-grid">
            <div class="field"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="schEnabled" ${schedule.enabled ? 'checked' : ''} style="width:auto"> Автоматический сбор по расписанию</label></div>
            <div class="field"><label>Интервал, минут</label><input type="number" id="schInterval" min="5" value="${schedule.interval_minutes || 60}"></div>
            <div class="field"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="schNotify" ${schedule.notify_new ? 'checked' : ''} style="width:auto"> Уведомлять о новых закупках</label></div>
            <div class="field"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="schAnalyze" ${schedule.auto_analyze ? 'checked' : ''} style="width:auto"> Автоматически отсеивать через ИИ</label></div>
            <div class="field"><label>Telegram: токен бота</label><input type="password" id="tgToken" placeholder="${state.notify.telegram_token_set ? 'токен сохранён — оставьте пустым' : '123456789:AAH...'}"></div>
            <div class="field"><label>Telegram: chat_id</label><input id="tgChat" value="${esc(state.notify.telegram_chat_hint || state.notify.telegram_chat || '')}" placeholder="например 123456789 или -1001234567890"></div>
          </div>
          <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
            <button class="btn primary" data-save-all>Сохранить настройки</button>
            <button class="btn" data-tg-test>Проверить уведомление</button>
          </div>
          <div class="tiny muted" style="margin-top:8px">Бот создаётся в @BotFather, chat_id узнаётся у @userinfobot. Уведомления приходят по закупкам с вердиктом «идём».</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">Журнал сбора</div>
        <div class="card-body" style="padding:0">
          <div class="table-wrap" style="max-height:320px"><table class="t">
            <thead><tr><th>Время</th><th>Источник</th><th>Результат</th><th>Найдено</th><th>Новых</th></tr></thead>
            <tbody>${(logsRes.logs || []).length ? logsRes.logs.map((l) => `<tr>
              <td class="tiny nowrap">${esc((l.created_at || '').replace('T', ' ').slice(5, 16))}</td>
              <td class="tiny">${esc(l.source_id)}</td>
              <td class="tiny" style="color:${l.status === 'ok' ? 'var(--ok)' : l.status === 'error' ? 'var(--bad)' : 'var(--warn)'}">${esc(l.message)}</td>
              <td class="num">${l.found}</td><td class="num">${l.added}</td></tr>`).join('')
              : '<tr><td colspan="5"><div class="empty">Пока не было запусков</div></td></tr>'}</tbody>
          </table></div>
        </div>
      </div>
    </div>

    <div class="stack">
      <div class="card">
        <div class="card-head">Как это работает</div>
        <div class="card-body tiny muted" style="display:grid;gap:9px">
          <div><b style="color:var(--ink)">1. ЕИС (44-ФЗ и 223-ФЗ)</b> — официальный источник. Живой поиск работает с российского IP: zakupki.gov.ru блокирует зарубежные адреса.</div>
          <div><b style="color:var(--ink)">2. Выгрузки ЭТП</b> — экспорт Excel/CSV с Росэлторга, B2B-Center, ЭТП ГПБ, ТЭК-Торга загружается сюда же и разбирается автоматически.</div>
          <div><b style="color:var(--ink)">3. XML ЕИС</b> — на карточке закупки на zakupki.gov.ru есть кнопка «Скачать XML». Файл загружается или кладётся в папку inbox.</div>
          <div><b style="color:var(--ink)">4. API площадки</b> — если у вас есть договор и токен, укажите адрес и путь к списку закупок.</div>
          <div style="padding-top:6px;border-top:1px solid var(--line-2)">Дедупликация идёт по паре «площадка + номер закупки», повторы не создаются.</div>
        </div>
      </div>
      <div class="card">
        <div class="card-head">Файлы в inbox</div>
        <div class="card-body">
          ${(logsRes.inbox || []).length ? logsRes.inbox.map((f) => `<div class="tiny" style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px dashed var(--line-2)"><span>${esc(f.name)}</span><span class="muted">${Math.round(f.size / 1024)} КБ</span></div>`).join('')
            : '<div class="tiny muted">Папка пуста. Загрузите XML/CSV — или положите файлы в папку вручную.</div>'}
          <button class="btn sm" data-scan-inbox style="margin-top:10px">Обработать папку</button>
        </div>
      </div>
      <div class="card" id="checkOut" style="display:none">
        <div class="card-head">Доступность площадок</div>
        <div class="card-body" id="checkBody"></div>
      </div>
    </div>
  </div>`;
  bindSources();
}

function bindSources() {
  const readSrc = () => {
    const srcs = JSON.parse(JSON.stringify(state.sources || []));
    $$('[data-src-toggle]').forEach((el) => {
      const s = srcs.find((x) => x.id === el.dataset.srcToggle);
      if (s) s.enabled = el.checked;
    });
    $$('[data-src-field]').forEach((el) => {
      const [id, field] = el.dataset.srcField.split(':');
      const s = srcs.find((x) => x.id === id);
      if (!s) return;
      let v = el.value;
      if (field === 'keywords') v = v.split(',').map((x) => x.trim()).filter(Boolean);
      else if (field === 'pages' || field === 'min_sum') v = Number(v) || 0;
      s.params[field] = v;
    });
    return srcs;
  };
  const saveAll = async (silent) => {
    const payload = {
      sources: readSrc(),
      schedule: {
        enabled: $('#schEnabled').checked,
        interval_minutes: Number($('#schInterval').value) || 60,
        notify_new: $('#schNotify').checked,
        auto_analyze: $('#schAnalyze').checked,
      },
    };
    await api('/sources', { method: 'PUT', body: JSON.stringify(payload) });
    const nPayload = { telegram_chat: $('#tgChat').value.trim() };
    const tok = $('#tgToken').value.trim();
    if (tok) nPayload.telegram_token = tok;
    await api('/notify', { method: 'PUT', body: JSON.stringify(nPayload) });
    if (!silent) toast('Настройки источников сохранены', 'ok');
  };
  $('[data-save-all]')?.addEventListener('click', () => saveAll(false));
  $('[data-run]')?.addEventListener('click', async (e) => {
    const b = e.currentTarget; b.disabled = true; b.innerHTML = '<span class="spinner"></span> Собираем…';
    try {
      await saveAll(true);
      const r = await api('/sources/run', { method: 'POST', body: JSON.stringify({ analyze: true }) });
      const errs = (r.results || []).flatMap((x) => (x.errors || []).map((e) => x.source + ': ' + e));
      toast(r.new ? `Собрано новых закупок: ${r.new}, проанализировано: ${r.analyzed}` : 'Новых закупок не найдено',
        r.new ? 'ok' : '', 6000);
      if (errs.length) toast(errs.join(' | '), 'err', 12000);
      await loadTenders();
      await renderSources();
    } catch (err) { toast(err.message, 'err'); b.disabled = false; }
  });
  $('[data-upload]')?.addEventListener('click', async () => {
    const f = $('#ingestFile')?.files?.[0];
    if (!f) { toast('Выберите файл XML, CSV или ZIP', 'err'); return; }
    const fd = new FormData(); fd.append('file', f);
    try {
      const r = await api('/ingest/upload', { method: 'POST', body: fd });
      toast(`Разобрано ${r.recognized}, добавлено новых ${r.added}` + (r.analyzed ? `, отсеяно ИИ ${r.analyzed}` : ''), 'ok', 6000);
      await loadTenders(); await renderSources();
    } catch (e) { toast(e.message, 'err', 8000); }
  });
  $('[data-scan-inbox]')?.addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    try {
      const r = await api('/ingest/scan-inbox', { method: 'POST' });
      toast(`Файлов ${(r.files || []).length}, добавлено ${r.added}`, 'ok');
      if ((r.errors || []).length) toast(r.errors.join(' | '), 'err', 8000);
      await loadTenders(); await renderSources();
    } catch (err) { toast(err.message, 'err'); }
  });
  $('[data-check]')?.addEventListener('click', async (e) => {
    const b = e.currentTarget; b.disabled = true; b.innerHTML = '<span class="spinner dark"></span> Проверяем…';
    try {
      const r = await api('/sources/check');
      $('#checkOut').style.display = '';
      $('#checkBody').innerHTML = r.results.map((x) => `<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed var(--line-2);font-size:12.8px">
        <span>${esc(x.name)}</span>
        <b style="color:${x.ok ? 'var(--ok)' : 'var(--bad)'}">${x.ok ? 'доступен' : (x.status ? 'HTTP ' + x.status : 'нет связи')}</b></div>`).join('')
        + `<div class="tiny muted" style="margin-top:8px">Если ЕИС недоступен — запускайте приложение с российского IP (домашний компьютер или VPS в РФ).</div>`;
    } catch (err) { toast(err.message, 'err'); }
    b.disabled = false; b.textContent = 'Проверить доступность площадок';
  });
  $('[data-tg-test]')?.addEventListener('click', async () => {
    await saveAll(true);
    const r = await api('/notify/test', { method: 'POST' });
    toast(r.ok ? 'Уведомление отправлено — проверьте Telegram' : 'Ошибка: ' + (r.error || 'не настроено'), r.ok ? 'ok' : 'err', 8000);
  });
}

/* ================= ROUTER / BOOT ================= */
async function route() {
  if (!state.user) return renderLogin();
  const hash = location.hash.replace('#/', '') || 'dashboard';
  const [view, arg] = hash.split('/');
  $$('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.nav === (view === 'tender' ? 'tenders' : view)));
  if (view === 'dashboard') await renderDashboard();
  else if (view === 'tenders') await renderTenders();
  else if (view === 'tender') await renderTender(+arg);
  else if (view === 'profile') await renderProfile();
  else if (view === 'ai') await renderAiSettings();
  else if (view === 'analytics') { await loadTenders(); await renderAnalytics(); }
  else if (view === 'sources') await renderSources();
  else await renderDashboard();
}

async function boot(force = false) {
  try {
    if (!state.user) { const r = await api('/me'); state.user = r.user; }
    const [cat, comp, ais] = await Promise.all([api('/catalog'), api('/company'), api('/ai/settings')]);
    state.catalog = cat; state.company = comp.company; state.ai = ais.settings; state.presets = ais.presets;
    renderShell();
    await loadTenders();
    await route();
  } catch (e) {
    if (e.message !== 'Требуется вход') { console.error(e); renderLogin(); }
  }
}

window.addEventListener('hashchange', () => { if (state.user) route(); });
document.addEventListener('DOMContentLoaded', async () => {
  try { const r = await api('/me'); state.user = r.user; await boot(true); }
  catch (e) { renderLogin(); }
});
