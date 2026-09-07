// プロフ用リンクのクリック計測 + LINEへリダイレクト（Cloudflare Worker）
// - どのパスに来ても（/stats 以外は）1クリックとしてD1に記録し、LINEへ302リダイレクト
// - ?src=015 のように付ければ動画別に内訳が取れる（デフォルトは "profile"）
// - /stats?key=STATS_KEY で集計をスマホからも見られる（key必須＝簡易パスワード）

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 集計ビュー
    if (url.pathname === "/stats") {
      if (url.searchParams.get("key") !== env.STATS_KEY) {
        return new Response("forbidden", { status: 403 });
      }
      return await statsPage(env);
    }

    // favicon等のノイズは計測せずスルー
    if (url.pathname === "/favicon.ico") {
      return new Response(null, { status: 204 });
    }

    // --- クリック計測 ---
    const src = (url.searchParams.get("src") || "profile").slice(0, 40);
    const insert = env.DB.prepare(
      "INSERT INTO clicks (ts, src, referer) VALUES (?, ?, ?)"
    ).bind(
      Date.now(),
      src,
      (request.headers.get("referer") || "").slice(0, 200)
    ).run();

    // 記録の完了を待たずにユーザーはLINEへ飛ばす（体感ゼロ遅延）
    ctx.waitUntil(insert);
    return Response.redirect(env.LINE_URL, 302);
  },
};

async function statsPage(env) {
  // JST(UTC+9)で日別集計
  const day = "strftime('%Y-%m-%d', ts/1000 + 9*3600, 'unixepoch')";

  const total = await env.DB.prepare("SELECT COUNT(*) AS n FROM clicks").first("n");
  const today = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM clicks WHERE ${day} = strftime('%Y-%m-%d','now','+9 hours')`
  ).first("n");

  const daily = (await env.DB.prepare(
    `SELECT ${day} AS d, COUNT(*) AS n FROM clicks GROUP BY d ORDER BY d DESC LIMIT 14`
  ).all()).results;

  const bySrc = (await env.DB.prepare(
    `SELECT src, COUNT(*) AS n FROM clicks GROUP BY src ORDER BY n DESC LIMIT 30`
  ).all()).results;

  const row = (a, b) => `<tr><td>${esc(a)}</td><td style="text-align:right">${b}</td></tr>`;
  const html = `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>クリック計測</title>
<style>
  body{font-family:-apple-system,'Hiragino Sans',sans-serif;margin:0;padding:20px;background:#f5f5f7;color:#1d1d1f}
  h1{font-size:17px;color:#6e6e73;font-weight:600;margin:0 0 16px}
  .big{display:flex;gap:12px;margin-bottom:24px}
  .card{flex:1;background:#fff;border-radius:14px;padding:16px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.06)}
  .card .num{font-size:34px;font-weight:700}
  .card .lbl{font-size:12px;color:#8a8a8e;margin-top:2px}
  table{width:100%;border-collapse:collapse;background:#fff;border-radius:14px;overflow:hidden;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
  th{font-size:12px;color:#8a8a8e;text-align:left;padding:10px 14px;border-bottom:1px solid #eee}
  td{padding:9px 14px;border-bottom:1px solid #f0f0f0;font-size:14px}
  tr:last-child td{border-bottom:none}
  h2{font-size:13px;color:#6e6e73;margin:0 0 8px}
</style></head><body>
<h1>プロフリンク クリック計測</h1>
<div class="big">
  <div class="card"><div class="num">${total}</div><div class="lbl">累計クリック</div></div>
  <div class="card"><div class="num">${today}</div><div class="lbl">今日</div></div>
</div>
<h2>日別（直近14日・JST）</h2>
<table><tr><th>日付</th><th style="text-align:right">クリック</th></tr>
${daily.map(r => row(r.d, r.n)).join("")}</table>
<h2>流入元 src 別</h2>
<table><tr><th>src</th><th style="text-align:right">クリック</th></tr>
${bySrc.map(r => row(r.src, r.n)).join("")}</table>
</body></html>`;

  return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
