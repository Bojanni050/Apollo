/**
 * A deliberately small Markdown renderer.
 *
 * This is not a full CommonMark implementation and is not meant to be one.
 * It covers the constructs that actually appear in architecture documents and
 * nothing more -- adding a full parser would be a dependency this MVP does not
 * need. Text is rendered into React elements, never via innerHTML, so
 * untrusted document content cannot inject markup.
 */
import { createElement, type ReactNode } from 'react'

function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  // Code spans first so their contents are not further transformed.
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let match: RegExpExecArray | null
  let key = 0

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const token = match[0]
    if (token.startsWith('`')) {
      nodes.push(createElement('code', { key: key++ }, token.slice(1, -1)))
    } else if (token.startsWith('**')) {
      nodes.push(createElement('strong', { key: key++ }, token.slice(2, -2)))
    } else if (token.startsWith('[')) {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token)
      if (link) {
        nodes.push(
          createElement('a', { key: key++, href: link[2], target: '_blank', rel: 'noreferrer' }, link[1]),
        )
      }
    } else {
      nodes.push(createElement('em', { key: key++ }, token.slice(1, -1)))
    }
    last = match.index + token.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

/** Strip characters that would be misread as Markdown syntax. */
function escapeCell(value: string): string {
  return value.replace(/\|/g, '\\|')
}

export function renderMarkdown(markdown: string): ReactNode {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const out: ReactNode[] = []
  let key = 0
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Fenced code block
    if (line.trimStart().startsWith('```')) {
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
        body.push(lines[i])
        i++
      }
      i++ // closing fence
      out.push(createElement('pre', { key: key++ }, createElement('code', null, body.join('\n'))))
      continue
    }

    // Heading
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      const level = Math.min(heading[1].length, 6)
      out.push(createElement(`h${level}`, { key: key++ }, inline(heading[2])))
      i++
      continue
    }

    // Horizontal rule
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      out.push(createElement('hr', { key: key++ }))
      i++
      continue
    }

    // Blockquote
    if (line.startsWith('> ')) {
      const body: string[] = []
      while (i < lines.length && lines[i].startsWith('> ')) {
        body.push(lines[i].slice(2))
        i++
      }
      out.push(createElement('blockquote', { key: key++ }, inline(body.join(' '))))
      continue
    }

    // Table
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:-]*-[\s|:-]*$/.test(lines[i + 1])) {
      const cells = (row: string) =>
        row.replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
      const head = cells(line)
      i += 2 // header + separator
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|')) {
        rows.push(cells(lines[i]))
        i++
      }
      out.push(
        createElement(
          'table',
          { key: key++ },
          createElement(
            'thead',
            null,
            createElement('tr', null, head.map((h, n) => createElement('th', { key: n }, inline(h)))),
          ),
          createElement(
            'tbody',
            null,
            rows.map((r, rn) =>
              createElement(
                'tr',
                { key: rn },
                r.map((c, cn) => createElement('td', { key: cn }, inline(escapeCell(c)))),
              ),
            ),
          ),
        ),
      )
      continue
    }

    // Lists
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items: string[] = []
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''))
        i++
      }
      out.push(
        createElement(
          ordered ? 'ol' : 'ul',
          { key: key++ },
          items.map((item, n) => createElement('li', { key: n }, inline(item))),
        ),
      )
      continue
    }

    // Paragraph (blank line separates)
    if (line.trim() === '') {
      i++
      continue
    }
    const para: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^(#{1,6})\s/.test(lines[i]) &&
      !lines[i].trimStart().startsWith('```') &&
      !/^\s*([-*+]|\d+\.)\s+/.test(lines[i]) &&
      !lines[i].startsWith('> ')
    ) {
      para.push(lines[i])
      i++
    }
    out.push(createElement('p', { key: key++ }, inline(para.join(' '))))
  }

  return createElement('div', { className: 'doc-viewer' }, out)
}

/** Colour a unified diff for display. */
export function renderDiff(diff: string): ReactNode {
  return (
    <pre className="diff">
      {diff.split('\n').map((line, n) => {
        let cls = ''
        if (line.startsWith('+') && !line.startsWith('+++')) cls = 'add'
        else if (line.startsWith('-') && !line.startsWith('---')) cls = 'del'
        else if (line.startsWith('@@')) cls = 'hunk'
        return (
          <span key={n} className={cls}>
            {line}
            {'\n'}
          </span>
        )
      })}
    </pre>
  )
}
