// The groups board: dragging must rearrange the view and touch no file.
//
// Drag-and-drop is the one interaction in the product a reader will use
// constantly and without thinking, so the property under test is not "a drag
// happened" but "a drag changed only the arrangement". The witness is the
// repository's file listing, read before and after, because a move that quietly
// relocated a file would look like success right up until the reader noticed.
//
// Playwright cannot synthesise a real HTML5 drag reliably, so the drag is
// performed through the DataTransfer the component actually reads. The
// application code path is the same one a user's gesture takes; only the
// gesture itself is replaced.
import { chromium } from "playwright";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const URL = process.env.SMOKE_URL || "http://localhost:5273";
const REPO = process.env.SMOKE_DOC_REPO;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

/* Every file in the documentation repository, by path and size. The witness of
   "the drag moved nothing". Read through the filesystem rather than the API, so
   a move is caught even if the app were to report success. */
function snapshot(root) {
  if (!root) return null;
  const out = {};
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === ".git" || entry.name === "node_modules") continue;
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else {
        out[full.slice(root.length).replace(/\\/g, "/")] = statSync(full).size;
      }
    }
  };
  walk(root);
  return out;
}

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);

// --- 1. The board must be reachable, and it must explain itself ------------
// The one idea a reader cannot guess is that a group is a view and not a
// folder. If the board does not say so, dragging looks like it should be
// rearranging files, and the reader will hesitate to use it.
// The tab can carry a count badge alongside its label, so the label is
// matched on its own text rather than the whole button.
const groupsTab = page.locator(".mindstack-tab", { hasText: "Groups" }).first();
if (!(await groupsTab.count())) {
  throw new Error("there is no Groups tab above the middle column");
}
await groupsTab.click();
await page.waitForTimeout(1200);

const hint = await page.locator(".groups-board-hint").textContent().catch(() => null);
console.log("board hint:", JSON.stringify(hint));
// The hint now has to mention both kinds of group, because that is the
// distinction the reader has to be able to see before they drag anything.
if (!hint || !/folder/i.test(hint) || !/ask you first/i.test(hint)) {
  throw new Error(
    `the board does not say that a group with a folder asks before moving a file: ${JSON.stringify(hint)}`,
  );
}

// --- 2. Two groups, so there is somewhere to drag to -----------------------
const made = await page.evaluate(async () => {
  const ws = (await (await fetch("/api/workspaces")).json())[0];
  const suffix = String(Date.now()).slice(-6);
  const names = [`Smoke A ${suffix}`, `Smoke B ${suffix}`];
  const created = [];
  for (const name of names) {
    const res = await fetch(`/api/workspaces/${ws.id}/groups`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    created.push({ name, status: res.status, body: await res.json() });
  }
  return { workspaceId: ws.id, created };
});
console.log("groups created:", JSON.stringify(made));
if (made.created.some((c) => c.status !== 201)) {
  throw new Error(
    `could not create the two groups the drag needs (workspace ${made.workspaceId}): ${JSON.stringify(made.created)}`,
  );
}

await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await page.locator(".mindstack-tab", { hasText: "Groups" }).first().click();
await page.waitForTimeout(1500);

// --- 3. A document in group A, and nothing in group B ----------------------
const firstName = made.created[0].name;
const secondName = made.created[1].name;

// Take a document the repository actually has, from the tree endpoint.
const seeded = await page.evaluate(
  async ([wsId, aName, bName]) => {
    const groups = await (await fetch(`/api/workspaces/${wsId}/groups`)).json();
    // Exact names, not `includes`. Earlier smoke runs leave groups behind on
    // failure, and a substring search then picks up one of those instead of the
    // pair this run just created -- which reads as "the seed silently did
    // nothing".
    const target = groups.find((g) => g.name === aName);
    const other = groups.find((g) => g.name === bName);
    if (!target || !other) return null;

    const ws = (await (await fetch("/api/workspaces")).json())[0];
    const repo = ws.repositories.find((r) => r.kind === "documentation") ?? ws.repositories[0];
    const tree = await (
      await fetch(`/api/workspaces/${wsId}/repositories/${repo.id}/tree`)
    ).json();
    const stack = [tree.root];
    while (stack.length) {
      const node = stack.pop();
      if (!node) continue;
      if (!node.is_dir && /\.mdx?$/i.test(node.path)) {
        const put = await fetch(
          `/api/workspaces/${wsId}/groups/${target.id}/documents`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repository_id: repo.id, path: node.path }),
          },
        );
        return {
          repositoryId: repo.id,
          path: node.path,
          status: put.status,
          groupA: target.id,
          groupB: other.id,
        };
      }
      for (const child of node.children ?? []) stack.push(child);
    }
    return null;
  },
  [made.workspaceId, firstName, secondName],
);
if (!seeded || seeded.status !== 201) {
  throw new Error("could not put a document into the first group to drag it");
}
console.log("dragging document:", seeded.path);

await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await page.locator(".mindstack-tab", { hasText: "Groups" }).first().click();
await page.waitForTimeout(1800);

const before = snapshot(REPO);
if (before) console.log("files in the repository before the drag:", Object.keys(before).length);

// --- 4. The drag, through the same DataTransfer the component reads ---------
const dragged = await page.evaluate(
  async ([fromName, toName, payload]) => {
    // Matched on the group's own heading rather than the whole card's text: the
    // card also contains every member's name and path, so a substring search
    // over the card can land on a document whose path happens to contain the
    // group's name.
    const headingOf = (card) =>
      (card.querySelector(".group-card-name")?.textContent ?? "").trim();
    const card = [...document.querySelectorAll(".group-card")].find(
      (c) => headingOf(c) === fromName,
    );
    const target = [...document.querySelectorAll(".group-card")].find(
      (c) => headingOf(c) === toName,
    );
    if (!card || !target) {
      return {
        ok: false,
        why: "a group card is missing",
        headings: [...document.querySelectorAll(".group-card-name")].map((n) =>
          n.textContent.trim(),
        ),
      };
    }

    const button = card.querySelector(".group-document");
    if (!button) return { ok: false, why: "the document card is missing" };

    // A real DataTransfer, not a stand-in object. The browser constructs the
    // DragEvent's dataTransfer from this and rejects anything it cannot convert
    // to a real DataTransfer, so an object literal never survives to the handler.
    const dataTransfer = new DataTransfer();

    button.dispatchEvent(
      new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer }),
    );
    target.dispatchEvent(
      new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer }),
    );
    target.dispatchEvent(
      new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }),
    );
    return { ok: true, effect: dataTransfer.dropEffect, payload };
  },
  [firstName, secondName, { repositoryId: seeded.repositoryId, path: seeded.path }],
);
if (!dragged.ok) throw new Error(`the drag could not be performed: ${dragged.why}`);

await page.waitForTimeout(2000);

// --- 5. The document moved between groups, and no file did -----------------
const membership = await page.evaluate(
  async ([wsId, a, b, path, repoId]) => {
    const groups = await (await fetch(`/api/workspaces/${wsId}/groups`)).json();
    const aName = groups.find((g) => g.id === a)?.name;
    const bName = groups.find((g) => g.id === b)?.name;
    const listA = await (
      await fetch(`/api/workspaces/${wsId}/groups/${a}/documents`)
    ).json();
    const listB = await (
      await fetch(`/api/workspaces/${wsId}/groups/${b}/documents`)
    ).json();
    return {
      aName,
      bName,
      inA: listA.some((d) => d.path === path),
      inB: listB.some((d) => d.path === path),
    };
  },
  [made.workspaceId, seeded.groupA, seeded.groupB, seeded.path],
);
console.log("after the drag:", JSON.stringify(membership));

if (membership.inA) {
  throw new Error(
    "the document is still in the group it was dragged out of; the drag did not move it",
  );
}
if (!membership.inB) {
  throw new Error(
    "the document is not in the group it was dragged onto; the drop did nothing",
  );
}

const after = snapshot(REPO);
if (before && after) {
  const changed = Object.keys(before).filter((k) => !(k in after));
  const added = Object.keys(after).filter((k) => !(k in before));
  const resized = Object.keys(before).filter((k) => k in after && before[k] !== after[k]);
  console.log(
    `files after: ${Object.keys(after).length} (removed ${changed.length}, added ${added.length}, resized ${resized.length})`,
  );
  if (changed.length || added.length || resized.length) {
    throw new Error(
      `dragging moved files: removed ${JSON.stringify(changed)}, added ${JSON.stringify(added)}`,
    );
  }
}

await page.screenshot({ path: "screenshots/groups-board.png" });

// --- 6. Clean up, so a smoke run does not leave litter ---------------------
await page.evaluate(
  async ([wsId, a, b]) => {
    for (const id of [a, b]) {
      await fetch(`/api/workspaces/${wsId}/groups/${id}`, { method: "DELETE" });
    }
  },
  [made.workspaceId, seeded.groupA, seeded.groupB],
);

if (errors.length) {
  throw new Error(`browser errors: ${errors.slice(0, 3).join(" | ")}`);
}
console.log("OK: dragging rearranges the groups and leaves every file where it was");
await browser.close();
