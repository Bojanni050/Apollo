// The context panel must describe the document in the reading pane.
//
// It used to list every applied item in the workspace, so it looked identical no
// matter which file was open -- it could not answer "what does this file link
// to?". This checks that it changes with the document, that its links open, and
// that a link is a link rather than a dead card.
import { chromium } from "playwright";

const URL = process.env.SMOKE_URL || "http://localhost:5273";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

// The document's real links, as the application requested them. Recorded rather
// than re-fetched here: re-fetching would prove the endpoint works, not that the
// panel reads it, and the panel reading it is the part that was missing.
const linkResponses = [];
page.on("response", async (response) => {
  if (!response.url().includes("/document/links")) return;
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  linkResponses.push({ status: response.status(), body });
});

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);

const openContextTab = async () => {
  const chip = page.locator(".context-nav-tabs button", { hasText: /this document/i });
  if (await chip.count()) await chip.first().click();
  await page.waitForTimeout(400);
};

// --- With nothing open it must say so, not show a stale list ---------------
const nav = page.locator(".nav-item", { hasText: /Delphi Pulse/i }).first();
if (await nav.count()) {
  await nav.click();
  await page.waitForTimeout(1200);
}

// The Delphi Pulse view lists the newest run's items, and a scheduled scan can
// leave a newer run with nothing in it -- on a repository whose documents are
// not committed yet, or a workspace configured for it. That run is the correct
// thing for the application to show and the wrong thing for this check to click,
// so the newest run that actually has items is chosen here. The alternative,
// failing on an empty run, made this smoke fail for a reason that has nothing to
// do with what it is verifying.
const pulseItems = await page.evaluate(async () => {
  const ws = (await (await fetch("/api/workspaces")).json())[0];
  const runs = await (
    await fetch(`/api/workspaces/${ws.id}/pulse/runs?limit=10`)
  ).json();
  return (runs ?? [])
    .filter((r) => (r.items ?? []).length > 0)
    .sort((a, b) => b.id - a.id)
    .flatMap((r) => r.items.map((i) => i.file_path));
});
if (pulseItems.length === 0) {
  throw new Error(
    "no Pulse run in this workspace has any items, so the Delphi Pulse view is empty",
  );
}
console.log("documents the Pulse view can list:", pulseItems.length);

const card = page
  .locator(".object-card")
  .filter({ hasText: pulseItems[0] })
  .first();
if ((await card.count()) === 0) {
  // The view is showing a different run than the one the API reports newest
  // with items. Fall back to whatever is on screen rather than hanging.
  const fallback = page.locator(".object-card").first();
  if ((await fallback.count()) === 0) {
    throw new Error("the Delphi Pulse view lists no documents at all");
  }
  await fallback.click();
} else {
  await card.click();
}
await page.waitForTimeout(1500);
await openContextTab();

const firstPath = await page
  .locator(".context-current-doc .context-card-title")
  .first()
  .textContent()
  .catch(() => null);
console.log("panel shows document:", firstPath);
if (!firstPath) {
  throw new Error('the panel does not name the document in the reading pane ("THIS DOCUMENT" block missing)');
}

// It must be a real repository path. A Pulse card that falls through to the
// demo branch sets documentPath to its card id ("pulse-1"), which silently
// emptied this panel of links because there was no real path to match on.
if (!firstPath.includes("/") || !/\.mdx?$/.test(firstPath)) {
  throw new Error(
    `the panel shows "${firstPath}", which is not a document path; the card opened as a placeholder`,
  );
}

// Actions must be present and must say what they do.
const actions = await page.locator(".context-action").count();
console.log("actions offered:", actions);
if (actions < 1) {
  throw new Error("the panel offers no actions for the open document");
}
const hints = await page.locator(".context-action-hint").allTextContents();
if (hints.some((h) => !h || h.trim().length < 8)) {
  throw new Error("an action has no plain-language hint, so it is as opaque as an icon");
}

// Links, if any, must be clickable and must actually open the target.
const links = page.locator(".context-object-card--click");
const linkCount = await links.count();
const labels = await page.locator(".context-section-label").allTextContents();
console.log("linked articles:", linkCount, "sections:", labels.join(" | "));

// --- The links are the author's, not only Delphi Pulse's ---------------------
// The panel used to be built entirely from Pulse's inferred connections, so a
// link written by hand in the text was invisible. Both sources are now read,
// and each row says which is which -- so a proposal can never again be read as
// a fact about the document.
const origins = await page.locator('.context-link-origin').count();
const inferredRows = await page.locator('.context-link-card--inferred').count();
console.log('inferred rows:', inferredRows, 'labelled as inferred:', origins);
if (inferredRows !== origins) {
  throw new Error(
    `${inferredRows} Pulse-proposed link(s) but only ${origins} say so; a proposal is being shown as if the author wrote it`,
  );
}

// And the real links must actually be reaching the UI, not merely existing in
// the API. The response the app itself received is compared against what the
// panel is showing, so a fetch that fails silently is caught here instead of
// looking like "this document simply has no links".
const served = linkResponses.at(-1);
if (!served) {
  throw new Error('the panel never asked for the document links');
}
if (served.status !== 200) {
  throw new Error(`the document links request returned ${served.status}`);
}
if (!served.body) {
  throw new Error('the document links response could not be read as JSON');
}
const written = served.body.outbound.length + served.body.inbound.length;
console.log('links reported for the document on screen:', written);
// Whether the panel SHOWS those links is asserted further down, on a
// document known to have some. Asserting it here would pass vacuously on
// the document that happens to be open, which usually has none.

if (linkCount > 0) {
  // Every link row must say which way its edge runs, and no document may appear
  // twice: they used to be listed under two headings, which showed every
  // inbound link both as a link and again as a mention.
  const directions = await page.locator(".context-link-direction").count();
  if (directions !== linkCount) {
    throw new Error(
      `${linkCount} links but ${directions} direction labels; a row does not say which way it points`,
    );
  }
  const linkPaths = await page.locator(".context-link-path").allTextContents();
  const uniquePaths = new Set(linkPaths.map((p) => p.trim()));
  if (uniquePaths.size !== linkPaths.length) {
    throw new Error(
      `a document appears ${linkPaths.length - uniquePaths.size} time(s) too often in one section`,
    );
  }
  const allSections = await page.locator(".context-section-label").allTextContents();
  if (allSections.some((s) => /mentions this file/i.test(s))) {
    throw new Error(
      'inbound links are back in their own section, duplicating the "Linked articles" rows',
    );
  }
  const target = (await links.first().locator(".context-link-path").textContent()).trim();
  await links.first().click();
  await page.waitForTimeout(1500);
  const nowOpen = await page
    .locator(".context-current-doc .context-card-title")
    .first()
    .textContent()
    .catch(() => null);
  console.log("followed link to:", nowOpen, "(target was", target, ")");
  if (nowOpen !== target) {
    throw new Error(
      `clicking a link did not open it: panel still shows "${nowOpen}", expected "${target}"`,
    );
  }
  // And the panel must now describe the NEW document, not the old one.
  if (nowOpen === firstPath) {
    throw new Error("the panel did not follow the reading pane to the new document");
  }
} else {
  console.log("note: this document has no links, so navigation was not exercised");
}

// --- The author's own links are actually found -------------------------------
// The document opened above happens to have none, which would let every link
// assertion pass vacuously. So the repository is walked for a document that
// DOES have hand-written links: the old panel would have shown nothing for it,
// because it only ever read Delphi Pulse's inferred data.
//
// The documents come from the tree endpoint rather than a hard-coded list of
// paths. A fixed list only proves anything while it happens to match the
// repository someone is running against; walking the tree does, and it also
// means the check does not have to be edited when the docs move.
// The workspace carries its repositories, so one request gives the workspace
// id, the repository id and the repository kind. There is no GET on
// /repositories to ask for that separately.
const found = await page.evaluate(async () => {
  const workspaces = await (await fetch('/api/workspaces')).json();
  const repos = workspaces[0]?.repositories ?? [];
  return {
    workspaceId: workspaces[0]?.id ?? null,
    repo: repos.find((r) => r.kind === 'documentation') ?? repos[0] ?? null,
  };
});
const workspaceId = found.workspaceId;
const docRepo = found.repo;
if (!workspaceId) {
  throw new Error('no workspace is configured, so there is no repository to check');
}
if (!docRepo) {
  throw new Error('the workspace has no repository, so there is nothing to read links from');
}

const linked = await page.evaluate(
  async ([ws, repoId]) => {
    const tree = await (
      await fetch(`/api/workspaces/${ws}/repositories/${repoId}/tree`)
    ).json();

    /* Depth-first over the tree, collecting the Markdown documents. The tree
       nests children, so this is iterative: a recursive closure in page.evaluate
       would have to be redefined per call, and a deep documentation tree is
       exactly where that gets awkward. */
    const paths = [];
    const stack = [tree.root];
    while (stack.length && paths.length < 60) {
      const node = stack.pop();
      if (!node) continue;
      if (!node.is_dir && /\.(md|markdown|mdx|txt)$/i.test(node.path)) {
        paths.push(node.path);
      }
      for (const child of node.children ?? []) stack.push(child);
    }

    /* Collect a handful of documents with links, then stop. One is not
       enough: the document opened below must be one the panel can also
       open, and a single candidate leaves that intersection empty.
       Capped because each request runs the inbound corpus scan. */
    const found = [];
    for (const path of paths) {
      if (found.length >= 6) break;
      const res = await fetch(
        `/api/workspaces/${ws}/repositories/${repoId}/document/links?path=${encodeURIComponent(path)}`,
      ).catch(() => null);
      if (!res || res.status !== 200) continue;
      const body = await res.json();
      if (body.outbound.length + body.inbound.length > 0) {
        found.push({
          path,
          outbound: body.outbound.length,
          inbound: body.inbound.length,
          external: body.external.length,
        });
      }
    }
    return found;
  },
  [workspaceId, docRepo.id],
);

console.log('first document with written links:', JSON.stringify(linked));
if (linked.length === 0) {
  throw new Error(
    'no document in the repository reports a single link, so the link graph is not being read at all',
  );
}
// --- Open one of those documents for real, and read its rows ----------------
// The check above proves the API knows the links. It does not prove the panel
// shows them, which is the part that was broken: the panel used to render only
// Delphi Pulse's inferred data, so a document with three hand-written links
// reported none. So one is opened here and its rows are read off the screen.
//
// The candidate is the intersection of two sets: documents the API reports
// links for, and documents the Delphi Pulse panel can open. The panel's
// documents are its run's items, rendered as .object-card in the contents
// column -- a document outside the run has no card here, and there is no
// document search on this view, so opening one would need the workspace panel
// instead. Taking the intersection is what makes the check reachable rather
// than theoretical.
const runItems = await page.evaluate(
  async (ws) => {
    const res = await fetch(`/api/workspaces/${ws}/pulse/runs?limit=1`).catch(() => null);
    if (!res || !res.ok) return [];
    const runs = (await res.json()) ?? [];
    // The newest run *with items*, matching what the application now shows.
    // An empty run lists no documents, so a document that is in an older run
    // would look unreachable here while it is on screen.
    const run = [...runs]
      .filter((r) => (r.items ?? []).length > 0)
      .sort((a, b) => b.id - a.id)[0];
    return (run?.items ?? []).map((i) => i.file_path);
  },
  workspaceId,
);
console.log(`documents the panel can open: ${runItems.length}`);

const openable = new Set(runItems);
const linkedDoc = linked.find((d) => openable.has(d.path)) ?? null;
if (!linkedDoc) {
  throw new Error(
    `no document that has written links is openable from this panel, so the link rows cannot be read on screen. The linked documents are: ${linked
      .map((d) => d.path)
      .join(", ")}`,
  );
}
console.log("opening a document that has written links:", linkedDoc.path);

const docCard = page
  .locator('.folder-contents-column .object-card')
  .filter({ hasText: linkedDoc.path })
  .first();
if (!(await docCard.count())) {
  throw new Error(
    `the panel was supposed to list ${linkedDoc.path} but no matching card is on screen`,
  );
}
await docCard.scrollIntoViewIfNeeded();
await docCard.click();
await page.waitForTimeout(1800);
await openContextTab();

const openedPath = (
  await page
    .locator(".context-current-doc .context-card-title")
    .first()
    .textContent()
    .catch(() => null)
)?.trim();
if (openedPath !== linkedDoc.path) {
  throw new Error(
    `opened ${JSON.stringify(openedPath)} instead of ${linkedDoc.path}, so the rows below would be about the wrong document`,
  );
}

// At least one row, and it must be marked as written by the author. A row with
// no origin marker is what the old panel produced, and it read as though the
// author had linked it when the model had only inferred it.
const writtenRows = page.locator(".context-link-card--written");
const writtenCount = await writtenRows.count();
console.log("written rows shown for", linkedDoc.path + ":", writtenCount);
if (writtenCount === 0) {
  throw new Error(
    `${linkedDoc.path} reports ${linkedDoc.outbound} outbound and ${linkedDoc.inbound} inbound links, but the panel shows no row marked as written by the author`,
  );
}

// And following one of those rows must actually move the reading pane. A
// written link that renders but does not navigate is the same failure the
// inferred rows had.
const linkPath = (await writtenRows.first().locator(".context-link-path").textContent()).trim();
await writtenRows.first().click();
await page.waitForTimeout(1500);
const followed = (
  await page
    .locator(".context-current-doc .context-card-title")
    .first()
    .textContent()
    .catch(() => null)
)?.trim();
console.log("followed a written link to:", followed, "(row said", linkPath, ")");
if (followed !== linkPath) {
  throw new Error(
    `clicking a written link did not open it: the panel shows ${JSON.stringify(followed)}, the row said ${JSON.stringify(linkPath)}`,
  );
}

// --- Links out of the repository are shown, and are not fake buttons -------
// These were returned by the API all along and dropped by the panel, so a
// document whose only links were external claimed to link to nothing. The
// treatment is deliberately not a link: nothing in Apollo can open an external
// URL, and a row that looks pressable and is not is the same overstatement the
// written-versus-inferred distinction was introduced to remove.
//
// A document with external links is located the same way the linked document
// above was, so this does not depend on any particular file existing.
const externalDoc = await page.evaluate(
  async ([ws, repoId, runPaths]) => {
    for (const path of runPaths) {
      const res = await fetch(
        `/api/workspaces/${ws}/repositories/${repoId}/document/links?path=${encodeURIComponent(path)}`,
      ).catch(() => null);
      if (!res || res.status !== 200) continue;
      const body = await res.json();
      if (body.external.length > 0) return { path, count: body.external.length };
    }
    return null;
  },
  [workspaceId, docRepo.id, runItems],
);

if (externalDoc) {
  console.log(`opening ${externalDoc.path}, which has ${externalDoc.count} external links`);
  const extCard = page
    .locator('.folder-contents-column .object-card')
    .filter({ hasText: externalDoc.path })
    .first();
  await extCard.scrollIntoViewIfNeeded();
  await extCard.click();
  await page.waitForTimeout(1800);
  await openContextTab();

  const rows = page.locator('.context-external-link');
  const shown = await rows.count();
  console.log("external rows shown:", shown, "reported:", externalDoc.count);
  if (shown === 0) {
    throw new Error(
      `${externalDoc.path} links outside the repository, but the panel shows no such rows`,
    );
  }

  // Each row must name where it goes and say that Apollo cannot open it.
  const targets = await page.locator('.context-external-target').allTextContents();
  if (targets.length !== shown) {
    throw new Error(`${shown} external rows but ${targets.length} target URLs`);
  }
  const origins = await page.locator('.context-external-link .context-link-origin').allTextContents();
  if (origins.some((o) => !/outside this repository/i.test(o))) {
    throw new Error(`an external row does not say it leaves the repository: ${JSON.stringify(origins)}`);
  }

  // Not a button, and not carrying the press affordance the openable rows have.
  const asButtons = await page.locator('button.context-external-link').count();
  if (asButtons !== 0) {
    throw new Error(
      'external links are rendered as buttons; they look pressable but nothing can open them',
    );
  }
} else {
  console.log('note: no openable document has external links, so that check was not exercised');
}

await page.screenshot({ path: "screenshots/context-document.png" });

if (errors.length) {
  throw new Error(`browser errors: ${errors.slice(0, 3).join(" | ")}`);
}
console.log("OK: the context panel describes the open document and its links");
await browser.close();
