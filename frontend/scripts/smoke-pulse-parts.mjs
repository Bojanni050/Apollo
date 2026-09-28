// Delphi Pulse: tags and connections must be separately acceptable, and the
// suggestion must be reviewable in the reading pane rather than the sidebar.
//
// The write behaviour is covered by the Python tests in
// backend/tests/test_pulse.py (test_tags_only_accept_leaves_connections), which
// can call the service directly. This covers the half that only exists in the
// browser: the review panel, its separate accept buttons, and the divider
// between the panel and the document.
import { chromium } from 'playwright'

const URL = process.env.SMOKE_URL || 'http://localhost:5273'

// The settled record is gold, and gold has one value per theme: the light-theme
// value is unreadable on a dark background. Measuring the record against its own
// panel in both schemes catches a colour that only works in the theme it was
// tuned in, which is exactly the mistake the two values exist to prevent.
async function checkSettledThemes(page) {
  for (const scheme of ['dark', 'light']) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(250)
    const contrast = await page.evaluate(() => {
      const el = document.querySelector('.pulse-accepted')
      if (!el) return null
      const panel = el.closest('.pulse-review') ?? document.body
      const parse = (s) => (s.match(/[\d.]+/g) ?? [0, 0, 0]).map(Number)
      const lum = ([r, g, b]) => (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
      return Math.abs(
        lum(parse(getComputedStyle(el).color)) -
          lum(parse(getComputedStyle(panel).backgroundColor)),
      )
    })
    console.log(`settled record contrast (${scheme}):`, contrast?.toFixed(2))
    if (contrast !== null && contrast < 0.25) {
      throw new Error(
        `the settled record is too faint to read in ${scheme} mode (${contrast.toFixed(2)})`,
      )
    }
    if (scheme === 'dark') {
      await page.screenshot({ path: 'screenshots/pulse-accepted-dark.png' })
    }
  }
  await page.emulateMedia({ colorScheme: 'light' })
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)

// --- One vocabulary, and the sidebar no longer decides ----------------------
// The context sidebar used to carry its own Apply/Skip buttons for every
// suggestion, so the same decision existed in two places under two names. It now
// only counts and navigates, and the reading pane decides with "Accept".
//
// Checked by name across the whole surface rather than per component: a leftover
// "Apply" here is exactly the inconsistency the change was for, and it would not
// show up in any single component's own test.
const vocabulary = await page.evaluate(() => {
  const body = document.body.innerText
  return {
    applyAll: /apply all suggestions/i.test(body),
    sidebarItems: document.querySelectorAll('.context-inventory-item').length,
    runSummary: document.querySelectorAll('.pulse-run-summary').length,
    runItems: document.querySelectorAll('.pulse-run-item').length,
  }
})
console.log('pulse vocabulary:', vocabulary)
if (vocabulary.applyAll) {
  throw new Error('the old "Apply All Suggestions" wording is still on screen')
}
if (vocabulary.runSummary > 0 && vocabulary.sidebarItems > 0) {
  throw new Error(
    'the sidebar still renders per-item pulse blocks next to the new run summary',
  )
}

// --- The reading pane renders the review panel ----------------------------
// A workspace can be named on the command line. The default run refuses to
// write (the documents in it are untracked, which the guarded write path
// rejects on purpose), and the fade cannot be observed against a write that
// never happens -- so a workspace with a committed document is worth passing.
const workspaceName = process.env.SMOKE_WORKSPACE
if (workspaceName) {
  await page.locator('.nav-user-info').first().click()
  await page.waitForTimeout(400)
  const target = page.locator('.popup-menu-item', { hasText: workspaceName }).first()
  if (await target.count()) {
    await target.click()
    await page.waitForTimeout(1800)
    console.log('switched workspace to:', workspaceName)
  } else {
    console.log(`note: workspace "${workspaceName}" not found; staying put`)
    await page.keyboard.press('Escape')
  }
}

const pulseNav = page.locator('.nav-item', { hasText: /Delphi Pulse/i }).first()
if (!(await pulseNav.count())) {
  console.log('note: no Delphi Pulse entry in the navigation, skipping the pane check')
} else {
  await pulseNav.click()
  await page.waitForTimeout(1500)

  const card = page.locator('.object-card').first()
  if (await card.count()) {
    await card.click()
    await page.waitForTimeout(1200)

    const panel = page.locator('.pulse-review')
    const count = await panel.count()
    console.log('pulse review panels in the reading pane:', count)

    if (count > 0) {
      const groups = await page.locator('.pulse-review-group-title').allTextContents()
      console.log('review groups:', groups)
      // Tags and connections get their own group, so they can be judged apart.
      if (!groups.some((g) => /tags/i.test(g))) {
        throw new Error('the review panel has no tags group')
      }
      if (!groups.some((g) => /connections/i.test(g))) {
        throw new Error('the review panel has no connections group')
      }
      // Each group with something to decide offers its own write, rather than
      // one button that takes both. The labels state the CONSEQUENCE ("write 5
      // tags to this file") rather than the abstraction ("accept tags"): the
      // click edits the document on disk, and the old wording did not say so.
      //
      // A half that was already taken in an earlier run shows its gold record
      // instead of a button, so each group counts as correct if it offers EITHER
      // a write OR a record. Requiring a button made the smoke fail on the
      // second run against the same workspace, which is a test that only works
      // once.
      const settled = (re) =>
        page
          .locator('.pulse-review-group', { hasText: re })
          .locator('.pulse-accepted')
          .count()
      const offered = (re) =>
        page
          .locator('.pulse-review-actions button.pulse-action--write', { hasText: re })
          .count()

      const tagWrites = await offered(/write \d+ tags?/i)
      const tagRecords = await settled(/tags/i)
      if (tagWrites + tagRecords < 1) {
        throw new Error('the tags group neither offers a write nor records one')
      }
      const connWrites = await offered(/write \d+ connections?/i)
      const connRecords = await settled(/connections/i)
      if (connWrites + connRecords < 1) {
        throw new Error('the connections group neither offers a write nor records one')
      }
      console.log(
        'tags write/record, connections write/record:',
        tagWrites, tagRecords, connWrites, connRecords,
      )
      // One route for the lot, in the summary row rather than beside a group.
      // Absent once both halves are in -- at that point there is nothing left to
      // offer, and the panel says so in words instead.
      const both = await page
        .locator('.pulse-review-decide button.pulse-action--write-all', {
          hasText: /write all \d+ changes/i,
        })
        .count()
      const allRecords = await page.locator('.pulse-review-closed').count()
      console.log('write all / already complete:', both, allRecords)
      if (both + allRecords < 1) {
        throw new Error(
          'neither a "write all N changes" button nor a completion note is shown',
        )
      }

      // The refusal used to appear as a second "Decline" under each group while
      // actually refusing everything. There must be exactly one, and it must say
      // what it does. Absent once the item is closed, since there is then
      // nothing left to refuse.
      const refusals = await page
        .locator('.pulse-review-decide button.pulse-action--refuse')
        .count()
      if (refusals > 1) {
        throw new Error(
          `expected at most one refusal in the summary row, found ${refusals}`,
        )
      }
      // And nothing outside that row may refuse anything.
      const strayRefusals = await page
        .locator('.pulse-review-group button')
        .filter({ hasText: /write nothing|decline/i })
        .count()
      if (strayRefusals > 0) {
        throw new Error(
          `a group still offers its own refusal (${strayRefusals}); that is the duplication`,
        )
      }
      // The last button row must be reachable: the floating pill bar sits over
      // the bottom of the pane, so the row has to clear it once scrolled into
      // view. Scrolled first, because a long panel legitimately starts below the
      // fold -- what matters is that nothing is permanently covered.
      //
      // Both row types count: the summary row is now the lowest one, so looking
      // only at the per-group rows would measure a row that is not last and pass
      // while the real one stays covered.
      const lastRow = page
        .locator('.pulse-review-actions, .pulse-review-decide')
        .last()
      await lastRow.scrollIntoViewIfNeeded()
      await page.waitForTimeout(300)
      const clearance = await page.evaluate(() => {
        const bar = document.querySelector('.file-floating-bottom-bar')
        const rows = [
          ...document.querySelectorAll('.pulse-review-actions, .pulse-review-decide'),
        ]
        const last = rows[rows.length - 1]
        if (!bar || !last) return null
        return last.getBoundingClientRect().bottom <= bar.getBoundingClientRect().top
      })
      if (clearance === false) {
        throw new Error('the last action row cannot be scrolled clear of the floating bar')
      }
      const divider = await page.locator('.pulse-document-divider').count()
      if (divider !== 1) {
        throw new Error('the divider between the panel and the document is missing')
      }

      // --- An accepted half fades to gold instead of vanishing -------------
      // The button used to disappear the moment it was clicked, which left no
      // trace of the click and put the next button under the cursor. Now it
      // stays put as a non-interactive record.
      //
      // This really writes to the repository, so it can only pass against a
      // document the guarded write path will accept. A fixture document that is
      // not committed is refused with a 409 on purpose -- the original would not
      // be preserved through Git -- and that is reported as a skip rather than
      // hidden, because "no gold record" then has a known cause instead of
      // looking like a broken button.
      const tagBtn = page
        .locator('.pulse-review-actions button.pulse-action--write', {
          hasText: /write \d+ tags?/i,
        })
        .first()
      if (await tagBtn.count()) {
        const goldBefore = await page.locator('.pulse-accepted').count()
        await tagBtn.click()
        await page.waitForTimeout(1200)

        const goldAfter = await page.locator('.pulse-accepted').count()
        console.log('gold records before / after accept:', goldBefore, goldAfter)
        if (goldAfter <= goldBefore) {
          // A refusal leaves the panel untouched, which is the one outcome that
          // is not a UI defect. Anything else that adds no gold record is.
          const alert = await page
            .locator('[role="alert"], .error, .banner-error')
            .first()
            .textContent()
            .catch(() => null)
          console.log('accept did not settle; on-screen message:', alert)
          if (!/untracked|preserved through git|commit/i.test(alert ?? '')) {
            throw new Error(
              'accepting tags left no gold "accepted" record where the button was',
            )
          }
          console.log('note: the write was refused (document not committed); fade not exercised')
        } else {
          // The record is a span, not a disabled button: a disabled control still
          // looks pressable and would invite a second click that answers 409.
          const stillClickable = await page.evaluate(() =>
            [...document.querySelectorAll('.pulse-accepted')].some((el) =>
              el.matches('button, a, [role="button"]'),
            ),
          )
          if (stillClickable) {
            throw new Error('the accepted record is still an interactive control')
          }
          await page.screenshot({ path: 'screenshots/pulse-accepted.png' })
          await checkSettledThemes(page)
        }
      } else {
        // Already settled by an earlier run: exercise the same guarantee on the
        // record that is already there, so the theme check still runs.
        console.log('note: tags already written, checking the existing record instead')
        if (!(await page.locator('.pulse-accepted').count())) {
          throw new Error(
            'there is no write button and no gold record for a settled half',
          )
        }
        const stillClickable = await page.evaluate(() =>
          [...document.querySelectorAll('.pulse-accepted')].some((el) =>
            el.matches('button, a, [role="button"]'),
          ),
        )
        if (stillClickable) {
          throw new Error('the accepted record is still an interactive control')
        }
        await checkSettledThemes(page)
      }
      // The panel must sit above the document, not replace it.
      const order = await page.evaluate(() => {
        const p = document.querySelector('.pulse-review')
        const b = document.querySelector('.file-document-body')
        if (!p || !b) return null
        return p.getBoundingClientRect().top < b.getBoundingClientRect().top
      })
      if (order === false) {
        throw new Error('the review panel is rendered below the document')
      }
      await page.screenshot({ path: 'screenshots/pulse-review.png' })
    } else {
      console.log('note: no pulse items in this run, panel not rendered')
    }
  } else {
    console.log('note: pulse section has no items, panel not rendered')
  }
}

// A 409 is the guarded write path turning a document down, which is a correct
// answer rather than a failure -- on a repository whose documents are not
// committed yet, that is the only answer it can give. The browser logs it as a
// console error all the same, so it is filtered here; treating it as a defect
// made this smoke fail on the very state most developers are in, and the
// message shown next to the button is asserted separately above.
const unexpected = errors.filter((e) => !/\b409\b/.test(e))
if (unexpected.length) throw new Error(`console/page errors:\n${unexpected.join('\n')}`)
console.log('OK: pulse review is in the reading pane with separate tags/connections accepts')
await browser.close()
