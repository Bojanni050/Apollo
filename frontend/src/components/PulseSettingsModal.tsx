import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Props {
  isOpen: boolean
  workspaceId: number
  onClose: () => void
  onSaved?: () => void
}

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const HOURS = Array.from({ length: 24 }, (_, i) => i)

export function PulseSettingsModal({ isOpen, workspaceId, onClose, onSaved }: Props) {
  const [mode, setMode] = useState<'suggest' | 'apply'>('suggest')
  const [scheduleEnabled, setScheduleEnabled] = useState(false)
  const [scheduleKind, setScheduleKind] = useState<'interval' | 'weekly'>('interval')
  const [intervalHours, setIntervalHours] = useState(1)
  const [weeklyDay, setWeeklyDay] = useState(0)
  const [weeklyHour, setWeeklyHour] = useState(0)
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
      .getPulseSettings(workspaceId)
      .then((res) => {
        setMode(res.mode === 'apply' ? 'apply' : 'suggest')
        setScheduleEnabled(!!res.schedule_enabled)
        setScheduleKind(res.schedule_kind === 'weekly' ? 'weekly' : 'interval')
        setIntervalHours(res.interval_hours || 1)
        setWeeklyDay(res.weekly_day || 0)
        setWeeklyHour(res.weekly_hour || 0)
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : 'Could not load Pulse settings.'),
      )
      .finally(() => setLoading(false))
  }, [isOpen, workspaceId])

  if (!isOpen) return null

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await api.updatePulseSettings(workspaceId, {
        mode,
        schedule_enabled: scheduleEnabled,
        schedule_kind: scheduleKind,
        interval_hours: intervalHours,
        weekly_day: weeklyDay,
        weekly_hour: weeklyHour,
      })
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
            <h3>Delphi Pulse</h3>
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
              <div className="form-group">
                <label className="form-label">Scan result</label>
                <div className="btn-row">
                  <button
                    type="button"
                    className={`btn ${mode === 'suggest' ? 'primary' : ''}`}
                    onClick={() => setMode('suggest')}
                  >
                    Suggestions
                  </button>
                  <button
                    type="button"
                    className={`btn ${mode === 'apply' ? 'primary' : ''}`}
                    onClick={() => setMode('apply')}
                  >
                    Auto-apply
                  </button>
                </div>
                <div className="settings-hint">
                  Suggestions wait for your approval; auto-apply writes tags and
                  connections into the documents directly.
                </div>
              </div>

              <div className="settings-divider" />

              <div className="form-group">
                <label className="form-label">
                  <input
                    type="checkbox"
                    checked={scheduleEnabled}
                    onChange={(e) => setScheduleEnabled(e.target.checked)}
                  />{' '}
                  Scan automatically
                </label>
              </div>

              {scheduleEnabled && (
                <div className="form-group">
                  <label className="form-label">Repeat</label>
                  <div className="btn-row">
                    <button
                      type="button"
                      className={`btn ${scheduleKind === 'interval' ? 'primary' : ''}`}
                      onClick={() => setScheduleKind('interval')}
                    >
                      Every N hours
                    </button>
                    <button
                      type="button"
                      className={`btn ${scheduleKind === 'weekly' ? 'primary' : ''}`}
                      onClick={() => setScheduleKind('weekly')}
                    >
                      Weekly
                    </button>
                  </div>

                  {scheduleKind === 'interval' ? (
                    <div className="form-group" style={{ marginTop: 10 }}>
                      <label className="form-label" htmlFor="pulse-interval">
                        Every
                      </label>
                      <div className="btn-row">
                        <select
                          id="pulse-interval"
                          className="settings-input"
                          value={intervalHours}
                          onChange={(e) => setIntervalHours(Number(e.target.value))}
                        >
                          {Array.from({ length: 24 }, (_, i) => i + 1).map((h) => (
                            <option key={h} value={h}>
                              {h} {h === 1 ? 'hour' : 'hours'}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ) : (
                    <div className="form-group" style={{ marginTop: 10 }}>
                      <label className="form-label" htmlFor="pulse-weekday">
                        Every
                      </label>
                      <div className="btn-row">
                        <select
                          id="pulse-weekday"
                          className="settings-input"
                          value={weeklyDay}
                          onChange={(e) => setWeeklyDay(Number(e.target.value))}
                        >
                          {WEEKDAYS.map((d, i) => (
                            <option key={d} value={i}>
                              {d}
                            </option>
                          ))}
                        </select>
                        <select
                          className="settings-input"
                          aria-label="Time"
                          value={weeklyHour}
                          onChange={(e) => setWeeklyHour(Number(e.target.value))}
                        >
                          {HOURS.map((h) => (
                            <option key={h} value={h}>
                              {String(h).padStart(2, '0')}:00
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {error && <div className="picker-error">{error}</div>}
              {saved && !error && (
                <div className="settings-saved">Saved. The schedule is active immediately.</div>
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
