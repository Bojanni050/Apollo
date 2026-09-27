import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type EmbeddingModel,
  type LocalRuntime,
  type ModelPullStatus,
} from '../api/client'

/** How often a running download is polled. */
const POLL_MS = 1000

function formatBytes(bytes: number | null): string {
  if (bytes === null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}

interface Props {
  /** Called after a model finished downloading, so the host can re-read state. */
  onInstalled?: () => void
}

export function EmbeddingModelsPanel({ onInstalled }: Props) {
  const [runtime, setRuntime] = useState<string | null>(null)
  const [runtimeLabel, setRuntimeLabel] = useState('')
  const [runtimeAvailable, setRuntimeAvailable] = useState<boolean | null>(null)
  const [runtimeMessage, setRuntimeMessage] = useState<string | null>(null)
  const [runtimeAddress, setRuntimeAddress] = useState('')
  const [runtimes, setRuntimes] = useState<LocalRuntime[]>([])
  const [models, setModels] = useState<EmbeddingModel[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // model name -> live progress. Keyed by name so two models are tracked
  // independently even though only one downloads at a time.
  const [pulls, setPulls] = useState<Record<string, ModelPullStatus>>({})
  const pollRef = useRef<number | null>(null)

  const load = useCallback(async (which?: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.getEmbeddingModels(which)
      setRuntime(res.runtime)
      setRuntimeLabel(res.runtime_label)
      setRuntimeAvailable(res.runtime_available)
      setRuntimeMessage(res.runtime_message)
      setRuntimeAddress(res.runtime_address)
      setRuntimes(res.runtimes)
      setModels(res.models)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read the model catalog.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearTimeout(pollRef.current)
      pollRef.current = null
    }
  }, [])

  // Never leave a timer running after the panel goes away: a poll resolving
  // into an unmounted component is a React warning at best.
  useEffect(() => stopPolling, [stopPolling])

  /** Poll the running model until it settles, then refresh the catalog. */
  const watch = useCallback(
    (model: string, forRuntime: string) => {
      const tick = async () => {
        try {
          const status = await api.getModelPullStatus(model, forRuntime)
          setPulls((prev) => ({ ...prev, [model]: status }))
          if (status.status === 'downloading' || status.status === 'starting') {
            pollRef.current = window.setTimeout(tick, POLL_MS)
            return
          }
          // Settled: the catalog's "installed" flag is now stale.
          void load(forRuntime)
          onInstalled?.()
        } catch {
          // A transient poll failure must not spin forever; stop and let the
          // operator press Download again.
          setError('Lost contact with the backend while downloading.')
        }
      }
      void tick()
    },
    [load, onInstalled],
  )

  const startDownload = async (model: string) => {
    if (!runtime) return
    setError(null)
    try {
      const status = await api.pullEmbeddingModel(model, runtime)
      setPulls((prev) => ({ ...prev, [model]: status }))
      if (status.status === 'downloading' || status.status === 'starting') {
        watch(model, runtime)
      } else {
        void load(runtime)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start the download.')
    }
  }

  const renderModel = (m: EmbeddingModel) => {
    const pull = pulls[m.name]
    const running = pull?.status === 'downloading' || pull?.status === 'starting'
    return (
      <div key={m.name} className="embed-model-row">
        <div className="embed-model-main">
          <div className="embed-model-title">
            <strong>{m.label}</strong>
            {m.in_use && <span className="object-card-pill">in use</span>}
            {m.installed && <span className="object-card-pill">installed</span>}
            {m.dimension != null && (
              <span className="faint" style={{ fontSize: 10.5 }}>{m.dimension}d</span>
            )}
            {/* A community conversion is labelled as one. Presenting it as
                the vendor's weights would make it look interchangeable with
                something nobody has verified. */}
            {m.official === false && (
              <span className="object-card-pill warn" title={m.source ?? undefined}>
                unofficial build
              </span>
            )}
          </div>
          <div className="hint">{m.note}</div>
          {m.official === false && m.source && (
            <div className="hint" style={{ color: 'var(--warn, #a60)' }}>{m.source}</div>
          )}

          {running && pull && (
            <div style={{ marginTop: 6 }}>
              <div className="embed-progress-track">
                {pull.percent != null ? (
                  <div className="embed-progress-fill" style={{ width: `${pull.percent}%` }} />
                ) : (
                  // Ollama's manifest and checksum phases report no byte
                  // counts. An indeterminate bar is honest here; a fake 0%
                  // would look permanently stuck.
                  <div className="embed-progress-fill indeterminate" />
                )}
              </div>
              <div className="hint" style={{ marginTop: 3 }}>
                {pull.message ?? 'Preparing…'}
                {pull.percent != null
                  ? ` · ${pull.percent}% (${formatBytes(pull.completed_bytes)} / ${formatBytes(pull.total_bytes)})`
                  : pull.total_bytes
                    ? ` · ${formatBytes(pull.completed_bytes)} of ${formatBytes(pull.total_bytes)}`
                    : ''}
              </div>
            </div>
          )}

          {pull?.status === 'completed' && (
            <div className="hint" style={{ marginTop: 6, color: 'var(--ok, #0a7)' }}>
              {runtime === 'llamacpp' ? (
                <>
                  Downloaded to <code>{runtimeAddress}</code>. Start{' '}
                  <code>llama-server -m &lt;file&gt; --embedding</code>, set{' '}
                  <code>EMBEDDING_API_BASE_URL</code> to your llama-server address, restart the
                  backend and re-index — the stored vectors came from the previous provider and
                  are not comparable.
                </>
              ) : (
                <>
                  Downloaded. Set{' '}
                  <code>EMBEDDING_API_BASE_URL=http://127.0.0.1:11434/v1</code> and restart the
                  backend, then re-index — the stored vectors came from the previous provider and
                  are not comparable.
                </>
              )}
            </div>
          )}
          {pull?.status === 'failed' && (
            <div className="picker-error" style={{ marginTop: 6 }}>
              {pull.message ?? 'The download failed.'}
            </div>
          )}
        </div>

        <div className="embed-model-action">
          {!m.downloadable ? (
            <span className="hint" style={{ textAlign: 'right' }}>
              Not available on
              <br />
              {runtimeLabel}.
            </span>
          ) : m.installed && !running ? (
            <span className="hint">On disk</span>
          ) : (
            <button
              type="button"
              className="btn primary text-sm"
              onClick={() => void startDownload(m.name)}
              disabled={running || runtimeAvailable === false}
              title={`Download to ${runtimeLabel}`}
            >
              {running ? 'Downloading…' : m.installed ? 'Re-download' : 'Download'}
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="embed-models">
      <div className="form-group" style={{ marginBottom: 0 }}>
        <label className="form-label">Recommended models</label>
        <p className="hint" style={{ marginTop: 2 }}>
          Which runtime manages the weights. Both serve the OpenAI embeddings API; they differ in
          how models are fetched and what they can fetch.
        </p>
        <div className="embed-runtime-picker">
          {runtimes.map((r) => (
            <button
              key={r.id}
              type="button"
              className={`embed-runtime-chip ${runtime === r.id ? 'active' : ''}`}
              onClick={() => void load(r.id)}
              title={r.message ?? r.address}
            >
              {r.label}
              {r.active && <span className="faint"> · configured</span>}
              {!r.available && <span className="faint"> · not running</span>}
            </button>
          ))}
        </div>
      </div>

      {runtimeAvailable === false && (
        <div className="picker-error" style={{ marginTop: 8 }}>
          <strong>{runtimeLabel} is not available.</strong> {runtimeMessage}
          <div className="hint" style={{ marginTop: 4 }}>
            {runtime === 'ollama' ? (
              <>
                Install and start <strong>Ollama</strong>, then press Retry. Tried:{' '}
              </>
            ) : (
              <>Check the models directory, then press Retry. Tried: </>
            )}
            <code>{runtimeAddress}</code>
          </div>
        </div>
      )}

      {error && <div className="picker-error" style={{ marginTop: 8 }}>{error}</div>}

      <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
        <button type="button" className="btn text-sm" onClick={() => void load()} disabled={loading}>
          {loading ? 'Checking…' : 'Retry'}
        </button>
      </div>

      <div style={{ marginTop: 4 }}>
        {models.length === 0 && !loading ? (
          <div className="picker-empty">No models in the catalog.</div>
        ) : (
          models.map(renderModel)
        )}
      </div>
    </div>
  )
}
