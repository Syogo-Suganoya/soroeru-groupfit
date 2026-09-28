/**
 * 提出用のデモ動画を撮る。一連の基本操作を、利用者と同じ順に画面を操作して録画する。
 *
 *   docker compose --profile shots run --rm shots docs/demo.js
 *
 * 出力は dist/soroeru-demo.mp4（リポジトリには入れない）。
 * 紹介ページ（/）は撮らない。アプリ（/app）の操作だけを録る。
 *
 * 流れ（友人の結婚式に4人で出る場面）:
 *   1. 幹事のさおりさんがルームを作り、試着して衣装を決め、合成に同意する
 *   2. あかねさんが参加して衣装を決める（ここは API で進め、画面は撮らない）
 *   3. 招待URLを開いたみずきさんが参加し、試着して決める → さおりさんと色かぶり
 *   4. みずきさんが代替案から選び直す → かぶりが消える。合成に同意する
 *   5. 幹事の画面に戻り、進み具合と未参加の人を確認する
 *
 * 総評は起動中の api の GEMINI_MODE に従う（live なら Gemini の文が録れる）。
 */

const { execFileSync } = require("child_process");
const fs = require("fs");
const puppeteer = require("puppeteer");

const BASE = process.env.BASE_URL || "http://api:8080";
const OUT_DIR = process.env.DEMO_DIR || "/work/dist";
const MP4 = `${OUT_DIR}/soroeru-demo.mp4`;
const W = 1440;
const H = 900;

// api は画像のURLを PUBLIC_BASE_URL（localhost）で返す。撮影用ブラウザからは届かないので読み込みだけ差し替える
const FRAMES = "/tmp/soroeru-demo-frames";
const PUBLIC = /^http:\/\/localhost(:\d+)?/;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function inDays(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

// ------------------------------------------------------------ 画面に重ねる演出
// ヘッドレスのブラウザにはマウスカーソルが無いので、見せるための矢印と、
// 今どの手順かを示す字幕を、ページ読み込みのたびに差し込む。アプリのコードは触らない。
const OVERLAY = () => {
  const install = () => {
    if (document.getElementById("demo-cursor")) return;
    const style = document.createElement("style");
    style.textContent = `
      #demo-cursor { position: fixed; left: 0; top: 0; width: 22px; height: 22px; z-index: 99999;
        pointer-events: none; transform: translate(-3px, -2px); transition: transform .05s linear; }
      #demo-cursor.down svg { transform: scale(.85); }
      #demo-caption { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%);
        z-index: 99998; pointer-events: none; background: rgba(40, 36, 34, .88); color: #fff;
        font: 700 20px/1.6 "M PLUS Rounded 1c", "Noto Sans CJK JP", sans-serif;
        padding: 10px 26px; border-radius: 999px; white-space: nowrap; opacity: 0; transition: opacity .3s; }
      #demo-caption.on { opacity: 1; }
      #demo-caption small { font-size: 14px; font-weight: 500; opacity: .8; margin-left: 12px; }
      .demo-ring { position: fixed; z-index: 99997; pointer-events: none; border: 3px solid #ff6f61;
        border-radius: 50%; width: 36px; height: 36px; margin: -18px 0 0 -18px;
        animation: demo-ring .5s ease-out forwards; }
      @keyframes demo-ring { from { transform: scale(.4); opacity: 1 } to { transform: scale(1.4); opacity: 0 } }`;
    document.head.appendChild(style);
    const cursor = document.createElement("div");
    cursor.id = "demo-cursor";
    cursor.innerHTML = `<svg viewBox="0 0 22 22" width="22" height="22"><path d="M2 1 L2 18 L6.5 13.8 L9.6 20.5 L12.6 19.2 L9.6 12.6 L15.5 12.6 Z"
      fill="#fff" stroke="#222" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
    document.body.appendChild(cursor);
    const caption = document.createElement("div");
    caption.id = "demo-caption";
    document.body.appendChild(caption);

    const pos = window.__demoPos || { x: W / 2, y: H / 2 };
    const place = (x, y) => {
      window.__demoPos = { x, y };
      cursor.style.transform = `translate(${x - 3}px, ${y - 2}px)`;
    };
    place(pos.x, pos.y);
    document.addEventListener("mousemove", (e) => place(e.clientX, e.clientY), true);
    document.addEventListener("mousedown", (e) => {
      cursor.classList.add("down");
      const ring = document.createElement("div");
      ring.className = "demo-ring";
      ring.style.left = `${e.clientX}px`;
      ring.style.top = `${e.clientY}px`;
      document.body.appendChild(ring);
      setTimeout(() => ring.remove(), 600);
    }, true);
    document.addEventListener("mouseup", () => cursor.classList.remove("down"), true);
    if (window.__demoCaption) window.__setCaption(...window.__demoCaption);
  };
  window.__setCaption = (text, sub) => {
    window.__demoCaption = [text, sub];
    const el = document.getElementById("demo-caption");
    if (!el) return;
    el.innerHTML = text ? `${text}${sub ? `<small>${sub}</small>` : ""}` : "";
    el.classList.toggle("on", !!text);
  };
  const W = window.innerWidth;
  const H = window.innerHeight;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
};

// ------------------------------------------------------------ 録画
// puppeteer の page.screencast() は再生速度が実時間とずれるため、CDP の screencast で
// フレームを自前で受け取り、受け取った時刻どおりの長さで並べて動画にする。
// 待ち時間（Gemini の応答や、裏で API を叩いている間）は録画を止めて詰める。
class Recorder {
  constructor(page, dir) {
    this.page = page;
    this.dir = dir;
    this.frames = []; // { file, t }（t は詰めたあとの秒）
    this.paused = false;
    this.cut = 0; // 詰めた秒数の合計
    this.pausedAt = 0;
  }

  async start() {
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(this.dir, { recursive: true });
    this.client = await this.page.createCDPSession();
    this.client.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
      this.client.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
      if (this.paused || this.stopped) return;
      const file = `${this.dir}/${String(this.frames.length).padStart(6, "0")}.jpg`;
      fs.writeFileSync(file, Buffer.from(data, "base64"));
      this.frames.push({ file, t: metadata.timestamp - this.cut });
    });
    await this.client.send("Page.startScreencast", { format: "jpeg", quality: 92, everyNthFrame: 1 });
    this.t0 = Date.now() / 1000;
  }

  pause() {
    if (this.paused) return;
    this.paused = true;
    this.pausedAt = Date.now() / 1000;
  }

  // 再開したら1回描き直させる。画面が止まったままだと、次のフレームが来ず古い絵が残るため
  async resume() {
    if (!this.paused) return;
    this.cut += Date.now() / 1000 - this.pausedAt;
    this.paused = false;
    await this.page.evaluate(() => {
      const c = document.getElementById("demo-cursor");
      if (c) c.style.opacity = c.style.opacity === "0.999" ? "1" : "0.999";
    });
    await sleep(120);
  }

  async stop(out) {
    await sleep(300);
    this.stopped = true;
    const end = Date.now() / 1000 - this.cut;
    await this.client.send("Page.stopScreencast");
    // 各フレームを「次のフレームが来るまで」の長さで並べる（ffmpeg の concat 形式）
    const lines = ["ffconcat version 1.0"];
    this.frames.forEach((f, i) => {
      const next = i + 1 < this.frames.length ? this.frames[i + 1].t : end;
      lines.push(`file '${f.file}'`, `duration ${Math.max(0.001, next - f.t).toFixed(4)}`);
    });
    lines.push(`file '${this.frames[this.frames.length - 1].file}'`);
    const list = `${this.dir}/frames.txt`;
    fs.writeFileSync(list, lines.join("\n"));
    execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list,
      "-vf", "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
      "-preset", "slow", "-crf", "20", "-movflags", "+faststart", out]);
    fs.rmSync(this.dir, { recursive: true, force: true });
    return end - this.frames[0].t;
  }
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_BIN || "/usr/bin/chromium-browser",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none", "--lang=ja-JP",
      `--window-size=${W},${H}`],
    env: { ...process.env, LANG: "ja_JP.UTF-8" },
  });
  const page = await browser.newPage();
  await page.setExtraHTTPHeaders({ "Accept-Language": "ja-JP,ja" });
  await page.setViewport({ width: W, height: H, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(OVERLAY);

  page.on("console", (m) => { if (m.type() === "error") console.log(`[画面] ${m.text()}`); });
  page.on("pageerror", (e) => console.log(`[画面] ${e.message}`));
  page.on("dialog", (d) => d.accept());

  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    if (PUBLIC.test(url)) req.continue({ url: url.replace(PUBLIC, BASE) });
    else req.continue();
  });

  // ------------------------------------------------------------ 操作の道具
  // カーソル位置は画面遷移をまたいで覚えておく（遷移のたびに真ん中へ飛ばないように）
  let cursor = { x: W / 2, y: H / 2 };
  let rec = null; // 録画中の Recorder（録画前の下準備では null）

  const caption = async (text, sub = "") => {
    await page.evaluate((t, s) => window.__setCaption(t, s), text, sub);
  };

  const settle = async (ms = 400) => {
    await page.waitForNetworkIdle({ idleTime: 300, timeout: 30000 }).catch(() => {});
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all([...document.images].filter((i) => i.src && !i.complete)
        .map((i) => new Promise((r) => { i.onload = i.onerror = r; })));
    });
    await sleep(ms);
  };

  // 要素が画面の中ほどに来るまで、ゆっくりスクロールする
  const scrollTo = async (selector, where = 0.35) => {
    await page.evaluate(async (sel, where) => {
      const el = document.querySelector(sel);
      if (!el) return;
      const top = el.getBoundingClientRect().top + scrollY - innerHeight * where;
      const target = Math.max(0, Math.min(top, document.documentElement.scrollHeight - innerHeight));
      const start = scrollY;
      const steps = Math.max(1, Math.round(Math.abs(target - start) / 30));
      for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        scrollTo(0, start + (target - start) * (1 - (1 - t) ** 3));
        await new Promise((r) => setTimeout(r, 16));
      }
    }, selector, where);
    await sleep(300);
  };

  const moveTo = async (selector) => {
    const el = await page.waitForSelector(selector, { visible: true, timeout: 20000 });
    const box = await el.boundingBox();
    if (box.y < 60 || box.y + box.height > H - 90) {
      await scrollTo(selector, 0.45);
    }
    const b = await el.boundingBox();
    const target = { x: b.x + Math.min(b.width / 2, 60), y: b.y + b.height / 2 };
    const steps = 25;
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      const e = 1 - (1 - t) ** 3;
      await page.mouse.move(cursor.x + (target.x - cursor.x) * e, cursor.y + (target.y - cursor.y) * e);
      await sleep(12);
    }
    cursor = target;
    await sleep(150);
  };

  // 押した直後の「作成しています…」などは少し見せ、その先の応答待ちは録画から詰める
  const click = async (selector, after = 600) => {
    await moveTo(selector);
    await page.mouse.down();
    await sleep(90);
    await page.mouse.up();
    await sleep(700);
    rec?.pause();
    await settle(0);
    await rec?.resume();
    await sleep(after);
  };

  const type = async (selector, text) => {
    await click(selector, 100);
    await page.evaluate((s) => { document.querySelector(s).value = ""; }, selector);
    await page.type(selector, text, { delay: 90 });
    await sleep(250);
  };

  const call = (method, path, body) =>
    page.evaluate(async (base, method, path, body) => {
      const res = await fetch(base + path, {
        method, headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await res.text();
      if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${text}`);
      return text ? JSON.parse(text) : null;
    }, BASE, method, path, body);

  const goto = async (path) => {
    rec?.pause();
    await page.goto(`${BASE}${path}`, { waitUntil: "networkidle0" });
    await page.mouse.move(cursor.x, cursor.y);
    await settle(0);
    await rec?.resume();
    await sleep(200);
  };

  // ------------------------------------------------------------ 録画前の下準備
  // 前回の続きから始まらないよう、誰として開くかの記憶を消しておく
  await goto("/app");
  await page.evaluate(() => localStorage.clear());
  await goto("/app");

  rec = new Recorder(page, FRAMES);
  await rec.start();
  await sleep(600);

  // ------------------------------------------------------------ 1. 幹事がルームを作る
  await caption("1. 幹事がルームを作る", "イベント名・開催日・ドレスコードを入れるだけ");
  await sleep(1400);
  await page.evaluate((d) => { document.getElementById("date").value = d; }, inDays(30));
  await type("#organizer", "さおり");
  await type("#dresscode", "白NG・ファーNG");
  await sleep(400);
  await click("#btn-create", 1200);
  await caption("招待URLができました", "これをグループのトークに貼って送ります");
  await moveTo("#btn-created-copy");
  await sleep(2400);

  const roomId = await page.evaluate(() => new URLSearchParams(location.search).get("room"));
  const inviteUrl = await page.$eval("#created-url", (el) => el.value.replace(/^http:\/\/localhost(:\d+)?/, ""));

  // ------------------------------------------------------------ 2. 幹事も試着して決める
  await caption("2. 候補を試着して、1着に決める", "衣装が決まるまでは「衣装を選ぶ」が左上に大きく出ます");
  await scrollTo("#sec-fit", 0.25);
  await click("#btn-tryon", 1200);
  await sleep(900);
  await click('#fits [data-fit="g_navy_satin"]', 1400);
  await caption("衣装が決まると、左上は集合プレビューに", "まだ合成に同意していないので、シルエットで並びます");
  await scrollTo("#sec-preview", 0.2);
  await sleep(2600);

  await caption("合成への同意は本人が決める", "同意すると、集合プレビューに顔が出ます。いつでも撤回できます");
  await click("#btn-consent", 1400);
  await scrollTo("#sec-preview", 0.2);
  await sleep(2400);

  // ------------------------------------------------------------ あかねさん（画面は撮らない）
  // 裏で API を叩いている間は録画を止める（画面は変わらないので、そのまま詰める）
  rec.pause();
  const akane = (await call("POST", `/api/rooms/${roomId}/members`, { display_name: "あかね" })).uid;
  await call("POST", `/api/rooms/${roomId}/members/${akane}/consent`, { granted: true });
  await call("POST", `/api/rooms/${roomId}/members/${akane}/tryon`, { garment_ids: ["g_sage", "g_terracotta", "g_dusty_blue"] });
  await call("POST", `/api/rooms/${roomId}/members/${akane}/select`, { garment_id: "g_sage" });
  await call("POST", `/api/rooms/${roomId}/members`, { display_name: "ゆい" }); // まだ何もしていない人（幹事の画面で未確定として出る）
  await rec.resume();

  // ------------------------------------------------------------ 3. みずきさんが招待URLから参加
  await caption("3. 招待URLを開いたメンバーは、名前を入れて参加", "インストールもログインも要りません");
  await page.evaluate((r) => localStorage.removeItem(`soroeru:${r}`), roomId);
  await goto(inviteUrl);
  await sleep(1600);
  await type("#new-member", "みずき");
  await click("#btn-join", 1400);

  await caption("試着して、1着に決める", "他の人がすでに決めた衣装は候補から外れます");
  await scrollTo("#sec-fit", 0.25);
  await click("#btn-tryon", 1200);
  await sleep(900);
  await click('#fits [data-fit="g_navy_lace"]', 1600);

  // ------------------------------------------------------------ 4. かぶりの指摘と代替案
  await caption("4. さおりさんと色がかぶると、その場で指摘", "色差 ΔE の数値つき。従うかどうかは本人が決めます");
  await page.evaluate(() => scrollTo({ top: 0, behavior: "instant" }));
  await sleep(500);
  await scrollTo("#sec-summary", 0.12);
  await sleep(2600);
  await moveTo("#sec-my-warn .warn");
  await sleep(2400);

  await caption("「代替案を見る」で、かぶらない衣装を試着", "他の人の色から離れていて、ドレスコードにも触れない衣装だけが出ます");
  await click("#btn-alt", 1400);
  await scrollTo("#sec-fit", 0.25);
  await sleep(1400);
  const alt = await page.$eval("#fits [data-fit]:not(.on)", (el) => el.dataset.fit);
  await click(`#fits [data-fit="${alt}"]`, 1600);

  await caption("選び直すと、かぶりの指摘が消えます", "集合プレビューもすぐに作り直されます");
  await page.evaluate(() => scrollTo({ top: 0, behavior: "instant" }));
  await settle(600);
  await sleep(1800);
  await scrollTo("#sec-preview", 0.2);
  await sleep(2000);

  await caption("みずきさんも合成に同意", "同意した人だけ、顔が集合プレビューに出ます");
  await click("#btn-consent", 1400);
  await scrollTo("#sec-preview", 0.2);
  await sleep(2200);

  // ------------------------------------------------------------ 5. 幹事は全体を見渡す
  await caption("5. 幹事の画面に切り替え", "誰がまだ決めていないか、同意していないかが一目でわかります");
  await page.evaluate(() => scrollTo({ top: 0, behavior: "instant" }));
  await click("#btn-switch", 900);
  await click('#people [data-pick]:first-child', 1400);
  await sleep(1800);
  await moveTo("#sec-roster");
  await sleep(2200);
  await caption("まだ決めていない人には、ここからリマインド", "リマインドはアプリ内のお知らせで届きます");
  await click("#btn-remind", 1200);
  await sleep(1800);

  await caption("ソロエル", "並んだときの見え方を、当日ではなく事前に。");
  await page.evaluate(() => scrollTo({ top: 0, behavior: "smooth" }));
  await sleep(3000);

  // 提出先で再生しやすいよう mp4（H.264）にする
  const seconds = await rec.stop(MP4);
  await browser.close();
  console.log(`録画: ${Math.round(seconds)}秒（待ち時間を詰めたあと）`);
  console.log(`出力: ${MP4.replace("/work/", "")}`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
