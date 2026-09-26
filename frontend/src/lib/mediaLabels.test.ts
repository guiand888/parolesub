import { describe, it, expect } from "vitest"
import { itemName, episodeCode, fullLabel, headline } from "./mediaLabels"
import type { WantedItem } from "./types"

// Minimal WantedItem builder - only the fields these helpers actually read
// vary per test; everything else is filler to satisfy the type.
function makeItem(overrides: Partial<WantedItem> = {}): WantedItem {
  return {
    id: "movie:1",
    kind: "movie",
    ext_id: 1,
    title: "Some Title",
    media_path: "/media/some.mkv",
    has_any_subs: false,
    missing_subtitles: [],
    audio_language: [],
    last_polled: "2024-01-01T00:00:00Z",
    active_job_id: null,
    active_job_status: null,
    active_job_progress: null,
    series_title: null,
    season_number: null,
    episode_number: null,
    ...overrides,
  }
}

describe("itemName", () => {
  it("returns the movie title for a movie", () => {
    const item = makeItem({ kind: "movie", title: "The Matrix" })
    expect(itemName(item)).toBe("The Matrix")
  })

  it("returns series_title for a backfilled episode", () => {
    const item = makeItem({
      kind: "episode",
      title: "Pilot",
      series_title: "Breaking Bad",
    })
    expect(itemName(item)).toBe("Breaking Bad")
  })

  it("falls back to the bare title when series_title hasn't backfilled yet", () => {
    const item = makeItem({
      kind: "episode",
      title: "Episode 1",
      series_title: null,
    })
    expect(itemName(item)).toBe("Episode 1")
  })
})

describe("episodeCode", () => {
  it("returns null for a movie", () => {
    const item = makeItem({ kind: "movie", season_number: 1, episode_number: 1 })
    expect(episodeCode(item)).toBeNull()
  })

  it("formats season and episode zero-padded to 2 digits", () => {
    const item = makeItem({
      kind: "episode",
      season_number: 4,
      episode_number: 1,
    })
    expect(episodeCode(item)).toBe("S04E01")
  })

  it("pads single-digit season and episode independently", () => {
    const item = makeItem({
      kind: "episode",
      season_number: 1,
      episode_number: 9,
    })
    expect(episodeCode(item)).toBe("S01E09")
  })

  it("widens naturally instead of truncating for numbers above 99", () => {
    const item = makeItem({
      kind: "episode",
      season_number: 100,
      episode_number: 1,
    })
    expect(episodeCode(item)).toBe("S100E01")
  })

  it("does not pad numbers already 2+ digits", () => {
    const item = makeItem({
      kind: "episode",
      season_number: 12,
      episode_number: 34,
    })
    expect(episodeCode(item)).toBe("S12E34")
  })

  it("returns null when season_number is missing (not backfilled)", () => {
    const item = makeItem({
      kind: "episode",
      season_number: null,
      episode_number: 1,
    })
    expect(episodeCode(item)).toBeNull()
  })

  it("returns null when episode_number is missing (not backfilled)", () => {
    const item = makeItem({
      kind: "episode",
      season_number: 1,
      episode_number: null,
    })
    expect(episodeCode(item)).toBeNull()
  })

  it("returns null when both numbers are missing", () => {
    const item = makeItem({ kind: "episode", season_number: null, episode_number: null })
    expect(episodeCode(item)).toBeNull()
  })

  it("formats season 0 (specials) as a real code, not as missing", () => {
    // season_number: 0 is a real, distinct state elsewhere in this codebase
    // (SEASON_ORDER_BUCKET_SQL buckets it as "specials", separately from
    // NULL/"unknown") - `== null` must not treat the falsy 0 as missing.
    const item = makeItem({ kind: "episode", season_number: 0, episode_number: 5 })
    expect(episodeCode(item)).toBe("S00E05")
  })
})

describe("headline", () => {
  it("returns the bare title for a movie", () => {
    const item = makeItem({ kind: "movie", title: "The Matrix" })
    expect(headline(item)).toBe("The Matrix")
  })

  it("appends the episode code when backfilled", () => {
    const item = makeItem({
      kind: "episode",
      title: "Pilot",
      series_title: "Breaking Bad",
      season_number: 4,
      episode_number: 1,
    })
    expect(headline(item)).toBe("Breaking Bad · S04E01")
  })

  it("omits the code and falls back to the bare title when nothing backfilled", () => {
    const item = makeItem({
      kind: "episode",
      title: "Episode 1",
      series_title: null,
      season_number: null,
      episode_number: null,
    })
    expect(headline(item)).toBe("Episode 1")
  })
})

describe("fullLabel", () => {
  it("returns the bare title for a movie", () => {
    const item = makeItem({ kind: "movie", title: "The Matrix" })
    expect(fullLabel(item)).toBe("The Matrix")
  })

  it("composes series · SxxEyy · episode title when fully backfilled", () => {
    const item = makeItem({
      kind: "episode",
      title: "Pilot",
      series_title: "Breaking Bad",
      season_number: 1,
      episode_number: 1,
    })
    expect(fullLabel(item)).toBe("Breaking Bad · S01E01 · Pilot")
  })

  it("omits the episode code when season/episode numbers haven't backfilled", () => {
    const item = makeItem({
      kind: "episode",
      title: "Pilot",
      series_title: "Breaking Bad",
      season_number: null,
      episode_number: null,
    })
    expect(fullLabel(item)).toBe("Breaking Bad · Pilot")
  })

  it("collapses to the bare title when nothing has backfilled, avoiding duplication", () => {
    // itemName() falls back to item.title when series_title is null, so a
    // naive "<itemName> · <title>" template would render "Episode 1 ·
    // Episode 1". fullLabel must collapse to just the bare title instead.
    const item = makeItem({
      kind: "episode",
      title: "Episode 1",
      series_title: null,
      season_number: null,
      episode_number: null,
    })
    expect(fullLabel(item)).toBe("Episode 1")
  })

  it("still shows the code when numbers backfilled ahead of series_title", () => {
    const item = makeItem({
      kind: "episode",
      title: "Episode 1",
      series_title: null,
      season_number: 1,
      episode_number: 1,
    })
    expect(fullLabel(item)).toBe("Episode 1 · S01E01")
  })

  it("zero-pads the code within the composed label", () => {
    const item = makeItem({
      kind: "episode",
      title: "Homecoming",
      series_title: "The Example Show",
      season_number: 4,
      episode_number: 1,
    })
    expect(fullLabel(item)).toBe("The Example Show · S04E01 · Homecoming")
  })
})
