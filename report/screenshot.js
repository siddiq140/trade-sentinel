// Render the report page to PNG images for GitHub: node report/screenshot.js <report.html> <out_dir>
// Uses Playwright's Chromium (preinstalled in Claude cloud sessions; locally: npm i playwright && npx playwright install chromium).
const fs = require("fs"), path = require("path");
const { chromium } = require("playwright");
(async () => {
  const [src, outDir] = process.argv.slice(2);
  if (!src || !outDir) { console.error("usage: node screenshot.js <report.html> <out_dir>"); process.exit(2); }
  fs.mkdirSync(outDir, { recursive: true });
  const html = fs.readFileSync(src, "utf8");
  const doc = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{margin:0}</style></head><body>' + html + "</body></html>";
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1100, height: 1000 }, colorScheme: "light", deviceScaleFactor: 2 });
  await page.setContent(doc, { waitUntil: "load" });
  await page.waitForTimeout(1200); // web fonts
  const shots = [["summary.png", ["header", "#s-funnel"]], ["cards.png", ["#s-cards"]], ["plans.png", ["#s-plans"]], ["scores.png", ["#s-scores"]]];
  for (const [name, [first, last]] of shots.map(([n, s]) => [n, [s[0], s[s.length - 1]]])) {
    const a = await page.$(first), b = await page.$(last);
    if (!a || !b) continue;
    const ba = await a.boundingBox(), bb = await b.boundingBox();
    const clip = { x: 0, y: Math.max(0, ba.y - 16), width: 1100, height: bb.y + bb.height - ba.y + 32 };
    await page.screenshot({ path: path.join(outDir, name), clip, fullPage: true });
    console.log("  " + path.join(outDir, name));
  }
  const full = await browser.newPage({ viewport: { width: 1100, height: 1000 }, colorScheme: "light", deviceScaleFactor: 1 });
  await full.setContent(doc, { waitUntil: "load" });
  await full.waitForTimeout(1200);
  await full.screenshot({ path: path.join(outDir, "full.png"), fullPage: true });
  console.log("  " + path.join(outDir, "full.png"));
  await browser.close();
})().catch(e => { console.error(e.message); process.exit(1); });
