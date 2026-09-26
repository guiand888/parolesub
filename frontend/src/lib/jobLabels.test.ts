import { describe, it, expect } from "vitest"
import {
  jobMedia,
  mediaFileName,
  bazarrRef,
  jobHeadline,
  jobHeadlineText,
  jobSubtitleParts,
  jobFullLabel,
  type JobLabelInput,
} from "./jobLabels"

function makeJob(overrides: Partial<JobLabelInput> = {}): JobLabelInput {
  return {
    source: "manual",
    source_ref: null,
    media_path: "/tv/Show/Season 01/Show - S01E01 - Title.mkv",
    title: null,
    series_title: null,
    season_number: null,
    episode_number: null,
    ...overrides,
  }
}

describe("mediaFileName", () => {
  it("returns the last path segment", () => {
    expect(mediaFileName("/a/b/file.mkv")).toBe("file.mkv")
  })

  it("normalizes Windows-style separators", () => {
    expect(mediaFileName("C:\\media\\Show\\file.mkv")).toBe("file.mkv")
  })
})

describe("jobMedia", () => {
  it("is null for a manual job", () => {
    expect(jobMedia(makeJob({ source: "manual" }))).toBeNull()
  })

  it("is null for a Bazarr job with no snapshot (cache row was gone)", () => {
    const job = makeJob({ source: "bazarr_episode", source_ref: "999", title: null })
    expect(jobMedia(job)).toBeNull()
  })

  it("is null for a Bazarr job with an empty-string title (treated the same as no snapshot)", () => {
    const job = makeJob({ source: "bazarr_episode", source_ref: "999", title: "" })
    expect(jobMedia(job)).toBeNull()
  })

  it("maps a job with season 0 (specials) - a real value, not a missing one", () => {
    const job = makeJob({
      source: "bazarr_episode",
      source_ref: "500",
      title: "Christmas Special",
      series_title: "The Example Show",
      season_number: 0,
      episode_number: 1,
    })
    expect(jobMedia(job)).toEqual({
      kind: "episode",
      title: "Christmas Special",
      series_title: "The Example Show",
      season_number: 0,
      episode_number: 1,
    })
  })

  it("maps a fully-snapshotted episode job", () => {
    const job = makeJob({
      source: "bazarr_episode",
      source_ref: "4242",
      title: "Episode 1",
      series_title: "The Example Show",
      season_number: 4,
      episode_number: 1,
    })
    expect(jobMedia(job)).toEqual({
      kind: "episode",
      title: "Episode 1",
      series_title: "The Example Show",
      season_number: 4,
      episode_number: 1,
    })
  })

  it("maps a movie job", () => {
    const job = makeJob({ source: "bazarr_movie", source_ref: "200", title: "Dune (2021)" })
    expect(jobMedia(job)).toEqual({
      kind: "movie",
      title: "Dune (2021)",
      series_title: null,
      season_number: null,
      episode_number: null,
    })
  })
})

describe("bazarrRef", () => {
  it("formats a Bazarr episode ref", () => {
    expect(bazarrRef(makeJob({ source: "bazarr_episode", source_ref: "4242" }))).toBe(
      "Bazarr #4242",
    )
  })

  it("formats a Bazarr movie ref", () => {
    expect(bazarrRef(makeJob({ source: "bazarr_movie", source_ref: "200" }))).toBe(
      "Bazarr #200",
    )
  })

  it("is null for a manual job even with a stray source_ref", () => {
    expect(bazarrRef(makeJob({ source: "manual", source_ref: "789" }))).toBeNull()
  })

  it("is null for a Bazarr job missing a source_ref", () => {
    expect(bazarrRef(makeJob({ source: "bazarr_episode", source_ref: null }))).toBeNull()
  })
})

describe("degraded states (Queue/History/Detail label table)", () => {
  it("episode, full snapshot", () => {
    const job = makeJob({
      source: "bazarr_episode",
      source_ref: "4242",
      title: "Episode 1",
      series_title: "The Example Show",
      season_number: 4,
      episode_number: 1,
    })
    expect(jobHeadline(job)).toEqual({ name: "The Example Show", code: "S04E01" })
    expect(jobHeadlineText(job)).toBe("The Example Show · S04E01")
    expect(jobSubtitleParts(job)).toEqual(["Episode 1"])
    expect(jobFullLabel(job)).toBe("The Example Show · S04E01 · Episode 1")
  })

  it("episode, season 0 (a special) - a real code, not treated as missing", () => {
    const job = makeJob({
      source: "bazarr_episode",
      source_ref: "500",
      title: "Christmas Special",
      series_title: "The Example Show",
      season_number: 0,
      episode_number: 1,
    })
    expect(jobHeadline(job)).toEqual({ name: "The Example Show", code: "S00E01" })
    expect(jobFullLabel(job)).toBe("The Example Show · S00E01 · Christmas Special")
  })

  it("episode, series known but numbers not backfilled", () => {
    const job = makeJob({
      source: "bazarr_episode",
      source_ref: "4242",
      title: "Episode 1",
      series_title: "The Example Show",
      season_number: null,
      episode_number: null,
    })
    expect(jobHeadline(job)).toEqual({ name: "The Example Show", code: null })
    expect(jobSubtitleParts(job)).toEqual(["Episode 1"])
    expect(jobFullLabel(job)).toBe("The Example Show · Episode 1")
  })

  it("episode, title only (flattened snapshot taken before the first sync)", () => {
    const job = makeJob({
      source: "bazarr_episode",
      source_ref: "300",
      title: "The Example Show - Episode 3",
      series_title: null,
      season_number: null,
      episode_number: null,
    })
    expect(jobHeadline(job)).toEqual({ name: "The Example Show - Episode 3", code: null })
    expect(jobSubtitleParts(job)).toEqual([])
    expect(jobFullLabel(job)).toBe("The Example Show - Episode 3")
  })

  it("movie", () => {
    const job = makeJob({ source: "bazarr_movie", source_ref: "200", title: "Dune (2021)" })
    expect(jobHeadline(job)).toEqual({ name: "Dune (2021)", code: null })
    expect(jobSubtitleParts(job)).toEqual(["Movie"])
    expect(jobFullLabel(job)).toBe("Dune (2021)")
  })

  it("Bazarr job with no snapshot falls back to the file name", () => {
    const job = makeJob({
      source: "bazarr_episode",
      source_ref: "999",
      title: null,
      media_path: "/tv/Gone/Season 01/Gone - S01E01.mkv",
    })
    expect(jobHeadline(job)).toEqual({ name: "Gone - S01E01.mkv", code: null })
    expect(jobSubtitleParts(job)).toEqual([])
    expect(jobFullLabel(job)).toBe("Gone - S01E01.mkv")
    expect(bazarrRef(job)).toBe("Bazarr #999")
  })

  it("manual job falls back to the file name with no Bazarr ref", () => {
    const job = makeJob({ source: "manual", media_path: "/uploads/clip.mp4" })
    expect(jobHeadline(job)).toEqual({ name: "clip.mp4", code: null })
    expect(jobSubtitleParts(job)).toEqual([])
    expect(jobFullLabel(job)).toBe("clip.mp4")
    expect(bazarrRef(job)).toBeNull()
  })
})
