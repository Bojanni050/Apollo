// Verifies the setup wizard renders on a truly empty database and that it
// creates a workspace through the UI (no curl).
//
// Two preconditions worth stating, because both have bitten before: the
// database must be empty (a workspace already exists, so the app never shows the
// wizard), and the backend must be reachable through the same origin.
//
// Two steps now, not three: name the workspace, then choose a folder. Adding
// documents is the optional second half of step 2, exercised here through
// "Add a whole folder" -- copying an existing folder's documents into the
// Inbox -- since that is the path earlier revisions of this wizard used to
// implement wrong, as registering a separate read-in-place repository instead
// of copying into the Inbox like every other way documents get in.
import { chromium } from 'playwright'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const working = mkdtempSync(join(tmpdir(), 'apollo-smoke-setup-'))
// A documentation folder of this script's own, rather than a path that exists on
// one person's machine. The optional "Add a whole folder" step copies a real
// directory's contents, and pointing it at somebody's actual project folder
// would copy their real files into this throwaway workspace.
const docs = join(working, 'documentatie')
mkdirSync(docs, { recursive: true })
writeFileSync(join(docs, 'aantekening.md'), '# Aantekening\n')

const browser = await chromium.launch()
const page = await browser.newPage()
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

try {
  await page.goto(URL, { waitUntil: 'networkidle' })

  // The wizard, not the old curl instructions.
  const heading = await page.textContent('.setup h2')
  console.log('heading:', heading)
  if (!/Step 1 of 2/i.test(heading || '')) {
    throw new Error(`expected the setup wizard, got: ${heading}`)
  }
  if ((await page.content()).includes('curl -X POST')) {
    throw new Error('the old curl instructions are still on the page')
  }
  await page.screenshot({ path: 'screenshots/setup-step1.png' })

  // Fill in and submit the workspace step.
  const name = `Smoke WS ${Date.now()}`
  await page.fill('#ws-name', name)
  await page.fill('#ws-desc', 'created by the wizard smoke test')
  await page.click('.setup button[type=submit]')

  // Step two: the folder. Its own folder-chooser sub-question, before anything
  // about documents is asked.
  await page.waitForSelector('#working-path', { timeout: 10000 })
  const folderHeading = await page.textContent('.setup h2')
  console.log('step 2 heading:', folderHeading)
  if (!/Step 2 of 2/i.test(folderHeading || '')) {
    throw new Error(`expected the folder step, got: ${folderHeading}`)
  }
  // It must show what the choice creates, because the Inbox folder appearing
  // later without warning reads as something the reader did not agree to.
  const treeShown = await page.locator('.setup-tree-body').count()
  if (treeShown === 0) {
    throw new Error('step 2 does not show what the choice will create')
  }
  await page.screenshot({ path: 'screenshots/setup-step2-folder.png' })

  await page.fill('#working-path', working)
  // A locator, not page.click(selector, { hasText }): page.click has no such
  // option and silently clicks the first match -- here the Browse button, which
  // opens the picker and achieves nothing.
  await page.locator('.setup button', { hasText: 'Use this folder' }).click()
  // Chosen: the wizard now confirms what it did before moving on, rather than
  // asking the reader to trust that something happened.
  await page.waitForSelector('.setup-picked', { timeout: 10000 })
  const picked = (await page.textContent('.setup-picked'))?.trim()
  console.log('folder chosen:', picked)
  if (picked !== working) {
    throw new Error(`the wizard did not record the folder: ${picked}`)
  }

  // The folder alone must already have created a repository -- the point of
  // moving that creation out of the first upload -- so chat is not left
  // blocked on an optional step nobody has to take.
  const hasRepoAfterFolder = await page.evaluate(async () => {
    const all = await (await fetch('/api/workspaces')).json()
    const mine = all.find((w) => w.name.startsWith('Smoke WS'))
    return (await (await fetch(`/api/workspaces/${mine.id}`)).json()).repositories.length > 0
  })
  if (!hasRepoAfterFolder) {
    throw new Error('choosing a folder did not create a repository before any upload')
  }

  // The optional half: copy in an existing folder's documents, through the
  // same InboxDropzone the workspace itself uses. Chosen with the folder
  // picker rather than typed, so this exercises the same picker every other
  // "point at a folder" action in the app uses. They must land in the
  // Inbox -- copied, structure kept -- not registered as a separate,
  // read-in-place repository.
  await page.locator('.setup button', { hasText: 'Add a whole folder' }).click()
  await page.waitForSelector('.folder-picker-modal .picker-path-input', { timeout: 10000 })
  await page.screenshot({ path: 'screenshots/setup-step2-import.png' })

  await page.fill('.folder-picker-modal .picker-path-input', docs)
  await page.locator('.folder-picker-modal button', { hasText: 'Go' }).click()
  // Wait for the browse to land on the folder just typed, not whichever one
  // the picker opened on -- clicking "Select Folder" a moment too early would
  // otherwise copy in the wrong directory.
  await page.waitForFunction(
    (expected) => document.querySelector('.folder-picker-modal .picker-selected-path')?.textContent?.includes(expected),
    docs,
    { timeout: 10000 },
  )
  await page.locator('.folder-picker-modal button', { hasText: 'Select Folder' }).click()
  await page.waitForSelector('.inbox-outcome', { timeout: 10000 })
  const outcome = await page.locator('.inbox-outcome').first().textContent()
  console.log('import outcome:', outcome)
  if (!outcome || !/^Copied/.test(outcome.trim())) {
    throw new Error(`the folder was not copied in: ${outcome}`)
  }

  const inInbox = await page.evaluate(async () => {
    const all = await (await fetch('/api/workspaces')).json()
    const mine = all.find((w) => w.name.startsWith('Smoke WS'))
    const inbox = await (await fetch(`/api/workspaces/${mine.id}/inbox`)).json()
    return inbox.files.some((f) => f.path.endsWith('aantekening.md'))
  })
  if (!inInbox) {
    throw new Error('the imported document is not listed in the Inbox')
  }

  // Finishing is a separate, always-available action -- not gated on having
  // used the optional half at all.
  await page.locator('.setup button', { hasText: 'Finish setup' }).click()

  // The app should now be the normal three-panel layout. The full class name on
  // purpose: a shorter selector that also matches part of some other element
  // would keep passing while the layout itself was renamed.
  try {
    await page.waitForSelector('.mindstack-layout', { timeout: 10000 })
  } catch {
    // What the page said, not just that it was not what we wanted. A wizard that
    // fails on the last step otherwise reports nothing but "timeout", which is
    // the least useful thing it could possibly say.
    const said = (await page.locator('.setup').innerText().catch(() => '(geen)'))
      .replace(/\s+/g, ' ')
    await page.screenshot({ path: 'screenshots/setup-stuck.png' })
    throw new Error(`the wizard did not hand over to the app; it said: ${said}`)
  }
  await page.waitForTimeout(800)
  // The new workspace is the one on screen. Asserted through the name the
  // navigation column actually renders, not through a titlebar dropdown that
  // this app has not had for some time: a selector nobody checks is a selector
  // that quietly stops proving anything.
  const shown = (await page.textContent('.nav-brand-name'))?.trim()
  const slug = name.toLowerCase().replace(/\s+/g, '-')
  console.log('workspace now shown:', shown)
  if (shown !== slug) {
    throw new Error(`the app did not select the new workspace: ${shown} (expected ${slug})`)
  }
  await page.screenshot({ path: 'screenshots/setup-done.png' })

  // The choice has to survive into the workspace, or the wizard only pretended.
  const recorded = await page.evaluate(async () => {
    const all = await (await fetch('/api/workspaces')).json()
    const mine = all.find((w) => w.name.startsWith('Smoke WS'))
    return (await (await fetch(`/api/workspaces/${mine.id}/working-dir`)).json()).working_dir
  })
  console.log('recorded on the workspace:', recorded)
  if (recorded !== working) {
    throw new Error(`the workspace does not carry the chosen folder: ${recorded}`)
  }

  if (errors.length) throw new Error('console errors:\n' + errors.join('\n'))
  console.log('OK: wizard created a workspace, chose a folder, and copied in an existing folder')
} finally {
  await browser.close()
  rmSync(working, { recursive: true, force: true })
}
