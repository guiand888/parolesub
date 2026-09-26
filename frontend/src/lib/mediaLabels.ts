// Pure label-composition helpers for anything shaped like a Bazarr movie or
// episode - Wanted items and, via lib/jobLabels.ts, jobs carrying a media
// label snapshot. Kept separate from any one page so they're unit-testable
// without rendering a table, and so the various degraded/backfill states
// (series_title, season_number, episode_number all nullable) are handled in
// one place.

// Structural, not `WantedItem` itself: `WantedItem` satisfies this as-is,
// and lib/jobLabels.ts adapts a `JobResponse`'s snapshot fields to it too.
export interface MediaLabelInput {
  kind: "movie" | "episode"
  title: string
  series_title: string | null
  season_number: number | null
  episode_number: number | null
}

// The "series or movie" name shown in the primary column.
// Episode: falls back to the item's own bare title when series_title
// hasn't backfilled yet, so the row still shows *something* instead of
// a blank cell.
export function itemName(item: MediaLabelInput): string {
  if (item.kind === "episode") {
    return item.series_title ?? item.title
  }
  return item.title
}

// "S04E01"-style code, zero-padded to at least 2 digits each (numbers above
// 99 just widen naturally, e.g. "S100E01"). Null for movies, or for an
// episode missing either number (not yet backfilled).
export function episodeCode(item: MediaLabelInput): string | null {
  if (item.kind !== "episode") return null
  if (item.season_number == null || item.episode_number == null) return null
  const season = String(item.season_number).padStart(2, "0")
  const episode = String(item.episode_number).padStart(2, "0")
  return `S${season}E${episode}`
}

// "<name> · S04E01", or just the name when there's no code yet. For a
// one-line heading (e.g. the Queue card title) that doesn't need the bare
// episode title too - see fullLabel() for that.
export function headline(item: MediaLabelInput): string {
  const code = episodeCode(item)
  return code ? `${itemName(item)} · ${code}` : itemName(item)
}

// Full label for contexts (e.g. the Transcribe dialog header) that want as
// much identifying detail as is available, degrading gracefully as
// series_title / season_number / episode_number backfill in:
//   - Movie: item.title
//   - Episode with full data: "<series> · S04E01 · <episode title>"
//   - Episode missing the code (not backfilled): "<series-or-title> · <episode title>"
//   - Episode with neither series_title nor a code: just item.title (avoids
//     the "Episode 1 · Episode 1" duplication that a naive template would
//     produce once itemName() has already fallen back to item.title).
export function fullLabel(item: MediaLabelInput): string {
  if (item.kind !== "episode") return item.title

  const parts: string[] = [itemName(item)]

  const code = episodeCode(item)
  if (code) parts.push(code)

  // Only append the bare episode title as its own part when itemName()
  // didn't already fall back to it (i.e. series_title is present).
  if (item.series_title != null) parts.push(item.title)

  return parts.join(" · ")
}
