/*
 * Смоук раскладки страниц после левой панели (T223, issue #268).
 *
 * `smoke_sidenav.mjs` проверяет саму панель. Этот — то, что рядом с ней:
 * помещается ли содержимое страницы. Разбором разметки этого не доказать —
 * всё зависит от ширины окна, длины слов и шрифта.
 *
 * Обходит все разделы левой панели роли, плюс страницы месяца, табеля и
 * ведомости, на 1440, 900, 768 и 390. Пробы:
 *
 *  - страница не едет вбок;
 *  - ничего не уходит за правый край содержимого, если оно не внутри
 *    прокручиваемого контейнера (обрезанное — это невидимое, а не «широкое»);
 *  - ряд вкладок и переключателей не ломается на два ряда (на 900 и шире);
 *  - заголовки колонок не ломаются по букве в столбик;
 *  - липкая шапка таблицы не налезает на первую строку;
 *  - шапка таблицы залита одним фоном во всех колонках;
 *  - поле выбора файла не нативное;
 *  - на телефоне до содержимого не больше 120 px, а нижняя полоса разделов
 *    не закрывает конец страницы.
 *
 *     chrome-for-testing --headless=new --remote-debugging-port=9341 \
 *         --user-data-dir=/tmp/chrome-layout &
 *     APP=http://127.0.0.1:8000 USER_PASS=… OUT=/tmp/layout node tools/smoke_layout.mjs
 *
 * ROLES — роли через запятую (умолчание: accountant,manager). Выходит с
 * ненулевым кодом и называет страницу, ширину и пробу, если хоть одна не
 * сошлась. Снимки нескольких страниц — в OUT, для сверки с эталоном.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { evalIn, goto, login, send } from "./guide_browser.mjs";

const OUT = process.env.OUT || "/tmp/layout-shots";
mkdirSync(OUT, { recursive: true });
const pass = process.env.USER_PASS;
const ROLES = (process.env.ROLES || "accountant,manager").split(",");
const WIDTHS = { wide: [1440, 900], tablet: [900, 1000], narrow: [768, 1000], phone: [390, 844] };
// Шапка телефона до содержимого — требование сверки по T222 (issue #268).
const PHONE_HEAD_MAX = 120;
const SHOTS = new Set(["/timesheets/", "/payroll/sheet/", "/analytics/people/", "/periods/"]);
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function size(w, h) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 720 });
  await pause(250);
}

async function shot(name) {
  await evalIn("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); 0");
  await pause(150);
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.data, "base64"));
  return `${OUT}/${name}.png`;
}

/** Адреса, которые обходим у роли: разделы панели и вложенные экраны месяца. */
const pages = () => evalIn(`(async () => {
  const own = (a) => a.origin === location.origin;
  const nav = [...document.querySelectorAll('.sidenav__list a.sidenav__item, .sidenav__list .sidenav__current')]
    .map((a) => a.href || location.href).filter((h) => h);
  const out = new Set(nav.map((h) => new URL(h).pathname));
  // Страница месяца, табель и ведомость — со страницы периодов.
  const r = await fetch('/periods/'); const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
  const period = [...doc.querySelectorAll('main a[href^="/periods/"]')].map((a) => a.getAttribute('href'))
    .find((h) => /^\\/periods\\/[0-9a-f-]{36}\\/$/.test(h));
  if (period) {
    out.add(period);
    const id = period.split('/')[2];
    out.add('/timesheets/' + id + '/');
  }
  out.add('/payroll/sheet/');
  return [...out];
})()`);

const probe = (phone) => evalIn(`(() => {
  const main = document.querySelector('main');
  const mr = main.getBoundingClientRect();
  const cs = (e) => getComputedStyle(e);
  const shown = (e) => e.getClientRects().length > 0 && cs(e).visibility !== 'hidden';
  const name = (e) => (e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\\s+/).join('.') : '') + ' «' + (e.innerText || e.value || '').trim().slice(0, 30).replace(/\\s+/g, ' ') + '»');
  const scroller = (e) => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) { const o = cs(p).overflowX; if (o === 'auto' || o === 'scroll') return p; } return null; };
  const clipper = (e) => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) { const o = cs(p).overflowX; if (o === 'hidden' || o === 'clip') return p; } return null; };
  const sr = (e) => { for (let p = e; p; p = p.parentElement) { if (p.classList && p.classList.contains('sr-only')) return true; } return false; };
  const r = { hscroll: document.documentElement.scrollWidth - document.documentElement.clientWidth, over: [], clipped: [], wraps: [], thBroken: [], stickyOverlap: [], thFill: [], file: 0, headTop: 0, barCovers: 0, scrolls: [] };
  const all = [...main.querySelectorAll('*')].filter((e) => shown(e) && !sr(e));
  const overIn = new Set();
  for (const e of all) {
    const b = e.getBoundingClientRect();
    if (!b.width) continue;
    if (b.right > mr.right + 1 && !scroller(e)) {
      if (!(e.parentElement && overIn.has(e.parentElement))) r.over.push(name(e) + ' +' + Math.round(b.right - mr.right));
      overIn.add(e);
    }
    const c = clipper(e);
    if (c && !scroller(e) && b.right > c.getBoundingClientRect().right + 1 && !overIn.has(e.parentElement)) r.clipped.push(name(e));
  }
  for (const s of main.querySelectorAll('*')) {
    if (!shown(s)) continue;
    const o = cs(s).overflowX;
    if ((o === 'auto' || o === 'scroll') && s.scrollWidth > s.clientWidth + 1) r.scrolls.push(name(s).slice(0, 40) + ' ' + (s.scrollWidth - s.clientWidth));
  }
  // Ряды вкладок, переключателей и шагов не переносятся.
  for (const row of main.querySelectorAll('.tabs, .seg, .cuts, .steps, [role=tablist], nav.subnav, .switch')) {
    if (!shown(row)) continue;
    const tops = new Set([...row.children].filter(shown).map((c) => Math.round(c.getBoundingClientRect().top)));
    if (tops.size > 1) r.wraps.push(name(row).slice(0, 60));
  }
  // Слово, разорванное переносом: у его отрезка текста больше одного
  // прямоугольника строки. Число строк само по себе не дефект — длинный
  // заголовок имеет право на две строки, а «ФО/Т» столбиком нет.
  for (const th of main.querySelectorAll('th')) {
    if (!shown(th) || sr(th)) continue;
    const walker = document.createTreeWalker(th, NodeFilter.SHOW_TEXT);
    let broken = null;
    for (let n = walker.nextNode(); n && !broken; n = walker.nextNode()) {
      if (sr(n.parentElement)) continue;
      const re = /\S+/g; let m;
      while ((m = re.exec(n.data)) && !broken) {
        const rg = document.createRange(); rg.setStart(n, m.index); rg.setEnd(n, m.index + m[0].length);
        const tops = new Set([...rg.getClientRects()].filter((q) => q.width > 0).map((q) => Math.round(q.top)));
        if (tops.size > 1) broken = m[0];
      }
    }
    if (broken) r.thBroken.push(name(th) + ' — слово «' + broken + '» разорвано');
  }
  for (const t of main.querySelectorAll('table')) {
    if (!shown(t)) continue;
    const head = t.tHead; const body = t.tBodies[0];
    if (!head || !body || !body.rows.length) continue;
    const firstRow = [...body.rows].find(shown);
    const ths = [...head.querySelectorAll('th')].filter(shown);
    const sticky = ths.filter((th) => cs(th).position === 'sticky' || cs(head).position === 'sticky');
    if (firstRow && sticky.length) {
      const hb = Math.max(...sticky.map((th) => th.getBoundingClientRect().bottom));
      const rt = firstRow.getBoundingClientRect().top;
      if (hb > rt + 1) r.stickyOverlap.push(name(t).slice(0, 40) + ' на ' + Math.round(hb - rt) + 'px');
    }
    // Ярусы шапки при прокрутке таблицы вниз: каждый следующий ряд
    // прилипает под предыдущим, а не поверх него (у ведомости два яруса —
    // группы колонок и сами колонки, и оба стояли на top: 0).
    const box = scroller(t);
    if (box && head.rows.length > 1 && box.scrollHeight > box.clientHeight + 40) {
      box.scrollTop = 200;
      const rows = [...head.rows].filter(shown).map((row) => row.getBoundingClientRect());
      for (let i = 1; i < rows.length; i++) if (rows[i].top < rows[i - 1].bottom - 1) r.stickyOverlap.push(name(t).slice(0, 40) + ' ярус ' + (i + 1) + ' поверх яруса ' + i + ' на ' + Math.round(rows[i - 1].bottom - rows[i].top) + 'px');
      box.scrollTop = 0;
    }
    const lastRow = [...head.rows].pop();
    const fills = new Set([...lastRow.cells].filter(shown).map((th) => { let bg = cs(th).backgroundColor; if (bg === 'rgba(0, 0, 0, 0)') bg = cs(lastRow).backgroundColor; if (bg === 'rgba(0, 0, 0, 0)') bg = cs(head).backgroundColor; return bg; }));
    if (fills.size > 1) r.thFill.push(name(t).slice(0, 40) + ' ' + [...fills].join(' / '));
  }
  // Кнопка поля файла — шрифтом продукта, а не системным: так видно, что
  // правило продукта до неё доехало (нативная берёт шрифт браузера).
  const bodyFont = cs(document.body).fontFamily;
  r.file = [...main.querySelectorAll('input[type=file]')].filter((i) => shown(i) && getComputedStyle(i, '::file-selector-button').fontFamily !== bodyFont).length;
  if (${phone}) {
    r.headTop = Math.round(mr.top + window.scrollY);
    const bar = document.querySelector('.sidenav__list');
    window.scrollTo(0, document.documentElement.scrollHeight);
    // Последний видимый блок страницы, а не последняя ячейка: ячейка внутри
    // прокручиваемой таблицы может лежать ниже края своего контейнера и
    // никому не видна — она не «закрыта полосой».
    const last = [...main.children].filter(shown).pop();
    if (bar && last) r.barCovers = Math.max(0, Math.round(last.getBoundingClientRect().bottom - bar.getBoundingClientRect().top));
    window.scrollTo(0, 0);
  }
  r.over = r.over.slice(0, 6); r.clipped = r.clipped.slice(0, 6); r.thBroken = r.thBroken.slice(0, 4); r.scrolls = r.scrolls.slice(0, 4);
  return r;
})()`);

const report = {};
const shots = [];
for (const role of ROLES) {
  await send("Network.enable");
  await send("Network.clearBrowserCookies");
  await size(1440, 900);
  await login(role, pass);
  await goto("/periods/");
  const list = await pages();
  for (const [k, [w, h]] of Object.entries(WIDTHS)) {
    await size(w, h);
    for (const page of list) {
      await goto(page);
      report[`${role} ${k} ${page}`] = await probe(k === "phone");
      const kind = page.replace(/[0-9a-f-]{36}\//, "");
      if (role === ROLES[0] && k !== "narrow" && SHOTS.has(kind)) shots.push(await shot(`${role}-${k}-${kind.replace(/\//g, "_")}`));
    }
  }
}
writeFileSync(`${OUT}/report.json`, JSON.stringify({ report, shots }, null, 1));

const bad = [];
for (const [k, v] of Object.entries(report)) {
  const wide = / (wide|tablet) /.test(k);
  if (v.hscroll > 0) bad.push(`${k}: страница едет вбок на ${v.hscroll}px`);
  if (v.over.length) bad.push(`${k}: за правым краем: ${v.over.join("; ")}`);
  if (v.clipped.length) bad.push(`${k}: обрезано: ${v.clipped.join("; ")}`);
  if (wide && v.wraps.length) bad.push(`${k}: ряд в два ряда: ${v.wraps.join("; ")}`);
  if (v.thBroken.length) bad.push(`${k}: заголовок колонки столбиком: ${v.thBroken.join("; ")}`);
  if (v.stickyOverlap.length) bad.push(`${k}: липкая шапка на первой строке: ${v.stickyOverlap.join("; ")}`);
  if (v.thFill.length) bad.push(`${k}: шапка залита не целиком: ${v.thFill.join("; ")}`);
  if (v.file) bad.push(`${k}: нативное поле выбора файла: ${v.file}`);
  if (v.headTop > PHONE_HEAD_MAX) bad.push(`${k}: шапка телефона ${v.headTop}px > ${PHONE_HEAD_MAX}`);
  if (v.barCovers > 0) bad.push(`${k}: нижняя полоса закрывает конец страницы на ${v.barCovers}px`);
}
console.log(`страниц×ширин: ${Object.keys(report).length}; снимки: ${shots.join(" ")}`);
if (bad.length) {
  console.log(bad.join("\n"));
  process.exit(1);
}
console.log("раскладка сошлась");
process.exit(0);
