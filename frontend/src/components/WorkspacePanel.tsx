import { useState } from 'react'
import type { DocNode, Repository, Workspace } from '../api/client'

/** Human labels for the evidence categories, so claims are never ambiguous. */
export const EVIDENCE_LABELS: Record<string, { label: string; cls: string }> = {
  verified_implementation: { label: 'verified in code', cls: 'ok' },
  explicit_decision: { label: 'recorded decision', cls: 'accent' },
  documented_intention: { label: 'documented intent', cls: 'accent' },
  ai_interpretation: { label: 'AI interpretation', cls: 'warn' },
  uncertainty: { label: 'unresolved', cls: 'warn' },
}

interface TreeProps {
  nodes: DocNode[]
  selected: string | null
  onSelect: (node: DocNode) => void
}

function TreeBranch({ nodes, selected, onSelect, depth = 0 }: TreeProps & { depth?: number }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  return (
    <>
      {nodes.map((node) => {
        const key = node.path
        const isOpen = !collapsed[key]
        return (
          <div className="tree-node" key={key}>
            <div
              className={`tree-row ${selected === node.path ? 'selected' : ''}`}
              style={{ paddingLeft: 6 + depth * 12 }}
              onClick={() => {
                if (node.is_dir) setCollapsed({ ...collapsed, [key]: !collapsed[key] })
                else onSelect(node)
              }}
              title={node.path}
            >
              <span className="tree-caret">{node.is_dir ? (isOpen ? '▾' : '▸') : ''}</span>
              <span className="tree-icon">{node.is_dir ? '▫' : '·'}</span>
              <span className="tree-name">{node.name}</span>
            </div>
            {node.is_dir && isOpen && (
              <TreeBranch
                nodes={node.children}
                selected={selected}
                onSelect={onSelect}
                depth={depth + 1}
              />
            )}
          </div>
        )
      })}
    </>
  )
}

export function WorkspacePanel({
  workspace,
  repository,
  tree,
  selectedPath,
  onSelectDocument,
}: {
  workspace: Workspace
  repository: Repository | null
  tree: DocNode | null
  selectedPath: string | null
  onSelectDocument: (path: string) => void
}) {
  const docs = workspace.repositories.filter((r) => r.kind === 'documentation')
  const sources = workspace.repositories.filter((r) => r.kind === 'source')

  return (
    <>
      <div className="section">
        <h3>Workspace</h3>
        <div className="list-title">{workspace.name}</div>
        <div className="list-sub">
          {workspace.repositories.length} repositor{workspace.repositories.length === 1 ? 'y' : 'ies'}
        </div>
      </div>

      {docs.map((repo) => (
        <div className="section" key={repo.id}>
          <h3>
            {repo.name}
            {repo.writable ? <span className="tag ok" style={{ marginLeft: 6 }}>writable</span> : null}
          </h3>
          {repo.head_revision ? (
            <div className="list-sub mono">{repo.head_revision.slice(0, 8)}</div>
          ) : null}
          {repository?.id === repo.id && tree ? (
            <div style={{ marginTop: 6 }}>
              {tree.children.length === 0 ? (
                <div className="faint" style={{ fontSize: 12, padding: '4px 6px' }}>
                  No documents.
                </div>
              ) : (
                <TreeBranch
                  nodes={tree.children}
                  selected={selectedPath}
                  onSelect={(node) => onSelectDocument(node.path)}
                />
              )}
            </div>
          ) : null}
        </div>
      ))}

      {sources.length > 0 && (
        <div className="section">
          <h3>Source (read-only)</h3>
          {sources.map((repo) => (
            <div key={repo.id} className="list-sub" style={{ padding: '2px 0' }}>
              {repo.name}
              {repo.current_branch ? ` · ${repo.current_branch}` : ''}
            </div>
          ))}
        </div>
      )}
    </>
  )
}
