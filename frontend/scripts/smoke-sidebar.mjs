// The panel, and the navigation it sits in.
//
// Two claims are worth more than pixels here, and neither is visible in a
// screenshot:
//
//  1. The front page is the workflow. Everything else is one disclosure away but
//     still reachable -- frozen means demoted, not removed.
//  2. A group and a document are described in the same panel, each with its
//     origin, its members and its findings, and switching between them does not
//     leave the previous one's answer on screen.
//
// The group and its membership are created through the API and deleted again at
// the end, so a smoke run leaves no litter in the reader's workspace.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(2500)

// --- 1. The front page is the workflow --------------------------------------
// Only what is actually on screen. Everything inside the folded "More" is in the
// DOM from the start -- a <details> hides its contents, it does not remove them
// -- so counting elements here would report a folded section as still visible,
// which is the mistake this test exists to catch.
const visible = await page.locator('.nav-item:visible').allTextContents()
const flat = visible.map((t) => t.replace(/\s+/g, ' ').trim())
console.log('nav without opening anything:', JSON.stringify(flat))

for (const core of ['Inbox', 'All objects', 'Groups', 'Notes & Docs']) {
  if (!flat.some((t) => t.includes(core))) {
    throw new Error(`the core section ${core} is not on the front page`)
  }
}
for (const demoted of [
  'Repositories',
  'Decisions (ADRs)',
  'Open Questions',
  'Inventory Runs',
]) {
  if (flat.some((t) => t.includes(demoted))) {
    throw new Error(`${demoted} is still on the front page`)
  }
}
// Delphi Pulse keeps its own pinned button, so the list must not offer it twice.
if (flat.some((t) => t.includes('Delphi Pulse'))) {
  throw new Error('Delphi Pulse is listed in the navigation as well as pinned below')
}
if ((await page.locator('.nav-ai-pulse-btn').count()) === 0) {
  throw new Error('the pinned Delphi Pulse button is gone')
}

// --- 2. Nothing is hidden, only folded -------------------------------------
const more = page.locator('.nav-more-summary')
if ((await more.count()) !== 1) {
  throw new Error('there is no "More" disclosure in the navigation')
}
if (await more.evaluate((el) => el.closest('details').open)) {
  throw new Error('"More" starts open, which is the same as not folding it away')
}
await more.click()
await page.waitForTimeout(300)
const folded = (await page.locator('.nav-more .nav-item:visible').allTextContents()).map(
  (t) => t.replace(/\s+/g, ' ').trim(),
)
console.log('inside "More":', JSON.stringify(folded))
for (const name2 of [
  'Repositories',
  'Decisions (ADRs)',
  'Open Questions',
  'Inventory Runs',
]) {
  if (!folded.some((t) => t.includes(name2))) {
    throw new Error(`${name2} is not reachable: the fold hid it instead of demoting it`)
  }
}


// --- 3. A group with a document in it ---------------------------------------
const name = `Smoke sidebar ${String(Date.now()).slice(-6)}`
const made = await page.evaluate(
  async ([groupName, description]) => {
    const ws = (await (await fetch('/api/workspaces')).json())[0]
    const res = await fetch(`/api/workspaces/${ws.id}/groups`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // source=ai, so the panel has to say Delphi proposed it. A group the
      // reader made would exercise the other half of the same sentence.
      body: JSON.stringify({ name: groupName, description, source: 'ai' }),
    })
    const group = await res.json()
    // A document the repository really has, taken from the tree endpoint so the
    // panel is describing a file that exists. The nodes hang under `root`, which
    // is the one thing about this response worth stating: walking the response
    // itself finds no children and quietly reports an empty repository.
    const repos = (await (await fetch(`/api/workspaces/${ws.id}`)).json()).repositories
    const docRepo = repos.find((r) => !r.is_storage)
    let member = null
    const walk = (node) => {
      for (const child of node?.children ?? []) {
        if (!member && !child.is_dir) {
          member = { repository_id: docRepo.id, path: child.path }
          return true
        }
        if (child.is_dir && walk(child)) return true
      }
      return false
    }
    const tree = await (
      await fetch(
        `/api/workspaces/${ws.id}/repositories/${docRepo.id}/tree?path=${encodeURIComponent('.')}`,
      )
    ).json()
    walk(tree.root)
    if (member) {
      await fetch(`/api/workspaces/${ws.id}/groups/${group.id}/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(member),
      })
    }
    return { workspaceId: ws.id, groupId: group.id, member }
  },
  [name, 'These two documents keep contradicting each other about the same date.'],
)
console.log('group under test:', JSON.stringify(made))
if (!made.member) {
  throw new Error('the documentation repository has no document to put in the group')
}

await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(2000)

// --- 4. The group describes itself in the panel -----------------------------
await page.locator('.nav-item', { hasText: 'Groups' }).first().click()
await page.waitForTimeout(1200)
await page
  .locator('.group-card', { hasText: name })
  .locator('.group-card-details')
  .click()
await page.waitForTimeout(1200)

const panel = await page.locator('.context-scroll-body').innerText()
console.log('panel for the group:\n' + panel)
if (!panel.includes('THIS GROUP')) {
  throw new Error('the panel does not say it is describing a group')
}
if (!panel.includes(name)) {
  throw new Error('the panel does not name the group')
}
if (!/Delphi proposed this/i.test(panel)) {
  throw new Error('the panel does not say Delphi proposed the group')
}
if (!panel.includes('contradicting each other')) {
  throw new Error('the panel does not show the reason the group was proposed with')
}
if (!panel.includes(made.member.path)) {
  throw new Error(`the panel does not list the member ${made.member.path}`)
}
if (!panel.includes('FINDINGS')) {
  throw new Error('the panel has no section for what Delphi found in the group')
}
// The tab label lives in the tab bar above the scroll body, so it is read from
// there. A panel that names a group while the tab still claims to describe a
// document is the half-switched state this is checking for.
const tabs = await page.locator('.context-nav-tabs').innerText()
console.log('tabs:', JSON.stringify(tabs))
if (!tabs.includes('This group')) {
  throw new Error('the tab still says "This document" while a group is showing')
}
await page.screenshot({ path: 'screenshots/sidebar-group.png' })

// --- 5. And the document says where it sits --------------------------------
await page.locator('.nav-item', { hasText: 'Notes & Docs' }).first().click()
await page.waitForTimeout(1200)
await page.locator('.object-card').first().click()
await page.waitForTimeout(1500)

const docPanel = await page.locator('.context-scroll-body').innerText()
console.log('panel for the document:\n' + docPanel)
if (!docPanel.includes('BELONGS TO')) {
  throw new Error('the panel does not say which groups the document is in')
}
if (!/Delphi proposed this group/i.test(docPanel)) {
  throw new Error("the panel does not mark the group as Delphi's proposal")
}
await page.screenshot({ path: 'screenshots/sidebar-document.png' })

// Clicking the group in that list brings the group back, in the same panel.
await page.locator('.context-member-list button', { hasText: name }).first().click()
await page.waitForTimeout(1200)
const back = await page.locator('.context-scroll-body').innerText()
if (!back.includes('THIS GROUP')) {
  throw new Error('clicking the group in the document panel did not show the group')
}
if (back.includes('BELONGS TO')) {
  throw new Error('the panel still describes the document after switching to the group')
}

// --- 6. Clean up ------------------------------------------------------------
await page.evaluate(
  async ([wsId, groupId]) => {
    await fetch(`/api/workspaces/${wsId}/groups/${groupId}`, { method: 'DELETE' })
  },
  [made.workspaceId, made.groupId],
)

if (errors.length) {
  throw new Error(`browser errors: ${errors.slice(0, 3).join(' | ')}`)
}
console.log('OK: the front page is the workflow, and the panel describes groups and documents')
await browser.close()
