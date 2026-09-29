import { chromium } from "playwright";

const URL = process.env.SMOKE_URL || "http://localhost:5274";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);

const all = page.locator(".object-card").allTextContents();
console.log("object-cards:", JSON.stringify(all));
console.log("nav-items:", JSON.stringify(await page.locator(".nav-item").allTextContents()));
console.log("kolommen:", await page.locator(".mindstack-layout > *").count());
const heading = await page.locator("h2, .setup h2").first().textContent().catch(() => null);
console.log("h2:", JSON.stringify(heading));
await page.screenshot({ path: "screenshots/_debug.png", fullPage: true });
await browser.close();
