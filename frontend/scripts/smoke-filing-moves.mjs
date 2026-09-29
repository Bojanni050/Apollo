// Filing a document into a group that has a folder, in one run: give the group a
// folder, drop a document in, see the proposal appear, accept it, and check the file
// moved -- and that nothing anywhere else lost a reference to it.

// Against a throwaway workspace: SMOKE_URL, SMOKE_DOC_REPO and a backend whose
// database is not yours. The script cleans up what it created first, so it is
// re-runnable rather than a one-shot that needs a clean database.

import { readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';

const URL = process.env.SMOKE_URL || 'http://localhost:5273';
const REPO = process.env.SMOKE_DOC_REPO;

if (!REPO) {
  console.error('set SMOKE_DOC_REPO to a scratch git repository');
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

try {
  await page.goto(URL, { waitUntil: "networkidle" });

  // --- 1. A workspace with a repository we can write to --------------------
  const ws = (await api(page, "/api/workspaces")).body;
  if (!ws || !ws.length) fail("no workspace to test with");
  const workspaceId = ws[0].id;

  // A previous run that died half-way leaves a documentation repository behind,
  // and this workspace may only have one. Clearing it here is what makes the
  // script re-runnable instead of a one-shot that only works on a clean database.
  const existingRepos = (await api(page, `/api/workspaces/${workspaceId}`)).body
    .repositories;
  for (const r of existingRepos ?? []) {
    if (r.kind === "documentation") {
      await api(page, `/api/workspaces/${workspaceId}/repositories/${r.id}`, {
        method: "DELETE",
      });
    }
  }
  const staleGroups = (await api(page, `/api/workspaces/${workspaceId}/groups`)).body;
  for (const g of staleGroups) {
    await api(page, `/api/workspaces/${workspaceId}/groups/${g.id}`, { method: "DELETE" });
  }
  const staleProposals = (
    await api(page, `/api/workspaces/${workspaceId}/proposals?status_filter=pending`)
  ).body;
  for (const p of staleProposals) {
    await api(page, `/api/workspaces/${workspaceId}/proposals/${p.id}/reject`, {
      method: "POST",
    });
  }

  const full = await api(
    page,
    `/api/workspaces/${workspaceId}/repositories`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: `smoke-filing-${Date.now().toString().slice(-6)}`,
        local_path: REPO,
        branch: "main",
        kind: "documentation",
        writable: true,
      }),
    },
  );
  if (full.status !== 201 && full.status !== 200) {
    fail(`could not register the scratch repository: ${JSON.stringify(full)}`);
  }
  const repoId = full.body.id;
  console.log("repository:", repoId);

  // A document to move. Written straight into the repository, because this
  // test is about the filing, not about getting a document in.
  const fileName = `smoke-filing-${Date.now().toString().slice(-6)}.md`;
  await writeFile(`${REPO}/${fileName}`, "# Smoke\n\nA document to file.\n", "utf8");
  // Committed before the test starts moving things, because a move refuses an
  // untracked document -- correctly: without a commit the original would not be
  // recoverable. The inbox commits every dropped file for exactly this reason, so
  // a document that reached this state through the application is already
  // tracked, and only a file written behind the application's back is not.
  const { execFileSync } = await import("node:child_process");
  execFileSync("git", ["-C", REPO, "add", "--", fileName]);
  execFileSync("git", ["-C", REPO, "commit", "-q", "-m", `add ${fileName}`]);
  console.log("wrote and committed", fileName);

  // --- 2. A group with no folder is a view ---------------------------------
  const view = (
    await api(page, `/api/workspaces/${workspaceId}/groups`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: `Smoke view ${Date.now().toString().slice(-6)}` }),
    })
  ).body;
  if (view.folder !== null) {
    fail(`a new group should have no folder, got ${JSON.stringify(view.folder)}`);
  }
  console.log("view group has no folder, as it should");

  // --- 3. A group that names a folder -------------------------------------
  const folder = `SmokeMap${Date.now().toString().slice(-6)}`;
  const filed = (
    await api(page, `/api/workspaces/${workspaceId}/groups`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: `Smoke filed ${folder}`, folder }),
    })
  ).body;
  if (filed.folder !== folder) {
    fail(`the folder was not stored: ${JSON.stringify(filed)}`);
  }
  console.log("group folder:", filed.folder);

  // --- 4. A drop into the view changes nothing on disk ---------------------
  const droppedInView = (
    await api(page, `/api/workspaces/${workspaceId}/groups/${view.id}/documents`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ repository_id: repoId, path: fileName }),
    })
  ).body;
  if (droppedInView.proposal_id !== null) {
    fail("a view group should not propose a move");
  }
  const stillAtRoot = await readFile(`${REPO}/${fileName}`, "utf8");
  if (!stillAtRoot.includes("A document to file")) {
    fail("the document moved when it was dropped into a view");
  }
  console.log("drop into a view: no proposal, no move");

  // --- 5. A drop into the filed group proposes, and still moves nothing ----
  const dropped = (
    await api(page, `/api/workspaces/${workspaceId}/groups/${filed.id}/documents`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ repository_id: repoId, path: fileName }),
    })
  ).body;
  if (!dropped.proposal_id) {
    fail("dropping into a group with a folder should propose a move");
  }
  const proposalId = dropped.proposal_id;
  console.log("proposal:", proposalId);

  // The folder must not exist yet, and the file must still be where it was.
  const stillThere = await readFile(`${REPO}/${fileName}`, "utf8");
  if (!stillThere.includes("A document to file")) {
    fail("the document moved before the proposal was accepted");
  }
  console.log("proposed, and nothing moved yet");

  // --- 6. The board says so, where the drop happened -----------------------
  const nav = page.locator(".mindstack-tab", { hasText: "Groups" }).first();
  if ((await nav.count()) === 0) {
    fail("there is no Groups tab above the middle column");
  }
  await nav.click();
  await page.waitForTimeout(1500);

  const folderLabels = await page.locator(".group-folder code").allTextContents();
  if (!folderLabels.includes(folder)) {
    fail(`the board does not show the folder ${folder}: ${JSON.stringify(folderLabels)}`);
  }
  const viewLabels = await page.locator(".group-folder-label--muted").allTextContents();
  if (viewLabels.length === 0) {
    fail("no group on the board says it is a view; the two kinds must be visible");
  }
  console.log("board shows both kinds of group");

  await page.screenshot({ path: "screenshots/filing-folders.png" });

  // --- 7. Accepting moves the file ----------------------------------------
  const accepted = await api(
    page,
    `/api/workspaces/${workspaceId}/proposals/${proposalId}/accept`,
    { method: "POST" },
  );
  if (accepted.status !== 200) {
    fail(`accepting failed: ${JSON.stringify(accepted)}`);
  }
  if (accepted.body.applied_paths[0] !== `${folder}/${fileName}`) {
    fail(`unexpected applied path: ${JSON.stringify(accepted.body.applied_paths)}`);
  }
  const moved = await readFile(`${REPO}/${folder}/${fileName}`, "utf8");
  if (!moved.includes("A document to file")) {
    fail("the file did not arrive in the folder");
  }
  console.log("accepted: the file is in", folder);

  // --- 8. The group followed it -------------------------------------------
  const members = (
    await api(page, `/api/workspaces/${workspaceId}/groups/${filed.id}/documents`)
  ).body;
  if (members.length !== 1 || members[0].path !== `${folder}/${fileName}`) {
    fail(
      `the group did not follow the document: ${JSON.stringify(members)}`,
    );
  }
  console.log("the group followed the document to its new path");

  // --- 9. And the view group, which also held it --------------------------
  const viewMembers = (
    await api(page, `/api/workspaces/${workspaceId}/groups/${view.id}/documents`)
  ).body;
  if (viewMembers.length !== 1 || viewMembers[0].path !== `${folder}/${fileName}`) {
    fail(
      `a second group was left on the old path: ${JSON.stringify(viewMembers)}`,
    );
  }
  console.log("the other group followed too");

  // --- 10. Nothing was lost ------------------------------------------------
  const tree = (
    await api(page, `/api/workspaces/${workspaceId}/repositories/${repoId}/tree`)
  ).body;
  // The tree is nested (root -> children), so a flat read of the top level would
  // find the folder but never the file inside it -- which would fail this check
  // for a repository that is in fact perfectly correct.
  const pathsIn = (node) => [
    node.path,
    ...(node.children ?? []).flatMap((child) => pathsIn(child)),
  ];
  const entries = pathsIn(tree.root ?? tree).map((p) => p.replace(/\\/g, "/"));
  if (!entries.includes(`${folder}/${fileName}`)) {
    fail(`the repository tree does not show the moved file: ${JSON.stringify(entries)}`);
  }
  if (entries.includes(fileName)) {
    fail(`the tree still shows the file at its old path: ${JSON.stringify(entries)}`);
  }
  console.log("the repository tree agrees, at the new path only");

  // --- Clean up, in the same shape as the setup ----------------------------
  for (const gid of [view.id, filed.id]) {
    await api(page, `/api/workspaces/${workspaceId}/groups/${gid}`, { method: "DELETE" });
  }
  await api(page, `/api/workspaces/${workspaceId}/proposals/${proposalId}/reject`, {
    method: "POST",
  });
  console.log("\nfiling smoke: OK");
} catch (err) {
  console.error("\nfiling smoke FAILED:", err.message);
  await page.screenshot({ path: "screenshots/filing-failed.png" }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}