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

interface ProviderPreset {
  id: string
  label: string
  baseUrl: string
  needsKey: boolean
  local: boolean
}

const PROVIDER_PRESETS: ProviderPreset[] = [
  { id: 'openai', label: 'OpenAI', baseUrl: 'https://api.openai.com/v1', needsKey: true, local: false },
  { id: 'ollama', label: 'Ollama (local)', baseUrl: 'http://127.0.0.1:11434/v1', needsKey: false, local: true },
  { id: 'lmstudio', label: 'LM Studio (local)', baseUrl: 'http://127.0.0.1:1234/v1', needsKey: false, local: true },
]

function priceLabel(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `$${v < 1 ? v.toString() : v.toFixed(2)}/M`
}

interface LlmSectionProps {
  title: string
  hint: string
  baseUrl: string
  setBaseUrl: (v: string) => void
  apiKey: string
  setApiKey: (v: string) => void
  apiKeyConfigured: boolean
  model: string
  setModel: (v: string) => void
  contextTokens: string
  setContextTokens: (v: string) => void
  maxOutputTokens: string
  setMaxOutputTokens: (v: string) => void
  showTokenFields: boolean
  optionalTokens: boolean
}

function LlmSection({
  title,
  hint,
  baseUrl,
  setBaseUrl,
  apiKey,
  setApiKey,
  apiKeyConfigured,
  model,
  setModel,
  contextTokens,
  setContextTokens,
  maxOutputTokens,
  setMaxOutputTokens,
  showTokenFields,
  optionalTokens,
}: LlmSectionProps) {
  const [models, setModels] = useState<LlmModelInfo[]>([])
  const [modelsError, setModelsError] = useState<string | null>(null)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [modelFilter, setModelFilter] = useState('')

  const sectionId = title.toLowerCase().replace(/[^a-z0-9]+/g, '-')

  const activePreset = PROVIDER_PRESETS.find((p) => p.baseUrl === baseUrl.trim().replace(/\/$/, ''))

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

  const selectedModelInfo = models.find((m) => m.id === model)

  return (
    <div className="settings-section">
      <div className="settings-section-title">{title}</div>
      <p className="settings-hint">{hint}</p>
      <div className="form-group">
        <label className="form-label">Provider</label>
        <div className="provider-presets">
          {PROVIDER_PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`provider-preset ${activePreset?.id === p.id ? 'active' : ''} ${p.local ? 'local' : ''}`}
              onClick={() => setBaseUrl(p.baseUrl)}
              title={p.baseUrl}
            >
              {p.local ? '💻 ' : '☁️ '}{p.label}
            </button>
          ))}
          <span className="provider-presets-or">or enter a base URL below</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor={`${sectionId}-base-url`}>Base URL</label>
        <input
          id={`${sectionId}-base-url`}
          className="settings-input"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.openai.com/v1  ·  http://127.0.0.1:11434/v1 (Ollama)  ·  http://127.0.0.1:1234/v1 (LM Studio)"
          spellCheck={false}
        />
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor={`${sectionId}-api-key`}>
          API key{' '}
          {apiKeyConfigured && <span className="settings-key-status">configured ✓</span>}
          {activePreset && !activePreset.needsKey && (
            <span className="settings-key-note">not needed for this provider</span>
          )}
        </label>
        <input
          id={`${sectionId}-api-key`}
          className="settings-input"
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={apiKeyConfigured ? 'Leave empty to keep the current key' : 'sk-… (empty for local endpoints)'}
          spellCheck={false}
        />
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor={`${sectionId}-model`}>Model</label>
        <div className="model-combo">
          <input
            id={`${sectionId}-model`}
            className="settings-input"
            value={model}
            onChange={(e) => {
              setModel(e.target.value)
              if (models.length > 0) {
                setDropdownOpen(true)
                setModelFilter(e.target.value)
              }
            }}
            placeholder="Fetch the list, or type a model id"
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
      {showTokenFields && (
        <div className="settings-row">
          <div className="form-group">
            <label className="form-label" htmlFor={`${sectionId}-context-tokens`}>
              Context tokens{optionalTokens ? ' (optional)' : ''}
            </label>
            <input
              id={`${sectionId}-context-tokens`}
              className="settings-input"
              value={contextTokens}
              onChange={(e) => setContextTokens(e.target.value)}
              inputMode="numeric"
              placeholder={optionalTokens ? 'falls back to the primary model' : '128000'}
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor={`${sectionId}-max-output-tokens`}>
              Max output tokens{optionalTokens ? ' (optional)' : ''}
            </label>
            <input
              id={`${sectionId}-max-output-tokens`}
              className="settings-input"
              value={maxOutputTokens}
              onChange={(e) => setMaxOutputTokens(e.target.value)}
              inputMode="numeric"
              placeholder={optionalTokens ? 'falls back to the primary model' : '8192'}
            />
          </div>
        </div>
      )}
    </div>
  )
}

export function SettingsModal({ isOpen, onClose, onSaved }: Props) {
  const [data, setData] = useState<LlmSettings | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [contextTokens, setContextTokens] = useState('')
  const [maxOutputTokens, setMaxOutputTokens] = useState('')
  const [temperature, setTemperature] = useState('0.2')
  const [bgBaseUrl, setBgBaseUrl] = useState('')
  const [bgModel, setBgModel] = useState('')
  const [bgApiKey, setBgApiKey] = useState('')
  const [bgContextTokens, setBgContextTokens] = useState('')
  const [bgMaxOutputTokens, setBgMaxOutputTokens] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!isOpen) return
    setLoading(true)
    setError(null)
    setSaved(false)
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
        setBgBaseUrl(res.background_base_url || '')
        setBgModel(res.background_model || '')
        setBgApiKey('')
        setBgContextTokens(res.background_context_tokens ? String(res.background_context_tokens) : '')
        setBgMaxOutputTokens(res.background_max_output_tokens ? String(res.background_max_output_tokens) : '')
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load settings.'))
      .finally(() => setLoading(false))
  }, [isOpen])

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
      if (bgBaseUrl.trim() !== (data?.background_base_url || '')) payload.background_base_url = bgBaseUrl.trim()
      if (bgModel.trim() !== (data?.background_model || '')) payload.background_model = bgModel.trim()
      if (bgApiKey.trim()) payload.background_api_key = bgApiKey.trim()
      const bgCt = parseInt(bgContextTokens, 10)
      if (!Number.isNaN(bgCt) && bgCt !== (data?.background_context_tokens ?? null)) payload.background_context_tokens = bgCt
      const bgMot = parseInt(bgMaxOutputTokens, 10)
      if (!Number.isNaN(bgMot) && bgMot !== (data?.background_max_output_tokens ?? null)) payload.background_max_output_tokens = bgMot

      const res = await api.updateLlmSettings(payload)
      setData(res)
      setApiKey('')
      setBgApiKey('')
      setSaved(true)
      onSaved?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Saving failed.')
    } finally {
      setSaving(false)
    }
  }

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
              <LlmSection
                title="Chat model (primary)"
                hint="The strong model for the interactive architecture chat, ADR consistency checks and diff proposals. Changes are saved to backend/.env and apply immediately."
                baseUrl={baseUrl}
                setBaseUrl={setBaseUrl}
                apiKey={apiKey}
                setApiKey={setApiKey}
                apiKeyConfigured={!!data?.api_key_configured}
                model={model}
                setModel={setModel}
                contextTokens={contextTokens}
                setContextTokens={setContextTokens}
                maxOutputTokens={maxOutputTokens}
                setMaxOutputTokens={setMaxOutputTokens}
                showTokenFields
                optionalTokens={false}
              />
              <div className="settings-divider" />
              <LlmSection
                title="Background model (inventory & classification)"
                hint="Optional. A compact/cheap model (Haiku, 4o-mini, local Qwen 14B) for structural digests and document classification. Leave empty to reuse the primary model for everything."
                baseUrl={bgBaseUrl}
                setBaseUrl={setBgBaseUrl}
                apiKey={bgApiKey}
                setApiKey={setBgApiKey}
                apiKeyConfigured={!!data?.background_api_key_configured}
                model={bgModel}
                setModel={setBgModel}
                contextTokens={bgContextTokens}
                setContextTokens={setBgContextTokens}
                maxOutputTokens={bgMaxOutputTokens}
                setMaxOutputTokens={setBgMaxOutputTokens}
                showTokenFields
                optionalTokens
              />
              <div className="form-group">
                <label className="form-label" htmlFor="llm-temperature">Temperature</label>
                <input
                  id="llm-temperature"
                  className="settings-input settings-temperature"
                  value={temperature}
                  onChange={(e) => setTemperature(e.target.value)}
                  inputMode="decimal"
                />
              </div>
              {error && <div className="picker-error">{error}</div>}
              {saved && !error && (
                <div className="settings-saved">Saved. New configuration is active immediately.</div>
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
