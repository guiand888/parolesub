import { describe, it, expect, beforeEach } from "vitest"
import { useJobsStore, selectHasActiveJobForSource } from "./jobsStore"
import type { LiveJob } from "./jobsStore"
import type { JobResponse, SseEventData } from "./types"

// Mock job data
const MOCK_JOB: JobResponse = {
  id: "job-1",
  status: "queued",
  progress_percent: 0,
  progress_message: null,
  progress_stage: null,
  progress_step_index: null,
  progress_step_total: null,
  source: "bazarr_movie",
  source_ref: "123",
  media_path: "/path/to/file.mp4",
  title: null,
  series_title: null,
  season_number: null,
  episode_number: null,
  language_code: "en",
  language_mode: "explicit",
  mistral_detected_language: null,
  needs_language_review: false,
  output_format: "srt",
  created_at: "2024-01-01T00:00:00Z",
  started_at: null,
  finished_at: null,
  cancel_requested: false,
  audio_duration_seconds: null,
  runtime_seconds: null,
  estimated_cost_usd: null,
  output_path: null,
  priority: 0,
  worker_id: null,
  mistral_usage_json: null,
  error_message: null,
  updated_at: "2024-01-01T00:00:00Z",
}

const MOCK_JOB_2: JobResponse = {
  ...MOCK_JOB,
  id: "job-2",
  source_ref: "456",
}

describe("jobsStore", () => {
  beforeEach(() => {
    // Reset the store before each test
    const { getState } = useJobsStore
    getState().jobs
    // Force reset by creating a fresh state
    useJobsStore.setState({
      jobs: {},
      pendingNewCount: 0,
      lastTerminalJobId: null,
    })
  })

  describe("seed()", () => {
    it("initializes jobs from API response", () => {
      const store = useJobsStore.getState()
      store.seed([MOCK_JOB, MOCK_JOB_2])

      const jobs = useJobsStore.getState().jobs
      expect(Object.keys(jobs)).toHaveLength(2)
      expect(jobs["job-1"]).toBeDefined()
      expect(jobs["job-2"]).toBeDefined()
      expect(jobs["job-1"].status).toBe("queued")
      expect(jobs["job-1"].source_ref).toBe("123")
    })

    it("maps JobResponse fields to LiveJob correctly", () => {
      const store = useJobsStore.getState()
      store.seed([MOCK_JOB])

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.id).toBe("job-1")
      expect(job.status).toBe("queued")
      expect(job.percent).toBe(0)
      expect(job.stage).toBe("")
      expect(job.message).toBe("")
      expect(job.step_index).toBeNull()
      expect(job.step_total).toBeNull()
      expect(job.media_path).toBe("/path/to/file.mp4")
      expect(job.language_code).toBe("en")
      expect(job.output_format).toBe("srt")
      expect(job.audio_duration_seconds).toBeNull()
      expect(job.estimated_cost_usd).toBeNull()
    })

    it("maps progress_stage/step fields from JobResponse", () => {
      const store = useJobsStore.getState()
      store.seed([
        {
          ...MOCK_JOB,
          status: "running",
          progress_stage: "transcribe",
          progress_step_index: 2,
          progress_step_total: 3,
        },
      ])

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.stage).toBe("transcribe")
      expect(job.step_index).toBe(2)
      expect(job.step_total).toBe(3)
    })

    it("replaces previous jobs when seeding", () => {
      const store = useJobsStore.getState()
      store.seed([MOCK_JOB])
      expect(Object.keys(useJobsStore.getState().jobs)).toHaveLength(1)

      store.seed([MOCK_JOB_2])
      expect(Object.keys(useJobsStore.getState().jobs)).toHaveLength(1)
      expect(useJobsStore.getState().jobs["job-2"]).toBeDefined()
      expect(useJobsStore.getState().jobs["job-1"]).toBeUndefined()
    })
  })

  describe("apply() - progress event", () => {
    beforeEach(() => {
      useJobsStore.getState().seed([MOCK_JOB])
    })

    it("updates job to running state with progress", () => {
      const event: SseEventData = {
        event: "progress",
        job_id: "job-1",
        percent: 50,
        stage: "transcribing",
        message: "Processing audio",
        step_index: null,
        step_total: null,
      }

      useJobsStore.getState().apply(event)

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("running")
      expect(job.percent).toBe(50)
      expect(job.stage).toBe("transcribing")
      expect(job.message).toBe("Processing audio")
    })

    it("upserts progress events for unknown jobs instead of dropping them", () => {
      const event: SseEventData = {
        event: "progress",
        job_id: "non-existent",
        percent: 50,
        stage: "transcribing",
        message: "Processing",
        step_index: null,
        step_total: null,
      }

      useJobsStore.getState().apply(event)

      // An event that fired before the seed caught up must still surface, so
      // the UI shows live progress rather than requiring a manual refresh.
      const job = useJobsStore.getState().jobs["non-existent"]
      expect(job).toBeDefined()
      expect(job.status).toBe("running")
      expect(job.percent).toBe(50)
      expect(job.stage).toBe("transcribing")
    })
  })

  describe("apply() - done event", () => {
    beforeEach(() => {
      useJobsStore.getState().seed([MOCK_JOB])
    })

    it("marks job as done and sets lastTerminalJobId", () => {
      const event: SseEventData = {
        event: "done",
        job_id: "job-1",
        status: "done",
      }

      useJobsStore.getState().apply(event)

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("done")
      expect(job.percent).toBe(100)
      expect(useJobsStore.getState().lastTerminalJobId).toBe("job-1")
    })

    it("handles failed job status", () => {
      const event: SseEventData = {
        event: "done",
        job_id: "job-1",
        status: "failed",
      }

      useJobsStore.getState().apply(event)

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("failed")
      expect(useJobsStore.getState().lastTerminalJobId).toBe("job-1")
    })
  })

  describe("apply() - cancel event", () => {
    beforeEach(() => {
      useJobsStore.getState().seed([MOCK_JOB])
    })

    it("marks job as cancelled and sets lastTerminalJobId", () => {
      const event: SseEventData = {
        event: "cancel",
        job_id: "job-1",
      }

      useJobsStore.getState().apply(event)

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("cancelled")
      expect(useJobsStore.getState().lastTerminalJobId).toBe("job-1")
    })
  })

  describe("apply() - new event", () => {
    it("increments pendingNewCount", () => {
      const event: SseEventData = {
        event: "new",
        job_id: "job-new",
      }

      expect(useJobsStore.getState().pendingNewCount).toBe(0)
      useJobsStore.getState().apply(event)
      expect(useJobsStore.getState().pendingNewCount).toBe(1)

      useJobsStore.getState().apply(event)
      expect(useJobsStore.getState().pendingNewCount).toBe(2)
    })
  })

  describe("merge()", () => {
    it("adds new jobs from the API without wiping existing live progress", () => {
      // A job already has live SSE progress in the store.
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 42,
        stage: "transcribe",
        message: "Half done",
        step_index: null,
        step_total: null,
      })

      // A re-seed (triggered by a "new" event) must not reset the progress.
      useJobsStore.getState().merge([MOCK_JOB])

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.percent).toBe(42)
      expect(job.stage).toBe("transcribe")
      expect(job.message).toBe("Half done")
      // Server-authoritative fields are still adopted.
      expect(job.source_ref).toBe("123")
    })

    it("adopts server-authoritative fields for known jobs", () => {
      useJobsStore.getState().seed([MOCK_JOB])
      useJobsStore.getState().merge([
        { ...MOCK_JOB, status: "running", estimated_cost_usd: 0.05 },
      ])

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("running")
      expect(job.estimated_cost_usd).toBe(0.05)
    })

    it("adds jobs that the store has never seen", () => {
      useJobsStore.getState().merge([MOCK_JOB_2])
      const jobs = useJobsStore.getState().jobs
      expect(jobs["job-2"]).toBeDefined()
      expect(jobs["job-2"].source_ref).toBe("456")
    })

    it("does not resurrect a terminal job the user already dismissed via remove()", () => {
      // job-1 finished, faded out, and was removed from the store - it's no
      // longer present, but GET /api/jobs?limit=200 still returns it.
      useJobsStore.getState().seed([MOCK_JOB])
      useJobsStore.getState().remove("job-1")
      expect(useJobsStore.getState().jobs["job-1"]).toBeUndefined()

      useJobsStore.getState().merge([{ ...MOCK_JOB, status: "done" }])

      expect(useJobsStore.getState().jobs["job-1"]).toBeUndefined()
    })
  })

  describe("replace()", () => {
    it("overwrites live SSE progress with the server's persisted values", () => {
      // A job has live SSE progress ahead of the DB (worker publishes to
      // Redis before persisting, debounced at ~1Hz). merge() would preserve
      // the SSE-fresh 42%; replace() must adopt the server's 40% wholesale.
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 42,
        stage: "transcribe",
        message: "SSE-fresh",
        step_index: 2,
        step_total: 3,
      })

      useJobsStore.getState().replace([
        { ...MOCK_JOB, status: "running", progress_percent: 40 },
      ])

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.percent).toBe(40)
      expect(job.stage).toBe("") // server's progress_stage was null → ""
      expect(job.message).toBe("") // server's progress_message was null → ""
      expect(job.step_index).toBeNull()
      expect(job.step_total).toBeNull()
    })

    it("adopts server-authoritative fields wholesale", () => {
      useJobsStore.getState().seed([MOCK_JOB])
      useJobsStore.getState().replace([
        {
          ...MOCK_JOB,
          status: "running",
          estimated_cost_usd: 0.07,
          progress_percent: 80,
          progress_stage: "transcribe",
          progress_step_index: 4,
          progress_step_total: 5,
        },
      ])

      const job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("running")
      expect(job.estimated_cost_usd).toBe(0.07)
      expect(job.percent).toBe(80)
      expect(job.stage).toBe("transcribe")
      expect(job.step_index).toBe(4)
      expect(job.step_total).toBe(5)
    })

    it("does not resurrect a terminal job the user already dismissed", () => {
      useJobsStore.getState().seed([MOCK_JOB])
      useJobsStore.getState().remove("job-1")
      expect(useJobsStore.getState().jobs["job-1"]).toBeUndefined()

      useJobsStore.getState().replace([
        { ...MOCK_JOB, status: "done", progress_percent: 100 },
      ])

      expect(useJobsStore.getState().jobs["job-1"]).toBeUndefined()
    })

    it("adds queued and running jobs the store has never seen", () => {
      useJobsStore.getState().replace([MOCK_JOB, MOCK_JOB_2])
      const jobs = useJobsStore.getState().jobs
      expect(jobs["job-1"]).toBeDefined()
      expect(jobs["job-2"]).toBeDefined()
      expect(jobs["job-1"].source_ref).toBe("123")
      expect(jobs["job-2"].source_ref).toBe("456")
    })
  })

  describe("remove()", () => {
    beforeEach(() => {
      useJobsStore.getState().seed([MOCK_JOB, MOCK_JOB_2])
    })

    it("removes job by ID", () => {
      expect(Object.keys(useJobsStore.getState().jobs)).toHaveLength(2)

      useJobsStore.getState().remove("job-1")

      const jobs = useJobsStore.getState().jobs
      expect(Object.keys(jobs)).toHaveLength(1)
      expect(jobs["job-1"]).toBeUndefined()
      expect(jobs["job-2"]).toBeDefined()
    })

    it("handles removal of non-existent job gracefully", () => {
      useJobsStore.getState().remove("non-existent")
      // Should not crash
      expect(Object.keys(useJobsStore.getState().jobs)).toHaveLength(2)
    })
  })

  describe("State transitions", () => {
    beforeEach(() => {
      useJobsStore.getState().seed([MOCK_JOB])
    })

    it("transitions job from queued to running to done", () => {
      let job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("queued")

      // Progress event transitions to running
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 25,
        stage: "transcribing",
        message: "Processing",
        step_index: null,
        step_total: null,
      })

      job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("running")
      expect(job.percent).toBe(25)

      // Done event transitions to done
      useJobsStore.getState().apply({
        event: "done",
        job_id: "job-1",
        status: "done",
      })

      job = useJobsStore.getState().jobs["job-1"]
      expect(job.status).toBe("done")
      expect(job.percent).toBe(100)
      expect(useJobsStore.getState().lastTerminalJobId).toBe("job-1")
    })
  })

  describe("Multiple jobs", () => {
    beforeEach(() => {
      useJobsStore.getState().seed([MOCK_JOB, MOCK_JOB_2])
    })

    it("maintains independent state for multiple jobs", () => {
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 50,
        stage: "transcribing",
        message: "Job 1 processing",
        step_index: null,
        step_total: null,
      })

      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-2",
        percent: 75,
        stage: "formatting",
        message: "Job 2 processing",
        step_index: null,
        step_total: null,
      })

      const job1 = useJobsStore.getState().jobs["job-1"]
      const job2 = useJobsStore.getState().jobs["job-2"]

      expect(job1.percent).toBe(50)
      expect(job1.stage).toBe("transcribing")
      expect(job2.percent).toBe(75)
      expect(job2.stage).toBe("formatting")
    })

    it("marks one job as terminal without affecting others", () => {
      useJobsStore.getState().apply({
        event: "progress",
        job_id: "job-1",
        percent: 100,
        stage: "done",
        message: "Completed",
        step_index: null,
        step_total: null,
      })

      useJobsStore.getState().apply({
        event: "done",
        job_id: "job-1",
        status: "done",
      })

      const job1 = useJobsStore.getState().jobs["job-1"]
      const job2 = useJobsStore.getState().jobs["job-2"]

      expect(job1.status).toBe("done")
      expect(job2.status).toBe("queued")
      expect(useJobsStore.getState().lastTerminalJobId).toBe("job-1")
    })
  })
})

// Build a minimal LiveJob for selector tests, overriding only the fields
// selectHasActiveJobForSource cares about.
function makeLiveJob(overrides: Partial<LiveJob>): LiveJob {
  return {
    id: "job-x",
    status: "queued",
    percent: 0,
    stage: "",
    message: "",
    step_index: null,
    step_total: null,
    source: "manual",
    source_ref: null,
    media_path: "/path/to/file.mp4",
    title: null,
    series_title: null,
    season_number: null,
    episode_number: null,
    language_code: "en",
    language_mode: "explicit",
    output_format: "srt",
    created_at: "",
    started_at: null,
    finished_at: null,
    cancel_requested: false,
    audio_duration_seconds: null,
    runtime_seconds: null,
    estimated_cost_usd: null,
    error_message: null,
    ...overrides,
  }
}

describe("selectHasActiveJobForSource()", () => {
  it("returns true when a queued job matches media_path and language_code", () => {
    const jobs = {
      "job-1": makeLiveJob({ id: "job-1", status: "queued" }),
    }
    expect(
      selectHasActiveJobForSource(jobs, "/path/to/file.mp4", "en"),
    ).toBe(true)
  })

  it("returns true when a running job matches media_path and language_code", () => {
    const jobs = {
      "job-1": makeLiveJob({ id: "job-1", status: "running" }),
    }
    expect(
      selectHasActiveJobForSource(jobs, "/path/to/file.mp4", "en"),
    ).toBe(true)
  })

  it("returns false when the matching job is done/failed/cancelled", () => {
    for (const status of ["done", "failed", "cancelled"]) {
      const jobs = {
        "job-1": makeLiveJob({ id: "job-1", status }),
      }
      expect(
        selectHasActiveJobForSource(jobs, "/path/to/file.mp4", "en"),
      ).toBe(false)
    }
  })

  it("returns false when media_path does not match", () => {
    const jobs = {
      "job-1": makeLiveJob({ id: "job-1", status: "queued", media_path: "/other.mp4" }),
    }
    expect(
      selectHasActiveJobForSource(jobs, "/path/to/file.mp4", "en"),
    ).toBe(false)
  })

  it("returns false when language_code does not match", () => {
    const jobs = {
      "job-1": makeLiveJob({ id: "job-1", status: "queued", language_code: "fr" }),
    }
    expect(
      selectHasActiveJobForSource(jobs, "/path/to/file.mp4", "en"),
    ).toBe(false)
  })

  it("matches on null language_code (auto mode with no code yet)", () => {
    const jobs = {
      "job-1": makeLiveJob({ id: "job-1", status: "queued", language_code: null }),
    }
    expect(
      selectHasActiveJobForSource(jobs, "/path/to/file.mp4", null),
    ).toBe(true)
  })

  it("returns false for an empty jobs map", () => {
    expect(selectHasActiveJobForSource({}, "/path/to/file.mp4", "en")).toBe(false)
  })
})
