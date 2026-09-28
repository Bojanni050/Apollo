import { useEffect, useMemo, useState, type FormEvent } from 'react'
import type {
  Conversation,
  ConversationDetail,
  DocumentLinks,
  Group,
  GroupDocument,
  InventoryRun,
  Mode,
  Proposal,
  PulseRun,
  Repository,
  Signal,
} from '../api/client'
import { renderDiff } from '../markdown'

interface Props {
  isOpen: boolean
  onClose: () => void
  repository: Repository | null
  documentPath: string | null
  documentMarkdown: string | null
  inventoryRun: InventoryRun | null
  proposals: Proposal[]
  conversations: Conversation[]
  activeConversation: ConversationDetail | null
  // The active mode when no conversation exists yet: the chat creates one on
  // the first message, and the mode chosen in the meantime is carried over.
  pendingMode: Mode
  onSendMessage: (text: string) => Promise<void>
  onModeChange: (mode: Mode) => Promise<void>
  sending: boolean
  busy: boolean
  onAcceptProposal: (id: number) => void
  onRejectProposal: (id: number) => void
  onApplyInventoryAll: () => void
  onApplyInventoryItem: (id: number) => void
  onSkipInventoryItem: (id: number) => void
  pulseRun: PulseRun | null
  onAcceptPulseAll: () => void
  /** Open the document a suggestion is about, so the reader can judge it there. */
  onOpenPulseItem: (filePath: string) => void
  /** Follow a link out of the panel into the reading pane. */
  onOpenDocument: (path: string) => void
  /**
   * The document's real Markdown links, read from the repository.
   *
   * Null while loading or when the read failed, which is different from "this
   * document has no links": the panel says so rather than showing an empty
   * list, because a document with no links and a document whose links could not
   * be read are not the same claim.
   */
  documentLinks: DocumentLinks | null
  onOpenAiChat: () => void
  /** Ask Delphi Pulse to review the open file. Optional: absent when unavailable. */
  onRunPulse?: () => void
  /**
   * Bump to make the panel switch to the chat tab.
   *
   * The chat tab lives inside this component, so a button outside it (the
   * reading pane's chat bubble) cannot switch tabs directly. Passing a counter
   * rather than a boolean is deliberate: opening the panel twice must not look
   * like a second request, and a boolean set to `true` twice does not re-fire.
   */
  chatNonce?: number
  /**
   * The groups the open document sits in. Null while the answer is still being
   * read, which the panel words apart from "it is in no group": not knowing and
   * knowing there is nothing are different claims.
   */
  documentGroups?: Group[] | null
  /** The group the board has selected, if any. It takes over the panel's first tab. */
  selectedGroup?: Group | null
  groupMembers?: GroupDocument[]
  groupSignals?: Signal[]
  /** Show a group's details in the panel. */
  onOpenGroup?: (groupId: number) => void
  /**
   * Open a document by its own repository.
   *
   * Separate from onOpenDocument because a group member is identified by the
   * pair: a member can sit in the inbox while the panel is looking at the
   * documentation repository, and opening it against the wrong one shows the
   * reader a different file with the same name.
   */
  onOpenDocumentIn?: (repositoryId: number, path: string) => void
}

type Tab = 'related' | 'chat' | 'proposals'

/* What each mode actually does, in one sentence.

   The backend prompts are explicit that NO mode writes anything: Apply says
   "You do not apply them yourself -- the human accepts each one". So a chip
   labelled "apply" promises the one thing the system never does, and that is
   what "apply what?" is really asking. The labels below name the behaviour, and
   `propose` replaces `apply` on screen so nothing here overstates what a click
   will do. The value sent to the API is unchanged. */
const MODES: { value: Mode; label: string; blurb: string }[] = [
  {
    value: 'explore',
    label: 'Explore',
    blurb: 'Discuss ideas freely. Nothing is written and nothing is proposed.',
  },
  {
    value: 'investigate',
    label: 'Investigate',
    blurb:
      'Answer from evidence: reads documents and code, cites what it finds. Writes nothing.',
  },
  {
    value: 'apply',
    label: 'Propose',
    blurb: 'Drafts change proposals for you to review. Never applies them itself.',
  },
]

/* Filling the empty column with questions that work on any document beats a
   sparkle and a sentence. Clicking one only fills the box -- it does not send,
   because an unedited question sent on a single click is a decision made on the
   reader's behalf. */
const SUGGESTIONS = [
  'What does this document assume that it never states?',
  'Where does this contradict the rest of the architecture?',
  'Summarise the decision this file records, and its consequences.',
  'What is still undecided here?',
]

function fileNameOf(path: string): string {
  return path.split('/').pop() ?? path
}

/**
 * The group, in the panel: who thought it up, why, what is in it, and what
 * Delphi found in it.
 *
 * The board already lists the members, so this is not a second copy of the
 * board -- it is the board's answer to the two questions the board cannot ask:
 * why is this document in this group, and what is wrong with it. The reason and
 * the findings are the parts that exist only here, and both carry their
 * evidence: the description is the reason the group was proposed with, and every
 * finding shows the `why` it was recorded with.
 */
function GroupPanel({
  group,
  members,
  signals,
  onOpenDocumentIn,
}: {
  group: Group
  members: GroupDocument[]
  signals: Signal[]
  onOpenDocumentIn: (repositoryId: number, path: string) => void
}) {
  return (
    <>
      <div className="context-section-label">THIS GROUP</div>
      <div className="context-current-doc">
        <span className="context-card-title">{group.name}</span>
        <div className="context-doc-tags">
          {/* Who made it, said out loud. A group Delphi proposed and one the
              reader built are otherwise identical on screen, and the reader has
              to be able to tell which is which before trusting it. */}
          <span className="context-doc-tag">
            {group.source === 'ai'
              ? 'Delphi proposed this'
              : 'You made this group'}
          </span>
          {group.is_archive && <span className="context-doc-tag">Archive</span>}
        </div>
      </div>
      {group.description && <p className="context-card-body">{group.description}</p>}

      <div className="context-section-label" style={{ marginTop: 18 }}>
        DOCUMENTS IN THIS GROUP
      </div>
      {members.length === 0 ? (
        <div className="context-related-empty">
          Nothing in this group. Drag a document onto the group on the board to
          put one there; nothing moves on disk either way.
        </div>
      ) : (
        <ul className="context-member-list">
          {members.map((doc) => (
            <li key={`${doc.repository_id}:${doc.path}`}>
              <button
                type="button"
                className="context-object-card context-object-card--click"
                onClick={() => onOpenDocumentIn(doc.repository_id, doc.path)}
                title={doc.path}
              >
                <span className="context-card-title">{fileNameOf(doc.path)}</span>
                <span className="context-link-path">{doc.path}</span>
                {/* The same promise the board makes, restated where the decision
                    is made: moving it yourself is what the next analysis has to
                    respect. */}
                {doc.placed_by === 'ai' && (
                  <span className="context-link-origin">
                    Delphi put this here — move it yourself and it stays
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="context-section-label" style={{ marginTop: 18 }}>
        FINDINGS
      </div>
      {signals.length === 0 ? (
        <div className="context-related-empty">
          Nothing Delphi found in these documents that still needs a decision.
        </div>
      ) : (
        <ul className="context-signal-list">
          {signals.map((signal) => (
            <li key={signal.id} className="context-signal-item">
              <button
                type="button"
                className="context-signal-head"
                onClick={() =>
                  onOpenDocumentIn(signal.repository_id, signal.file_path)
                }
                title={`Open ${signal.file_path}`}
              >
                <span className={`signal-kind signal-kind--${signal.kind}`}>
                  {signal.label}
                </span>
                <span className="context-signal-path">{signal.file_path}</span>
              </button>
              {/* The evidence, in full. A finding shown without its reason is a
                  claim the reader has to take on trust, which is the one thing
                  Delphi is not allowed to ask for. */}
              <p className="context-card-body">{signal.why}</p>
              {signal.reference && (
                <span className="context-link-origin">
                  About {signal.reference}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

export function ContextSidebar({
  isOpen,
  onClose,
  repository: _repository,
  documentPath,
  documentMarkdown: _documentMarkdown,
  inventoryRun,
  proposals,
  conversations: _conversations,
  activeConversation,
  pendingMode,
  onSendMessage,
  onModeChange,
  sending,
  busy,
  onAcceptProposal,
  onRejectProposal,
  onApplyInventoryAll,
  onApplyInventoryItem,
  onSkipInventoryItem,
  pulseRun,
  onAcceptPulseAll,
  onOpenPulseItem,
  onOpenDocument,
  documentLinks,
  onOpenAiChat,
  onRunPulse,
  chatNonce = 0,
  documentGroups = null,
  selectedGroup = null,
  groupMembers = [],
  groupSignals = [],
  onOpenGroup = () => {},
  onOpenDocumentIn = () => {},
}: Props) {
  const [tab, setTab] = useState<Tab>('related')
  const [chatInput, setChatInput] = useState('')

  // A chat request from outside the panel lands here. Depends on the counter
  // rather than a flag, so every press counts, including two in a row.
  useEffect(() => {
    if (chatNonce > 0) setTab('chat')
  }, [chatNonce])

  /* Everything below is derived from the document in the reading pane, so the
     panel describes what is on screen rather than the workspace as a whole.

     Two sources, and the difference matters:

     - The REAL links, read from the repository. These are what the author
       wrote: `[the ADR](../decisions/x.md)`. They are the load-bearing ones, and
       the panel led with them.
     - Delphi Pulse's connections. These are the model's *inference* that two
       documents are related, which is useful precisely because nobody wrote it
       down -- but it is a proposal, not a fact, and it is labelled as one.

     The panel used to show only the second kind under a heading that said
     "linked articles", which read as if the author had linked them. It is now
     the first thing listed, with the inferred ones clearly separate.

     A link is matched on the path with and without its extension, because a
     connection may name "architecture/architecture.md" while the item that owns
     it is filed under the same name with a different suffix.

     Both directions are collected into ONE list, each marked with where the
     arrow points. They were separate sections before, which meant every inbound
     link appeared twice: once as a link and again under "mentions this file". A
     graph read in one direction is easier to follow than the same edges split
     across two headings. */
  const currentItem = pulseRun?.items.find((i) => i.file_path === documentPath)

  const stripExt = (p: string) => p.replace(/\.[^./]+$/, '')

  const links = useMemo(() => {
    if (!documentPath) return []
    const stem = stripExt(documentPath)
    const out: {
      path: string
      relation: string
      why: string | null
      direction: 'out' | 'in'
      /** 'written' = the author linked it. 'inferred' = Delphi Pulse proposed it. */
      source: 'written' | 'inferred'
    }[] = []

    // Outbound: what this document actually links to.
    for (const l of documentLinks?.outbound ?? []) {
      out.push({
        path: l.path,
        relation: 'links to',
        why: l.text || null,
        direction: 'out',
        source: 'written',
      })
    }

    // Inbound: what links to this document. The author wrote those, in the
    // other file.
    for (const l of documentLinks?.inbound ?? []) {
      out.push({
        path: l.path,
        relation: 'linked from',
        why: l.text || null,
        direction: 'in',
        source: 'written',
      })
    }

    // Pulse's inferred connections, outbound.
    for (const c of currentItem?.connections ?? []) {
      out.push({ path: c.path, relation: c.relation, why: c.why, direction: 'out', source: 'inferred' })
    }

    // Pulse's inferred connections, inbound.
    for (const item of pulseRun?.items ?? []) {
      if (item.file_path === documentPath) continue
      for (const c of item.connections) {
        if (c.path === documentPath || stripExt(c.path) === stem) {
          out.push({
            path: item.file_path,
            relation: c.relation,
            why: c.why,
            direction: 'in',
            source: 'inferred',
          })
        }
      }
      // An item that merely shares a folder is not a link, so it is not listed:
      // a panel full of same-directory files is noise, not context.
    }

    // Two documents may link each other, which would list each twice. One row
    // per document. A written link wins over an inferred one for the same
    // document: if the author linked it, that is the stronger statement, and
    // listing it twice would imply two independent findings.
    const byPath = new Map<string, (typeof out)[number]>()
    for (const l of out) {
      const existing = byPath.get(l.path)
      if (!existing) {
        byPath.set(l.path, l)
      } else if (existing.source === 'inferred' && l.source === 'written') {
        byPath.set(l.path, l)
      }
    }
    // Written links first, then by path: a stable, predictable order rather
    // than whatever order the two sources happened to arrive in.
    return [...byPath.values()].sort((a, b) => {
      if (a.source !== b.source) return a.source === 'written' ? -1 : 1
      return a.path.localeCompare(b.path)
    })
  }, [documentPath, documentLinks, pulseRun, currentItem])

  /* Links out of the repository, kept apart from `links` on purpose. They are
     written by the author, so they belong to the document -- but they are not
     navigable, and folding them into the same list would put rows in it that
     cannot be opened. Separate list, separate heading, separate treatment.

     Deduped on the target, because the same URL cited under three different
     labels is one reference, not three. */
  const externalLinks = useMemo(() => {
    const byTarget = new Map<string, { target: string; text: string }>()
    for (const ref of documentLinks?.external ?? []) {
      if (!byTarget.has(ref.target)) byTarget.set(ref.target, ref)
    }
    return [...byTarget.values()].sort((a, b) => a.target.localeCompare(b.target))
  }, [documentLinks])

  const currentTags = currentItem?.tags ?? []

  const handleSend = async (e: FormEvent) => {
    e.preventDefault()
    if (!chatInput.trim() || sending) return
    const text = chatInput.trim()
    setChatInput('')
    await onSendMessage(text)
  }

  return (
    <aside className={`context-sidebar ${isOpen ? 'open' : 'closed'}`}>
      <div className="context-sidebar-content">
        {/* 1. Header Bar matching screenshot */}
        <div className="context-header">
          <div className="context-title-block">
            <span className="context-title-hash">#</span>
            <h3 className="context-title">AI weave</h3>
          </div>

          <div className="context-header-actions">
            <button
              type="button"
              className="context-tab-mini"
              onClick={() => setTab(tab === 'chat' ? 'related' : 'chat')}
              title={tab === 'chat' ? 'Back to this document' : 'Open AI Chat'}
            >
              {tab === 'chat' ? 'This document' : 'Chat'}
            </button>
            <button
              type="button"
              className="context-close-btn"
              onClick={onClose}
              title="Collapse context sidebar"
            >
              ✕
            </button>
          </div>
        </div>

        {/* 2. Sub Tabs */}
        <div className="context-nav-tabs">
          <button
            type="button"
            className={`context-tab-chip ${tab === 'related' ? 'active' : ''}`}
            onClick={() => setTab('related')}
          >
            {selectedGroup ? 'This group' : 'This document'}
          </button>
          <button
            type="button"
            className={`context-tab-chip ${tab === 'chat' ? 'active' : ''}`}
            onClick={() => setTab('chat')}
          >
            AI Chat {activeConversation ? `(${activeConversation.messages.length})` : ''}
          </button>
          <button
            type="button"
            className={`context-tab-chip ${tab === 'proposals' ? 'active' : ''}`}
            onClick={() => setTab('proposals')}
          >
            Proposals {proposals.length > 0 ? `(${proposals.length})` : ''}
          </button>
        </div>

        {/* 3. Tab: This document
            The tab used to list every applied item in the workspace, so it looked
            identical no matter which document was open -- it could not answer
            "what does this file link to?". It now describes the file in the
            reading pane, and says so plainly when nothing is open yet. */}
        {tab === 'related' && (
          <div className="context-scroll-body">
            {selectedGroup ? (
              /* The panel describes one subject at a time. A group that is
                 selected replaces the document rather than stacking below it:
                 a panel showing a group's members above an unrelated document is
                 two answers to a question the reader did not ask. */
              <GroupPanel
                group={selectedGroup}
                members={groupMembers}
                signals={groupSignals}
                onOpenDocumentIn={onOpenDocumentIn}
              />
            ) : !documentPath ? (
              <>
                <div className="context-section-label">THIS DOCUMENT</div>
                <div className="context-related-empty">
                  Open a document and this panel fills with the articles it
                  connects to, and the things you can do with it.
                </div>
              </>
            ) : (
              <>
                <div className="context-section-label">THIS DOCUMENT</div>
                <div className="context-current-doc">
                  <span className="context-card-title">{documentPath}</span>
                  {currentTags.length > 0 && (
                    <div className="context-doc-tags">
                      {currentTags.map((t) => (
                        <span key={t} className="context-doc-tag">
                          #{t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Where this document sits, which is the question a reader has
                    when they open a file Delphi just grouped. It answers "why is
                    this here" in one click, and it states the origin of each
                    group, because a group the reader did not make should never
                    be mistaken for one that was always there. */}
                <div className="context-section-label" style={{ marginTop: 18 }}>
                  BELONGS TO
                </div>
                {documentGroups === null ? (
                  <div className="context-related-empty">
                    Reading which groups this file sits in…
                  </div>
                ) : documentGroups.length === 0 ? (
                  <div className="context-related-empty">
                    Not in any group. Run the analysis to have Delphi propose some,
                    or drag this file onto a group on the board.
                  </div>
                ) : (
                  <ul className="context-member-list">
                    {documentGroups.map((group) => (
                      <li key={group.id}>
                        <button
                          type="button"
                          className="context-object-card context-object-card--click"
                          onClick={() => onOpenGroup(group.id)}
                          title={`Show ${group.name} in this panel`}
                        >
                          <span className="context-card-title">{group.name}</span>
                          <span className="context-link-origin">
                            {group.source === 'ai'
                              ? 'Delphi proposed this group'
                              : 'Your group'}
                            {group.is_archive ? ' · archive' : ''}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                {/* Actions, first: what you can DO with this file. The reading
                    pane's own toolbar holds these too, and this is the same set
                    with words instead of icons, so the panel is useful to someone
                    who does not want to hunt the toolbar. */}
                <div className="context-section-label" style={{ marginTop: 18 }}>
                  ACTIONS
                </div>
                <div className="context-actions">
                  <button
                    type="button"
                    className="context-action"
                    onClick={onOpenAiChat}
                    disabled={!documentPath}
                  >
                    <span className="context-action-label">Ask about this document</span>
                    {/* The hint said "name this one if you want it to focus
                        here", which meant the button did not do that by itself.
                        It now sends the path with the next message, so the model
                        is told which file "this" is and reads it. */}
                    <span className="context-action-hint">
                      Opens AI Chat with this file named, so &ldquo;this
                      document&rdquo; means this one. It reads the file itself
                      rather than working from a copy.
                    </span>
                  </button>
                  <button
                    type="button"
                    className="context-action"
                    onClick={onRunPulse}
                    disabled={busy || !onRunPulse}
                  >
                    <span className="context-action-label">Suggest improvements</span>
                    <span className="context-action-hint">
                      Delphi Pulse reviews this file and offers tags and links
                    </span>
                  </button>
                </div>

                {/* The heading names what the list actually is. "Linked articles"
                    read as though every row were a link the author wrote, when
                    most of them were Delphi Pulse's inference. Splitting the two
                    is the fix, not a relabelling: an inferred connection is a
                    proposal about a relationship, and a written link is a fact
                    about the text. */}
                <div className="context-section-label" style={{ marginTop: 18 }}>
                  LINKS
                </div>
                {links.length === 0 ? (
                  <div className="context-related-empty">
                    {documentLinks === null
                      ? 'Links could not be read for this file.'
                      : 'This file links to nothing, and nothing links to it. Delphi Pulse may still find relationships worth proposing.'}
                  </div>
                ) : (
                  links.map((link) => (
                    <button
                      key={link.path}
                      type="button"
                      className={`context-object-card context-object-card--click context-link-card--${link.source}`}
                      onClick={() => onOpenDocument(link.path)}
                      title={link.why || `Open ${link.path}`}
                    >
                      <div className="context-card-header">
                        <span
                          className={`context-card-badge link ${link.relation}`}
                        >
                          {link.relation}
                        </span>
                        {/* Which way the edge runs, so "extends" is not read as
                            this file extending the other one. */}
                        <span className="context-link-direction">
                          {link.direction === 'out' ? 'this file →' : '→ this file'}
                        </span>
                      </div>
                      <span className="context-card-title context-link-path">
                        {link.path}
                      </span>
                      {link.why && <p className="context-card-body">{link.why}</p>}
                      {/* Says out loud where the row came from, rather than
                          leaving the reader to guess from a colour. */}
                      {link.source === 'inferred' && (
                        <span className="context-link-origin">
                          Delphi Pulse proposed this; the text does not link it
                        </span>
                      )}
                    </button>
                  ))
                )}

                {/* Links that leave the repository.

                    These are real: the author wrote them, and a document that
                    cites an ADR in another repository or a spec online is
                    making a statement about its own context. The API has always
                    returned them; the panel dropped them, so a document whose
                    only links were external claimed to link to nothing.

                    They are not rows you can click, and that is deliberate
                    rather than a gap. Every row above navigates within this
                    workspace, and these do not resolve to anything Apollo can
                    open. Rendering them like the others -- same card, same
                    press affordance -- would promise a destination that does
                    not exist, which is the same overstatement this panel was
                    corrected for. So they are listed as text, labelled as
                    external, and never offered as a hit target. */}
                {externalLinks.length > 0 && (
                  <>
                    <div className="context-section-label" style={{ marginTop: 18 }}>
                      LINKS OUTSIDE THIS REPOSITORY
                    </div>
                    {externalLinks.map((ref) => (
                      <div
                        key={`${ref.target}|${ref.text}`}
                        className="context-external-link"
                        title={ref.target}
                      >
                        <span className="context-card-title context-link-path">
                          {ref.text || ref.target}
                        </span>
                        <span className="context-external-target">{ref.target}</span>
                        <span className="context-link-origin">
                          Outside this repository; Apollo cannot open it
                        </span>
                      </div>
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        )}

        {/* 4. Tab: AI Chat

            The empty state used to be a sparkle and a sentence, floating in
            roughly 550px of nothing between it and the input at the bottom of a
            330px column. That is the "cramped" feeling: a panel that shows very
            little, very small, and says nothing about what the three modes do.

            So the modes state their own behaviour, and the empty space carries
            questions that are actually about the open file. */}
        {tab === 'chat' && (
          <div className="context-chat-body">
            <div className="context-mode-picker">
              <div
                className="context-mode-bar"
                role="radiogroup"
                aria-label="What AI weave should do"
              >
                {MODES.map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    role="radio"
                    aria-checked={
                      (activeConversation?.mode ?? pendingMode) === m.value
                    }
                    className={`context-mode-chip ${
                      (activeConversation?.mode ?? pendingMode) === m.value
                        ? 'active'
                        : ''
                    }`}
                    onClick={() => onModeChange(m.value)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              {/* One line saying what the selected mode will and will not do.
                  Without it the chips are three words, and "apply" in particular
                  implies a write that no mode performs. */}
              <p className="context-mode-blurb">
                {MODES.find(
                  (m) => m.value === (activeConversation?.mode ?? pendingMode),
                )?.blurb}
              </p>
            </div>

            {/* Chat messages */}
            <div className="context-messages-list">
              {(activeConversation?.messages.length ?? 0) === 0 ? (
                <div className="context-chat-empty">
                  <p className="context-chat-empty-lead">
                    {documentPath ? (
                      <>
                        Ask about{' '}
                        <span className="context-chat-empty-doc">
                          {documentPath}
                        </span>
                      </>
                    ) : (
                      'Open a document to ask about it, or start a general question.'
                    )}
                  </p>
                  <div className="context-suggestions">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className="context-suggestion"
                        onClick={() => setChatInput(s)}
                        title="Put this in the box so you can edit it first"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                activeConversation?.messages.map((m) => (
                  <div key={m.id} className={`context-msg ${m.role}`}>
                    <div className="msg-sender">
                      {m.role === 'assistant' ? '✦ AI weave' : 'You'}
                    </div>
                    <div className="msg-content">{m.content}</div>
                  </div>
                ))
              )}
            </div>

            {/* Chat input box */}
            <form className="context-chat-form" onSubmit={handleSend}>
              <input
                type="text"
                className="context-chat-input"
                placeholder="Ask AI weave..."
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                disabled={sending}
              />
              <button
                type="submit"
                className="context-chat-send-btn"
                disabled={sending || !chatInput.trim()}
              >
                {sending ? '…' : '↑'}
              </button>
            </form>
          </div>
        )}

        {/* 5. Tab: Proposals & Inventory */}
        {tab === 'proposals' && (
          <div className="context-scroll-body">
            <div className="context-section-label">AI PROPOSALS ({proposals.length})</div>

            {proposals.length === 0 ? (
              <p className="context-empty-hint">No active proposals for this workspace.</p>
            ) : (
              proposals.map((p) => {
                const target = p.changes?.[0]?.target_path || p.kind
                return (
                  <div key={p.id} className="context-proposal-card">
                    <div className="proposal-title">{p.title}</div>
                    <div className="proposal-path">{target}</div>
                    <div className="proposal-diff">
                      {p.diff ? renderDiff(p.diff) : <span style={{ color: 'var(--text-faint)' }}>{p.reason}</span>}
                    </div>
                    <div className="proposal-actions">
                      <button
                        type="button"
                        className="proposal-btn accept"
                        onClick={() => onAcceptProposal(p.id)}
                        disabled={busy}
                      >
                        Accept
                      </button>
                      <button
                        type="button"
                        className="proposal-btn reject"
                        onClick={() => onRejectProposal(p.id)}
                        disabled={busy}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                )
              })
            )}

            {inventoryRun && (
              <div style={{ marginTop: 20 }}>
                <div className="context-section-label">INVENTORY RUN #{inventoryRun.id}</div>
                <div style={{ display: 'flex', gap: 6, margin: '8px 0 12px' }}>
                  <button
                    type="button"
                    className="proposal-btn accept"
                    onClick={onApplyInventoryAll}
                    disabled={busy}
                  >
                    Apply All Changes
                  </button>
                </div>
                {inventoryRun.items.map((it) => (
                  <div key={it.id} className="context-inventory-item">
                    <div className="item-name">{it.source_path}</div>
                    <div className="item-status">{it.decision}</div>
                    <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                      <button
                        type="button"
                        className="btn text-sm"
                        style={{ fontSize: 11, padding: '2px 6px' }}
                        onClick={() => onApplyInventoryItem(it.id)}
                        disabled={busy || it.decision !== 'pending'}
                      >
                        Apply
                      </button>
                      <button
                        type="button"
                        className="btn text-sm"
                        style={{ fontSize: 11, padding: '2px 6px' }}
                        onClick={() => onSkipInventoryItem(it.id)}
                        disabled={busy || it.decision !== 'pending'}
                      >
                        Skip
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {pulseRun && (() => {
              /* The per-suggestion decision lives in the reading pane, above the
                 document it is about. This block used to offer its own Apply and
                 Skip per line, which meant two places to decide the same thing
                 under two different names ("Apply" here, "Accept" there), and a
                 half-accepted item that this list could only show as "pending".
                 So it no longer decides: it counts, and it navigates. */
              const pending = pulseRun.items.filter((it) => it.decision === 'pending')
              const settled = pulseRun.items.length - pending.length
              return (
                <div className="pulse-run-summary">
                  <div className="context-section-label">
                    AI PULSE RUN #{pulseRun.id}
                  </div>
                  {pulseRun.summary && (
                    <div className="item-status" style={{ margin: '6px 0 8px' }}>
                      {pulseRun.summary}
                    </div>
                  )}
                  <div className="pulse-run-count">
                    {pending.length === 0 ? (
                      <>All {pulseRun.items.length} settled.</>
                    ) : (
                      <>
                        <strong>{pending.length}</strong> to review
                        {settled > 0 && <> · {settled} settled</>}
                      </>
                    )}
                  </div>
                  {pending.length > 0 && (
                    <button
                      type="button"
                      className="proposal-btn accept"
                      onClick={onAcceptPulseAll}
                      disabled={busy}
                    >
                      Accept all remaining
                    </button>
                  )}
                  {pulseRun.items
                    .filter((it) => it.decision === 'pending')
                    .map((it) => (
                      <button
                        key={it.id}
                        type="button"
                        className="pulse-run-item"
                        onClick={() => onOpenPulseItem(it.file_path)}
                        title="Open this document to review the suggestion"
                      >
                        <span className="pulse-run-item-name">{it.file_path}</span>
                        {it.tags.length > 0 && (
                          <span className="pulse-run-item-tags">
                            {it.tags.length} tag{it.tags.length === 1 ? '' : 's'}
                            {it.connections.length > 0 &&
                              ` · ${it.connections.length} connection${
                                it.connections.length === 1 ? '' : 's'
                              }`}
                          </span>
                        )}
                      </button>
                    ))}
                </div>
              )
            })()}
          </div>
        )}
      </div>
    </aside>
  )
}
