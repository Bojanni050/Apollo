import { useEffect, useMemo, useState } from 'react'
import { api, type LlmModelInfo, type LlmSettings } from '../api/client'

interface Props {
  isOpen: boolean
  onClose: () => void
  onSaved?: () => void
}

const CAPABILITY_ICONS: Record<string, { icon: string; label: string }> = {
  vision: { icon: '👁', label: 'Vision (images)' },
  tools: { icon: '🔧', label: 'Tool use / function calling' },
  reasoning: { icon: '🧠', label: 'Reasoning' },
}

function priceLabel(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `$${v < 1 ? v.toString() : v.toFixed(2)}/M`
}

export function SettingsModal({ isOpen, onClose, onSaved }: Props) {
  const [data, setData] = useState<LlmSettings | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [contextTokens, setContextTokens] = useState('')
  const [maxOutputTokens, setMaxOutputTokens] = useState('')
  const [temperature, setTemperature] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  // Model catalogue from the provider's /models endpoint.
  const [models, setModels] = useState<LlmModelInfo[]>([])
  const [modelsError, setModelsError] = useState<string | null>(null)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [modelFilter, setModelFilter] = useState('')

  useEffect(() => {
    if (!isOpen) return
    setLoading(true)
    setError(null)
    setSaved(false)
    setModels([])
    setModelsError(null)
    setDropdownOpen(false)
    api
      .getLlmSettings()
      .then((res) => {
        setData(res)
        setBaseUrl(res.base_url || '')
        setModel(res.model || '')
        setApiKey('')
        setContextTokens(String(res.context_tokens))
        setMaxOutputTokens(String(res.max_output_tokens))
        setTemperature(String(res.temperature))
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load settings.'))
      .finally(() => setLoading(false))
  }, [isOpen])

  const handleFetchModels = async () => {
    setFetchingModels(true)
    setModelsError(null)
    try {
      const res = await api.fetchLlmModels(baseUrl.trim() || undefined, apiKey.trim() || undefined)
      setModels(res.models)
      setModelsError(res.error || null)
      if (res.models.length > 0) {
        setDropdownOpen(true)
        setModelFilter('')
      }
    } catch (err) {
      setModelsError(err instanceof Error ? err.message : 'Fetching models failed.')
    } finally {
      setFetchingModels(false)
    }
  }

  const filteredModels = useMemo(() => {
    const term = modelFilter.trim().toLowerCase()
    if (!term) return models
    return models.filter((m) => m.id.toLowerCase().includes(term))
  }, [models, modelFilter])

  if (!isOpen) return null

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const payload: Record<string, string | number> = {}
      if (baseUrl.trim() !== (data?.base_url || '')) payload.base_url = baseUrl.trim()
      if (model.trim() !== (data?.model || '')) payload.model = model.trim()
      if (apiKey.trim()) payload.api_key = apiKey.trim()
      const ct = parseInt(contextTokens, 10)
      if (!Number.isNaN(ct) && ct !== data?.context_tokens) payload.context_tokens = ct
      const mot = parseInt(maxOutputTokens, 10)
      if (!Number.isNaN(mot) && mot !== data?.max_output_tokens) payload.max_output_tokens = mot
      const t = parseFloat(temperature)
      if (!Number.isNaN(t) && t !== data?.temperature) payload.temperature = t

      const res = await api.updateLlmSettings(payload)
      setData(res)
      setApiKey('')
      setContextTokens(String(res.context_tokens))
      setMaxOutputTokens(String(res.max_output_tokens))
      setTemperature(String(res.temperature))
      setSaved(true)
      onSaved?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Saving failed.')
    } finally {
      setSaving(false)
    }
  }

  const selectedModelInfo = models.find((m) => m.id === model)

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-dialog settings-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-header">
          <div className="modal-title-row">
            <h3>Settings</h3>
            <button className="btn icon-btn" type="button" onClick={onClose} title="Close">
              ✕
            </button>
          </div>
        </div>
        <div className="settings-body">
          {loading ? (
            <div className="picker-empty">Loading settings…</div>
          ) : (
            <>
              <div className="settings-section-title">LLM provider (OpenAI-compatible)</div>
              <p className="settings-hint">
                Used for the architecture chat. Changes are saved to{' '}
                <span className="mono">backend/.env</span> and apply immediately.
              </p>
              <div className="form-group">
                <label className="form-label" htmlFor="llm-base-url">Base URL</label>
                <input
                  id="llm-base-url"
                  className="settings-input"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://api.openai.com/v1"
                  spellCheck={false}
                />
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="llm-api-key">
                  API key{' '}
                  {data?.api_key_configured && (
                    <span className="settings-key-status">configured ✓</span>
                  )}
                </label>
                <input
                  id="llm-api-key"
                  className="settings-input"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={
                    data?.api_key_configured ? 'Leave empty to keep the current key' : 'sk-…'
                  }
                  spellCheck={false}
                />
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="llm-model">Model</label>
                <div className="model-combo">
                  <input
                    id="llm-model"
                    className="settings-input"
                    value={model}
                    onChange={(e) => {
                      setModel(e.target.value)
                      if (models.length > 0) {
                        setDropdownOpen(true)
                        setModelFilter(e.target.value)
                      }
                    }}
                    placeholder="Pick from the list, or type a model id"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    className="btn"
                    onClick={handleFetchModels}
                    disabled={fetchingModels || !baseUrl.trim()}
                    title="Fetch the model list from the provider"
                  >
                    {fetchingModels ? 'Fetching…' : 'Fetch models'}
                  </button>
                </div>
                {dropdownOpen && filteredModels.length > 0 && (
                  <div className="model-dropdown">
                    <div className="model-dropdown-filter">
                      <input
                        className="settings-input"
                        value={modelFilter}
                        onChange={(e) => setModelFilter(e.target.value)}
                        placeholder="Filter models…"
                        spellCheck={false}
                        autoFocus
                      />
                    </div>
                    <div className="model-dropdown-list">
                      {filteredModels.map((m) => (
                        <button
                          key={m.id}
                          type="button"
                          className={`model-dropdown-item ${m.id === model ? 'selected' : ''}`}
                          onClick={() => {
                            setModel(m.id)
                            setDropdownOpen(false)
                          }}
                        >
                          <span className="model-item-id">{m.id}</span>
                          <span className="model-item-meta">
                            <span className="model-item-price" title="USD per 1M tokens, input / output">
                              {priceLabel(m.input_price_per_m)} / {priceLabel(m.output_price_per_m)}
                            </span>
                            <span className="model-item-caps">
                              {m.capabilities.map((c) => (
                                <span key={c} className="model-cap" title={CAPABILITY_ICONS[c]?.label || c}>
                                  {CAPABILITY_ICONS[c]?.icon || '•'}
                                </span>
                              ))}
                            </span>
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {modelsError && <div className="picker-error">{modelsError}</div>}
                {selectedModelInfo && (
                  <div className="model-selected-info">
                    {selectedModelInfo.context_window
                      ? `Context: ${selectedModelInfo.context_window.toLocaleString()} tokens · `
                      : ''}
                    Input {priceLabel(selectedModelInfo.input_price_per_m)} · Output{' '}
                    {priceLabel(selectedModelInfo.output_price_per_m)} ·{' '}
                    {selectedModelInfo.capabilities
                      .map((c) => CAPABILITY_ICONS[c]?.label || c)
                      .join(', ') || 'capabilities unknown'}
                  </div>
                )}
              </div>
              <div className="settings-row">
                <div className="form-group">
                  <label className="form-label" htmlFor="llm-context-tokens">Context tokens</label>
                  <input
                    id="llm-context-tokens"
                    className="settings-input"
                    value={contextTokens}
                    onChange={(e) => setContextTokens(e.target.value)}
                    inputMode="numeric"
                  />
                </div>
                <div className="form-group">
                  <label className="form-label" htmlFor="llm-max-output-tokens">Max output tokens</label>
                  <input
                    id="llm-max-output-tokens"
                    className="settings-input"
                    value={maxOutputTokens}
                    onChange={(e) => setMaxOutputTokens(e.target.value)}
                    inputMode="numeric"
                  />
                </div>
                <div className="form-group">
                  <label className="form-label" htmlFor="llm-temperature">Temperature</label>
                  <input
                    id="llm-temperature"
                    className="settings-input"
                    value={temperature}
                    onChange={(e) => setTemperature(e.target.value)}
                    inputMode="decimal"
                  />
                </div>
              </div>
              {error && <div className="picker-error">{error}</div>}
              {saved && !error && (
                <div className="settings-saved">Saved. The chat uses the new configuration.</div>
              )}
            </>
          )}
        </div>
        <div className="modal-footer">
          <div />
          <div className="btn-row">
            <button className="btn" type="button" onClick={onClose}>
              Close
            </button>
            <button
              className="btn primary"
              type="button"
              disabled={loading || saving}
              onClick={handleSave}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
