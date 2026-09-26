// Queue page: "Running" and "Queued" sections.
// Seeded from GET /api/jobs; kept live via the Zustand SSE store.
// Cards animate out 1.5s after reaching a terminal state, then disappear.

import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { RefreshCw, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { JobStatusIcon } from "@/components/JobStatusIcon"
import { useJobs, useCancelJob, useCreateJob } from "@/hooks/useJobs"
import { useJobsStore } from "@/lib/jobsStore"
import type { LiveJob } from "@/lib/jobsStore"
import type { JobCreate } from "@/lib/types"
import { ApiError } from "@/lib/api"
import { formatCost, formatDuration } from "@/lib/utils"
import { bazarrRef, jobHeadline, jobHeadlineText, jobSubtitleParts } from "@/lib/jobLabels"

// ── JobCard ───────────────────────────────────────────────────────────────────

interface JobCardProps {
  job: LiveJob
  onCancel: (id: string) => void
  isCancelling: boolean
  onCreate: (payload: JobCreate) => void
  isCreating: boolean
}

// Friendly labels for the pipeline's discrete stages.
const STAGE_LABELS: Record<string, string> = {
  init: "Preparing",
  extract: "Extracting audio",
  split: "Splitting audio",
  transcribe: "Transcribing",
  generate: "Generating subtitles",
  done: "Complete",
}

// Stages that emit continuous ffmpeg/segment sub-progress. Stages without a
// sub-progress source render an indeterminate (pulsing) bar instead of a
// frozen percentage.
const STAGES_WITH_SUBPROGRESS = new Set(["extract", "split", "transcribe"])

function stageLabel(job: LiveJob): string {
  const label = STAGE_LABELS[job.stage] ?? job.stage ?? "Processing"
  if (job.step_index != null && job.step_total != null) {
    return `Step ${job.step_index} of ${job.step_total} — ${label}`
  }
  return label
}

function JobCard({ job, onCancel, isCancelling, onCreate, isCreating }: JobCardProps) {
  const isTerminal =
    job.status === "done" ||
    job.status === "failed" ||
    job.status === "cancelled"

  const indeterminate =
    job.status === "running" && !STAGES_WITH_SUBPROGRESS.has(job.stage)

  // M6.g: the worker refused to overwrite an existing subtitle — offer a
  // retry that resubmits the same job with overwrite=true.
  const isOutputExists =
    job.status === "failed" &&
    job.error_message != null &&
    job.error_message.includes("output_exists")

  const headline = jobHeadline(job)

  return (
    <Card
      className={
        isTerminal ? "opacity-50 transition-opacity duration-700" : undefined
      }
    >
      <CardContent className="pt-4 pb-4 space-y-2">
        {/* Header row */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="flex items-baseline" title={jobHeadlineText(job)}>
              <span className="font-medium text-sm truncate">{headline.name}</span>
              {headline.code != null && (
                <>
                  {" "}
                  <span className="flex-none text-sm tabular-nums">· {headline.code}</span>
                </>
              )}
            </p>
            <p className="text-xs text-muted-foreground">
              {[
                ...jobSubtitleParts(job),
                job.language_code ?? (job.language_mode === "auto" ? "Auto" : "—"),
                job.output_format.toUpperCase(),
                bazarrRef(job),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-none">
            <JobStatusIcon status={job.status} />
            {(job.status === "queued" || job.status === "running") && (
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                title="Cancel job"
                disabled={isCancelling || job.cancel_requested}
                onClick={() => onCancel(job.id)}
              >
                <X className="h-4 w-4" />
              </Button>
            )}
            {isOutputExists && (
              <Button
                size="sm"
                variant="outline"
                className="h-7"
                title="Overwrite the existing subtitle and retry"
                disabled={isCreating}
                onClick={() =>
                  onCreate({
                    source: job.source as JobCreate["source"],
                    source_ref: job.source_ref,
                    media_path: job.media_path,
                    language_code:
                      job.language_mode === "auto" ? null : job.language_code,
                    language_mode: job.language_mode,
                    output_format: job.output_format as JobCreate["output_format"],
                    overwrite: true,
                  })
                }
              >
                {isCreating ? "Queuing…" : "Overwrite & retry"}
              </Button>
            )}
          </div>
        </div>

        {/* Progress bar (only for running jobs) */}
        {job.status === "running" && (
          <div className="space-y-1">
            {indeterminate ? (
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-primary/20">
                <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
              </div>
            ) : (
              <Progress value={job.percent} className="h-1.5" />
            )}
            <div className="flex justify-between text-xs text-muted-foreground">
              <span className="truncate">{stageLabel(job)}</span>
              <span className="flex-none ml-2">{job.percent}%</span>
            </div>
          </div>
        )}

        {/* Cost and duration info (for completed jobs) */}
        {isTerminal && (job.estimated_cost_usd !== null || job.audio_duration_seconds !== null || job.runtime_seconds !== null) && (
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            {job.runtime_seconds !== null && (
              <span className="flex items-center gap-1">
                <span>Runtime: {formatDuration(job.runtime_seconds)}</span>
              </span>
            )}
            {job.audio_duration_seconds !== null && (
              <span className="flex items-center gap-1">
                <span>Audio length: {formatDuration(job.audio_duration_seconds)}</span>
              </span>
            )}
            {job.estimated_cost_usd !== null && (
              <span className="flex items-center gap-1">
                <span>Cost: {formatCost(job.estimated_cost_usd)}</span>
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── QueuePage ─────────────────────────────────────────────────────────────────

export function QueuePage() {
  const queryClient = useQueryClient()
  // Fallback polling, default on (M11) so a freshly opened Queue page
  // live-updates without the operator having to opt in manually; the
  // operator can still turn it off. SSE is the primary live mechanism — this
  // only re-syncs from GET /api/jobs on the interval. Unlike the
  // "new"-event-triggered re-sync, the user explicitly wants fresh server
  // state here, so the data-update effect uses replace() (which overwrites
  // in-store progress) rather than merge() (which preserves it).
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [refreshInterval] = useState(10000)
  // Initial seed on mount, then Zustand store is the source of truth
  const { data, refetch, isFetching } = useJobs({
    limit: 200,
  })
  const cancelJob = useCancelJob()
  const createJob = useCreateJob()
  const seed = useJobsStore((s) => s.seed)
  const merge = useJobsStore((s) => s.merge)
  const replace = useJobsStore((s) => s.replace)
  const remove = useJobsStore((s) => s.remove)
  const lastTerminalJobId = useJobsStore((s) => s.lastTerminalJobId)
  const pendingNewCount = useJobsStore((s) => s.pendingNewCount)
  const jobs = useJobsStore((s) => s.jobs)

  // Track job IDs we're currently animating out
  const [fadingOut, setFadingOut] = useState<Set<string>>(new Set())

  // Seed the store once on mount; afterwards re-sync via merge so live SSE
  // progress is preserved — unless hardRefresh is set, in which case replace
  // wholesale adopts server values (user-initiated refresh).
  const seededOnce = useRef(false)
  const hardRefresh = useRef(false)
  useEffect(() => {
    if (!data?.jobs) return
    if (!seededOnce.current) {
      seed(data.jobs)
      seededOnce.current = true
    } else if (hardRefresh.current) {
      replace(data.jobs)
      hardRefresh.current = false
    } else {
      merge(data.jobs)
    }
  }, [data?.jobs, seed, merge, replace])

  // Auto-refresh polling: explicit setInterval rather than React Query's
  // refetchInterval so we can flag the resulting fetch as a hard refresh
  // (replace() instead of merge()). Without this flag, polling would go
  // through merge() which preserves stale store progress — the exact bug
  // we're fixing.
  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(() => {
      hardRefresh.current = true
      void refetch()
    }, refreshInterval)
    return () => clearInterval(id)
  }, [autoRefresh, refreshInterval, refetch])

  // When a job reaches terminal state: animate out, then remove after delay,
  // and invalidate the history + wanted caches
  useEffect(() => {
    if (!lastTerminalJobId) return
    const jobId = lastTerminalJobId
    setFadingOut((prev) => new Set([...prev, jobId]))
    void queryClient.invalidateQueries({ queryKey: ["wanted"] })
    const timer = setTimeout(() => {
      remove(jobId)
      setFadingOut((prev) => {
        const next = new Set(prev)
        next.delete(jobId)
        return next
      })
    }, 1500)
    return () => clearTimeout(timer)
  }, [lastTerminalJobId, remove, queryClient])

  // M5.8 (#2, verified): the "new" SSE event previously only bumped the
  // (otherwise dead) pendingNewCount counter, so jobs created by another actor
  // never appeared without a manual refresh. Invalidating ["jobs"] makes the
  // store re-seed from GET /api/jobs and the new card shows up live. The
  // re-seed goes through merge(), preserving any in-flight progress.
  useEffect(() => {
    if (pendingNewCount === 0) return
    void queryClient.invalidateQueries({ queryKey: ["jobs"] })
  }, [pendingNewCount, queryClient])

  const handleCancel = (jobId: string) => {
    cancelJob.mutate(jobId, {
      onError: (err) => {
        if (err instanceof ApiError) {
          toast.error(err.message)
        } else {
          toast.error("Cancel failed")
        }
      },
    })
  }

  const handleCreate = (payload: JobCreate) => {
    createJob.mutate(payload, {
      onSuccess: () => toast.success("Job queued"),
      onError: (err) => {
        if (err instanceof ApiError) {
          toast.error(err.message)
        } else {
          toast.error("Failed to queue job")
        }
      },
    })
  }

  const handleManualRefresh = () => {
    hardRefresh.current = true
    void refetch()
  }

  const toggleAutoRefresh = () => {
    setAutoRefresh((v) => !v)
  }

  const allJobs = Object.values(jobs)
  const running = allJobs.filter((j) => j.status === "running")
  const queued = allJobs.filter((j) => j.status === "queued")
  // Also show recent terminal jobs that are fading out
  const terminal = allJobs.filter(
    (j) =>
      (j.status === "done" || j.status === "failed" || j.status === "cancelled") &&
      fadingOut.has(j.id),
  )

  const isEmpty =
    running.length === 0 && queued.length === 0 && terminal.length === 0

  return (
    <div className="p-4 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Queue</h1>
          <p className="text-sm text-muted-foreground">
            Live job progress — updates stream in real time
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handleManualRefresh}
            disabled={isFetching}
            title="Refresh now"
          >
            <RefreshCw className={isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          </Button>
          <Button
            size="sm"
            variant={autoRefresh ? "default" : "outline"}
            onClick={toggleAutoRefresh}
            title="Fallback polling when the live stream is unavailable"
          >
            Auto-refresh: {autoRefresh ? "On" : "Off"}
          </Button>
        </div>
      </div>

      {isEmpty ? (
        <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
          No active jobs.
        </div>
      ) : (
        <>
          {/* Running */}
          {(running.length > 0 || terminal.length > 0) && (
            <section className="space-y-3">
              <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                Running
              </h2>
              <div className="space-y-2">
                {[...running, ...terminal].map((job) => (
                  <JobCard
                    key={job.id}
                    job={job}
                    onCancel={handleCancel}
                    isCancelling={cancelJob.isPending}
                    onCreate={handleCreate}
                    isCreating={createJob.isPending}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Queued */}
          {queued.length > 0 && (
            <section className="space-y-3">
              <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                Queued
              </h2>
              <div className="space-y-2">
                {queued.map((job) => (
                  <JobCard
                    key={job.id}
                    job={job}
                    onCancel={handleCancel}
                    isCancelling={cancelJob.isPending}
                    onCreate={handleCreate}
                    isCreating={createJob.isPending}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}
