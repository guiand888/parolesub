// Shared test-fixture factories for backend response shapes with many
// required fields, so adding a field to one of these types is a one-place
// change instead of touching every hand-built literal across the test
// suite.

import type { JobResponse } from "@/lib/types"

export function makeJobResponse(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: "job-1",
    status: "queued",
    source: "manual",
    source_ref: null,
    media_path: "/path/to/file.mp4",
    title: null,
    series_title: null,
    season_number: null,
    episode_number: null,
    output_path: null,
    language_code: null,
    language_mode: "explicit",
    mistral_detected_language: null,
    needs_language_review: false,
    output_format: "srt",
    priority: 0,
    progress_percent: 0,
    progress_message: null,
    progress_stage: null,
    progress_step_index: null,
    progress_step_total: null,
    cancel_requested: false,
    worker_id: null,
    audio_duration_seconds: null,
    runtime_seconds: null,
    mistral_usage_json: null,
    estimated_cost_usd: null,
    error_message: null,
    created_at: "2024-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2024-01-01T00:00:00Z",
    ...overrides,
  }
}
