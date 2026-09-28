// Choosing the folder to work in.
//
// The property worth proving is not a rendering, it is that the promise is real:
// a document dropped in afterwards must land in the folder the reader chose, on
// this disk, and that folder must carry a history of its own. A card that says
// the right thing while the bytes go somewhere else is the failure this guards
// against -- and the browser is here to prove the choice is actually reachable
// and reported, which no service test can see.
//
// The folder is created in the system temp directory and removed afterwards. It
// is never a real workspace folder and never left behind.
import { chromium } from 'playwright'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

/**
 * Type a path into the open picker and confirm it.
 *
 * The wait on the "Selected:" line is the point. Confirming whatever happens to
 * be selected is how a picker that never navigated gets mistaken for one that
 * did: the folder is then re-chosen instead of changed, the panel looks fine,
 * and the script proves nothing.
 */
async function pick(page, path) {
  // Normalised on both sides, because the picker shows the path as the backend
  // reports it: a trailing separator or a different drive-letter case is the same
  // folder, and failing on that would make this test flaky rather than strict.
  const norm = (s) => s.trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  await page.locator('.picker-path-input').fill(path)
  await page.locator('.picker-path-form button[type=submit]').click()
  try {
    await page.waitForFunction(
      (want) => {
        const el = document.querySelector('.picker-selected-path .mono')
        if (!el) return false
        const norm = (s) => s.trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
        return norm(el.textContent || '') === want
      },
      norm(path),
      { timeout: 10000 },
    )
  } catch {
    const shown = await page
      .locator('.picker-selected-path')
      .innerText()
      .catch(() => '(geen)')
    const typed = await page
      .locator('.picker-path-input')
      .inputValue()
      .catch(() => '(geen)')
    const complained = await page
      .locator('.picker-error')
      .innerText()
      .catch(() => '(geen foutmelding)')
    throw new Error(
      `the picker did not navigate to ${path} before confirming; it showed ${shown}; ` +
        `the input held ${typed}; it said ${complained}`,
    )
  }
  await page.locator('.modal-footer button', { hasText: 'Select Folder' }).click()
  await page.waitForTimeout(2500)
}

/** Put a folder (or '' for Apollo's own) on the workspace, and say what happened.
 *  The status is returned rather than thrown on: a restore to a folder that has
 *  since been deleted is a fact to report, not a reason to crash in the cleanup. */
async function setWorking(page, path) {
  return page.evaluate(async (p) => {
    const ws = (await (await fetch('/api/workspaces')).json())[0]
    const res = await fetch(`/api/workspaces/${ws.id}/working-dir`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: p }),
    })
    return `${res.status} ${JSON.stringify(await res.json())}`
  }, path)
}

let browser
let page
const root = mkdtempSync(join(tmpdir(), 'apollo-smoke-werkmap-'))
const chosen = join(root, 'mijn-werkmap')
const occupied = join(root, 'reeds-vol')
/** The folder to put back when this is over: the workspace is shared, persistent
 *  state, and a script that leaves it pointing at a folder it just deleted would
 *  make the *next* script start from a broken place. */
let restore = null

try {
  mkdirSync(chosen)
  mkdirSync(occupied)
  // Something the reader put there themselves, to prove it is not touched.
  writeFileSync(join(occupied, 'mijn-geschrift.md'), '# Mijn\n')

  browser = await chromium.launch()
  page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  await page.goto(URL, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2500)

  // Whatever was chosen before this script touched anything, so it can be put
  // back. A workspace left pointing at a temp folder that no longer exists is a
  // trap for whichever script runs next. Read after the page is loaded, because
  // the request is relative to the app and there is nothing to be relative to
  // before that.
  restore = await page.evaluate(async () => {
    const ws = (await (await fetch('/api/workspaces')).json())[0]
    return (await (await fetch(`/api/workspaces/${ws.id}/working-dir`)).json()).working_dir
  })
  if (restore) {
    console.log('was gekozen:', restore, '->', await setWorking(page, ''))
    // The card fetched its answer when the page loaded, so a reset behind its
    // back leaves it describing the folder that was just given up.
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(2000)
  }
  await page.waitForTimeout(2500)

  // --- The question is asked before anything is dropped in ----------------
  await page.locator('.nav-item', { hasText: 'Inbox' }).first().click()
  await page.waitForSelector('.working-folder-card', { timeout: 10000 })
  const ask = (await page.locator('.working-folder-card').innerText()).replace(/\s+/g, ' ')
  console.log('card:', JSON.stringify(ask))
  if (!/Apollo is using a folder of its own/i.test(ask)) {
    throw new Error(`the inbox does not say where documents go yet: ${ask}`)
  }

  // --- Choose, through the same picker the button opens -------------------
  await page.locator('.working-folder-card button', { hasText: 'Choose a folder' }).click()
  await page.waitForSelector('.picker-path-input', { timeout: 10000 })
  await pick(page, chosen)

  const after = (await page.locator('.working-folder-card').innerText()).replace(/\s+/g, ' ')
  console.log('chosen:', JSON.stringify(after))
  if (!after.includes(chosen)) {
    throw new Error(`the card does not show the folder that was chosen: ${after}`)
  }
  // Said out loud, once, because the guarantee is the reason to choose at all.
  if (!/history/i.test(after)) {
    throw new Error(`the card does not mention that the folder keeps a history: ${after}`)
  }

  // --- A document lands there, on this disk -------------------------------
  await page.evaluate(async () => {
    const ws = (await (await fetch('/api/workspaces')).json())[0]
    const body = new FormData()
    body.append('file', new File(['# Werkmap\n'], 'uit-de-werkmap.md', { type: 'text/markdown' }))
    const res = await fetch(`/api/workspaces/${ws.id}/inbox/upload`, { method: 'POST', body })
    if (!res.ok) throw new Error(`upload failed: ${res.status} ${await res.text()}`)
  })
  await page.waitForTimeout(2000)
  const stored = join(chosen, 'Inbox', 'uit-de-werkmap.md')
  if (!existsSync(stored)) {
    throw new Error(`the document did not land in the chosen folder: ${stored}`)
  }
  console.log('landed:', stored)

  // --- And the folder can give it back ------------------------------------
  const log = execFileSync('git', ['log', '--oneline'], { cwd: chosen, encoding: 'utf-8' })
  console.log('history:\n' + log.trim())
  if (!log.includes('uit-de-werkmap.md')) {
    throw new Error('the document that was just added is not in the folder history')
  }
  const author = execFileSync('git', ['log', '-1', '--format=%an'], { cwd: chosen, encoding: 'utf-8' })
  if (!author.includes('Apollo')) {
    throw new Error(`the history is attributed to ${author.trim()} rather than to Apollo`)
  }

  // --- A folder that is not empty is allowed, and said so ------------------
  await page.locator('.working-folder-card button', { hasText: 'Change' }).click()
  await page.waitForSelector('.picker-path-input', { timeout: 10000 })
  await pick(page, occupied)

  const warned = (await page.locator('.working-folder-card').innerText()).replace(/\s+/g, ' ')
  console.log('occupied:', JSON.stringify(warned))
  if (!warned.includes(occupied)) {
    throw new Error(`the card still shows the previous folder: ${warned}`)
  }
  if (!/already holds 1 item/i.test(warned)) {
    throw new Error(`a folder with something in it was accepted without saying so: ${warned}`)
  }
  if (!existsSync(join(occupied, 'mijn-geschrift.md'))) {
    throw new Error("the reader's own file in the chosen folder was not left alone")
  }
  if (!existsSync(stored)) {
    throw new Error('the earlier document was moved by a later choice; it must not be')
  }

  if (errors.length) {
    throw new Error(`browser errors: ${errors.slice(0, 3).join(' | ')}`)
  }
  console.log('OK: the chosen folder is where documents go, and it keeps their history')
} finally {
  if (browser) await browser.close()
  // Put the workspace back the way it was found, before the folder goes away --
  // in that order, because restoring a choice to a deleted folder would leave
  // the next run staring at a path that does not exist.
  if (restore !== null && page) {
    // A folder that no longer exists cannot be restored, and saying so beats a
    // silently dangling path for the next run to trip over.
    console.log('terugzetten naar:', restore, '->', await setWorking(page, restore))
  }
  // Only ever what this script created.
  rmSync(root, { recursive: true, force: true })
}