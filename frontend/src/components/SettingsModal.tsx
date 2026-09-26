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
  { id: 'gemini', label: 'Gemini', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai/', needsKey: true, local: false },
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
  sameAsChatToggle?: boolean
  sameAsChat?: boolean
  setSameAsChat?: (v: boolean) => void
  providerBaseUrl?: string
  providerApiKey?: string
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
  sameAsChatToggle,
  sameAsChat,
  setSameAsChat,
  providerBaseUrl,
  providerApiKey,
}: LlmSectionProps) {
  const [models, setModels] = useState<LlmModelInfo[]>([])
  const [modelsError, setModelsError] = useState<string | null>(null)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [modelFilter, setModelFilter] = useState('')

  const sectionId = title.toLowerCase().replace(/[^a-z0-9]+/g, '-')


  const handleFetchModels = async () => {
    setFetchingModels(true)
    setModelsError(null)
    try {
      let url = baseUrl.trim()
      let key = apiKey.trim()
      if (sameAsChatToggle && sameAsChat) {
        url = (providerBaseUrl || '').trim()
        key = (providerApiKey || '').trim()
      }
      const res = await api.fetchLlmModels(url || undefined, key || undefined)
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
      {sameAsChatToggle && (
        <div className="form-group">
          <label className="checkbox" htmlFor={`${sectionId}-same-as-chat`}>
            <input
              id={`${sectionId}-same-as-chat`}
              type="checkbox"
              checked={!!sameAsChat}
              onChange={(e) => setSameAsChat?.(e.target.checked)}
            />
            <span>Same provider as chat &mdash; reuse its base URL and API key; only the model below can differ</span>
          </label>
        </div>
      )}
      <LlmSectionFields
        sectionId={sectionId}
        baseUrl={baseUrl}
        setBaseUrl={setBaseUrl}
        apiKey={apiKey}
        setApiKey={setApiKey}
        apiKeyConfigured={apiKeyConfigured}
        model={model}
        setModel={setModel}
        contextTokens={contextTokens}
        setContextTokens={setContextTokens}
        maxOutputTokens={maxOutputTokens}
        setMaxOutputTokens={setMaxOutputTokens}
        showTokenFields={showTokenFields}
        optionalTokens={optionalTokens}
        showProviderFields={!sameAsChatToggle || !sameAsChat}
        sameAsChat={!!sameAsChat}
        onFetch={handleFetchModels}
        fetching={fetchingModels}
        models={models}
        modelsError={modelsError}
        dropdownOpen={dropdownOpen}
        setDropdownOpen={setDropdownOpen}
        modelFilter={modelFilter}
        setModelFilter={setModelFilter}
        filteredModels={filteredModels}
        selectedModelInfo={selectedModelInfo}
      />
    </div>
  )
}

interface LlmSectionFieldsProps {
  sectionId: string
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
  showProviderFields: boolean
  sameAsChat: boolean
  onFetch: () => void
  fetching: boolean
  models: LlmModelInfo[]
  modelsError: string | null
  dropdownOpen: boolean
  setDropdownOpen: (v: boolean) => void
  modelFilter: string
  setModelFilter: (v: string) => void
  filteredModels: LlmModelInfo[]
  selectedModelInfo: LlmModelInfo | undefined
}

function LlmSectionFields({
  sectionId,
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
  showProviderFields,
  sameAsChat,
  onFetch,
  fetching,
  models,
  modelsError,
  dropdownOpen,
  setDropdownOpen,
  modelFilter,
  setModelFilter,
  filteredModels,
  selectedModelInfo,
}: LlmSectionFieldsProps) {
  const activePreset = PROVIDER_PRESETS.find((p) => p.baseUrl === baseUrl.trim().replace(/\/$/, ''))

  return (
    <>
      {showProviderFields && (
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
      )}
      <div className="form-group">
        <label className="form-label" htmlFor={`${sectionId}-base-url`}>Base URL</label>
        <input
          id={`${sectionId}-base-url`}
          className="settings-input"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.openai.com/v1  ·  https://generativelanguage.googleapis.com/v1beta/openai/ (Gemini)  ·  http://127.0.0.1:11434/v1 (Ollama)  ·  http://127.0.0.1:1234/v1 (LM Studio)"
          spellCheck={false}
          disabled={sameAsChat}
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
          placeholder={sameAsChat ? 'Reused from the chat model' : apiKeyConfigured ? 'Leave empty to keep the current key' : 'sk-… (empty for local endpoints)'}
          spellCheck={false}
          disabled={sameAsChat}
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
            onClick={onFetch}
            disabled={fetching || (!baseUrl.trim() && !sameAsChat)}
            title="Fetch the model list from the provider"
          >
            {fetching ? 'Fetching…' : 'Fetch models'}
          </button>
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
                    <span className="model-item-side">
                      <span className="model-item-side-row">
                        <span className="model-item-side-label">In:</span>
                        <span className="model-item-side-value">{priceLabel(m.input_price_per_m)}</span>
                      </span>
                      <span className="model-item-side-row">
                        <span className="model-item-side-label">Out:</span>
                        <span className="model-item-side-value">{priceLabel(m.output_price_per_m)}</span>
                      </span>
                    </span>
                    <span className="model-item-main">
                      <span className="model-item-head">
                        <span className="model-item-id">{m.id}</span>
                      </span>
                      <span className="model-item-caps">
                        {m.capabilities.map((c) => (
                          <span key={c} className="model-cap" title={CAPABILITY_ICONS[c]?.label || c}>
                            <span className="model-cap-icon">{CAPABILITY_ICONS[c]?.icon || '•'}</span>
                            {CAPABILITY_ICONS[c]?.label || c}
                          </span>
                        ))}
                      </span>
                      {m.context_window ? (
                        <span className="model-item-note">
                          {m.context_window.toLocaleString()} token context window
                        </span>
                      ) : null}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
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
    </>
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
  const [bgSameAsChat, setBgSameAsChat] = useState(true)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [resetArmed, setResetArmed] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState<string | null>(null)
  const [resetDone, setResetDone] = useState<number | null>(null)

  useEffect(() => {
    if (!isOpen) return
    setLoading(true)
    setError(null)
    setSaved(false)
    setResetArmed(false)
    setResetError(null)
    setResetDone(null)
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
        setBgSameAsChat(!res.background_base_url && !res.background_api_key_configured)
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
      if (bgSameAsChat) {
        if (bgBaseUrl.trim() !== (data?.background_base_url || '')) payload.background_base_url = ''
        if (bgApiKey.trim()) payload.background_api_key = ''
      } else {
        if (bgBaseUrl.trim() !== (data?.background_base_url || '')) payload.background_base_url = bgBaseUrl.trim()
        if (bgApiKey.trim()) payload.background_api_key = bgApiKey.trim()
      }
      if (bgModel.trim() !== (data?.background_model || '')) payload.background_model = bgModel.trim()
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
                sameAsChatToggle
                sameAsChat={bgSameAsChat}
                setSameAsChat={setBgSameAsChat}
                providerBaseUrl={baseUrl}
                providerApiKey={apiKey}
                title="Background model (inventory & classification)"
                hint="Optional. A compact/cheap model (Haiku, 4o-mini, local Qwen 14B) for structural digests and document classification. Leave the model empty to use the primary model for everything; check 'same provider' to pick a different model from the chat provider without re-entering its URL and key."
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
              <div className="settings-divider" />
              <div className="settings-section">
                <div className="settings-section-title">Danger zone</div>
                <p className="settings-hint">
                  Reset deletes every workspace, repository registration, conversation, decision,
                  question, proposal and Delphi Pulse run from the database. Your files on disk are
                  never touched. This cannot be undone.
                </p>
                {resetDone !== null && !resetError && (
                  <div className="settings-saved">
                    Database reset — {resetDone} workspace{resetDone === 1 ? '' : 's'} deleted. Reload the window to start fresh.
                  </div>
                )}
                {resetError && <div className="picker-error">{resetError}</div>}
                {!resetDone && !resetError && (
                  <div className="btn-row">
                    {!resetArmed ? (
                      <button
                        className="btn danger"
                        type="button"
                        onClick={() => setResetArmed(true)}
                      >
                        Reset database…
                      </button>
                    ) : (
                      <>
                        <button
                          className="btn"
                          type="button"
                          onClick={() => setResetArmed(false)}
                        >
                          Cancel
                        </button>
                        <button
                          className="btn danger"
                          type="button"
                          disabled={resetting}
                          onClick={async () => {
                            setResetting(true)
                            setResetError(null)
                            try {
                              const res = await api.resetDatabase()
                              setResetDone(res.deleted_workspaces)
                              onSaved?.()
                            } catch (err) {
                              setResetError(err instanceof Error ? err.message : 'Reset failed.')
                            } finally {
                              setResetting(false)
                            }
                          }}
                        >
                          {resetting ? 'Resetting…' : 'Yes, delete everything'}
                        </button>
                      </>
                    )}
                  </div>
                )}
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
