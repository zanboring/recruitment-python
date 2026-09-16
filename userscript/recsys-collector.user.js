// ==UserScript==
// @name         RecSys 招聘数据采集器
// @namespace    recsys.local
// @version      1.0.0
// @description  在你真实登录态的浏览器里采集招聘岗位，回传到本地 RecSys 服务（默认 localhost:8080）。不依赖服务端爬虫，因此不存在集中 IP 访问与无头浏览器指纹。
// @author       RecSys
// @match        https://www.zhipin.com/*
// @match        https://we.51job.com/*
// @match        https://jobs.51job.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      localhost
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

/*
 * ─────────────────────────────────────────────────────────────
 *  为什么用油猴脚本，而不是在页面里直接 fetch？
 *
 *  页面里的 fetch 受同源策略约束：从 zhipin.com 向 localhost:8080 发请求会被
 *  CORS 拦掉（对方不会返回 Access-Control-Allow-Origin）。就算绕过响应读取，
 *  带自定义头的请求还会触发预检并被拒。
 *
 *  油猴的 GM_xmlhttpRequest 是**特权 API**，不受同源策略限制 —— 这就是「用前端
 *  代码采集」能跑通的唯一正路（纯页面 JS 做不到，见 README 的结论）。
 *
 *  副产品是抗封：请求发自用户真实浏览器（真实登录态、真实 IP、真实指纹），
 *  根本没有「集中 IP 访问」这个特征，也就不存在被封的触发条件。
 * ─────────────────────────────────────────────────────────────

 *  设计原则：本脚本只做「哑采集器」。
 *  只从 DOM 取原始文本，**不计算 job_key、不解析薪资、不做过滤** ——
 *  那些业务口径统一留在服务端，否则两个采集入口（爬虫 / 浏览器）必然漂移。
 */

(function () {
  'use strict';

  const CFG_KEYS = {
    server: 'recsys_server',
    token: 'recsys_token',
    pageGapMin: 'recsys_gap_min',
    pageGapMax: 'recsys_gap_max',
  };

  const cfg = {
    get server() { return GM_getValue(CFG_KEYS.server, 'http://localhost:8080'); },
    set server(v) { GM_setValue(CFG_KEYS.server, v); },
    get token() { return GM_getValue(CFG_KEYS.token, ''); },
    set token(v) { GM_setValue(CFG_KEYS.token, v); },
    get gapMin() { return Number(GM_getValue(CFG_KEYS.pageGapMin, 3)); },
    get gapMax() { return Number(GM_getValue(CFG_KEYS.pageGapMax, 8)); },
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const randBetween = (a, b) => a + Math.random() * (b - a);
  const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '');

  /* ───────────────────────── 站点适配器 ─────────────────────────
   * 每个适配器只需提供两样东西：怎么找到「岗位卡片」，以及每个字段从卡片里怎么取。
   * 选择器都给多个候选并按顺序尝试 —— 招聘网站的类名会改版，
   * 单一选择器一改就整个失效。
   */
  const SITES = [
    {
      name: 'BOSS直聘',
      platform: 'boss',
      matches: (h) => /(^|\.)zhipin\.com$/.test(h),
      hrefPattern: /\/job_detail\//,
      card: [
        'li.job-card-wrapper',
        'div.job-card-wrapper',
        '.job-list-box li',
        '.job-list-container li',
        'li[ka]',
      ],
      fields: {
        title: ['.job-name', 'span.job-name', '.job-card-left .job-name', 'h3'],
        salary: ['.salary', 'span.salary', '.job-salary'],
        company: ['.company-name', 'h3.company-name', '.company-name a'],
        area: ['.job-area', '.job-area-wrapper', '.job-card-left .job-area'],
        experience: ['.job-card-footer .tag-list li', '.tag-list li'],
        education: ['.job-card-footer .tag-list li', '.tag-list li'],
      },
      tags: ['.job-card-footer .tag-list li', '.tag-list li', '.tag-list span'],
    },
    {
      name: '前程无忧',
      platform: '51job',
      matches: (h) => /(^|\.)51job\.com$/.test(h),
      hrefPattern: /job_detail|jobs\.51job\.com/i,
      card: ['.j_joblist .e', '.joblist .e', '.joblist-item', '.jbox', 'div[class*="joblist"] > div'],
      fields: {
        title: ['.jname', '.jname a', 'p.t a', '.job-name', 'span.jname'],
        salary: ['.sal', 'span.sal', '.salary'],
        company: ['.cname', '.cname a', '.company-name', 'a.cname'],
        area: ['.area', '.d.at', 'span.area'],
        experience: ['.d', '.job-info .d'],
        education: ['.d', '.job-info .d'],
      },
      tags: ['.tags span', '.tag', 'span.tag'],
    },
  ];

  function currentSite() {
    const host = location.hostname;
    return SITES.find((s) => s.matches(host)) || null;
  }

  function pick(card, selectors) {
    for (const sel of selectors) {
      const el = card.querySelector(sel);
      if (el && txt(el)) return el;
    }
    return null;
  }

  /**
   * 兜底找卡片：按「岗位详情链接」定位，再向上找到文本量合适的容器。
   *
   * 这比硬编码类名耐改版 —— 类名会变，但「岗位标题是个指向详情页的链接」
   * 这个结构很少变。向上走的层数用文本量约束，避免把整个列表当成一张卡片。
   */
  function fallbackCards(site) {
    const seen = new Set();
    const cards = [];
    for (const a of document.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href') || a.href || '';
      if (!site.hrefPattern.test(href)) continue;

      let el = a;
      for (let i = 0; i < 5 && el.parentElement; i++) {
        el = el.parentElement;
        const len = (el.textContent || '').trim().length;
        if (len >= 20 && len <= 400) break;   // 文本量合适，认为这就是卡片
      }
      if (seen.has(el)) continue;
      seen.add(el);
      cards.push(el);
    }
    return cards;
  }

  function findCards(site) {
    for (const sel of site.card) {
      const found = [...document.querySelectorAll(sel)].filter((el) => txt(el).length > 5);
      if (found.length) return { cards: found, via: sel };
    }
    const fallback = fallbackCards(site);
    return { cards: fallback, via: '兜底（按详情链接定位）' };
  }

  /** 从一张卡片里取原始文本。绝不在这里做业务判断（薪资解析、技能抽取都归服务端）。 */
  function extractCard(card, site) {
    const f = site.fields;
    const title = txt(pick(card, f.title));
    if (!title) return null;

    const link = card.querySelector('a[href]');
    let url = link ? (link.getAttribute('href') || link.href || '') : '';
    if (url.startsWith('/')) url = location.origin + url;
    if (!/^https?:/.test(url)) url = '';

    // 标签区：经验/学历/技能混在一起，按关键词粗分，其余进 skills
    const tags = [];
    for (const sel of site.tags) {
      for (const el of card.querySelectorAll(sel)) {
        const t = txt(el);
        if (t && !tags.includes(t)) tags.push(t);
      }
      if (tags.length) break;
    }

    let experience = '';
    let education = '';
    const skills = [];
    for (const t of tags) {
      if (!experience && /(\d+\s*[-~]?\s*\d*\s*年|应届|经验不限|在校)/.test(t)) { experience = t; continue; }
      if (!education && /(大专|本科|硕士|博士|中专|高中|学历不限)/.test(t)) { education = t; continue; }
      skills.push(t);
    }

    const company = txt(pick(card, f.company));
    const area = txt(pick(card, f.area));
    const salary = txt(pick(card, f.salary));

    return {
      title,
      company_name: company,
      city: area.split(/[-·\s]/)[0] || '',   // 「长沙-岳麓区」→「长沙」
      salary,
      experience,
      education,
      skills: skills.join(','),
      url,
      description: [title, company, area, skills.join(' ')].filter(Boolean).join(' '),
    };
  }

  function collectCurrentPage() {
    const site = currentSite();
    if (!site) return { site: null, jobs: [], via: '' };
    const { cards, via } = findCards(site);
    const jobs = [];
    const seen = new Set();
    for (const card of cards) {
      const job = extractCard(card, site);
      if (!job) continue;
      const key = job.title + '|' + job.company_name + '|' + job.city;
      if (seen.has(key)) continue;   // 页面里的重复卡片先就地去掉，减少无谓传输
      seen.add(key);
      jobs.push(job);
    }
    return { site, jobs, via };
  }

  /* ───────────────────────── 回传 ───────────────────────── */

  function post(body) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: cfg.server.replace(/\/+$/, '') + '/api/crawler/ingest',
        headers: {
          'Content-Type': 'application/json',
          // 自定义头会触发跨源预检 —— 普通网页发不出这个请求，而特权 API 可以。
          // 这等于借浏览器的同源策略顺手挡掉了「任意网站往 localhost 灌数据」。
          'X-Collect-Token': cfg.token,
        },
        data: JSON.stringify(body),
        timeout: 20000,
        onload: (res) => {
          let payload = null;
          try { payload = JSON.parse(res.responseText); } catch (e) { /* 下面统一报错 */ }
          if (!payload) return reject(new Error('服务端返回的不是 JSON：' + res.responseText.slice(0, 200)));
          if (payload.code !== 200) return reject(new Error(payload.message || ('HTTP ' + res.status)));
          resolve(payload.data);
        },
        onerror: () => reject(new Error('连不上本地服务，请确认 RecSys 已启动（' + cfg.server + '）')),
        ontimeout: () => reject(new Error('请求超时')),
      });
    });
  }

  /* ───────────────────────── 界面 ───────────────────────── */

  const CSS = `
    #recsys-panel{position:fixed;right:18px;bottom:18px;z-index:2147483647;width:290px;
      font:13px/1.6 -apple-system,"Segoe UI",system-ui,"Microsoft YaHei",sans-serif;
      background:#fff;color:#303133;border:1px solid #dcdfe6;border-radius:10px;
      box-shadow:0 8px 28px rgba(0,0,0,.16);overflow:hidden}
    #recsys-panel.rc-collapsed .rc-body{display:none}
    .rc-head{display:flex;align-items:center;justify-content:space-between;padding:9px 12px;
      background:#409eff;color:#fff;cursor:move;user-select:none;font-weight:600}
    .rc-head span{cursor:pointer}
    .rc-body{padding:10px 12px;max-height:60vh;overflow:auto}
    .rc-row{display:flex;gap:8px;margin-bottom:8px}
    .rc-btn{flex:1;padding:7px 10px;border:1px solid #dcdfe6;border-radius:6px;background:#fff;
      cursor:pointer;font-size:12.5px;color:#303133}
    .rc-btn:hover{border-color:#409eff;color:#409eff}
    .rc-btn.rc-primary{background:#409eff;border-color:#409eff;color:#fff}
    .rc-btn.rc-primary:hover{background:#66b1ff}
    .rc-btn:disabled{opacity:.5;cursor:not-allowed}
    .rc-line{font-size:12px;color:#909399;margin-bottom:6px;word-break:break-all}
    .rc-out{max-height:150px;overflow:auto;background:#f5f7fa;border-radius:6px;padding:8px;
      font:11px/1.5 Consolas,Monaco,monospace;white-space:pre-wrap;display:none}
    .rc-out.rc-show{display:block}
    .rc-field{width:100%;box-sizing:border-box;padding:5px 7px;border:1px solid #dcdfe6;
      border-radius:5px;font-size:12px;margin-bottom:6px}
    .rc-ok{color:#67c23a}.rc-err{color:#f56c6c}
  `;

  let el = {};

  function buildPanel() {
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    const box = document.createElement('div');
    box.id = 'recsys-panel';
    box.innerHTML = `
      <div class="rc-head"><b>RecSys 采集器</b><span class="rc-toggle">－</span></div>
      <div class="rc-body">
        <div class="rc-line rc-status">正在检测页面…</div>
        <div class="rc-row">
          <button class="rc-btn rc-primary rc-collect">采集当前页</button>
          <button class="rc-btn rc-preview">预览</button>
        </div>
        <div class="rc-row">
          <button class="rc-btn rc-auto">自动翻页采集</button>
        </div>
        <div class="rc-row">
          <button class="rc-btn rc-config">设置</button>
        </div>
        <div class="rc-line rc-hint">采集只取原始文本，指纹/薪资/过滤均由服务端统一处理。</div>
        <pre class="rc-out"></pre>
      </div>`;
    document.body.appendChild(box);

    el = {
      box,
      status: box.querySelector('.rc-status'),
      out: box.querySelector('.rc-out'),
      collect: box.querySelector('.rc-collect'),
      preview: box.querySelector('.rc-preview'),
      auto: box.querySelector('.rc-auto'),
      config: box.querySelector('.rc-config'),
    };

    box.querySelector('.rc-toggle').onclick = () => box.classList.toggle('rc-collapsed');
    el.collect.onclick = onCollect;
    el.preview.onclick = onPreview;
    el.auto.onclick = onAutoPages;
    el.config.onclick = onConfig;

    // 整个 head 可拖动，避免面板挡住页面操作
    let dragging = null;
    box.querySelector('.rc-head').onmousedown = (ev) => {
      if (ev.target.classList.contains('rc-toggle')) return;
      dragging = { x: ev.clientX, y: ev.clientY, r: box.getBoundingClientRect() };
    };
    document.addEventListener('mousemove', (ev) => {
      if (!dragging) return;
      const dx = ev.clientX - dragging.x;
      const dy = ev.clientY - dragging.y;
      box.style.left = (dragging.r.left + dx) + 'px';
      box.style.top = (dragging.r.top + dy) + 'px';
      box.style.right = 'auto';
      box.style.bottom = 'auto';
    });
    document.addEventListener('mouseup', () => { dragging = null; });
  }

  function say(msg, kind) {
    if (!el.status) return;
    el.status.className = 'rc-line rc-status' + (kind ? ' rc-' + kind : '');
    el.status.textContent = msg;
  }

  function show(text) {
    if (!el.out) return;
    el.out.textContent = text;
    el.out.classList.add('rc-show');
  }

  function refreshStatus() {
    const site = currentSite();
    if (!site) { say('当前页面不在支持的站点上'); return; }
    const { jobs, via } = collectCurrentPage();
    const tokenState = cfg.token ? '' : ' · 未配置令牌';
    say(`${site.name} · 识别到 ${jobs.length} 个岗位 · 定位方式：${via}${tokenState}`,
        cfg.token ? null : 'err');
  }

  function onPreview() {
    const { site, jobs, via } = collectCurrentPage();
    if (!site) return say('当前页面不在支持的站点上', 'err');
    show(`定位方式：${via}\n共 ${jobs.length} 条\n\n` + JSON.stringify(jobs.slice(0, 5), null, 2)
      + (jobs.length > 5 ? `\n\n…（仅预览前 5 条）` : ''));
    say(`预览 ${jobs.length} 条（未发送）`);
  }

  async function send(jobs, site, keyword) {
    if (!cfg.token) {
      say('未配置采集令牌，点「设置」填写', 'err');
      return null;
    }
    return post({
      source_site: site.platform,
      city: jobs[0] ? jobs[0].city : '',
      keyword: keyword || '',
      jobs,
    });
  }

  async function onCollect() {
    const { site, jobs } = collectCurrentPage();
    if (!site) return say('当前页面不在支持的站点上', 'err');
    if (!jobs.length) return say('本页没识别到岗位，试试「预览」看定位方式', 'err');

    el.collect.disabled = true;
    say(`正在回传 ${jobs.length} 条…`);
    try {
      const r = await send(jobs, site);
      if (!r) return;
      say(`收到 ${r.received} · 新增 ${r.saved} · 重复 ${r.duplicate} · 被过滤 ${r.filtered} · 无效 ${r.invalid}`, 'ok');
    } catch (e) {
      say(e.message, 'err');
      show(String(e.message));
    } finally {
      el.collect.disabled = false;
    }
  }

  async function onAutoPages() {
    const { site } = collectCurrentPage();
    if (!site) return say('当前页面不在支持的站点上', 'err');

    const raw = prompt('最多翻几页？（每页之间会随机等待 ' + cfg.gapMin + '~' + cfg.gapMax + ' 秒）', '5');
    const maxPages = parseInt(raw || '0', 10);
    if (!maxPages || maxPages < 1) return;

    const totals = { saved: 0, duplicate: 0, filtered: 0, invalid: 0, pages: 0 };
    for (let page = 0; page < maxPages; page++) {
      const { jobs } = collectCurrentPage();
      if (!jobs.length) { say(`第 ${page + 1} 页没识别到岗位，停止`, 'err'); break; }

      try {
        const r = await send(jobs, site, '');
        if (!r) return;
        totals.saved += r.saved; totals.duplicate += r.duplicate;
        totals.filtered += r.filtered; totals.invalid += r.invalid;
        totals.pages += 1;
        say(`已采 ${totals.pages} 页 · 新增 ${totals.saved} · 重复 ${totals.duplicate}`, 'ok');
      } catch (e) {
        say(e.message, 'err');
        break;
      }

      if (page === maxPages - 1) break;
      // 下一页：招聘站普遍把「下一页」做成按钮或链接，两种都试
      const next = document.querySelector('a[href*="page="][class*="next"], .options-pages a:last-child, a.next, button.next')
        || [...document.querySelectorAll('a,button')].find((n) => /下一页|下页|next/i.test(txt(n)));
      if (!next) { say(`已到最后一页（共采 ${totals.pages} 页）`, 'ok'); break; }

      next.click();
      await sleep(randBetween(cfg.gapMin, cfg.gapMax) * 1000);   // 跟着人的节奏，不像脚本
    }
    say(`自动采集结束：${totals.pages} 页 · 新增 ${totals.saved} · 重复 ${totals.duplicate}`, 'ok');
  }

  function onConfig() {
    const server = prompt('RecSys 服务地址', cfg.server);
    if (server !== null) cfg.server = server.trim();
    const token = prompt(
      '采集令牌（与 .env 里的 BROWSER_COLLECT_TOKEN 一致）\n' +
      '生成方式：python -c "import secrets;print(secrets.token_urlsafe(24))"',
      cfg.token
    );
    if (token !== null) cfg.token = token.trim();
    refreshStatus();
  }

  /* ───────────────────────── 启动 ───────────────────────── */

  buildPanel();
  refreshStatus();

  GM_registerMenuCommand('采集当前页并回传', onCollect);
  GM_registerMenuCommand('预览本页识别结果', onPreview);

  // 招聘网站多是单页应用，切页不刷新 —— 监听地址变化后刷新计数
  let lastHref = location.href;
  setInterval(() => {
    if (location.href === lastHref) return;
    lastHref = location.href;
    setTimeout(refreshStatus, 1200);
  }, 1000);
})();
