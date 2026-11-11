/* ==========================================================================
   TenderPilot — статическая демо-версия (GitHub Pages).
   Перехватывает api() из app.js и отвечает из встроенных данных.
   Логика скоринга и маржи — порт app/scoring.py на JavaScript,
   поэтому профиль завода можно менять и оценки пересчитываются вживую.
   ========================================================================== */
'use strict';

(function () {
  const DATA = window.TP_DATA || { tenders: [], company: {}, catalog: {}, events: [] };
  const LS = {
    company: 'tp_demo_company',
    stages: 'tp_demo_stages',
    notes: 'tp_demo_notes',
    auth: 'tp_demo_auth',
    ai: 'tp_demo_ai',
  };
  const read = (k, def) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : def; } catch (e) { return def; } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };

  /* ---------- справочники ---------- */
  const REGION_LOGISTICS = DATA.region_logistics || {};
  const CATEGORY_BY_ID = {};
  (DATA.catalog.categories || []).forEach((c) => { CATEGORY_BY_ID[c.id] = c; });

  const CERT_VOCAB = [
    ['ИСО 9001', ['исо 9001', 'iso 9001', '9001']],
    ['ИСО 3834', ['исо 3834', 'iso 3834']],
    ['ТР ТС 010/2011', ['010/2011', 'тр тс 010']],
    ['ТР ТС 032/2013', ['032/2013', 'тр тс 032']],
    ['ТР ТС 004/2011', ['004/2011', 'тр тс 004']],
    ['ТР ТС 020/2011', ['020/2011', 'тр тс 020']],
    ['ТР ТС 012/2011', ['012/2011', 'взрывозащ']],
    ['СРО', ['сро']],
    ['ГОСТ', ['гост']],
    ['СДС ГАЗСЕРТ', ['газсерт']],
    ['РосатомрегисТр', ['росатомрегис']],
    ['НП-071 / аттестация изготовителя', ['нп-071', 'аттестац']],
  ];

  const norm = (s) => String(s || '').toLowerCase().replace(/ё/g, 'е');
  const nonEmpty = (v) => (Array.isArray(v) ? v.length > 0 : !!v);

  function fmtMoney(v) {
    v = Number(v || 0);
    if (!v) return '—';
    const s = (x) => String(x).replace('.', ',');
    if (v >= 1e9) return s((v / 1e9).toFixed(2)) + ' млрд ₽';
    if (v >= 1e6) return s((v / 1e6).toFixed(2).replace(/0$/, '')) + ' млн ₽';
    if (v >= 1e3) return Math.round(v / 1e3) + ' тыс ₽';
    return Math.round(v) + ' ₽';
  }

  function daysLeft(t) {
    const d = new Date(t.deadline);
    if (isNaN(d)) return 0;
    return Math.floor((d - new Date()) / 86400000);
  }

  /* ---------- скоринг (порт scoring.py) ---------- */
  function companyOkpd(company) {
    const set = new Set();
    (company.okpd || []).forEach((c) => { if (String(c).trim()) set.add(String(c).trim()); });
    (company.categories || []).forEach((id) => {
      const cat = CATEGORY_BY_ID[id];
      if (cat) (cat.okpd || []).forEach((c) => set.add(c));
    });
    return set;
  }

  function companyKeywords(company) {
    const words = new Set();
    (company.categories || []).forEach((id) => {
      const cat = CATEGORY_BY_ID[id];
      if (cat) (cat.keywords || []).forEach((k) => words.add(norm(k)));
    });
    norm(company.products).replace(/;/g, ',').split(',').forEach((w) => {
      w = w.trim();
      if (w.length > 2) words.add(w);
    });
    return words;
  }

  function okpdMatch(codes, okpd) {
    const t = String(okpd || '');
    if (!t) return [50, 'ОКПД2 в закупке не указан'];
    for (const c of codes) {
      if (t.indexOf(c) === 0 || c.indexOf(t) === 0) return [100, 'ОКПД2 ' + t + ' совпадает с вашим кодом ' + c];
    }
    const g4 = new Set(Array.from(codes).map((c) => c.slice(0, 4)));
    if (g4.has(t.slice(0, 4))) return [80, 'ОКПД2 ' + t + ' входит в вашу товарную группу ' + t.slice(0, 4)];
    const g2 = new Set(Array.from(codes).map((c) => c.slice(0, 2)));
    if (g2.has(t.slice(0, 2))) return [45, 'ОКПД2 ' + t + ' — смежная отрасль'];
    return [5, 'ОКПД2 ' + t + ' вне вашей номенклатуры'];
  }

  function keywordMatch(keywords, t) {
    const hay = norm([t.title, t.description, t.tz_text].join(' '));
    const hits = Array.from(keywords).filter((w) => w && hay.indexOf(w) >= 0);
    if (hits.length) return [Math.min(100, 55 + hits.length * 15), 'Совпадения по номенклатуре: ' + hits.slice(0, 6).join(', ')];
    const cat = CATEGORY_BY_ID[t.category];
    if (cat && hay.indexOf(cat.id) >= 0) return [40, 'Категория совпадает, но ключевые слова в ТЗ не найдены'];
    return [10, 'Номенклатура в ТЗ не пересекается с вашей продукцией'];
  }

  function regionMatch(company, t) {
    const regions = company.delivery_regions || [];
    if (!regions.length) return [60, 'Регионы поставки не заданы — считаем как «по РФ»'];
    if (regions.indexOf(t.region) >= 0) return [100, 'Регион ' + t.region + ' входит в вашу зону поставки'];
    return [35, 'Регион ' + t.region + ' вне вашей зоны поставки'];
  }

  function sumMatch(company, t) {
    const s = Number(t.sum || 0), hi = Number(company.max_order_sum || 0), lo = Number(company.min_order_sum || 0);
    if (hi && s > hi) return [Math.max(10, Math.round(90 - (s / hi - 1) * 60)),
      'Сумма ' + fmtMoney(s) + ' превышает вашу максимальную мощность (' + fmtMoney(hi) + ')'];
    if (lo && s < lo) return [55, 'Сумма ' + fmtMoney(s) + ' ниже вашего минимального объёма (' + fmtMoney(lo) + ')'];
    return [100, 'Сумма ' + fmtMoney(s) + ' в пределах вашего диапазона'];
  }

  function deadlineMatch(company, t) {
    const left = daysLeft(t);
    const lead = Number(company.lead_days || 30);
    const delivery = Number(t.delivery_days || lead);
    if (left < 0) return [0, 'Срок подачи заявки истёк'];
    let prep, prepNote;
    if (left < 3) { prep = 35; prepNote = 'на подготовку заявки всего ' + left + ' дн.'; }
    else if (left < 7) { prep = 75; prepNote = left + ' дн. на заявку — успеть можно'; }
    else { prep = 100; prepNote = left + ' дн. на заявку — с запасом'; }
    let prod, prodNote;
    if (delivery >= lead) { prod = 100; prodNote = 'срок поставки ' + delivery + ' дн. при вашем цикле ' + lead + ' дн.'; }
    else if (delivery >= lead * 0.75) { prod = 70; prodNote = 'срок поставки ' + delivery + ' дн. против цикла ' + lead + ' дн. — нужно ускорение'; }
    else { prod = 30; prodNote = 'срок поставки ' + delivery + ' дн. существенно меньше цикла ' + lead + ' дн.'; }
    return [Math.round(prep * 0.45 + prod * 0.55), prodNote + '; ' + prepNote];
  }

  function detectCerts(text) {
    const t = norm(text), out = [];
    CERT_VOCAB.forEach(([name, keys]) => { if (keys.some((k) => t.indexOf(norm(k)) >= 0)) out.push(name); });
    return out;
  }

  function checkRequirements(company, t) {
    const reqs = Array.isArray(t.requirements) ? t.requirements : [];
    const certText = norm((company.certificates || []).concat(company.licenses || []).join(' '));
    const avail = daysLeft(t);
    const lead = Number(company.lead_days || 30);
    const cats = company.categories || [];
    const exp = Number(company.experience_years || 0);
    const maxSum = Number(company.max_order_sum || 0);

    return reqs.map((r) => {
      const text = r.text || '', typ = r.type || 'docs';
      let status = 'unknown', comment = 'Требует уточнения у заказчика';
      if (typ === 'certificate') {
        const certs = detectCerts(text);
        if (!certs.length) { status = 'unknown'; comment = 'Формулировка нестандартная — проверить вручную'; }
        else {
          const have = certs.filter((c) => certText.indexOf(norm(c)) >= 0 || (c === 'ГОСТ' && certText.indexOf('гост') >= 0));
          const miss = certs.filter((c) => have.indexOf(c) < 0);
          if (!miss.length) { status = 'met'; comment = 'Все документы есть в профиле: ' + have.join(', '); }
          else if (have.length) { status = 'partial'; comment = 'Есть: ' + have.join(', ') + '. Нет: ' + miss.join(', '); }
          else { status = 'missing'; comment = 'В профиле нет: ' + miss.join(', '); }
        }
      } else if (typ === 'product') {
        if (CATEGORY_BY_ID[t.category] && cats.indexOf(t.category) >= 0) {
          status = 'met'; comment = 'Категория «' + CATEGORY_BY_ID[t.category].name + '» заявлена в профиле';
        } else { status = 'partial'; comment = 'Проверьте номенклатуру по спецификации закупки'; }
      } else if (typ === 'experience') {
        const need = (norm(text).indexOf('газпром') >= 0 || t.law === '223-ФЗ') ? 5 : 3;
        if (exp >= need) { status = 'met'; comment = 'Опыт ' + exp + ' лет ≥ требуемых ' + need; }
        else if (exp >= 2) { status = 'partial'; comment = 'Опыт ' + exp + ' лет — требуется ' + need + ', нужны референсы'; }
        else { status = 'missing'; comment = 'Недостаточный подтверждённый опыт'; }
      } else if (typ === 'finance') {
        if (maxSum && Number(t.sum || 0) > maxSum * 1.02) { status = 'partial'; comment = 'Обеспечение и оборотка выше вашего лимита — нужна БГ или партнёр'; }
        else { status = 'met'; comment = 'Обеспечение и отсрочка в пределах ваших возможностей'; }
      } else if (typ === 'timing') {
        const delivery = Number(t.delivery_days || lead);
        if (avail < 3) { status = 'partial'; comment = 'На подготовку заявки осталось ' + avail + ' дн.'; }
        else if (delivery >= lead) { status = 'met'; comment = 'Срок поставки ' + delivery + ' дн. при вашем цикле ' + lead + ' дн. — успеваем'; }
        else if (delivery >= lead * 0.75) { status = 'partial'; comment = 'Срок поставки ' + delivery + ' дн. против цикла ' + lead + ' дн. — нужно ускорение'; }
        else { status = 'missing'; comment = 'Срок ' + delivery + ' дн. существенно меньше вашего цикла ' + lead + ' дн.'; }
      } else {
        status = 'unknown'; comment = 'Организационное требование — учесть в заявке';
      }
      return { type: typ, text: text, status: status, comment: comment };
    });
  }

  function computeMatch(company, t) {
    const codes = companyOkpd(company);
    const kws = companyKeywords(company);
    const w = company.scoring || {};
    const parts = [];
    const add = (key, name, weight, pair) => parts.push({ key: key, name: name, weight: weight, score: pair[0], comment: pair[1] });
    add('okpd', 'Номенклатура / ОКПД2', w.w_okpd != null ? w.w_okpd : 30, okpdMatch(codes, t.okpd));
    add('keywords', 'Совпадение по ТЗ', w.w_keywords != null ? w.w_keywords : 25, keywordMatch(kws, t));
    add('sum', 'Объём / мощность', w.w_sum != null ? w.w_sum : 15, sumMatch(company, t));
    add('region', 'География', w.w_region != null ? w.w_region : 10, regionMatch(company, t));
    add('deadline', 'Сроки', w.w_deadline != null ? w.w_deadline : 10, deadlineMatch(company, t));

    const reqs = checkRequirements(company, t);
    const certReqs = reqs.filter((r) => r.type === 'certificate');
    let sCert, cCert;
    if (certReqs.length) {
      const map = { met: 100, partial: 55, missing: 10, unknown: 60 };
      sCert = certReqs.reduce((s, r) => s + map[r.status], 0) / certReqs.length;
      const miss = certReqs.filter((r) => r.status === 'missing' || r.status === 'partial').map((r) => r.text);
      cCert = miss.length ? 'Не хватает: ' + miss.slice(0, 2).join('; ') : 'Все документы в наличии';
    } else { sCert = 80; cCert = 'Отдельных сертификатов в ТЗ не требуется'; }
    add('certificates', 'Сертификаты / допуски', w.w_certificates != null ? w.w_certificates : 10, [Math.round(sCert), cCert]);

    const tw = parts.reduce((s, p) => s + p.weight, 0) || 1;
    const score = Math.round(parts.reduce((s, p) => s + p.score * p.weight, 0) / tw);
    const blockers = reqs.filter((r) => r.status === 'missing').map((r) => r.text);
    if (daysLeft(t) < 0) blockers.unshift('Срок подачи заявки истёк');
    const label = score >= 75 ? 'высокое' : score >= 50 ? 'среднее' : 'низкое';
    return { score: score, label: label, parts: parts, requirements: reqs, blockers: blockers };
  }

  /* ---------- маржа (порт scoring.py) ---------- */
  function computeMargin(company, t, override) {
    const e = company.economics || {};
    const o = override || {};
    const price = Number(o.price != null ? o.price : t.sum || 0);
    const vatMode = (e.tax_mode || 'vat') === 'vat';
    const net = vatMode ? price / 1.2 : price;

    const mat = Number(o.material_pct != null ? o.material_pct : (e.material_pct != null ? e.material_pct : 42));
    const lab = Number(o.labor_pct != null ? o.labor_pct : (e.labor_pct != null ? e.labor_pct : 10));
    const oh = Number(o.overhead_pct != null ? o.overhead_pct : (e.overhead_pct != null ? e.overhead_pct : 12));
    const logPct = Number(o.logistics_pct != null ? o.logistics_pct : (e.logistics_pct != null ? e.logistics_pct : 5));
    const regionFactor = REGION_LOGISTICS[t.region] || 1.25;
    const riskPct = Number(e.risk_reserve_pct != null ? e.risk_reserve_pct : 3);
    const bgRate = Number(e.bg_rate_pct != null ? e.bg_rate_pct : 3.5);

    const materials = net * mat / 100;
    const labor = net * lab / 100;
    const overhead = net * oh / 100;
    const logistics = net * logPct / 100 * regionFactor;

    const days = Number(t.delivery_days || 45) + 60;
    const security = price * ((Number(t.bid_security_pct || 0) + Number(t.contract_security_pct || 0)) / 100);
    const bgCost = security * bgRate / 100 * days / 365;
    const risk = net * riskPct / 100;
    const usnTax = vatMode ? 0 : price * 0.06;

    const costTotal = materials + labor + overhead + logistics + bgCost + risk + usnTax;
    const margin = net - costTotal;
    const marginPct = net ? margin / net * 100 : 0;
    const minM = Number(e.min_margin_pct != null ? e.min_margin_pct : 16);
    const targetM = Number(e.target_margin_pct != null ? e.target_margin_pct : 24);

    let verdict, comment;
    if (marginPct >= targetM) { verdict = 'good'; comment = 'Маржа выше целевой — можно играть по цене'; }
    else if (marginPct >= minM) { verdict = 'ok'; comment = 'Маржа в допустимом коридоре'; }
    else if (marginPct > 0) { verdict = 'thin'; comment = 'Маржа ниже вашего минимума — риск работать в ноль'; }
    else { verdict = 'negative'; comment = 'Убыток при текущей цене'; }

    const lines = [
      { name: 'Материалы и комплектующие', value: materials, pct: mat },
      { name: 'Оплата труда производственного персонала', value: labor, pct: lab },
      { name: 'Накладные расходы', value: overhead, pct: oh },
      { name: 'Логистика до объекта', value: logistics, pct: net ? logistics / net * 100 : 0, region_factor: regionFactor },
      { name: 'Банковские гарантии и обеспечение', value: bgCost, security_amount: security },
      { name: 'Резерв на риски и штрафы', value: risk, pct: riskPct },
    ];
    if (usnTax) lines.push({ name: 'Налог при УСН (6% с оборота)', value: usnTax, pct: 6 });

    return {
      price: price, price_with_vat: price, net_revenue: net, vat: vatMode ? price - net : 0, tax_mode: vatMode ? 'vat' : 'usn',
      lines: lines, cost_total: costTotal, margin: margin, margin_pct: Math.round(marginPct * 10) / 10,
      min_margin_pct: minM, target_margin_pct: targetM, verdict: verdict, comment: comment,
      working_capital: security + net * 0.35, breakeven_price: costTotal * (vatMode ? 1.2 : 1),
    };
  }

  /* ---------- подготовка карточки ---------- */
  function platformOf(id) {
    return (DATA.catalog.platforms || []).find((p) => p.id === id) || { name: id, short: id };
  }

  function buildTender(raw, company, stages, notes) {
    const t = Object.assign({}, raw);
    t.requirements = typeof t.requirements === 'string' ? JSON.parse(t.requirements || '[]') : (t.requirements || []);
    t.files = typeof t.files === 'string' ? JSON.parse(t.files || '[]') : (t.files || []);
    t.analysis = t.analysis ? (typeof t.analysis === 'string' ? JSON.parse(t.analysis) : t.analysis) : null;
    const p = platformOf(t.platform);
    t.platform_name = p.name; t.platform_short = p.short || p.name;
    const cat = CATEGORY_BY_ID[t.category];
    t.category_name = cat ? cat.name : '—';
    t.days_left = daysLeft(t);
    t.match = computeMatch(company, t);
    t.margin = computeMargin(company, t);
    if (stages[t.id]) t.stage = stages[t.id];
    if (notes[t.id] != null) t.note = notes[t.id];
    const st = (DATA.catalog.stages || []).find((s) => s.id === t.stage);
    t.stage_name = st ? st.name : t.stage;
    return t;
  }

  function currentCompany() {
    const saved = read(LS.company, null);
    if (!saved) return JSON.parse(JSON.stringify(DATA.company));
    const merged = JSON.parse(JSON.stringify(DATA.company));
    Object.keys(saved).forEach((k) => {
      if (saved[k] && typeof saved[k] === 'object' && !Array.isArray(saved[k])) merged[k] = Object.assign({}, merged[k], saved[k]);
      else merged[k] = saved[k];
    });
    return merged;
  }

  function allTenders(company) {
    const stages = read(LS.stages, {}), notes = read(LS.notes, {});
    return DATA.tenders.map((raw) => buildTender(raw, company, stages, notes));
  }

  function computeStats(items) {
    const sum = (arr, f) => arr.reduce((s, x) => s + (f(x) || 0), 0);
    const analyzed = items.filter((t) => t.analysis);
    const go = items.filter((t) => t.analysis && t.analysis.verdict === 'go');
    const consider = items.filter((t) => t.analysis && t.analysis.verdict === 'consider');
    const noGo = items.filter((t) => t.analysis && t.analysis.verdict === 'no_go');
    const pipeline = items.filter((t) => ['go', 'bid', 'review'].indexOf(t.stage) >= 0);
    const won = items.filter((t) => t.stage === 'won');
    const bid = items.filter((t) => ['won', 'lost', 'bid'].indexOf(t.stage) >= 0);
    const byPlatform = {};
    items.forEach((t) => {
      const k = t.platform_short;
      byPlatform[k] = byPlatform[k] || { count: 0, sum: 0, go: 0 };
      byPlatform[k].count++; byPlatform[k].sum += Number(t.sum || 0);
      if (t.analysis && t.analysis.verdict === 'go') byPlatform[k].go++;
    });
    const sorted = go.slice().sort((a, b) =>
      (b.match.score + b.margin.margin_pct) - (a.match.score + a.margin.margin_pct));
    return {
      totals: {
        tenders: items.length, analyzed: analyzed.length, go: go.length, consider: consider.length, no_go: noGo.length,
        sum_total: sum(items, (t) => t.sum), sum_go: sum(go, (t) => t.sum),
        avg_margin_go: go.length ? Math.round(sum(go, (t) => t.margin.margin_pct) / go.length * 10) / 10 : 0,
        pipeline_sum: sum(pipeline, (t) => t.sum), won_sum: sum(won, (t) => t.sum),
        win_rate: bid.length ? Math.round(won.length / bid.length * 100) : 0,
      },
      by_platform: byPlatform,
      top: sorted.slice(0, 5),
      deadline_soon: items.filter((t) => t.days_left >= 0 && t.days_left <= 7).sort((a, b) => a.days_left - b.days_left).slice(0, 6),
    };
  }

  /* ---------- ИИ-заключение в статической версии ---------- */
  function staticAnalysis(company, t) {
    if (t.analysis) return t.analysis;
    const m = t.match, mg = t.margin;
    const missing = m.requirements.filter((r) => r.status === 'missing');
    return {
      verdict: missing.length ? 'no_go' : (m.score >= 72 && mg.margin_pct >= mg.min_margin_pct ? 'go' : 'consider'),
      confidence: 55,
      summary: 'Заключение рассчитано встроенным движком (статическая демо-версия без LLM). ' +
        'Соответствие профилю ' + m.score + '/100, расчётная маржа ' + mg.margin_pct + '%.',
      match_notes: m.parts.map((p) => p.name + ': ' + p.score + '/100 — ' + p.comment),
      requirements: m.requirements,
      risks: missing.length ? [{ level: 'high', text: 'Не выполнены требования: ' + missing.slice(0, 3).map((r) => r.text).join('; ') }] : [],
      red_flags: [],
      competitive: { win_probability: Math.max(10, Math.min(85, Math.round(m.score * 0.7))), expected_bidders: t.law === '44-ФЗ' ? 3 : 5, comment: 'Оценка по типу процедуры и сумме.' },
      pricing: { recommended_price: mg.price * 0.97, max_discount_pct: Math.max(0, Math.round(mg.margin_pct - mg.min_margin_pct)), comment: 'Держать маржу не ниже ' + mg.min_margin_pct + '%.' },
      margin_comment: 'Маржа ' + mg.margin_pct + '% (' + fmtMoney(mg.margin) + ').',
      documents_needed: ['Заявка по форме площадки', 'Сертификаты на продукцию', 'Справка об опыте', 'Выписка ЕГРЮЛ'],
      next_steps: ['Проверить спецификацию', 'Запросить разъяснения по спорным пунктам ТЗ', 'Согласовать график с производством'],
      questions: m.requirements.filter((r) => r.status !== 'met').map((r) => r.text).slice(0, 4),
      source: 'heuristic', model: 'встроенный расчёт (демо)',
    };
  }

  /* ---------- роутер вместо сервера ---------- */
  const DEMO_USER = { id: 1, email: 'owner@zavod.ru', name: 'Владелец завода', role: 'manager', status: 'active' };
  const AI_SETTINGS = {
    provider: 'openai', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini',
    api_key_set: false, api_key_hint: '', temperature: 0.2, auto_analyze: true, match_threshold: 55,
  };

  async function fakeApi(path, opts) {
    const method = (opts && opts.method) || 'GET';
    const body = opts && opts.body && typeof opts.body === 'string' ? JSON.parse(opts.body) : (opts && opts.body) || {};
    const auth = read(LS.auth, false);
    const needAuth = () => { if (!auth) { const e = new Error('Требуется вход'); e.status = 401; throw e; } };

    // ---- авторизация ----
    if (path === '/me') { needAuth(); return { user: DEMO_USER }; }
    if (path === '/login') {
      if (String(body.password || '') !== 'demo1234') { const e = new Error('Неверный email или пароль'); e.status = 401; throw e; }
      write(LS.auth, true);
      return { ok: true, user: DEMO_USER, token: 'demo' };
    }
    if (path === '/logout') { write(LS.auth, false); return { ok: true }; }
    if (path === '/register') {
      const e = new Error('Статическая демо-версия без сервера: регистрация и панель администратора недоступны. Запустите приложение через start.bat.');
      e.status = 400; throw e;
    }

    needAuth();

    // ---- справочники ----
    if (path === '/catalog') return DATA.catalog;
    if (path === '/company' && method === 'GET') return { company: currentCompany() };
    if (path === '/company' && method === 'PUT') {
      const merged = currentCompany();
      Object.keys(body).forEach((k) => {
        if (body[k] && typeof body[k] === 'object' && !Array.isArray(body[k]) && merged[k] && typeof merged[k] === 'object') {
          merged[k] = Object.assign({}, merged[k], body[k]);
        } else merged[k] = body[k];
      });
      write(LS.company, merged);
      return { ok: true, company: merged };
    }

    // ---- настройки ИИ ----
    if (path === '/ai/settings' && method === 'GET') {
      return { settings: Object.assign({}, AI_SETTINGS, read(LS.ai, {})), presets: DATA.presets || {} };
    }
    if (path === '/ai/settings' && method === 'PUT') {
      const cur = Object.assign({}, AI_SETTINGS, read(LS.ai, {}), body);
      write(LS.ai, cur);
      return { ok: true, settings: Object.assign({}, cur, { api_key_set: !!body.api_key || cur.api_key_set }) };
    }
    if (path === '/ai/test') {
      return { ok: false, error: 'Это статическая демо-версия на GitHub Pages: здесь нет сервера, поэтому живую модель подключить нельзя. Ключ работает в локальной версии проекта (python -m uvicorn app.main:app).' };
    }

    // ---- тендеры ----
    const company = currentCompany();
    if (path.indexOf('/stats') === 0) return computeStats(allTenders(company));
    if (path.indexOf('/activity') === 0) {
      return { events: (DATA.events || []).slice(0, 30), sync: DATA.sync || [] };
    }
    if (path.indexOf('/sync') === 0) {
      return { found: [], count: 0, demo: true };
    }

    const mAnalyze = path.match(/^\/tenders\/(\d+)\/analyze$/);
    if (mAnalyze) {
      const id = Number(mAnalyze[1]);
      const t = allTenders(company).find((x) => x.id === id);
      const a = staticAnalysis(company, t);
      return { analysis: a, match: t.match, margin: t.margin,
        warning: 'Демо-версия на GitHub Pages: ИИ-анализ показан из предрасчёта, живая модель недоступна без сервера.' };
    }
    const mMargin = path.match(/^\/tenders\/(\d+)\/margin$/);
    if (mMargin) {
      const id = Number(mMargin[1]);
      const t = allTenders(company).find((x) => x.id === id);
      return { margin: computeMargin(company, t, body.overrides || {}) };
    }
    const mOne = path.match(/^\/tenders\/(\d+)$/);
    if (mOne) {
      const id = Number(mOne[1]);
      const t = allTenders(company).find((x) => x.id === id);
      if (!t) { const e = new Error('Закупка не найдена'); e.status = 404; throw e; }
      if (method === 'PATCH') {
        if (body.stage) { const st = read(LS.stages, {}); st[id] = body.stage; write(LS.stages, st); }
        if (body.note != null) { const nt = read(LS.notes, {}); nt[id] = body.note; write(LS.notes, nt); }
        return { ok: true };
      }
      const stages = read(LS.stages, {}), notes = read(LS.notes, {});
      t.events = (DATA.events || []).filter((e) => e.tender_id === id).slice(0, 20);
      if (stages[id]) t.stage_name = ((DATA.catalog.stages || []).find((s) => s.id === stages[id]) || {}).name || stages[id];
      if (notes[id] != null) t.note = notes[id];
      return { tender: t };
    }
    if (path.indexOf('/tenders') === 0) {
      const items = allTenders(company);
      return { items: items, total: items.length };
    }

    // ---- парсер: в статике недоступен ----
    if (path.indexOf('/tenders/parse') === 0) {
      const e = new Error('Разбор ссылок и файлов требует сервера — доступен в локальной версии проекта.');
      e.status = 400; throw e;
    }
    const e = new Error('В статической демо-версии этот раздел недоступен');
    e.status = 404; throw e;
  }

  window.api = fakeApi;
  window.TP_STATIC = true;
})();
