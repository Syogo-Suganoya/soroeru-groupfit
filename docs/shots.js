/**
 * 紹介ページ（web/index.html）と README に載せる画面の写しを撮る。
 *
 *   GEMINI_MODE=mock docker compose --profile shots run --rm shots
 *
 * 「友人4人でお呼ばれ」の場面を API で組み立ててから、各メンバーの画面を開いて撮る。
 * 毎回新しいルームを作るので、エミュレータを空にしなくても同じ絵になる。
 * 画面を変えたら撮り直すだけで追随する。
 *
 * GEMINI_MODE=mock を付けるのは、総評文を毎回同じにするため（live だと撮るたびに変わる）。
 */

const fs = require("fs");
const puppeteer = require("puppeteer");

const BASE = process.env.BASE_URL || "http://api:8080";
const OUT = process.env.OUT_DIR || "/work/web/shots";
const WIDTH = 960;
const PAD = 16;

// api は画像のURLを PUBLIC_BASE_URL（既定 localhost:8080）で返す。撮影用のブラウザからは
// そのホストに届かないので、読み込みだけ差し替える。画面に出る文字は触らない。
const PUBLIC = "http://localhost:8080";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function inDays(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_BIN || "/usr/bin/chromium-browser",
    // 言語を指定しないと日付の入力欄が 10/22/2026 のような米国式になる
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none", "--lang=ja-JP"],
    env: { ...process.env, LANG: "ja_JP.UTF-8" },
  });
  const page = await browser.newPage();
  await page.setExtraHTTPHeaders({ "Accept-Language": "ja-JP,ja" });
  await page.setViewport({ width: WIDTH, height: 900, deviceScaleFactor: 2 });

  page.on("console", (m) => { if (m.type() === "error") console.log(`[画面] ${m.text()}`); });
  page.on("pageerror", (e) => console.log(`[画面] ${e.message}`));

  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    if (url.startsWith(PUBLIC)) req.continue({ url: BASE + url.slice(PUBLIC.length) });
    else req.continue();
  });

  // ------------------------------------------------------------ API（場面づくり）
  // ブラウザの fetch を使う。Node 側の fetch の有無に左右されないようにするため。
  await page.goto(`${BASE}/health`);
  const call = (method, path, body) =>
    page.evaluate(
      async (base, method, path, body) => {
        const res = await fetch(base + path, {
          method,
          headers: { "Content-Type": "application/json" },
          body: body ? JSON.stringify(body) : undefined,
        });
        const text = await res.text();
        if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${text}`);
        return text ? JSON.parse(text) : null;
      },
      BASE, method, path, body,
    );

  // ------------------------------------------------------------ 撮影の道具
  const waitShown = async (id) => {
    try {
      await page.waitForFunction((i) => {
        const el = document.getElementById(i);
        return el && !el.hidden && el.getBoundingClientRect().height > 0;
      }, { timeout: 20000 }, id);
    } catch (e) {
      const seen = await page.evaluate(() => document.body.innerText.slice(0, 300));
      throw new Error(`#${id} が出なかった。画面にはこれが出ていた:\n${seen}`);
    }
  };

  // 画像とフォントが揃うまで待つ。途中で撮ると枠だけの絵になる。
  const settle = async () => {
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all(
        [...document.images]
          .filter((img) => img.src && !img.complete)
          .map((img) => new Promise((r) => { img.onload = img.onerror = r; })),
      );
    });
    await sleep(500);
  };

  // 画面のうち、説明したい区画だけを切り出す。
  // 上下に離れた区画を指定すると間の区画まで入って縦に長くなるので、撮る間だけ
  // それ以外の区画を隠して詰める。区画の中身と並び順は実際の画面のまま。
  const shot = async (name, selectors) => {
    await page.evaluate((sels) => {
      const keep = sels.flatMap((s) => [...document.querySelectorAll(s)]);
      document.querySelectorAll("main > *").forEach((el) => {
        if (el.hidden || keep.some((k) => el === k || el.contains(k))) return;
        el.hidden = true;
        el.dataset.shotHidden = "1"; // 撮り終えたら戻す印
      });
    }, selectors);
    await settle();
    const box = await page.evaluate((sels, pad) => {
      const rects = sels
        .flatMap((s) => [...document.querySelectorAll(s)])
        .filter((el) => !el.closest("[hidden]"))
        .map((el) => el.getBoundingClientRect())
        .filter((r) => r.height > 0);
      if (!rects.length) return null;
      const top = Math.min(...rects.map((r) => r.top)) + scrollY;
      const left = Math.min(...rects.map((r) => r.left)) + scrollX;
      const bottom = Math.max(...rects.map((r) => r.bottom)) + scrollY;
      const right = Math.max(...rects.map((r) => r.right)) + scrollX;
      return {
        x: Math.max(0, left - pad),
        y: Math.max(0, top - pad),
        width: Math.min(document.documentElement.clientWidth, right + pad) - Math.max(0, left - pad),
        height: bottom - top + pad * 2,
        full: document.documentElement.scrollHeight,
      };
    }, selectors, PAD);
    if (!box) throw new Error(`${name}: 写すものが見つからない（${selectors.join(", ")}）`);

    // 切り出し範囲がビューポートの外にはみ出すと欠けるので、ページ全体が入る高さにしてから撮る
    await page.setViewport({ width: WIDTH, height: Math.ceil(box.full), deviceScaleFactor: 2 });
    await sleep(200);
    const { full, ...clip } = box;
    await page.screenshot({ path: `${OUT}/${name}.png`, clip });
    console.log(`撮影: ${name}.png（${Math.round(clip.width)}×${Math.round(clip.height)}）`);
    await page.setViewport({ width: WIDTH, height: 900, deviceScaleFactor: 2 });
    await page.evaluate(() => {
      document.querySelectorAll("[data-shot-hidden]").forEach((el) => {
        el.hidden = false;
        delete el.dataset.shotHidden;
      });
    });
  };

  // 誰として開くかは localStorage で決まる（アプリの入室画面で選んだのと同じ状態）
  const openAs = async (roomId, uid) => {
    await page.goto(`${BASE}/app?room=${roomId}`, { waitUntil: "domcontentloaded" });
    await page.evaluate((r, u) => {
      if (u) localStorage.setItem(`soroeru:${r}`, u);
      else localStorage.removeItem(`soroeru:${r}`);
    }, roomId, uid);
    await page.goto(`${BASE}/app?room=${roomId}`, { waitUntil: "networkidle0" });
  };

  // ------------------------------------------------------------ 1. 幹事がルームを作る
  await page.goto(`${BASE}/app`, { waitUntil: "networkidle0" });
  await waitShown("view-create");
  await page.evaluate((date) => {
    document.getElementById("date").value = date;
    document.getElementById("organizer").value = "さおり";
    document.getElementById("lighting-new").value = "hall_evening";
  }, inDays(30));
  await shot("01-create", ["#view-create"]);

  // ------------------------------------------------------------ 場面: 友人4人でお呼ばれ
  const room = await call("POST", "/api/rooms", {
    title: "友人の結婚式",
    scene: "wedding",
    event_date: inDays(30),
    dress_code: "白NG・ファーNG",
    organizer_name: "さおり",
  });
  const R = room.room_id;
  const saori = room.members[0].uid;
  const join = async (name) => (await call("POST", `/api/rooms/${R}/members`, { display_name: name })).uid;
  const mizuki = await join("みずき");
  const akane = await join("あかね");
  const yui = await join("ゆい");

  // ゆいはまだ同意していない → 集合プレビューではシルエット
  for (const uid of [saori, mizuki, akane]) {
    await call("POST", `/api/rooms/${R}/members/${uid}/consent`, { granted: true });
  }

  const fit = async (uid, garments, pick) => {
    await call("POST", `/api/rooms/${R}/members/${uid}/tryon`, { garment_ids: garments });
    if (pick) await call("POST", `/api/rooms/${R}/members/${uid}/select`, { garment_id: pick });
  };
  await fit(saori, ["g_navy_satin", "g_bordeaux", "g_sage"], "g_navy_satin");
  await fit(akane, ["g_sage", "g_terracotta", "g_dusty_blue"], "g_sage");
  // みずきはさおりとほぼ同じネイビーを選ぶ → 色かぶりの指摘が出る
  await fit(mizuki, ["g_navy_lace", "g_dusty_blue", "g_floral_pink"], "g_navy_lace");

  // ------------------------------------------------------------ 2. 招待URLを開く
  await openAs(R, null);
  await waitShown("view-enter");
  await shot("02-enter", ["#view-enter"]);

  // ------------------------------------------------------------ 3. 参加者: 試着して決める
  await openAs(R, mizuki);
  await waitShown("sec-mine");
  await page.waitForFunction(() => document.querySelectorAll("#fits .fit img").length > 0, { timeout: 20000 });
  await shot("03-fitting", [".head-row", "#sec-mine"]);

  // ------------------------------------------------------------ 4. 参加者: かぶりの指摘
  await waitShown("sec-my-warn");
  await shot("04-harmony", ["#sec-preview", "#sec-my-warn", "#sec-my-notif"]);

  // ------------------------------------------------------------ 5. 幹事: 全体を見る
  await openAs(R, saori);
  await waitShown("sec-roster");
  await shot("05-organizer", [".head-row", "#sec-roster", "#sec-all-warn"]);

  // ------------------------------------------------------------ 6. 全員そろって仕上げ
  // みずきは代替案のくすみブルーに替え、ゆいも決める → 全員確定
  await call("POST", `/api/rooms/${R}/members/${mizuki}/select`, { garment_id: "g_dusty_blue" });
  await fit(yui, ["g_bordeaux", "g_terracotta", "g_floral_green"], "g_bordeaux");
  await call("POST", `/api/rooms/${R}/lighting`, { lighting: "hall_evening" });
  await call("POST", `/api/rooms/${R}/movie`);

  await openAs(R, saori);
  await waitShown("sec-finish");
  // ムービーはあとから読み込まれる。測った後に伸びると下が切れるので、描けるまで待つ
  await page.waitForFunction(() => {
    const img = document.getElementById("movie-img");
    return img && !img.hidden && img.complete && img.naturalWidth > 0;
  }, { timeout: 20000 });
  // 記念ムービーは集合プレビューと同じ構図なので、仕上げの区画だけを写す（並べると同じ絵が2枚になる）
  await shot("06-finish", ["#sec-finish"]);

  await browser.close();
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
