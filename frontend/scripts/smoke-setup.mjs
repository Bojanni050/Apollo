// Verifies the setup wizard renders on a truly empty database and that it
// creates a workspace through the UI (no curl).
//
// Two preconditions worth stating, because both have bitten before: the
// database must be empty (a workspace already exists, so the app never shows the
// wizard), and the backend must be reachable through the same origin.
//
// Two steps now, not three: name the workspace, then choose a folder. Adding
// documents -- uploading, or registering an existing documentation folder --
// is the optional second half of step 2, exercised here through the
// "point at an existing folder" path since that is the one earlier revisions
// of this wizard used to make into a step of its own.
import { chromium } from 'playwright'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const working = mkdtempSync(join(tmpdir(), 'apollo-smoke-setup-'))
// A documentation folder of this script's own, rather than a path that exists on
// one person's machine. The optional "existing folder" path registers a real
// directory, and pointing it at somebody's actual project folder would put a
// repository in their real workspace and register their real files.
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

  // The optional half: point at an existing documentation folder instead of
  // uploading. Collapsed by default, and must say plainly that it is a
  // different folder from the one just chosen.
  await page.locator('.setup button', { hasText: 'Point at an existing folder' }).click()
  await page.waitForSelector('#repo-path', { timeout: 10000 })
  const reminder = await page.locator('.setup-inline-form .hint').first().textContent()
  if (!reminder || !/different folder/i.test(reminder)) {
    throw new Error(`the optional step does not say it is a different folder: ${reminder}`)
  }
  await page.screenshot({ path: 'screenshots/setup-step2-existing.png' })

  await page.fill('#repo-path', docs)
  await page.locator('.setup-inline-form button[type=submit]').click()
  await page.waitForSelector('.setup-inline-form .hint:has-text("Registered")', { timeout: 10000 })

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
  console.log('OK: wizard created a workspace, chose a folder, and registered an existing folder')
} finally {
  await browser.close()
  rmSync(working, { recursive: true, force: true })
}
