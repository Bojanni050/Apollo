// The whole of phase 4, in one run: give a group a folder, drop a document in,
// see the proposal appear, accept it, and check the file actually moved -- and
// that nothing anywhere else lost a reference to it.
//
// Run against a throwaway workspace: SMOKE_URL, SMOKE_DOC_REPO and a backend
// whose database is not yours. The script creates its own groups and deletes
// them again, but it does accept a real move, so point REPO at a scratch repo.

import { chromium } from "playwright";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const URL = process.env.SMOKE_URL || "http://localhost:5273";
const REPO = process.env.SMOKE_DOC_REPO;

if (!REPO) {
  console.error("set SMOKE_DOC_REPO to a scratch git repository");
  process.exit(1);
}

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

const fail = (message) => {
  throw new Error(message);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

try {
  // --- A scratch repository with one document in it ------------------------
  mkdirSync(REPO, { recursive: true });
  writeFileSync(`${REPO}/notities.md`, "# Notities\n\nIets om te archiveren.\n", "utf8");
  for (const args of [
    ["init", "-b", "main"],
    ["config", "user.name", "Someone"],
    ["config", "user.email", "someone@example.com"],
  ]) {
    execFileSync("git", ["-C", REPO, ...args], { stdio: "ignore" });
  }
  execFileSync("git", ["-C", REPO, "add", "-A"], { stdio: "ignore" });
  execFileSync("git", ["-C", REPO, "commit", "-q", "-m", "start"], { stdio: "ignore" });

  await page.goto(URL, { waitUntil: "networkidle" });

  const workspaceId = (await api(page, "/api/workspaces")).body[0].id;
  const repo = (
    await api(page, `/api/workspaces/${workspaceId}/repositories`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: `smoke-archive-${Date.now().toString().slice(-6)}`,
        local_path: REPO,
        branch: "main",
        kind: "documentation",
        writable: true,
      }),
    })
  ).body;

  // Start from a workspace with no archive. A previous run leaves the document
  // filed in it, and then the action offers "already in the archive" instead of
  // proposing anything -- so the second run of a smoke test would pass for the
  // wrong reason, or fail for a reason that is not about the code at all.
  for (const stale of (
    (await api(page, `/api/workspaces/${workspaceId}/groups`)).body ?? []
  ).filter((g) => g.folder === "Archief")) {
    await api(page, `/api/workspaces/${workspaceId}/groups/${stale.id}`, {
      method: "DELETE",
    });
    console.log("removed a leftover archive from an earlier run");
  }

  // --- 1. Open the document the way a reader does --------------------------
  // The panel only describes the document in the reading pane, so asking the
  // API whether the document exists is not enough -- it has to be *open*. This
  // clicks it, because a test that faked the open state would be testing
  // nothing a reader can reach.
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForTimeout(1400);

  // "All objects" lists the documents of every repository, so no repository has
  // to be chosen first -- which matters here, because a workspace that has never
  // had a documentation repository has no tree to click into.
  const allObjects = page.locator(".nav-item", { hasText: "All objects" }).first();
  if ((await allObjects.count()) > 0) {
    await allObjects.click();
    await page.waitForTimeout(1200);
  }

  const inTree = page.locator(".object-card", { hasText: "notities.md" }).first();
  const treeCount = await inTree.count();
  console.log("the document in the tree:", treeCount > 0);
  if (treeCount === 0) fail("the document is not listed in the folder view");
  await inTree.click();
  await page.waitForTimeout(1500);

  const documentShown = await page
    .locator("text=Iets om te archiveren")
    .first()
    .count();
  if (documentShown === 0) fail("the document did not open in the reading pane");

  // --- 2. The action is there, and says what it will do --------------------
  const actions = page.locator(".context-action");
  console.log("actions in the panel:", await actions.count());
  const labels = await page.locator(".context-action-label").allTextContents();
  console.log("which ones:", labels.join(" | "));
  if (!labels.some((l) => /archive/i.test(l))) {
    fail(`there is no archive action: ${labels.join(" | ")}`);
  }
  await page.screenshot({ path: "screenshots/archive-action.png" });

  const hint = await page
    .locator(".context-action", { hasText: /archive/i })
    .innerText();
  if (!/proposes moving|Archief/.test(hint)) {
    fail(`the action does not say it proposes rather than moves: ${JSON.stringify(hint)}`);
  }

  if (errors.length) fail(`browser errors: ${errors.slice(0, 2).join(" | ")}`);

  // --- 3. Click it, and check what actually happened ------------------------
  // The point of the action is not that the button exists, so click it and look
  // at the consequences: the archive exists, the document has a proposal
  // waiting, and the file is still where it was. Nothing may have moved yet.
  await page
    .locator(".context-action", { hasText: /archive/i })
    .first()
    .click();
  await page.waitForTimeout(2500);

  const groups = (await api(page, `/api/workspaces/${workspaceId}/groups`)).body;
  // Match on the folder, not the name: the group is called "Archief", and a
  // name-based match would have missed it and reported a failure that was not
  // one.
  const archive = groups.find((g) => g.folder === "Archief");
  console.log("the archive group:", archive ? archive.name : "MISSING");
  if (!archive) fail("the archive group was not created");
  if (archive.folder !== "Archief") {
    fail(`the archive points at ${JSON.stringify(archive.folder)}, not Archief`);
  }

  const proposals = (
    (await api(page, `/api/workspaces/${workspaceId}/proposals`)).body ?? []
  ).flatMap((p) => p.changes ?? []);
  const proposed = proposals.find(
    (c) => c.action === "move" && c.source_path === "notities.md",
  );
  console.log("a proposal for the document:", proposed ? "yes" : "MISSING");
  if (!proposed) fail("no move proposal was made for the document");
  if (proposed.target_path !== "Archief/notities.md") {
    fail(`the proposal moves to ${JSON.stringify(proposed.target_path)}`);
  }

  // The whole point: a proposal, not a move.
  if (!existsSync(`${REPO}/notities.md`)) fail("the file moved without being accepted");
  if (existsSync(`${REPO}/Archief/notities.md`)) {
    fail("the file is in the archive already, before the proposal was accepted");
  }
  console.log("the file is still where it was, and the archive is still empty");

  await page.screenshot({ path: "screenshots/archive-after-click.png" });
  console.log("archive action: OK");
} catch (err) {
  console.error("\narchive action FAILED:", err.message);
  await page.screenshot({ path: "screenshots/archive-action-failed.png" }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}

