// Verifies the setup wizard renders on a truly empty database and that the
// workspace step creates a workspace through the UI (no curl).
//
// Two preconditions worth stating, because both have bitten before: the
// database must be empty (a workspace already exists, so the app never shows the
// wizard), and the backend must be reachable through the same origin.
//
// The wizard asks where documents should go before it asks which documentation
// to read, so there are three steps now rather than two. Both are driven here,
// and the working folder is driven through the API rather than the picker: the
// picker is a dialog over a native file browser and has its own script.
import { chromium } from 'playwright'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

const working = mkdtempSync(join(tmpdir(), 'apollo-smoke-setup-'))
// A documentation folder of this script's own, rather than a path that exists on
// one person's machine. The repository step registers a real directory, and
// pointing it at somebody's actual project folder would put a repository in
// their real workspace and register their real files.
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
  if (!/Step 1 of 3/i.test(heading || '')) {
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

  // Step two: Apollo's own working folder. Asked before the documentation
  // folder, because a reader with no documentation yet needs this answer first.
  // The heading is asserted by its step number, not its wording: the point of
  // this check is that the wizard says which question it is asking, since both
  // steps end in "pick a folder" and confusing the two is the trap.
  await page.waitForSelector('#working-path', { timeout: 10000 })
  const workingHeading = await page.textContent('.setup h2')
  console.log('step 2 heading:', workingHeading)
  if (!/Step 2 of 3/i.test(workingHeading || '')) {
    throw new Error(`expected the working-folder step, got: ${workingHeading}`)
  }
  // The two steps must not read alike, or the second is indistinguishable from
  // the first to somebody who just answered it.
  if (/existing documentation/i.test(workingHeading || '')) {
    throw new Error(`step 2 is worded like the documentation step: ${workingHeading}`)
  }
  // And it must show what the choice creates, because the Inbox folder appearing
  // later without warning reads as something the reader did not agree to.
  const treeShown = await page.locator('.setup-tree-body').count()
  if (treeShown === 0) {
    throw new Error('step 2 does not show what the choice will create')
  }
  await page.screenshot({ path: 'screenshots/setup-step2-working.png' })

  await page.fill('#working-path', working)
  // A locator, not page.click(selector, { hasText }): page.click has no such
  // option and silently clicks the first match -- here the Browse button, which
  // opens the picker and achieves nothing.
  await page.locator('.setup button', { hasText: 'Use this folder' }).click()
  // Chosen: the wizard now confirms what it did before moving on, rather than
  // asking the reader to trust that something happened.
  await page.waitForSelector('.setup-picked', { timeout: 10000 })
  const picked = (await page.textContent('.setup-picked'))?.trim()
  console.log('working folder chosen:', picked)
  if (picked !== working) {
    throw new Error(`the wizard did not record the folder: ${picked}`)
  }
  await page.click('.setup button[type=submit]')

  // Step three: the documentation repository. A different question from step 2,
  // and the screen has to say so -- see the check on step 2's heading.
  await page.waitForSelector('#repo-path', { timeout: 10000 })
  const repoHeading = await page.textContent('.setup h2')
  console.log('step 3 heading:', repoHeading)
  if (!/Step 3 of 3/i.test(repoHeading || '')) {
    throw new Error(`expected the documentation step, got: ${repoHeading}`)
  }
  // It must name the folder chosen in step 2, so the two are told apart in the
  // reader's terms rather than left to be remembered.
  const reminder = await page.locator('.setup .hint code').first().textContent()
  if (!reminder || !reminder.includes(working)) {
    throw new Error(`step 3 does not recall the step 2 folder: ${reminder}`)
  }
  await page.screenshot({ path: 'screenshots/setup-step3.png' })

  // Register this repository itself as the docs source.
  await page.fill('#repo-path', docs)
  await page.click('.setup button[type=submit]')

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
  console.log('OK: wizard created a workspace, chose a working folder, and registered a repository')
} finally {
  await browser.close()
  rmSync(working, { recursive: true, force: true })
}
