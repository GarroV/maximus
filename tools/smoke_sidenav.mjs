/*
 * Смоук левой панели (T222, D081) — то, чего не доказать разбором разметки.
 *
 * Роли × размеры (1440×900 и 1440×768 — панель целиком, 900 — планшет, тоже
 * целиком, 375 — разделы полосой у нижнего края) × три экрана, затем
 * английский, сербский и тёмная тема. Пробы:
 *
 *  - страница не едет вбок (поймано на 900: слой склада ставил body 1280);
 *  - открытый раздел помечен ровно один раз, раскрытий и списков в панели нет;
 *  - все разделы влезают без прокрутки панели на 1440×900 у самых тяжёлых
 *    ролей — администратора и бухгалтера — на всех трёх языках (сверка с
 *    эталоном по T222); на 768 результат записывается, но не валит прогон;
 *  - ни одна подпись не обрезана многоточием, и у каждой ссылки и кнопки
 *    панели есть имя для диктора;
 *  - на английской странице внизу панели нет роли по-русски (название
 *    партнёра — данные, оно не проверяется);
 *  - полоса на телефоне показывает, что едет (гаснущий край), подписи
 *    служебных пунктов видны словами, всё нажимаемое не меньше 44;
 *  - первая табуляция встаёт на «к содержанию», а на снимках её нет.
 *
 * Снимки — в OUT, для сверки с эталоном.
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
const SIZES = { wide: [1440, 900], short: [1440, 768], tablet: [900, 1000], phone: [375, 800] };
const HEAVY = ["admin", "accountant"];
const SHOT_ROLES = ["accountant", "manager", "admin"];
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function size(w, h) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 720 });
  await pause(300);
}

// Снимок без фокуса: иначе на нём осталась бы «К содержанию» после пробы
// табуляции, и снимок врал бы о том, как экран выглядит обычно.
async function shot(name) {
  await evalIn("document.activeElement && document.activeElement.blur(); 0");
  await pause(150);
  const skip = await evalIn("document.querySelector('.skip-link').getBoundingClientRect().bottom");
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.data, "base64"));
  return { path: `${OUT}/${name}.png`, skipVisible: skip > 0 };
}

const probe = () => evalIn(`(() => {
  const nav = document.querySelector('nav.sidenav');
  const list = document.querySelector('.sidenav__list');
  const shown = (e) => e.getClientRects().length > 0;
  const labels = [...nav.querySelectorAll('.sidenav__label, .nav-offer')].filter(shown);
  const cut = labels.filter((l) => {
    const cs = getComputedStyle(l);
    const clipped = cs.clip && cs.clip !== 'auto';
    return !clipped && (cs.textOverflow === 'ellipsis' || l.scrollWidth > l.clientWidth + 1);
  }).map((l) => l.textContent.trim());
  const nameless = [...nav.querySelectorAll('a, button, [aria-current]')].filter(
    (e) => !((e.getAttribute('aria-label') || '') + e.textContent).trim()
  ).map((e) => e.outerHTML.slice(0, 80));
  const tappable = [...nav.querySelectorAll('a, button, .sidenav__choice, .sidenav__current')].filter(shown);
  const items = [...list.querySelectorAll('.sidenav__item, .sidenav__current')].filter(shown);
  const service = [...nav.querySelectorAll('.sidenav__foot .sidenav__label, .sidenav__logout .sidenav__label')];
  // Роль, имя и выход — без партнёра: название партнёра — данные, а не
  // строка интерфейса, и не переводится (у сида оно «Dodo Serbia (тестовые
  // данные)»).
  const who = [...document.querySelectorAll('.sidenav__whoami, .sidenav__logout')].map((e) => e.innerText).join(' ');
  return {
    hscroll: document.documentElement.scrollWidth - window.innerWidth,
    current: document.querySelectorAll('[aria-current="page"]').length,
    details: nav.querySelectorAll('details, select').length,
    navWidth: Math.round(nav.getBoundingClientRect().width),
    listOverflow: list.scrollHeight - list.clientHeight,
    groupsShown: [...nav.querySelectorAll('.sidenav__group')].filter(shown).length,
    whoShown: !!document.querySelector('.sidenav__who') && shown(document.querySelector('.sidenav__who')),
    cut, nameless,
    minTap: Math.round(Math.min(...tappable.map((e) => e.getBoundingClientRect().height))),
    minItem: Math.round(Math.min(...items.map((e) => e.getBoundingClientRect().height))),
    mask: getComputedStyle(list).maskImage || getComputedStyle(list).webkitMaskImage || 'none',
    serviceHidden: service.filter((l) => l.getBoundingClientRect().width < 2).map((l) => l.textContent.trim()),
    cyrillicWho: /[А-Яа-яЁё]/.test(who), who,
    lang: document.documentElement.lang,
  };
})()`);

async function tabFirst() {
  // Фокус в документ ставится явно: у безголового окна фокуса может не быть
  // вовсе, и первая табуляция тогда уходит в никуда — ложное «сломано».
  await send("Page.bringToFront");
  await evalIn("document.activeElement && document.activeElement.blur(); document.body.tabIndex = -1; document.body.focus(); document.body.removeAttribute('tabindex'); 0");
  for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", { type, key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await pause(200);
  return evalIn("document.activeElement.className + ' ' + (document.activeElement.getAttribute('href')||'')");
}

async function setLanguage(code) {
  await goto("/periods/");
  await evalIn(`(() => { const f=[...document.querySelectorAll('form')].find(x=>x.action.endsWith('/i18n/setlang/')); const b=f.querySelector('button[value="${code}"]'); if (b) b.click(); })()`);
  await pause(1800);
}

async function setTheme(code) {
  await evalIn(`(() => { const b=document.querySelector('form[action="/theme/"] button[value="${code}"]'); if (b) b.click(); })()`);
  await pause(1800);
}

const report = {};
const shots = [];
for (const role of ["accountant", "manager", "director", "admin"]) {
  await send("Network.enable");
  await send("Network.clearBrowserCookies");
  await size(1440, 900);
  await login(role, pass);
  for (const [k, [w, h]] of Object.entries(SIZES)) {
    await size(w, h);
    for (const page of ["/periods/", "/expenses/", "/inbox/"]) {
      await goto(page);
      report[`${role} ${k} ${page}`] = await probe();
    }
    await goto("/periods/");
    if (k === "wide") report[`${role} tab`] = await tabFirst();
    if (SHOT_ROLES.includes(role)) shots.push([`${role}-${k}`, await shot(`${role}-${k}`)]);
  }
  if (HEAVY.includes(role)) {
    for (const lang of ["en", "sr-latn"]) {
      await setLanguage(lang);
      for (const k of ["wide", "short"]) {
        await size(...SIZES[k]);
        await goto("/periods/");
        report[`${role} ${lang} ${k}`] = await probe();
      }
      if (role === "admin" && lang === "en") {
        await size(...SIZES.wide);
        await goto("/periods/");
        shots.push(["admin-en-wide", await shot("admin-en-wide")]);
        await size(...SIZES.phone);
        await goto("/periods/");
        report["admin en phone"] = await probe();
        shots.push(["admin-en-phone", await shot("admin-en-phone")]);
        await size(...SIZES.wide);
        await goto("/periods/");
        await setTheme("dark");
        await goto("/periods/");
        report["admin en dark"] = await evalIn("document.documentElement.getAttribute('data-theme')");
        shots.push(["admin-en-dark", await shot("admin-en-dark")]);
        await setTheme("system");
      }
    }
    await setLanguage("ru");
  }
}
// Страница учётной записи — служебный пункт ведёт на свою страницу.
await size(...SIZES.wide);
await goto("/account/");
report["admin account"] = await probe();
shots.push(["admin-account", await shot("admin-account")]);

report.shots = Object.fromEntries(shots);
writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));

const bad = [];
const notes = [];
for (const [k, v] of Object.entries(report)) {
  if (k.endsWith(" tab") && !String(v).startsWith("skip-link")) bad.push(`${k}: первая табуляция — «${v}», а не «к содержанию»`);
  if (typeof v !== "object" || k === "shots") continue;
  const phone = k.includes("phone");
  if (v.hscroll > 0) bad.push(`${k}: страница едет вбок на ${v.hscroll}px`);
  if (v.current !== 1) bad.push(`${k}: открытых разделов ${v.current}, а не один`);
  if (v.details) bad.push(`${k}: в панели раскрытие или выпадающий список`);
  if (v.cut.length) bad.push(`${k}: подписи обрезаны: ${v.cut.join(", ")}`);
  if (v.nameless.length) bad.push(`${k}: без имени для диктора: ${v.nameless.join(" | ")}`);
  if (!phone && v.minItem < 34) bad.push(`${k}: пункт ниже 34 пикселей (${v.minItem})`);
  if (phone && v.minTap < 44) bad.push(`${k}: нажимаемое меньше пальца (${v.minTap})`);
  if (phone && v.mask === "none") bad.push(`${k}: не видно, что полоса разделов едет`);
  if (phone && v.serviceHidden.length) bad.push(`${k}: служебные пункты без подписи: ${v.serviceHidden.join(", ")}`);
  if (k.includes("tablet") && (!v.whoShown || !v.groupsShown)) bad.push(`${k}: на планшете не видно роли или групп`);
  if ((k.includes(" en ") || k.endsWith(" en")) && v.cyrillicWho) bad.push(`${k}: внизу панели русский на английской странице`);
  const heavy = HEAVY.some((r) => k.startsWith(r + " "));
  if (heavy && k.includes("wide") && v.listOverflow > 0) bad.push(`${k}: разделы не влезают, панель прокручивается на ${v.listOverflow}px`);
  if (heavy && k.includes("short")) notes.push(`${k}: ${v.listOverflow > 0 ? `прокрутка панели ${v.listOverflow}px` : "влезает"}`);
}
for (const [name, s] of shots) if (s.skipVisible) bad.push(`${name}: на снимке видна «К содержанию»`);
console.log(notes.join("\n"));
console.log(bad.length ? bad.join("\n") : "всё сошлось");
process.exit(bad.length ? 1 : 0);
