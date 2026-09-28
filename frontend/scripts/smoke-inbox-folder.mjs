// Adding a whole folder as a source.
//
// The one property worth proving in a browser is not a rendering: it is that the
// reader's own folder survives. The script asserts against the filesystem -- every
// file's bytes and its modification time, before and after -- and the browser
// exists here to prove the feature is reachable and reports honestly, which is
// the half a service test cannot see.
//
// The folder is created in the system temp directory and removed afterwards. It is
// never added to the reader's workspace as a repository and never left behind.
import { chromium } from 'playwright'
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readdirSync,
  statSync,
  rmSync,
} from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

/** A folder that looks like something a reader already has. */
function makeSource() {
  const root = mkdtempSync(join(tmpdir(), 'apollo-smoke-bron-'))
  mkdirSync(join(root, 'mijn-project', 'notities'), { recursive: true })
  writeFileSync(join(root, 'mijn-project', 'verslag.md'), '# Verslag\n\nDe eerste.\n')
  writeFileSync(join(root, 'mijn-project', 'notities', 'januari.md'), '# Januari\n')
  writeFileSync(join(root, 'mijn-project', 'notities', 'februari.md'), '# Februari\n')
  // Noise, to prove it is not walked: a dependency tree.
  mkdirSync(join(root, 'mijn-project', 'node_modules', 'pkg'), { recursive: true })
  writeFileSync(join(root, 'mijn-project', 'node_modules', 'pkg', 'readme.md'), '# pkg\n')
  return join(root, 'mijn-project')
}

/** Every file: its bytes and its modification time, the reader's evidence. */
function fingerprint(root) {
  const out = {}
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else
        out[full.slice(root.length).replace(/\\/g, '/')] = [
          readFileSync(full).toString('utf-8'),
          statSync(full).mtimeMs,
        ]
    }
  }
  walk(root)
  return out
}

const source = makeSource()
const before = fingerprint(source)
let browser

try {
  browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  await page.goto(URL, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2500)

  // --- The affordance is reachable ----------------------------------------
  await page.locator('.nav-item', { hasText: 'Inbox' }).first().click()
  await page.waitForTimeout(1000)
  const open = page.locator('.inbox-folder-open', { hasText: 'Add a whole folder' })
  if ((await open.count()) === 0) {
    throw new Error('there is no way to add a whole folder in the inbox')
  }
  await open.first().click()
  await page.waitForSelector('.inbox-folder-input', { timeout: 10000 })

  // The promise is on screen before the request is made, not after.
  const hint = (await page.locator('.inbox-folder-hint').innerText()).replace(/\s+/g, ' ')
  console.log('hint:', JSON.stringify(hint))
  if (!/not moved, not renamed and not emptied/i.test(hint)) {
    throw new Error(`the panel does not say the folder itself is left alone: ${hint}`)
  }

  // --- Copy it in ---------------------------------------------------------
  await page.locator('.inbox-folder-input').fill(source)
  await page.locator('.inbox-folder-row button', { hasText: 'Copy in' }).click()
  await page.waitForTimeout(2500)

  const outcomes = await page.locator('.inbox-outcome').allTextContents()
  console.log('reported:\n' + outcomes.join('\n'))
  const copied = outcomes.filter((t) => t.startsWith('Copied '))
  if (copied.length !== 3) {
    throw new Error(`expected 3 copied documents, the panel reported ${copied.length}`)
  }
  // The structure is kept, so the report says which folder the document came
  // from rather than only its new name. A number before the extension is a
  // second run of this script against the same inbox, and it is the
  // never-overwrite rule working rather than a different result: the first copy
  // is still there, which is what the assertion below checks.
  const paths = copied.map((t) => t.replace('Copied ', ''))
  const wanted = ['notities/februari.md', 'notities/januari.md', 'verslag.md']
  for (const target of wanted) {
    const slash = target.lastIndexOf('/')
    const dir = slash === -1 ? '' : target.slice(0, slash + 1)
    const file = slash === -1 ? target : target.slice(slash + 1)
    const dot = file.indexOf('.')
    const stem = file.slice(0, dot)
    const ext = file.slice(dot)
    const numbered = new RegExp(`^${stem}-\\d+${ext}$`)
    const landed = paths.some((p) => {
      const base = p.split('/').pop()
      return (
        p.startsWith(`Inbox/mijn-project/${dir}`) && (base === file || numbered.test(base))
      )
    })
    if (!landed) {
      throw new Error(`no copied document reported for ${target}: ${JSON.stringify(paths)}`)
    }
  }
  // The dependency tree was not walked, and that is visible rather than silent.
  if (outcomes.some((t) => t.includes('readme.md'))) {
    throw new Error('a file inside node_modules was copied')
  }
  await page.screenshot({ path: 'screenshots/inbox-folder-import.png' })

  // --- The reader's folder is exactly as it was ----------------------------
  const after = fingerprint(source)
  const changed = Object.keys({ ...before, ...after }).filter(
    (k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]),
  )
  if (changed.length) {
    throw new Error(`adding the folder changed files in it: ${JSON.stringify(changed)}`)
  }
  // The fingerprint above is the evidence; this is the shape a reader would
  // recognise. `source` is the project folder itself, so its own name is not
  // expected inside it.
  const stillThere = readdirSync(source)
  if (!stillThere.includes('notities') || !stillThere.includes('verslag.md')) {
    throw new Error(`the structure of the reader's folder was altered: ${JSON.stringify(stillThere)}`)
  }

  // --- And the copies are openable from the inbox -------------------------
  const listing = await page.evaluate(async () => {
    const ws = (await (await fetch('/api/workspaces')).json())[0]
    return (await (await fetch(`/api/workspaces/${ws.id}/inbox`)).json()).files
  })
  console.log('inbox now:', JSON.stringify(listing.map((f) => f.path)))
  if (listing.length < 3) {
    throw new Error('the copies are not in the inbox listing')
  }
  const nested = listing.find((f) => f.path.endsWith('notities/januari.md'))
  if (!nested) {
    throw new Error('a document copied from a subfolder is not listed by its own path')
  }
  const opened = await page.evaluate(async (p) => {
    const ws = (await (await fetch('/api/workspaces')).json())[0]
    const repo = (await (await fetch(`/api/workspaces/${ws.id}`)).json()).repositories.find(
      (r) => r.is_storage,
    )
    const res = await fetch(
      `/api/workspaces/${ws.id}/repositories/${repo.id}/document?path=${encodeURIComponent(p)}`,
    )
    return res.ok ? (await res.json()).raw_markdown : null
  }, nested.path)
  if (!opened || !opened.includes('Januari')) {
    throw new Error('a document copied from a subfolder does not open')
  }

  if (errors.length) {
    throw new Error(`browser errors: ${errors.slice(0, 3).join(' | ')}`)
  }
  console.log(
    'OK: a folder can be added whole, and the folder itself is left exactly as it was',
  )
} finally {
  if (browser) await browser.close()
  // The reader's folder goes away with the test: this script is the only thing
  // that ever deletes it, and it only ever deletes what it created itself.
  rmSync(join(source, '..'), { recursive: true, force: true })
}
