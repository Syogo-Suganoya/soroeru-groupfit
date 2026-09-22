/**
 * web/favicon.svg から PNG のアイコン一式を書き出す。
 *
 *   docker compose --profile shots run --rm shots docs/icons.js
 *
 * 形の正は SVG だけに置き、PNG はここで作り直す。手で描き分けると形がずれていく。
 */

const fs = require("fs");
const puppeteer = require("puppeteer");

const SRC = "/work/web/favicon.svg";
const OUT = "/work/web";

// 用途ごとの大きさ。apple-touch とマニフェストは角丸を端末側が付けるので余白ごと塗る。
const TARGETS = [
  { file: "favicon-32.png", size: 32 },
  { file: "apple-touch-icon.png", size: 180 },
  { file: "icon-192.png", size: 192 },
  { file: "icon-512.png", size: 512 },
];

async function main() {
  const svg = fs.readFileSync(SRC, "utf8");
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_BIN || "/usr/bin/chromium-browser",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();

  for (const { file, size } of TARGETS) {
    await page.setViewport({ width: size, height: size, deviceScaleFactor: 1 });
    await page.setContent(
      `<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`,
    );
    await page.screenshot({ path: `${OUT}/${file}`, omitBackground: true });
    console.log(`書き出し: ${file}（${size}px）`);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
