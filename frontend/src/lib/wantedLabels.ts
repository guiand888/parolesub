// Pure label-composition helpers for Wanted items. Kept separate from
// WantedPage.tsx so they're unit-testable without rendering the table, and
// so the various degraded/backfill states (series_title, season_number,
// episode_number all nullable - see lib/types.ts) are handled in one place.

import type { WantedItem } from "./types"

// The "series or movie" name shown in the primary column.
// Episode: falls back to the item's own bare title when series_title
// hasn't backfilled yet, so the row still shows *something* instead of
// a blank cell.
export function itemName(item: WantedItem): string {
  if (item.kind === "episode") {
    return item.series_title ?? item.title
  }
  return item.title
}

// "S04E01"-style code, zero-padded to at least 2 digits each (numbers above
// 99 just widen naturally, e.g. "S100E01"). Null for movies, or for an
// episode missing either number (not yet backfilled).
export function episodeCode(item: WantedItem): string | null {
  if (item.kind !== "episode") return null
  if (item.season_number == null || item.episode_number == null) return null
  const season = String(item.season_number).padStart(2, "0")
  const episode = String(item.episode_number).padStart(2, "0")
  return `S${season}E${episode}`
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
export function fullLabel(item: WantedItem): string {
  if (item.kind !== "episode") return item.title

  const parts: string[] = [itemName(item)]

  const code = episodeCode(item)
  if (code) parts.push(code)

  // Only append the bare episode title as its own part when itemName()
  // didn't already fall back to it (i.e. series_title is present).
  if (item.series_title != null) parts.push(item.title)

  return parts.join(" · ")
}
