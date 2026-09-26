import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { HistoryPage } from "./HistoryPage"
import { api } from "@/lib/api"
import { useJobsStore } from "@/lib/jobsStore"
import type { JobResponse } from "@/lib/types"

const EMPTY_HISTORY = {
  jobs: [],
  stats: {
    total_jobs: 0,
    total_cost_usd: 0,
    total_audio_length_seconds: 0,
    total_runtime_seconds: 0,
    average_cost_usd: 0,
    average_audio_length_seconds: 0,
    average_runtime_seconds: 0,
    count_by_status: {},
    count_by_language: {},
    count_by_source: {},
  },
  total: 0,
  limit: 20,
  offset: 0,
}

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
}))

const BASE_JOB: JobResponse = {
  id: "job-1",
  status: "done",
  source: "bazarr_movie",
  source_ref: "1",
  media_path: "/movies/test.mkv",
  title: "Test Movie (2024)",
  series_title: null,
  season_number: null,
  episode_number: null,
  output_path: "/movies/test.und.srt",
  language_code: "und",
  language_mode: "auto",
  mistral_detected_language: null,
  needs_language_review: true,
  output_format: "srt",
  priority: 0,
  progress_percent: 100,
  progress_message: null,
  progress_stage: null,
  progress_step_index: null,
  progress_step_total: null,
  cancel_requested: false,
  worker_id: null,
  audio_duration_seconds: 60,
  runtime_seconds: 60,
  mistral_usage_json: null,
  estimated_cost_usd: 0.01,
  error_message: null,
  created_at: "2024-01-01T00:00:00Z",
  started_at: "2024-01-01T00:00:00Z",
  finished_at: "2024-01-01T00:01:00Z",
  updated_at: "2024-01-01T00:01:00Z",
}

function historyWith(jobs: JobResponse[]) {
  return { ...EMPTY_HISTORY, jobs, total: jobs.length }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

// A plain failed job with no output_exists sentinel and no language-review
// flag - the ordinary case the M12 Retry button targets.
const FAILED_JOB: JobResponse = {
  ...BASE_JOB,
  status: "failed",
  needs_language_review: false,
  language_code: "en",
  language_mode: "explicit",
  error_message: "transcription crashed",
}

describe("HistoryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue(EMPTY_HISTORY)
    // Reset the live jobs store between tests - the Retry button's
    // visibility is derived from it.
    useJobsStore.setState({ jobs: {}, pendingNewCount: 0, lastTerminalJobId: null })
  })

  it("opens the Status filter without throwing Radix empty-value error", async () => {
    const user = userEvent.setup()
    render(<HistoryPage />, { wrapper })

    // The filter form renders immediately (not gated by loading state).
    // Before the fix, Radix throws when it encounters <SelectItem value="">.
    const [statusTrigger] = await screen.findAllByRole("combobox")
    await user.click(statusTrigger)

    // The trigger shows the placeholder and the dropdown item shows the label;
    // both contain this text so use getAllByText.
    expect(screen.getAllByText("All statuses").length).toBeGreaterThan(0)
  })

  it("opens the Source filter without throwing Radix empty-value error", async () => {
    const user = userEvent.setup()
    render(<HistoryPage />, { wrapper })

    const triggers = await screen.findAllByRole("combobox")
    await user.click(triggers[1])

    expect(screen.getAllByText("All sources").length).toBeGreaterThan(0)
  })

  it("renders each job status exactly once", async () => {
    vi.mocked(api.get).mockResolvedValue(historyWith([BASE_JOB]))

    render(<HistoryPage />, { wrapper })

    await waitFor(() => {
      expect(screen.getAllByText("Done")).toHaveLength(1)
    })
  })

  it("shows a review warning and lets the user correct an 'und' auto-mode job", async () => {
    vi.mocked(api.get).mockResolvedValue(historyWith([BASE_JOB]))
    vi.mocked(api.patch).mockResolvedValue({
      ...BASE_JOB,
      language_code: "fr",
      needs_language_review: false,
      output_path: "/movies/test.fr.srt",
    })
    const user = userEvent.setup()

    render(<HistoryPage />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("und")).toBeInTheDocument()
    })
    await user.click(screen.getByTitle(/couldn't detect a language/i))

    await waitFor(() => {
      expect(screen.getByText("Set language")).toBeInTheDocument()
    })
    const dialog = within(screen.getByRole("dialog"))
    const input = dialog.getByPlaceholderText("e.g. en, fr")
    await user.clear(input)
    await user.type(input, "fr")
    await user.click(dialog.getByText("Save"))

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith("/api/jobs/job-1/language", {
        language_code: "fr",
      })
    })
  })

  it("does not show the review icon for a job that doesn't need review", async () => {
    vi.mocked(api.get).mockResolvedValue(
      historyWith([{ ...BASE_JOB, needs_language_review: false, language_code: "fr" }]),
    )

    render(<HistoryPage />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("fr")).toBeInTheDocument()
    })
    expect(screen.queryByTitle(/couldn't detect a language/i)).not.toBeInTheDocument()
  })

  it("shows a passive mismatch icon when an explicit pick disagrees with Mistral's detection", async () => {
    vi.mocked(api.get).mockResolvedValue(
      historyWith([
        {
          ...BASE_JOB,
          language_mode: "explicit",
          language_code: "en",
          mistral_detected_language: "fr",
          needs_language_review: false,
        },
      ]),
    )

    render(<HistoryPage />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("en")).toBeInTheDocument()
    })
    expect(
      screen.getByTitle('Selected "en", but Mistral detected "fr"'),
    ).toBeInTheDocument()
  })

  it("does not show the mismatch icon when the explicit pick matches Mistral's detection", async () => {
    vi.mocked(api.get).mockResolvedValue(
      historyWith([
        {
          ...BASE_JOB,
          language_mode: "explicit",
          language_code: "fr",
          mistral_detected_language: "fr",
          needs_language_review: false,
        },
      ]),
    )

    render(<HistoryPage />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("fr")).toBeInTheDocument()
    })
    expect(screen.queryByTitle(/Mistral detected/)).not.toBeInTheDocument()
  })

  it("shows Runtime and Audio Length columns instead of Duration", async () => {
    vi.mocked(api.get).mockResolvedValue(historyWith([BASE_JOB]))

    render(<HistoryPage />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("Runtime")).toBeInTheDocument()
    })
    expect(screen.getByText("Audio Length")).toBeInTheDocument()
    expect(screen.queryByText("Duration")).not.toBeInTheDocument()
  })

  it("displays job runtime and audio length values in the table", async () => {
    vi.mocked(api.get).mockResolvedValue(historyWith([BASE_JOB]))

    render(<HistoryPage />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText("Runtime")).toBeInTheDocument()
    })
    expect(screen.getAllByText("1m 0s")).toHaveLength(2)
  })

  describe("Item column (label snapshot)", () => {
    it("shows the headline, type badge, and Bazarr ref for a fully-snapshotted episode job", async () => {
      const episodeJob: JobResponse = {
        ...BASE_JOB,
        id: "job-episode",
        source: "bazarr_episode",
        source_ref: "456",
        title: "Episode 1",
        series_title: "The Parisian Agency",
        season_number: 4,
        episode_number: 1,
      }
      vi.mocked(api.get).mockResolvedValue(historyWith([episodeJob]))

      render(<HistoryPage />, { wrapper })

      // The name and its "S04E01" code sit in separate sibling nodes (kept
      // that way so only the name truncates), so match on the cell's full
      // textContent rather than getByText's default (per-node-own-text)
      // matching, which would only ever see one fragment at a time.
      await waitFor(() => {
        expect(
          screen.getByText(
            (_, element) => element?.textContent === "The Parisian Agency · S04E01",
          ),
        ).toBeInTheDocument()
      })
      expect(screen.getByText("episode")).toBeInTheDocument()
      expect(screen.getByText("· Bazarr #456")).toBeInTheDocument()
      expect(
        screen.getByTitle("The Parisian Agency · S04E01 · Episode 1"),
      ).toBeInTheDocument()
    })

    it("falls back to the file name (and no Bazarr ref) for a job with no media snapshot", async () => {
      const manualJob: JobResponse = {
        ...BASE_JOB,
        id: "job-manual",
        source: "manual",
        source_ref: null,
        title: null,
        series_title: null,
        season_number: null,
        episode_number: null,
        media_path: "/uploads/clip.mp4",
      }
      vi.mocked(api.get).mockResolvedValue(historyWith([manualJob]))

      render(<HistoryPage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByText("clip.mp4")).toBeInTheDocument()
      })
      expect(screen.getByText("manual")).toBeInTheDocument()
      expect(screen.queryByText(/Bazarr #/)).not.toBeInTheDocument()
    })
  })

  describe("Retry action (M12)", () => {
    it("renders a Retry button for a plain failed job", async () => {
      vi.mocked(api.get).mockResolvedValue(historyWith([FAILED_JOB]))

      render(<HistoryPage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
      })
    })

    it("does not render Retry for done/running/queued jobs", async () => {
      for (const status of ["done", "running", "queued"] as const) {
        vi.mocked(api.get).mockResolvedValue(
          historyWith([{ ...FAILED_JOB, status, needs_language_review: false }]),
        )
        const { unmount } = render(<HistoryPage />, { wrapper })
        await waitFor(() => {
          expect(screen.getByText(FAILED_JOB.media_path)).toBeInTheDocument()
        })
        expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
        unmount()
      }
    })

    it("does not render Retry for a job already covered by output_exists (Overwrite & retry)", async () => {
      vi.mocked(api.get).mockResolvedValue(
        historyWith([
          {
            ...FAILED_JOB,
            error_message: "output_exists: refusing to overwrite",
          },
        ]),
      )

      render(<HistoryPage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Overwrite & retry" })).toBeInTheDocument()
      })
      expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
    })

    it("does not render Retry for a job already covered by needs_language_review (Rename)", async () => {
      vi.mocked(api.get).mockResolvedValue(
        historyWith([{ ...FAILED_JOB, needs_language_review: true }]),
      )

      render(<HistoryPage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Rename" })).toBeInTheDocument()
      })
      expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
    })

    it("clicking Retry posts buildRetry(job)'s exact payload, shows a success toast, and hides Retry once the store reflects the new queued job", async () => {
      vi.mocked(api.get).mockResolvedValue(historyWith([FAILED_JOB]))
      const newJob: JobResponse = {
        ...FAILED_JOB,
        id: "job-retry-1",
        status: "queued",
        error_message: null,
      }
      vi.mocked(api.post).mockResolvedValue(newJob)
      const user = userEvent.setup()

      render(<HistoryPage />, { wrapper })

      const retryButton = await screen.findByRole("button", { name: "Retry" })
      await user.click(retryButton)

      await waitFor(() => {
        expect(api.post).toHaveBeenCalledWith("/api/jobs", {
          source: FAILED_JOB.source,
          source_ref: FAILED_JOB.source_ref,
          media_path: FAILED_JOB.media_path,
          output_path: FAILED_JOB.output_path,
          language_code: FAILED_JOB.language_code,
          language_mode: FAILED_JOB.language_mode,
          output_format: FAILED_JOB.output_format,
          overwrite: true,
        })
      })

      await waitFor(() => {
        expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
      })
    })

    it("does not affect the existing Overwrite & retry / Rename buttons", async () => {
      vi.mocked(api.get).mockResolvedValue(
        historyWith([
          { ...FAILED_JOB, id: "job-overwrite", error_message: "output_exists: nope" },
          { ...FAILED_JOB, id: "job-rename", needs_language_review: true },
        ]),
      )

      render(<HistoryPage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Overwrite & retry" })).toBeInTheDocument()
      })
      expect(screen.getByRole("button", { name: "Rename" })).toBeInTheDocument()
    })
  })

  it("renders Apply before Reset as an equal-width matched pair (M13)", async () => {
    render(<HistoryPage />, { wrapper })

    const applyButton = await screen.findByRole("button", { name: /apply/i })
    const resetButton = screen.getByRole("button", { name: /reset/i })

    const allButtons = screen.getAllByRole("button")
    expect(allButtons.indexOf(applyButton)).toBeLessThan(
      allButtons.indexOf(resetButton),
    )

    // Both buttons share the same width-related utility class so they read
    // as a matched pair.
    const applyClasses = applyButton.className.split(/\s+/)
    const resetClasses = resetButton.className.split(/\s+/)
    const sharedWidthClass = applyClasses.find(
      (c) => resetClasses.includes(c) && /^(flex-1|w-)/.test(c),
    )
    expect(sharedWidthClass).toBeTruthy()
  })

  it("Apply submits the language filter and Reset clears it", async () => {
    const user = userEvent.setup()
    render(<HistoryPage />, { wrapper })

    const languageInput = await screen.findByPlaceholderText("e.g. en, fr")
    await user.type(languageInput, "fr")
    await user.click(screen.getByRole("button", { name: /apply/i }))

    await waitFor(() => {
      const calls = vi.mocked(api.get).mock.calls
      const lastUrl = calls[calls.length - 1]?.[0] as string
      expect(lastUrl).toContain("language_filter=fr")
    })

    await user.click(screen.getByRole("button", { name: /reset/i }))

    await waitFor(() => {
      const calls = vi.mocked(api.get).mock.calls
      const lastUrl = calls[calls.length - 1]?.[0] as string
      expect(lastUrl).not.toContain("language_filter")
    })
    // Reset also clears the input back to empty. The filter form remounts
    // on each refetch (HistoryPage shows a full-page loading state while
    // fetching), so re-query rather than reuse the earlier `languageInput`
    // reference, which points at a now-detached node.
    await waitFor(() => {
      expect(screen.getByPlaceholderText("e.g. en, fr")).toHaveValue("")
    })
  })
})
