// TypeScript mirrors of the backend Pydantic schemas.
// Wired to the *implemented* backend (audio_to_subs/api/routes/*),
// which differs from the design-doc sketches in places.

// --------------- Auth ---------------

export interface UserOut {
  id: number
  username: string
}

export interface LoginResponse {
  user: UserOut
}

// --------------- Jobs ---------------

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled"
export type JobSource = "bazarr_movie" | "bazarr_episode" | "manual"
export type OutputFormat = "srt" | "vtt" | "webvtt" | "sbv"
export type LanguageMode = "auto" | "explicit"

export interface JobResponse {
  id: string
  status: JobStatus
  source: JobSource
  source_ref: string | null
  media_path: string
  // Label snapshot taken from the Bazarr cache when the job was created
  // (migration 0008) - never refreshed afterwards, so a renamed or
  // removed Bazarr item doesn't change or blank an old job's label. All
  // four are null for a manual job. A freshly-created Bazarr job always
  // has these set (job creation is rejected outright if no cache row
  // matches); a Bazarr job can only end up with all four null via the
  // migration's one-time historical backfill of a pre-existing job whose
  // cache row was already gone by the time that migration ran. See
  // lib/jobLabels.ts.
  title: string | null
  series_title: string | null
  season_number: number | null
  episode_number: number | null
  output_path: string | null
  language_code: string | null
  language_mode: LanguageMode
  mistral_detected_language: string | null
  needs_language_review: boolean
  output_format: OutputFormat
  priority: number
  progress_percent: number
  progress_message: string | null
  progress_stage: string | null
  progress_step_index: number | null
  progress_step_total: number | null
  cancel_requested: boolean
  worker_id: string | null
  audio_duration_seconds: number | null
  runtime_seconds: number | null
  mistral_usage_json: string | null
  estimated_cost_usd: number | null
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

// GET /api/jobs response.
// NOTE: `total` is the page-length, not the global total.
// The per-status counts (queued, running, etc.) are global across all jobs.
export interface JobListResponse {
  jobs: JobResponse[]
  total: number
  queued: number
  running: number
  done: number
  failed: number
  cancelled: number
}

// GET /api/jobs/{id}/logs response.
export interface JobLogsResponse {
  logs: LogEntry[]
  total: number
}

export interface JobCreate {
  source: JobSource
  source_ref?: string | null
  media_path: string // required even for bazarr sources
  output_path?: string | null
  language_code?: string | null
  language_mode?: LanguageMode
  output_format?: OutputFormat
  priority?: number
  // M6.g: allow replacing an existing output subtitle (overwrite guard)
  overwrite?: boolean
}

export interface JobLanguagePatch {
  language_code: string
  // M6.g: allow renaming onto an existing target file (overwrite guard)
  overwrite?: boolean
}

// M6.g: structured 409 conflict detail returned by the backend when a job
// creation / language correction collides with an existing or active job.
export type JobConflictCode = "job_already_active" | "subtitle_exists"

export interface JobConflictDetail {
  code: JobConflictCode
  message: string
  existing_path?: string
  existing_mtime?: string
  language_code?: string
}

// --------------- Wanted ---------------

export interface MissingSubtitle {
  code2: string
  code3?: string | null
  name?: string | null
  hi?: boolean
  forced?: boolean
}

// NOTE: field is `missing_subtitles` (not `missing`), `kind` (not `type`),
// and `id` is a composite string like "movie:123" / "episode:456".
export interface WantedItem {
  id: string
  kind: "movie" | "episode"
  ext_id: number
  // The item's OWN bare title: for a movie, the movie title; for an
  // episode, the bare episode title (e.g. "Episode 1", "Pilot") - NOT a
  // flattened "series - episode" string. Combine with series_title /
  // season_number / episode_number below (see lib/mediaLabels.ts) to
  // build a display label.
  title: string
  media_path: string
  has_any_subs: boolean
  missing_subtitles: MissingSubtitle[]
  audio_language: MissingSubtitle[]
  last_polled: string
  active_job_id: string | null
  active_job_status: string | null
  active_job_progress: number | null
  // Episode's series name; null for movies and for not-yet-backfilled rows.
  series_title: string | null
  // Null for movies and not-yet-backfilled episode rows.
  season_number: number | null
  // Null for movies and not-yet-backfilled episode rows.
  episode_number: number | null
}

export interface WantedListResponse {
  items: WantedItem[]
  total: number
  last_refreshed_at: string | null
}

// Display filter over the synced library (GET /api/wanted?scope=...). Never
// narrows what a refresh re-syncs - only what's shown from the cache.
export type WantedScope = "all" | "missing" | "no_subs"

// --------------- Settings ---------------

export interface SettingsOut {
  mistral_model: string
  mistral_rate_usd_per_minute: number
  mistral_input_token_rate_usd: number | null
  mistral_output_token_rate_usd: number | null
  bazarr_poll_interval: number
  bazarr_url: string | null
  bazarr_api_key: string | null
  bazarr_timeout: number
  path_mappings: Record<string, string>[]
  default_language: string
  default_output_format: string
  subtitles_same_directory: boolean
  max_audio_length: number
  timezone: string
}

export interface SettingsPatch {
  mistral_model?: string
  mistral_rate_usd_per_minute?: number
  mistral_input_token_rate_usd?: number | null
  mistral_output_token_rate_usd?: number | null
  bazarr_poll_interval?: number
  bazarr_url?: string | null
  bazarr_api_key?: string | null
  bazarr_timeout?: number
  path_mappings?: Record<string, string>[]
  default_language?: string
  default_output_format?: string
  subtitles_same_directory?: boolean
  max_audio_length?: number
  timezone?: string
}

// --------------- Logs ---------------

export type LogLevel = "debug" | "info" | "warning" | "error"

export interface LogEntry {
  id: number
  job_id: string | null
  ts: string
  level: LogLevel
  message: string
}

export interface LogsPage {
  logs: LogEntry[]
  total: number
}

// Global logs response
export interface GlobalLogsResponse {
  logs: LogEntry[]
  total: number
}

// Logs filters
export interface LogsFilters {
  job_id?: string
  level_filter?: LogLevel
  since?: string
  until?: string
  limit?: number
  offset?: number
}

// Settings
export interface SettingsFormData {
  mistral_model: string
  mistral_rate_usd_per_minute: number
  mistral_input_token_rate_usd: number | null
  mistral_output_token_rate_usd: number | null
  bazarr_poll_interval: number
  bazarr_url: string | null
  bazarr_api_key: string | null
  bazarr_timeout: number
  path_mappings: Array<{ from: string; to: string }>
  default_language: string
  default_output_format: OutputFormat
  subtitles_same_directory: boolean
  max_audio_length: number
  timezone: string
}

// Path mapping
export interface PathMapping {
  from: string
  to: string
}

// Media type
export type MediaType = "movie" | "series" | "unknown"

// --------------- SSE events ---------------
// The backend emits unnamed SSE `message` events. The JSON payload discriminates
// on the `event` field. Do NOT use named EventSource listeners.

export type SseEventData =
  // Emitted once immediately after the backend's Redis pubsub.subscribe
  // completes, so the client knows events published after this point will
  // be delivered. Critical for the refresh flow: the client waits for this
  // before POSTing /api/wanted/refresh, eliminating the race where a fast
  // refresh publishes refresh_done before the subscription exists.
  | { event: "stream_ready" }
  | { event: "new"; job_id: string }
  | {
      event: "progress"
      job_id: string
      percent: number
      stage: string
      message: string
      step_index: number | null
      step_total: number | null
    }
  | { event: "cancel"; job_id: string }
  | { event: "done"; job_id: string; status: string; error?: string }
  | {
      event: "refresh_progress"
      refresh_id: string
      processed: number
      total: number | null
      percent: number
      stage: string
    }
  | {
      event: "refresh_done"
      refresh_id: string
      status: "completed" | "failed"
      movies_processed: number
      episodes_processed: number
      error?: string
    }

// --------------- Bazarr Connection Test ---------------

// Mirrors the Literal[...] error field on the backend's
// BazarrConnectionTestResponse (audio_to_subs/api/routes/settings.py).
// Keep these two lists in sync.
export type BazarrErrorCode =
  | "bazarr_not_configured"
  | "authentication_failed"
  | "resource_not_found"
  | "rate_limited"
  | "server_error"
  | "unexpected_response"
  | "connection_failed"
  | "internal_error"

export interface BazarrConnectionTestResponse {
  success: boolean
  message: string | null
  error: BazarrErrorCode | null
}

// --------------- Wanted Refresh ---------------

export interface WantedRefreshRequest {
  item_type: "all" | "movie" | "episode"
  // Optional client-supplied id. When the client opens its SSE subscription
  // before POSTing (so it doesn't miss refresh_done on a fast refresh), it
  // generates the id and sends it here. Server generates one when omitted.
  refresh_id?: string
}

export interface WantedRefreshResponse {
  status: "started" | "completed" | "failed"
  refresh_id: string
  movies_processed: number
  episodes_processed: number
  error: string | null
}

// GET /api/wanted/refresh/{refresh_id} - persisted snapshot a client reads
// when it missed the terminal SSE event (late subscription, dropped
// connection, sleep). See useWantedRefresh's watchdog.
export interface WantedRefreshStatusResponse {
  refresh_id: string
  status: "started" | "completed" | "failed"
  processed: number
  total: number | null
  percent: number
  stage: string
  movies_processed: number
  episodes_processed: number
  error: string | null
  updated_at: string
}

// --------------- History ---------------

export interface HistoryStats {
  total_jobs: number
  total_cost_usd: number
  total_audio_length_seconds: number
  total_runtime_seconds: number
  average_cost_usd: number
  average_audio_length_seconds: number
  average_runtime_seconds: number
  count_by_status: Record<string, number>
  count_by_language: Record<string, number>
  count_by_source: Record<string, number>
}

export interface HistoryResponse {
  jobs: JobResponse[]
  stats: HistoryStats
  total: number
  limit: number
  offset: number
}

export interface HistoryFilters {
  status_filter?: JobStatus[]
  source_filter?: JobSource
  language_filter?: string
  since?: string
  until?: string
  limit?: number
  offset?: number
}
