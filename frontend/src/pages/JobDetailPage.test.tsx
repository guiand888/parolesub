import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { JobDetailPage } from "./JobDetailPage"
import { makeJobResponse } from "@/test/factories"
import type { JobResponse } from "@/lib/types"

// JobDetailPage reads its id via useParams({ from: "/layout/jobs/$jobId" }),
// which needs a real router context to resolve. Mocked the same way
// AppLayout.test.tsx mocks @tanstack/react-router, so the page can be
// rendered standalone.
vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ jobId: "job-1" }),
  Link: ({ children, ...props }: { children: React.ReactNode } & Record<string, unknown>) => (
    <a {...(props as React.AnchorHTMLAttributes<HTMLAnchorElement>)}>{children}</a>
  ),
}))

// useTimezoneSetting (used for the Created/Started/Finished fields, not
// under test here) goes through a real useQuery, so the page still needs a
// QueryClientProvider and a mocked api.get even though useJob/useJobLogs
// below are mocked directly.
vi.mock("@/lib/api", () => ({
  api: { get: vi.fn().mockResolvedValue({}), patch: vi.fn(), post: vi.fn() },
}))

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const mockUseJobReturn: {
  data: JobResponse | undefined
  isLoading: boolean
  isError: boolean
} = {
  data: undefined,
  isLoading: false,
  isError: false,
}

const mockUseJobLogsReturn: {
  data: { logs: unknown[]; total: number } | undefined
  isLoading: boolean
} = {
  data: { logs: [], total: 0 },
  isLoading: false,
}

vi.mock("@/hooks/useJobs", () => ({
  useJob: () => mockUseJobReturn,
  useJobLogs: () => mockUseJobLogsReturn,
}))

function setJob(job: JobResponse) {
  mockUseJobReturn.data = job
  mockUseJobReturn.isLoading = false
  mockUseJobReturn.isError = false
}

describe("JobDetailPage", () => {
  beforeEach(() => {
    mockUseJobReturn.data = undefined
    mockUseJobReturn.isLoading = false
    mockUseJobReturn.isError = false
    mockUseJobLogsReturn.data = { logs: [], total: 0 }
    mockUseJobLogsReturn.isLoading = false
  })

  it("shows the status badge and full label, and 'Episode · Bazarr #<ref>' for a fully-snapshotted episode job", () => {
    // Status "running" (rather than "done") avoids a duplicate "done" text
    // match: a terminal status is also echoed verbatim into the Progress
    // field below, which would make the badge's text ambiguous to query.
    setJob(
      makeJobResponse({
        status: "running",
        source: "bazarr_episode",
        source_ref: "4242",
        title: "Episode 1",
        series_title: "The Example Show",
        season_number: 4,
        episode_number: 1,
      }),
    )

    render(<JobDetailPage />, { wrapper })

    expect(screen.getByText("running")).toBeInTheDocument()
    expect(
      screen.getByText("The Example Show · S04E01 · Episode 1"),
    ).toBeInTheDocument()
    expect(screen.getByText("Episode · Bazarr #4242")).toBeInTheDocument()
  })

  it("shows 'Movie · Bazarr #<ref>' for a movie job", () => {
    setJob(
      makeJobResponse({
        status: "done",
        source: "bazarr_movie",
        source_ref: "200",
        title: "Dune (2021)",
      }),
    )

    render(<JobDetailPage />, { wrapper })

    expect(screen.getByText("Dune (2021)")).toBeInTheDocument()
    expect(screen.getByText("Movie · Bazarr #200")).toBeInTheDocument()
  })

  it("falls back to the file name and shows just 'Manual' (no Bazarr ref) for a manual job", () => {
    setJob(
      makeJobResponse({
        status: "done",
        source: "manual",
        source_ref: null,
        media_path: "/uploads/clip.mp4",
      }),
    )

    render(<JobDetailPage />, { wrapper })

    expect(screen.getByText("clip.mp4")).toBeInTheDocument()
    expect(screen.getByText("Manual")).toBeInTheDocument()
    expect(screen.queryByText(/Bazarr #/)).not.toBeInTheDocument()
  })
})
