// Zustand store for live job state, fed by the SSE stream at /api/jobs/stream.
// The store is seeded from GET /api/jobs on page mount and kept fresh via SSE events.
//
// NOTE: The backend emits unnamed SSE `message` events (not named events like
// "progress" / "done"). The JSON payload carries the event type in the `event` field.

import { create } from "zustand"
import type { JobResponse, LanguageMode, SseEventData } from "./types"

export interface LiveJob {
  id: string
  status: string
  percent: number
  stage: string
  message: string
  // Step-based progress (persisted/forwarded from the pipeline)
  step_index: number | null
  step_total: number | null
  // fields seeded from the GET /api/jobs response
  source: string
  source_ref: string | null
  media_path: string
  // Label snapshot (migration 0008) - see JobResponse's field comment in
  // lib/types.ts for the null semantics.
  title: string | null
  series_title: string | null
  season_number: number | null
  episode_number: number | null
  language_code: string | null
  language_mode: LanguageMode
  output_format: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  cancel_requested: boolean
  // Cost and duration (available for completed jobs)
  audio_duration_seconds: number | null
  runtime_seconds: number | null
  estimated_cost_usd: number | null
  // M6.g: populated for FAILED jobs so the UI can offer an "Overwrite & retry"
  // action when the worker refused to overwrite an existing subtitle.
  error_message: string | null
}

interface JobsState {
  jobs: Record<string, LiveJob>
  // increments on every "new" SSE event so components can trigger a refetch
  pendingNewCount: number
  // set to the job_id whenever a terminal event (done/cancel) arrives
  lastTerminalJobId: string | null

  apply: (data: SseEventData) => void
  seed: (jobs: JobResponse[]) => void
  // Re-sync from REST without discarding live SSE progress. Adopt
  // server-authoritative fields, but keep any live percent/stage/message/
  // step_* already present in the store so an in-flight progress bar isn't
  // reset by a re-seed triggered on a "new" event.
  merge: (jobs: JobResponse[]) => void
  // User-initiated refresh (manual "Refresh now" button or auto-refresh
  // polling): adopt server values wholesale, including progress. This is
  // the recovery path when SSE has gone quiet — merge() would preserve
  // the store's possibly-stale SSE-driven progress fields, defeating the
  // whole point of polling. The "don't resurrect dismissed terminal jobs"
  // carve-out from merge() is preserved: a job the store doesn't know
  // about is only added if still active, so hitting Refresh right after a
  // job fades out won't make it reappear.
  replace: (jobs: JobResponse[]) => void
  remove: (jobId: string) => void
}

// Build a LiveJob from a REST JobResponse.
function liveJobFromApi(j: JobResponse): LiveJob {
  return {
    id: j.id,
    status: j.status,
    percent: j.progress_percent,
    stage: j.progress_stage ?? "",
    message: j.progress_message ?? "",
    step_index: j.progress_step_index ?? null,
    step_total: j.progress_step_total ?? null,
    source: j.source,
    source_ref: j.source_ref,
    media_path: j.media_path,
    title: j.title,
    series_title: j.series_title,
    season_number: j.season_number,
    episode_number: j.episode_number,
    language_code: j.language_code,
    language_mode: j.language_mode,
    output_format: j.output_format,
    created_at: j.created_at,
    started_at: j.started_at,
    finished_at: j.finished_at,
    cancel_requested: j.cancel_requested,
    audio_duration_seconds: j.audio_duration_seconds,
    runtime_seconds: j.runtime_seconds,
    estimated_cost_usd: j.estimated_cost_usd,
    error_message: j.error_message,
  }
}

// Minimal LiveJob created purely from an SSE event when the store has never
// seen the job (e.g. a progress event fired before the seed caught up).
function liveJobFromEvent(event: "progress" | "cancel" | "done", jobId: string): LiveJob {
  const status = event === "cancel" ? "cancelled" : "running"
  return {
    id: jobId,
    status,
    percent: 0,
    stage: "",
    message: "",
    step_index: null,
    step_total: null,
    source: "manual",
    source_ref: null,
    media_path: "",
    title: null,
    series_title: null,
    season_number: null,
    episode_number: null,
    language_code: null,
    language_mode: "auto",
    output_format: "srt",
    created_at: "",
    started_at: null,
    finished_at: null,
    cancel_requested: false,
    audio_duration_seconds: null,
    runtime_seconds: null,
    estimated_cost_usd: null,
    error_message: null,
  }
}

export const useJobsStore = create<JobsState>()((set) => ({
  jobs: {},
  pendingNewCount: 0,
  lastTerminalJobId: null,

  apply: (data) =>
    set((state) => {
      const jobs = { ...state.jobs }

      switch (data.event) {
        case "new":
          return { pendingNewCount: state.pendingNewCount + 1 }

        case "progress": {
          const prev = jobs[data.job_id] ?? liveJobFromEvent("progress", data.job_id)
          jobs[data.job_id] = {
            ...prev,
            status: "running",
            percent: data.percent,
            stage: data.stage,
            message: data.message,
            step_index: data.step_index,
            step_total: data.step_total,
          }
          return { jobs }
        }

        case "cancel": {
          const prev = jobs[data.job_id] ?? liveJobFromEvent("cancel", data.job_id)
          jobs[data.job_id] = {
            ...prev,
            status: "cancelled",
          }
          return { jobs, lastTerminalJobId: data.job_id }
        }

        case "done": {
          const prev = jobs[data.job_id] ?? liveJobFromEvent("done", data.job_id)
          jobs[data.job_id] = {
            ...prev,
            status: data.status,
            percent: 100,
            error_message: data.error ?? null,
          }
          return { jobs, lastTerminalJobId: data.job_id }
        }

        default:
          return {}
      }
    }),

  seed: (apiJobs) =>
    set(() => {
      const jobs: Record<string, LiveJob> = {}
      for (const j of apiJobs) {
        // Include all jobs, not just queued/running
        // This allows cost/duration to be available for recently completed jobs
        jobs[j.id] = liveJobFromApi(j)
      }
      return { jobs }
    }),

  // Re-sync from REST without discarding live SSE progress. Adopt
  // server-authoritative fields, but keep any live percent/stage/message/
  // step_* already present in the store so an in-flight progress bar isn't
  // reset by a re-seed triggered on a "new" event.
  merge: (apiJobs) =>
    set((state) => {
      const jobs = { ...state.jobs }
      for (const j of apiJobs) {
        const existing = jobs[j.id]
        if (existing) {
          jobs[j.id] = {
            ...liveJobFromApi(j),
            // preserve live progress if the store already had it
            percent: existing.percent,
            stage: existing.stage,
            message: existing.message,
            step_index: existing.step_index,
            step_total: existing.step_total,
          }
        } else if (j.status === "queued" || j.status === "running") {
          // Don't resurrect a job the store doesn't know about unless it's
          // still active. GET /api/jobs?limit=200 keeps returning recently
          // finished jobs after they finish, so a terminal job absent from
          // the store here means the user already watched it fade out and
          // remove() dropped it - re-adding it on the next merge (e.g. a
          // "new"-triggered re-seed) would make a dismissed card reappear.
          jobs[j.id] = liveJobFromApi(j)
        }
      }
      return { jobs }
    }),

  replace: (apiJobs) =>
    set((state) => {
      const jobs: Record<string, LiveJob> = { ...state.jobs }
      for (const j of apiJobs) {
        const existing = state.jobs[j.id]
        if (existing || j.status === "queued" || j.status === "running") {
          // Wholesale adopt server values, including progress. See the
          // comment on the type declaration for why this differs from
          // merge() and when each should be called.
          jobs[j.id] = liveJobFromApi(j)
        }
      }
      return { jobs }
    }),

  remove: (jobId) =>
    set((state) => {
      const jobs = { ...state.jobs }
      delete jobs[jobId]
      return { jobs }
    }),
}))

// Pure selector: is there already a queued/running job for this exact
// media_path + language_code? Used to gate the History page's Retry button
// off live store state, so it correctly disappears once a retry is in
// flight and reappears if that retry itself later fails.
export function selectHasActiveJobForSource(
  jobs: Record<string, LiveJob>,
  mediaPath: string,
  languageCode: string | null,
): boolean {
  return Object.values(jobs).some(
    (j) =>
      j.media_path === mediaPath &&
      j.language_code === languageCode &&
      (j.status === "queued" || j.status === "running"),
  )
}
