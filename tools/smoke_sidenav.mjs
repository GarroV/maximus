/*
 * Смоук левой панели (T222, D081) — то, чего не доказать разбором разметки.
 *
 * Четыре роли × три ширины (1440 — панель целиком, 900 — свёрнута до значков,
 * 375 — разделы полосой у нижнего края) × три экрана, затем английский и
 * тёмная тема. На каждом: страница не едет вбок (именно это поймано на 900 —
 * слой склада ставил body ширину 1280), открытый раздел помечен ровно один
 * раз, в панели нет раскрытий и выпадающих списков, в свёрнутой панели
 * подписи не выкинуты из дерева доступности; первая табуляция встаёт на
 * «к содержанию». Снимки — в OUT, для сверки с эталоном.
 *
 *     chrome-for-testing --headless=new --remote-debugging-port=9341 \
 *         --user-data-dir=/tmp/chrome-smoke &
 *     APP=http://127.0.0.1:8000 USER_PASS=… OUT=/tmp/shots node tools/smoke_sidenav.mjs
 *
 * Выходит с ненулевым кодом и называет пробу, если хоть одна не сошлась.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { evalIn, goto, login, send } from "./guide_browser.mjs";
const OUT = process.env.OUT || "/tmp/sidenav-shots";
mkdirSync(OUT, { recursive: true });
const pass = process.env.USER_PASS;
const sizes = { wide: [1440, 900], tablet: [900, 1000], phone: [375, 800] };
async function size(w, h) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 720 });
  await new Promise(r => setTimeout(r, 300));
}
async function shot(name) {
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.data, "base64"));
  return `${OUT}/${name}.png`;
}
const probe = () => evalIn(`(() => {
  const nav = document.querySelector('nav.sidenav');
  const labels = [...document.querySelectorAll('.sidenav__list .sidenav__label')];
  const list = document.querySelector('.sidenav__list');
  return {
    hscroll: document.documentElement.scrollWidth - window.innerWidth,
    current: document.querySelectorAll('[aria-current="page"]').length,
    details: nav ? nav.querySelectorAll('details, select').length : -1,
    navWidth: nav ? Math.round(nav.getBoundingClientRect().width) : 0,
    labelsDisplayNone: labels.filter(l => getComputedStyle(l).display === 'none').length,
    labels: labels.length,
    listPos: list ? getComputedStyle(list).position : '',
    groups: [...document.querySelectorAll('.sidenav__group')].map(g => g.textContent.trim()),
    foot: !!document.querySelector('.sidenav__foot form[action="/i18n/setlang/"], .sidenav__foot form[action="/theme/"]'),
    who: (document.querySelector('.sidenav__who')||{}).innerText || '',
    lang: document.documentElement.lang,
    font: getComputedStyle(document.body).fontFamily.slice(0,40),
    minTap: Math.min(...[...document.querySelectorAll('.sidenav a, .sidenav button')].filter(e=>e.offsetParent).map(e=>e.getBoundingClientRect().height)),
  };
})()`);
async function tabFirst() {
  // Фокус в документ ставится явно: у безголового окна фокуса может не быть
  // вовсе, и первая табуляция тогда уходит в никуда — ложное «сломано».
  await send("Page.bringToFront");
  await evalIn("document.activeElement && document.activeElement.blur(); document.body.tabIndex = -1; document.body.focus(); document.body.removeAttribute('tabindex'); 0");
  for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", { type, key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await new Promise(r => setTimeout(r, 200));
  return evalIn("document.activeElement.className + ' ' + (document.activeElement.getAttribute('href')||'')");
}
const report = {};
for (const role of ["accountant", "manager", "director", "admin"]) {
  await send("Network.clearBrowserCookies").catch(() => {});
  await send("Network.enable");
  await send("Network.clearBrowserCookies");
  await size(1440, 900);
  await login(role, pass);
  for (const [k, [w, h]] of Object.entries(sizes)) {
    await size(w, h);
    for (const page of ["/periods/", "/expenses/", "/inbox/"]) {
      await goto(page);
      report[`${role} ${k} ${page}`] = await probe();
    }
    await goto("/periods/");
    if (k === "wide") report[`${role} tab`] = await tabFirst();
    if (role === "manager" || role === "accountant") report[`${role} ${k} shot`] = await shot(`${role}-${k}`);
  }
}
// английский: директор, широкий экран
await size(1440, 900);
await goto("/periods/");
await evalIn(`(() => { const f=[...document.querySelectorAll('form')].find(x=>x.action.endsWith('/i18n/setlang/')); const b=f.querySelector('button[value="en"]'); b.click(); })()`);
await new Promise(r => setTimeout(r, 2000));
await goto("/periods/");
report["admin en wide"] = await probe();
report["admin en shot"] = await shot("admin-en-wide");
// тёмная тема
await evalIn(`document.querySelector('form[action="/theme/"] button[value="dark"]').click()`);
await new Promise(r => setTimeout(r, 2000));
await goto("/periods/");
report["admin en dark"] = await evalIn("document.documentElement.getAttribute('data-theme')");
report["admin en dark shot"] = await shot("admin-en-dark");
await evalIn(`document.querySelector('form[action="/theme/"] button[value="system"]').click()`);
await new Promise(r => setTimeout(r, 1500));
await evalIn(`(() => { const f=[...document.querySelectorAll('form')].find(x=>x.action.endsWith('/i18n/setlang/')); f.querySelector('button[value="ru"]').click(); })()`);
await new Promise(r => setTimeout(r, 1500));
writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));
const bad = [];
for (const [k, v] of Object.entries(report)) {
  if (k.endsWith(" tab") && !String(v).startsWith("skip-link")) bad.push(`${k}: первая табуляция — «${v}», а не «к содержанию»`);
  if (typeof v !== "object") continue;
  if (v.hscroll > 0) bad.push(`${k}: страница едет вбок на ${v.hscroll}px`);
  if (v.current !== 1) bad.push(`${k}: открытых разделов ${v.current}, а не один`);
  if (v.details) bad.push(`${k}: в панели раскрытие или выпадающий список`);
  if (k.includes("tablet") && v.labelsDisplayNone) bad.push(`${k}: подписи выкинуты из дерева доступности`);
}
console.log(bad.length ? bad.join("\n") : "всё сошлось");
process.exit(bad.length ? 1 : 0);
