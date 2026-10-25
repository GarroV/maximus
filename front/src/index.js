// Фронт MAXIMUS на Cloudflare — по образцу decimus (D236) и meridius.
//
// Публичного адреса у сервера нет (vps-infra, решение 09.10.2026 «сслип давай
// убираем»: sslip.io в стране пользователя заблокирован). Единственный вход —
// этот Worker: он принимает запрос на своём адресе *.workers.dev и пересылает
// его туннелем (привязка EDGE — Workers VPC) прямо в Caddy на VPS. Своей логики
// у него нет.
//
// Три правки по дороге, и все три — чтобы сервер не заметил посредника:
//  1. Origin/Referer со своего адреса переписываются на адрес сервера — иначе
//     проверка CSRF в Django отбивала бы каждую форму как пришедшую с чужого
//     сайта. Чужой Origin уходит нетронутым и получает тот же отказ.
//  2. Адрес посетителя уходит отдельным заголовком вместе с ключом фронта.
//     Caddy пускает к продукту ТОЛЬКО запрос с верным ключом, и только тогда
//     верит этому адресу.
//  3. Location с адресом сервера переписывается на адрес фронта, иначе
//     переадресация уводила бы на внутреннее имя, которое нигде не открывается.
//
// Запасного пути через интернет, как у meridius, здесь нет: публичного имени
// у сервера больше нет. Туннель лёг — честный 502, а не тишина.

const FRONT_KEY_HEADER = "X-Maximus-Front-Key";
const CLIENT_IP_HEADER = "X-Maximus-Client-IP";

export default {
  async fetch(request, env) {
    if (!env.ORIGIN_HOST || !env.FRONT_KEY || !env.EDGE) {
      // Без настроек не пересылаем вовсе: Caddy всё равно отказал бы, а так
      // причина видна сразу.
      return new Response("front is not configured", { status: 503 });
    }
    const front = new URL(request.url);
    const origin = `https://${env.ORIGIN_HOST}`;
    // Хост сервера закреплён, а не собран из пути: `new URL("//evil.com/x", origin)`
    // читает путь как адрес другого сайта, и запрос ушёл бы туда вместе с
    // ключом фронта. Путь только присваивается, итог сверяется ещё раз.
    const upstream = new URL(origin);
    upstream.pathname = front.pathname.replace(/^\/+/, "/");
    upstream.search = front.search;
    if (upstream.origin !== origin) {
      return new Response("bad request", { status: 400 });
    }

    const headers = new Headers(request.headers);
    for (const name of ["Origin", "Referer"]) {
      const value = headers.get(name);
      if (
        value &&
        (value === front.origin || value.startsWith(front.origin + "/"))
      ) {
        headers.set(name, origin + value.slice(front.origin.length));
      }
    }
    headers.set(FRONT_KEY_HEADER, env.FRONT_KEY);
    headers.set(
      CLIENT_IP_HEADER,
      request.headers.get("CF-Connecting-IP") || "",
    );
    headers.delete("Host");

    const hasBody = !["GET", "HEAD"].includes(request.method);
    let response;
    try {
      response = await env.EDGE.fetch(upstream, {
        method: request.method,
        headers,
        body: hasBody ? request.body : undefined,
        redirect: "manual",
      });
    } catch (error) {
      console.error("tunnel failed", String(error));
      return new Response("server is unreachable", { status: 502 });
    }

    const location = response.headers.get("Location");
    if (!location || !location.startsWith(origin)) {
      return response;
    }
    const rewritten = new Response(response.body, response);
    rewritten.headers.set(
      "Location",
      front.origin + location.slice(origin.length),
    );
    return rewritten;
  },
};
