// Pure label-composition helpers for jobs, built on top of lib/mediaLabels.ts.
// Structural input so both `JobResponse` (REST) and `LiveJob` (the SSE-fed
// Zustand store) satisfy it without a conversion step. Used by the Queue,
// History and Job detail pages so a given job reads the same way everywhere.

import {
  episodeCode,
  fullLabel,
  headline,
  itemName,
  type MediaLabelInput,
} from "./mediaLabels"

export interface JobLabelInput {
  source: string
  source_ref: string | null
  media_path: string
  // Label snapshot (migration 0008) - see JobResponse's field comment in
  // lib/types.ts for the null semantics.
  title: string | null
  series_title: string | null
  season_number: number | null
  episode_number: number | null
}

function isBazarrSource(source: string): boolean {
  return source === "bazarr_movie" || source === "bazarr_episode"
}

// Adapts a job's snapshot fields to `MediaLabelInput`. Null when there's no
// snapshot to show: a manual job, or a Bazarr job predating this feature
// whose cache row was already gone by the time migration 0008's historical
// backfill ran (title itself is then falsy - see migration 0008; a
// freshly-created Bazarr job always has a title, since job creation is
// rejected outright if no cache row matches).
export function jobMedia(job: JobLabelInput): MediaLabelInput | null {
  if (!isBazarrSource(job.source) || !job.title) return null
  return {
    kind: job.source === "bazarr_movie" ? "movie" : "episode",
    title: job.title,
    series_title: job.series_title,
    season_number: job.season_number,
    episode_number: job.episode_number,
  }
}

// The trailing segment of a filesystem path, for the fallback label when a
// job has no media snapshot. Path separators are normalized so a
// Windows-style path (possible via a path-mapped Bazarr root) still yields
// just the filename rather than the whole string.
export function mediaFileName(mediaPath: string): string {
  const parts = mediaPath.replace(/\\/g, "/").split("/")
  return parts[parts.length - 1] ?? mediaPath
}

// "Bazarr #13373" for a Bazarr job with a reference id; null for a manual
// job (even one that happens to carry a stray source_ref) or a Bazarr job
// missing one.
export function bazarrRef(job: JobLabelInput): string | null {
  if (!isBazarrSource(job.source) || !job.source_ref) return null
  return `Bazarr #${job.source_ref}`
}

// Name + episode code for a one-line heading (e.g. the Queue card's first
// line), kept as separate strings so the UI can truncate the name without
// ever truncating the code.
export function jobHeadline(job: JobLabelInput): { name: string; code: string | null } {
  const media = jobMedia(job)
  if (!media) return { name: mediaFileName(job.media_path), code: null }
  return { name: itemName(media), code: episodeCode(media) }
}

// The parts that lead a job's subtitle line, before language/format/ref are
// appended by the caller:
//   - Episode, full or partial snapshot: the bare episode title, if it adds
//     information beyond what jobHeadline() already showed (i.e. a series
//     name is known - see mediaLabels.fullLabel's identical rule).
//   - Movie: the literal "Movie" (movies have nothing else to show here).
//   - No snapshot (manual, or a pre-existing Bazarr job backfilled with no
//     matching cache row): none.
export function jobSubtitleParts(job: JobLabelInput): string[] {
  const media = jobMedia(job)
  if (!media) return []
  if (media.kind === "movie") return ["Movie"]
  return media.series_title != null ? [media.title] : []
}

// As much identifying detail as is available, for a single-line context
// (e.g. the Job detail page title): the shared fullLabel() when there's a
// snapshot, or the file name otherwise.
export function jobFullLabel(job: JobLabelInput): string {
  const media = jobMedia(job)
  return media ? fullLabel(media) : mediaFileName(job.media_path)
}

// Re-exported so callers that already have a JobLabelInput don't need a
// separate import just to build the headline's own combined string (e.g. a
// `title=` tooltip attribute).
export function jobHeadlineText(job: JobLabelInput): string {
  const media = jobMedia(job)
  return media ? headline(media) : mediaFileName(job.media_path)
}
