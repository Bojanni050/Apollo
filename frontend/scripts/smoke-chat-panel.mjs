// The AI Chat tab must be usable in a narrow column, and honest about its modes.
//
// Two real defects are locked down here. The chips were labelled "explore /
// investigate / apply" with no explanation, and NO mode writes anything -- the
// backend prompt for apply says so outright -- so the label promised the one
// thing the system never does. And the empty state was a sparkle floating in
// ~550px above an input, which is what made the panel read as cramped.
import { chromium } from "playwright";

const URL = process.env.SMOKE_URL || "http://localhost:5273";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);

const nav = page.locator(".nav-item", { hasText: /Delphi Pulse/i }).first();
if (await nav.count()) {
  await nav.click();
  await page.waitForTimeout(1200);
}
const card = page.locator(".object-card").first();
await card.click();
await page.waitForTimeout(1500);

// The path is read HERE, on the "this document" tab, because that block is
// only rendered there -- the chat tab replaces it. Capturing it after the
// switch below would read nothing and pass a null comparison.
const openTitle = await page
  .locator(".context-current-doc .context-card-title")
  .first()
  .textContent()
  .catch(() => null);
if (!openTitle) {
  throw new Error("no document is open, so there is nothing to ask about");
}
console.log("asking about document:", openTitle);

await page.locator(".context-nav-tabs button", { hasText: /AI Chat/i }).first().click();
await page.waitForTimeout(600);

// --- 1. The input must be reachable, not parked below a void ---------------
const input = page.locator(".context-chat-input");
const ib = await input.boundingBox();
const panel = await page.locator(".context-sidebar").boundingBox();
console.log(`panel ${panel.width}x${panel.height}, input at y=${ib.y}`);
if (ib.y + ib.height > panel.height) {
  throw new Error("the message input sits below the panel; you cannot reach it");
}

// --- 2. The modes must not claim to write ----------------------------------
const chipLabels = await page.locator(".context-mode-chip").allTextContents();
console.log("modes:", chipLabels.map((c) => c.trim()).join(" / "));
if (chipLabels.some((c) => /^\s*apply\s*$/i.test(c))) {
  throw new Error(
    'a mode is labelled "apply", but no mode applies anything -- the label promises a write that never happens',
  );
}
if (chipLabels.length !== 3) {
  throw new Error(`expected three modes, found ${chipLabels.length}`);
}

// Each mode must state its behaviour, and every blurb must deny writing: the
// whole point is that the reader learns this without sending a message.
for (const label of chipLabels) {
  await page.locator(".context-mode-chip", { hasText: new RegExp(label.trim(), "i") }).first().click();
  await page.waitForTimeout(500);
  const blurb = (await page.locator(".context-mode-blurb").textContent())?.trim() ?? "";
  console.log(`  ${label.trim()}: ${blurb}`);
  if (blurb.length < 20) {
    throw new Error(`mode "${label.trim()}" has no explanation of what it does`);
  }
  if (!/nothing|never|does not|not apply/i.test(blurb)) {
    throw new Error(
      `mode "${label.trim()}" does not say that it writes nothing: "${blurb}"`,
    );
  }
}

// --- 3. The chips must be hit targets, not 10px decoration ------------------
const chipBox = await page.locator(".context-mode-chip").first().boundingBox();
if (chipBox.height < 28) {
  throw new Error(`mode chips are ${chipBox.height}px tall, too small to click confidently`);
}

// --- 4. A suggestion fills the box and does NOT send -----------------------
//
// Conditional, because the suggestions only exist in the empty chat state. Once
// the panel has a conversation it shows the transcript instead, and a check
// that waited for a suggestion would hang forever on a workspace that has been
// used -- a failure about the state of the data, not about the suggestion.
const suggestions = page.locator(".context-suggestion");
if (await suggestions.count()) {
  const msgsBefore = await page.locator(".context-msg").count();
  await suggestions.first().click();
  await page.waitForTimeout(400);
  const filled = await input.inputValue();
  console.log("suggestion filled the box:", JSON.stringify(filled.slice(0, 48)));
  if (!filled.trim()) {
    throw new Error("clicking a suggestion did not put anything in the input");
  }
  if ((await page.locator(".context-msg").count()) !== msgsBefore) {
    throw new Error("clicking a suggestion sent a message without asking");
  }
  // The text is cleared again below, so the send check starts from an empty box
  // rather than from whatever the suggestion put there.
  await input.fill("");
} else {
  console.log("note: this chat already has messages, so there are no empty-state suggestions");
}

// --- 5. Sending must carry the open document, not just the question ------
//
// "Ask about this document" opens the chat and does nothing else unless the
// path travels with the message. The API accepts a message without it, so
// nothing about the UI fails visibly when it stops being sent -- the chat keeps
// working, it just quietly answers "this document" about a guess. So the
// request body is inspected, not the rendered result.
const chatRequests = [];
page.on("request", (request) => {
  // `URL` is shadowed by the SMOKE_URL constant at the top of this file, so the
  // global constructor is reached under a different name here.
  const { pathname } = new globalThis.URL(request.url());
  if (!/conversations\/\d+\/messages$/.test(pathname)) return;
  if (request.method() !== "POST") return;
  try {
    chatRequests.push(JSON.parse(request.postData() || "{}"));
  } catch {
    chatRequests.push(null);
  }
});

// `openTitle` was captured before the tab switch above, because the block that
// names the document is only rendered on the "this document" tab.
await input.fill("what does this document assume?");
// The send button, not Enter: it is a form, so Enter would work too, but the
// button is what a reader clicks, and it is disabled until the box has text --
// so clicking it also proves the control was actually enabled.
await page.locator(".context-chat-send-btn").click();
// The LLM is not necessarily configured in a smoke environment; the request
// being made correctly is the thing under test, not the answer coming back.
await page.waitForTimeout(2500);

if (chatRequests.length === 0) {
  throw new Error("no chat request was made, so nothing was verified about its body");
}
const last = chatRequests[chatRequests.length - 1];
if (!last) {
  throw new Error("the chat request body was not readable JSON");
}
console.log("chat request body:", JSON.stringify(last));
if (!("document_path" in last)) {
  throw new Error(
    "the chat request has no document_path field, so the model is never told which file 'this' is",
  );
}
if (last.document_path !== openTitle) {
  throw new Error(
    `the chat request names document_path ${JSON.stringify(last.document_path)}, but the open document is ${JSON.stringify(openTitle)}`,
  );
}
if (typeof last.content !== "string" || !last.content.trim()) {
  throw new Error("the chat request carries no message text");
}

await page.screenshot({ path: "screenshots/chat-panel.png" });
if (errors.length) {
  throw new Error(`browser errors: ${errors.slice(0, 3).join(" | ")}`);
}
console.log("OK: the chat panel fits its column and its modes state what they do");
await browser.close();
