import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { QueuePage } from "./QueuePage"
import { useJobsStore } from "@/lib/jobsStore"
import type { JobResponse } from "@/lib/types"
import { makeJobResponse } from "@/test/factories"

// Mock the API
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(
      public readonly status: number,
      public readonly detail: string,
    ) {
      super(detail)
      this.name = "ApiError"
    }
  },
}))

// Stable mock fns so tests can assert on hook return values.
const mockCreateMutate = vi.fn()
const mockCancelMutate = vi.fn()

// Stable return value for useJobs so tests can mutate `data` between renders
// (needed to simulate "refetch resolved with new server data"). The mock
// factory returns this same object reference each render so React's effect
// deps see the change when `data` is reassigned.
const mockUseJobsReturn: {
  data: { jobs: JobResponse[] } | undefined
  refetch: ReturnType<typeof vi.fn>
  isFetching: boolean
} = {
  data: undefined,
  refetch: vi.fn(),
  isFetching: false,
}

// Mock the hooks
vi.mock("@/hooks/useJobs", () => ({
  useJobs: () => mockUseJobsReturn,
  useCreateJob: () => ({
    mutate: mockCreateMutate,
    isPending: false,
  }),
  useCancelJob: () => ({
    mutate: mockCancelMutate,
    isPending: false,
  }),
}))

vi.mock("@/hooks/useJobsStream", () => ({
  useJobsStream: vi.fn(),
}))

// Mock the JobStatusIcon component
vi.mock("@/components/JobStatusIcon", () => ({
  JobStatusIcon: ({ status }: { status: string }) => <span>Status: {status}</span>,
}))

// No media label snapshot (title stays null) - exercises the file-name
// fallback path.
const MOCK_JOB_QUEUED: JobResponse = makeJobResponse({
  id: "job-1",
  status: "queued",
  source: "bazarr_movie",
  source_ref: "123",
  media_path: "/path/to/file1.mp4",
  language_code: "en",
  language_mode: "explicit",
  output_format: "srt",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
})

// Full media label snapshot - exercises the "The Parisian Agency · S04E01"
// headline + "Episode 1" subtitle rendering.
const MOCK_JOB_RUNNING: JobResponse = makeJobResponse({
  id: "job-2",
  status: "running",
  progress_percent: 50,
  progress_message: "Transcribing audio",
  progress_stage: "transcribe",
  progress_step_index: 2,
  progress_step_total: 3,
  source: "bazarr_episode",
  source_ref: "456",
  media_path: "/path/to/file2.mp4",
  title: "Episode 1",
  series_title: "The Parisian Agency",
  season_number: 4,
  episode_number: 1,
  language_code: "fr",
  language_mode: "explicit",
  output_format: "vtt",
  created_at: "2024-01-01T01:00:00Z",
  started_at: "2024-01-01T01:05:00Z",
  worker_id: "worker-1",
  updated_at: "2024-01-01T01:05:00Z",
})

// Manual job, no snapshot - exercises the file-name fallback with no
// "Bazarr #" ref.
const MOCK_JOB_DONE: JobResponse = makeJobResponse({
  id: "job-3",
  status: "done",
  progress_percent: 100,
  progress_stage: "done",
  source: "manual",
  source_ref: "789",
  media_path: "/path/to/file3.mp4",
  language_code: "es",
  language_mode: "explicit",
  output_format: "srt",
  created_at: "2024-01-01T02:00:00Z",
  started_at: "2024-01-01T02:05:00Z",
  finished_at: "2024-01-01T02:15:00Z",
  audio_duration_seconds: 120,
  runtime_seconds: 600,
  estimated_cost_usd: 0.01,
  output_path: "/path/to/output.srt",
  worker_id: "worker-1",
  updated_at: "2024-01-01T02:15:00Z",
})

const MOCK_JOB_FAILED: JobResponse = {
  ...MOCK_JOB_DONE,
  id: "job-4",
  status: "failed",
  finished_at: "2024-01-01T02:20:00Z",
  output_path: null,
  error_message: "Output file already exists (output_exists): /path/to/output.srt",
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe("QueuePage", () => {
  beforeEach(() => {
    // Reset store before each test
    useJobsStore.setState({
      jobs: {},
      pendingNewCount: 0,
      lastTerminalJobId: null,
    })
    // Reset useJobs mock state (clearAllMocks alone won't reset the
    // implementation or data, so reset explicitly).
    mockUseJobsReturn.data = undefined
    mockUseJobsReturn.isFetching = false
    mockUseJobsReturn.refetch = vi.fn()
    vi.clearAllMocks()
  })

  describe("Display", () => {
    it("shows 'No active jobs' when queue is empty", () => {
      render(<QueuePage />, { wrapper })
      expect(screen.getByText("No active jobs.")).toBeInTheDocument()
    })

    it("displays queued jobs in Queued section", async () => {
      // Seed the store directly (simulating API initialization)
      useJobsStore.getState().seed([MOCK_JOB_QUEUED])

      render(<QueuePage />, { wrapper })

      // Check that job title is displayed (this means the job is rendered in the appropriate section)
      // No media label snapshot, so the header falls back to the file name,
      // with the Bazarr ref surfaced in the subtitle line.
      await waitFor(() => {
        expect(screen.getByText("file1.mp4")).toBeInTheDocument()
      })
      expect(screen.getByText(/Bazarr #123/)).toBeInTheDocument()

      // Verify the job is in the store with queued status
      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("queued")
    })

    it("displays running jobs in Running section with progress bar", async () => {
      useJobsStore.getState().seed([MOCK_JOB_RUNNING])

      render(<QueuePage />, { wrapper })

      // Check job title and progress (these are more specific than just "RUNNING").
      // Full snapshot: headline is "The Parisian Agency" + "S04E01" (in
      // separate sibling spans), subtitle line carries the episode title and
      // the Bazarr ref.
      await waitFor(() => {
        expect(screen.getByText("The Parisian Agency")).toBeInTheDocument()
      })
      expect(screen.getByText(/S04E01/)).toBeInTheDocument()
      expect(screen.getByText(/Episode 1/)).toBeInTheDocument()
      expect(screen.getByText(/Bazarr #456/)).toBeInTheDocument()

      // Check progress info is displayed
      await waitFor(() => {
        expect(screen.getByText(/50%/)).toBeInTheDocument()
      })
      expect(
        screen.getByText(/Step 2 of 3 — Transcribing/),
      ).toBeInTheDocument()
    })

    it("displays jobs grouped by status", async () => {
      useJobsStore.getState().seed([MOCK_JOB_QUEUED, MOCK_JOB_RUNNING])

      render(<QueuePage />, { wrapper })

      await waitFor(() => {
        const text = screen.getAllByText(/RUNNING|QUEUED/i)
        expect(text.length).toBeGreaterThan(0)
      })
    })
  })

  describe("Store as single source of truth", () => {
    it("reads job state from Zustand store on initial render", async () => {
      // Set up store before rendering
      useJobsStore.getState().seed([MOCK_JOB_QUEUED])

      render(<QueuePage />, { wrapper })

      // Job should be visible from store
      await waitFor(() => {
        expect(screen.getByText("file1.mp4")).toBeInTheDocument()
      })
    })

    it("updates when store is mutated via apply()", async () => {
      // Start with queued job
      useJobsStore.getState().seed([MOCK_JOB_QUEUED])

      const { rerender } = render(<QueuePage />, { wrapper })

      // Initially queued - verify the job exists in store
      await waitFor(() => {
        const jobs = useJobsStore.getState().jobs
        expect(jobs["job-1"]).toBeDefined()
        expect(jobs["job-1"].status).toBe("queued")
      })

      // Manually apply a progress update to the store (simulating SSE event)
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 60,
        stage: "transcribing",
        message: "60% complete",
        step_index: null,
        step_total: null,
      })

      // Verify store was updated
      const updatedJob = useJobsStore.getState().jobs["job-1"]
      expect(updatedJob.status).toBe("running")
      expect(updatedJob.percent).toBe(60)

      rerender(<QueuePage />)

      // Should now show as running with updated progress
      await waitFor(() => {
        expect(screen.getByText(/60%/)).toBeInTheDocument()
      })
    })

    it("handles terminal job removal from store", async () => {
      useJobsStore.getState().seed([MOCK_JOB_DONE])
      useJobsStore.setState({ lastTerminalJobId: "job-3" })

      const { rerender } = render(<QueuePage />, { wrapper })

      // Job should initially be visible (manual job, no snapshot → file-name
      // fallback, and no Bazarr ref since it's not a Bazarr source).
      await waitFor(() => {
        expect(screen.getByText("file3.mp4")).toBeInTheDocument()
      })
      expect(screen.queryByText(/Bazarr #/)).not.toBeInTheDocument()

      // Remove job from store (simulating cleanup after animation)
      useJobsStore.getState().remove("job-3")

      rerender(<QueuePage />)

      // Job should now be gone
      await waitFor(() => {
        expect(screen.queryByText("file3.mp4")).not.toBeInTheDocument()
      })
    })
  })

  describe("Multiple jobs", () => {
    it("displays multiple jobs with independent state", async () => {
      useJobsStore.getState().seed([MOCK_JOB_QUEUED, MOCK_JOB_RUNNING])

      render(<QueuePage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByText("file1.mp4")).toBeInTheDocument()
        expect(screen.getByText("The Parisian Agency")).toBeInTheDocument()
      })
    })

    it("handles updates to multiple jobs independently", async () => {
      useJobsStore.getState().seed([MOCK_JOB_QUEUED, MOCK_JOB_RUNNING])

      const { rerender } = render(<QueuePage />, { wrapper })

      // Update first job to 25% progress
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 25,
        stage: "starting",
        message: "Starting job 1",
        step_index: null,
        step_total: null,
      })

      // Update second job to 75% progress
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-2",
        percent: 75,
        stage: "finishing",
        message: "Finishing job 2",
        step_index: null,
        step_total: null,
      })

      rerender(<QueuePage />)

      // Both should show updated progress
      await waitFor(() => {
        expect(screen.getByText(/25%/)).toBeInTheDocument()
        expect(screen.getByText(/75%/)).toBeInTheDocument()
      })
    })
  })

  describe("Terminal job details", () => {
    it("shows Runtime and Audio length labels for completed jobs", async () => {
      useJobsStore.getState().seed([MOCK_JOB_DONE])
      useJobsStore.setState({ lastTerminalJobId: "job-3" })

      render(<QueuePage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByText(/Runtime:/i)).toBeInTheDocument()
      })
      expect(screen.getByText(/Audio length:/i)).toBeInTheDocument()
      expect(screen.queryByText(/^Duration:/i)).not.toBeInTheDocument()
    })
  })

  describe("Step-based UX (M5.8)", () => {
    it("renders a step label for a running job with step fields", async () => {
      useJobsStore.getState().seed([MOCK_JOB_RUNNING])

      render(<QueuePage />, { wrapper })

      await waitFor(() => {
        expect(
          screen.getByText(/Step 2 of 3 — Transcribing/),
        ).toBeInTheDocument()
      })
    })

    it("invalidates the jobs query when a 'new' SSE event arrives", async () => {
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      })
      const invalidate = vi.spyOn(client, "invalidateQueries")
      const wrap = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      )

      render(<QueuePage />, { wrapper: wrap })

      act(() => {
        useJobsStore.getState().apply({ event: "new", job_id: "job-new-1" })
      })

      await waitFor(() => {
        expect(invalidate).toHaveBeenCalledWith({ queryKey: ["jobs"] })
      })
    })
  })

  describe("Overwrite & retry (M6.g)", () => {
    it("offers overwrite-retry when a job failed due to an existing subtitle", async () => {
      useJobsStore.getState().seed([MOCK_JOB_FAILED])
      useJobsStore.setState({ lastTerminalJobId: MOCK_JOB_FAILED.id })

      render(<QueuePage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByText(/Overwrite & retry/i)).toBeInTheDocument()
      })

      await userEvent.click(screen.getByText(/Overwrite & retry/i))

      expect(mockCreateMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          media_path: MOCK_JOB_FAILED.media_path,
          overwrite: true,
        }),
        expect.any(Object),
      )
    })

    it("does not offer overwrite-retry for an ordinary failure", async () => {
      useJobsStore.getState().seed([
        { ...MOCK_JOB_FAILED, id: "job-5", error_message: "Model crashed" },
      ])
      useJobsStore.setState({ lastTerminalJobId: "job-5" })

      render(<QueuePage />, { wrapper })

      await waitFor(() => {
        expect(screen.getByText("file3.mp4")).toBeInTheDocument()
      })
      expect(screen.queryByText(/Overwrite & retry/i)).not.toBeInTheDocument()
    })
  })

  describe("Auto-refresh default (M11)", () => {
    it("defaults to On, and the toggle flips it Off then back On", async () => {
      render(<QueuePage />, { wrapper })

      const toggle = screen.getByTitle(
        "Fallback polling when the live stream is unavailable",
      )

      // Default state on mount, before any interaction.
      expect(toggle).toHaveTextContent("Auto-refresh: On")

      await userEvent.click(toggle)
      expect(toggle).toHaveTextContent("Auto-refresh: Off")

      await userEvent.click(toggle)
      expect(toggle).toHaveTextContent("Auto-refresh: On")
    })
  })

  describe("Refresh behavior (hard-refresh path replaces live progress)", () => {
    // Regression tests for the bug where the manual "Refresh now" button and
    // the auto-refresh polling toggle showed no visible change: merge()
    // preserved the store's possibly-stale SSE-driven progress and discarded
    // the server's fresh values. The fix routes user-initiated refreshes
    // through replace() instead, which adopts server values wholesale.

    it("manual 'Refresh now' adopts server percent over stale SSE state", async () => {
      // Initial render: server has job-1 at percent=0.
      mockUseJobsReturn.data = { jobs: [MOCK_JOB_QUEUED] }
      const { rerender } = render(<QueuePage />, { wrapper })
      await waitFor(() => {
        expect(useJobsStore.getState().jobs["job-1"]).toBeDefined()
      })

      // SSE pushes percent=42 (ahead of the DB's 0).
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 42,
        stage: "transcribe",
        message: "SSE-fresh",
        step_index: null,
        step_total: null,
      })
      expect(useJobsStore.getState().jobs["job-1"].percent).toBe(42)

      // Click "Refresh now" — sets hardRefresh flag and calls refetch.
      // Crucially, set the fresh server data AFTER the click so the effect
      // doesn't run prematurely via merge() before hardRefresh is set.
      fireEvent.click(screen.getByTitle("Refresh now"))
      expect(mockUseJobsReturn.refetch).toHaveBeenCalledTimes(1)
      // Same job id as the initial seed; only the progress fields change.
      mockUseJobsReturn.data = {
        jobs: [{ ...MOCK_JOB_QUEUED, status: "running", progress_percent: 50 }],
      }
      await act(async () => {
        rerender(<QueuePage />)
      })

      // replace() must have run, adopting the server's 50 over the store's 42.
      expect(useJobsStore.getState().jobs["job-1"].percent).toBe(50)
    })

    it("auto-refresh tick adopts server percent over stale SSE state", async () => {
      vi.useFakeTimers()
      try {
        mockUseJobsReturn.data = { jobs: [MOCK_JOB_QUEUED] }
        const { rerender } = render(<QueuePage />, { wrapper })
        // Flush the initial seed effect.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0)
        })
        expect(useJobsStore.getState().jobs["job-1"]).toBeDefined()

        // SSE pushes percent=42. Wrapped in act() so the resulting store
        // subscription re-render (and its data effect, which sees the
        // still-old, pre-reassignment `data`) flushes now rather than being
        // deferred to the next act() boundary below — where it would
        // otherwise spuriously consume the not-yet-set new server data via
        // merge() before the interval tick has a chance to flag hardRefresh.
        act(() => {
          useJobsStore.getState().apply({
            event: "progress",
            job_id: "job-1",
            percent: 42,
            stage: "transcribe",
            message: "SSE-fresh",
            step_index: null,
            step_total: null,
          })
        })
        expect(useJobsStore.getState().jobs["job-1"].percent).toBe(42)

        // Auto-refresh is On by default (M11), so the interval is already
        // running. Set the fresh server data that the interval's refetch
        // will "return" — same job id as the initial seed, only the
        // progress fields change.
        mockUseJobsReturn.data = {
          jobs: [{ ...MOCK_JOB_QUEUED, status: "running", progress_percent: 50 }],
        }

        // Advance past the 10s interval. The callback sets hardRefresh=true
        // and calls refetch() (a vi.fn no-op in the mock, so we must force a
        // re-render to let the data-update effect see the new data).
        await act(async () => {
          await vi.advanceTimersByTimeAsync(10_000)
        })
        expect(mockUseJobsReturn.refetch).toHaveBeenCalledTimes(1)
        await act(async () => {
          rerender(<QueuePage />)
        })
        expect(useJobsStore.getState().jobs["job-1"].percent).toBe(50)
      } finally {
        vi.useRealTimers()
      }
    })

    it("'new' event still preserves live progress of an in-flight job (merge path)", async () => {
      // This guards against regressing the original 4f75319 fix: a "new"-event
      // invalidation triggers a refetch, and merge() must preserve the live
      // SSE progress of an unrelated running job rather than resetting it to
      // the (slightly stale) DB value.
      mockUseJobsReturn.data = { jobs: [MOCK_JOB_QUEUED] }
      render(<QueuePage />, { wrapper })
      await waitFor(() => {
        expect(useJobsStore.getState().jobs["job-1"]).toBeDefined()
      })

      // SSE pushes percent=42 (ahead of the DB's 0).
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 42,
        stage: "transcribe",
        message: "SSE-fresh",
        step_index: null,
        step_total: null,
      })

      // A "new" event triggers invalidateQueries(["jobs"]); the refetch returns
      // job-1 still at the DB's stale percent=0 (no hardRefresh flag set).
      mockUseJobsReturn.data = { jobs: [MOCK_JOB_QUEUED] }
      await act(async () => {
        useJobsStore.getState().apply({ event: "new", job_id: "job-new-1" })
      })

      // merge() must have preserved the SSE-fresh 42, not reset to 0.
      expect(useJobsStore.getState().jobs["job-1"].percent).toBe(42)
    })
  })
})
