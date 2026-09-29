// Checks how a document is named in the context sidebar.
//
// The screenshot this answers showed a path -- "_decisions/chapters-to-add-
// universal-document.md" -- where a reader expects a name. A path is long, and it
// changes every time the document is filed, so it is the wrong thing to put
// there. This builds documents with a short title, a very long title, and no
// title at all, and asserts which name each one gets.
import { chromium } from "playwright";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, rmSync as _rm } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";

const URL = process.env.SMOKE_URL || "http://localhost:5273";
const REPO = process.env.SMOKE_DOC_REPO;

if (!REPO) {
  console.error("set SMOKE_DOC_REPO to a scratch git repository");
  process.exit(1);
}

const SHORT = "# Universal document\n\nA short title, which should win.\n";
const LONG =
  "# " +
  "A heading that runs on well past the point where anybody would still be " +
  "reading it in a narrow sidebar column without losing the thread\n\nBody.\n";
const NONE = "No heading at all, just a paragraph.\n";
// A heading that is a question. It ends in punctuation and is still a perfectly
// good name, so it is here to stop the rule below from being too eager.
const QUESTION = "# Why is the migration reversible?\n\nBody.\n";

const fail = (message) => {
  throw new Error(message);
};

const api = async (page, path, options = {}) =>
  page.evaluate(
    async ([p, o]) => {
      const res = await fetch(p, o);
      const text = await res.text();
      let body = null;
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
      return { status: res.status, body };
    },
    [path, options],
  );

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

try {
  mkdirSync(REPO, { recursive: true });
  writeFileSync(join(REPO, "universal.md"), SHORT, "utf8");
  writeFileSync(join(REPO, "long-title.md"), LONG, "utf8");
  writeFileSync(join(REPO, "no-heading.md"), NONE, "utf8");
  writeFileSync(join(REPO, "question.md"), QUESTION, "utf8");
  for (const args of [
    ["init", "-b", "main"],
    ["config", "user.name", "Someone"],
    ["config", "user.email", "someone@example.com"],
  ]) {
    execFileSync("git", ["-C", REPO, ...args], { stdio: "ignore" });
  }
  execFileSync("git", ["-C", REPO, "add", "-A"], { stdio: "ignore" });
  execFileSync("git", ["-C", REPO, "commit", "-q", "-m", "start"], {
    stdio: "ignore",
  });

  await page.goto(URL, { waitUntil: "networkidle" });

  const workspaceId = (await api(page, "/api/workspaces")).body[0].id;
  const repo = (
    await api(page, `/api/workspaces/${workspaceId}/repositories`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: `smoke-title-${Date.now().toString().slice(-6)}`,
        local_path: REPO,
        branch: "main",
        kind: "documentation",
        writable: true,
      }),
    })
  ).body;

  // The repository did not exist when the page first loaded, so what is on
  // screen is from before it did. Reload rather than click around a stale view:
  // a test that cannot get past this would be reporting the wrong thing.
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // "All objects" first: a fresh session opens on the Inbox, which shows the
  // documents that were dropped in rather than the repository's own files, so
  // the cards this test is looking for are not on screen yet.
  const allObjects = page.locator(".nav-item", { hasText: "All objects" }).first();
  if ((await allObjects.count()) === 0) {
    fail("there is no 'All objects' section to open");
  }
  await allObjects.click();
  await page.waitForSelector(".object-card", { timeout: 15000 });
  await page.waitForTimeout(600);

  for (const [file, expected] of [
    ["universal.md", "Universal document"],
    ["long-title.md", "long-title.md"],
    ["no-heading.md", "no-heading.md"],
    ["question.md", "Why is the migration reversible?"],
  ]) {
    const card = page.locator(".object-card", { hasText: file }).first();
    if ((await card.count()) === 0) {
      fail(`${file} is not in the folder view`);
    }
    await card.click();
    await page.waitForTimeout(1400);

    // The name is rendered on the Chat tab, which is not the default one -- the
    // panel opens on "related". Switching by clicking the real tab rather than
    // assuming a default, so a change there cannot quietly stop this.
    if ((await page.locator(".context-chat-empty-doc").count()) === 0) {
      const chatTab = page.locator(".context-tab-chip", { hasText: "Chat" }).first();
      if ((await chatTab.count()) === 0) {
        fail("there is no Chat tab to switch to");
      }
      await chatTab.click();
      await page.waitForTimeout(700);
    }

    const shown = (
      await page.locator(".context-chat-empty-doc").first().textContent()
    )?.trim();
    console.log(`${file} -> "${shown}"`);
    if (shown !== expected) {
      fail(
        `${file} is shown as "${shown}", expected "${expected}". A path here ` +
          `means the naming never ran; a wrong name means the rule is wrong.`,
      );
    }
  }
  await page.screenshot({ path: "screenshots/document-name.png" });

  if (errors.length) fail(`browser errors: ${errors.slice(0, 2).join(" | ")}`);
  console.log("document name: OK");
} catch (err) {
  console.error("\ndocument name FAILED:", err.message);
  await page
    .screenshot({ path: "screenshots/document-name-failed.png" })
    .catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
  rmSync(REPO, { recursive: true, force: true });
}
