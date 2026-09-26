// History page: table of completed jobs with aggregate statistics.
// Fetches from GET /api/history with pagination and filters.

import { useState, useEffect } from "react"
import { AlertTriangle, ChevronLeft, ChevronRight, Filter, Info } from "lucide-react"
import { toast } from "sonner"

import { useHistory } from "@/hooks/useHistory"
import { useCreateJob, useUpdateJobLanguage } from "@/hooks/useJobs"
import { useTimezoneSetting, formatDateTime } from "@/lib/datetime"
import { formatCost, formatDuration } from "@/lib/utils"
import { ApiError } from "@/lib/api"
import { useJobsStore, selectHasActiveJobForSource } from "@/lib/jobsStore"
import { jobHeadline, bazarrRef, jobFullLabel } from "@/lib/jobLabels"
import type {
  HistoryFilters,
  HistoryResponse,
  JobCreate,
  JobResponse,
  JobStatus,
  JobSource,
} from "@/lib/types"

// Import from shadcn/ui
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { JobStatusIcon } from "@/components/JobStatusIcon"

const STATUS_OPTIONS: { value: JobStatus; label: string }[] = [
  { value: "done", label: "Done" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
]

const SOURCE_OPTIONS: { value: JobSource; label: string }[] = [
  { value: "bazarr_movie", label: "Bazarr Movie" },
  { value: "bazarr_episode", label: "Bazarr Episode" },
  { value: "manual", label: "Manual" },
]

// M6.g: a FAILED job whose error carries the `output_exists` sentinel was
// refused by the worker's write-time overwrite guard. Such jobs can be retried
// with overwrite=true from the History page.
function isOutputExists(job: JobResponse): boolean {
  return (
    job.status === "failed" &&
    job.error_message != null &&
    job.error_message.includes("output_exists")
  )
}

// Language detection failed (auto mode resolved to "und"), so the job needs a
// manual language rename from the History page.
function needsLanguageRename(job: JobResponse): boolean {
  return job.needs_language_review
}

// Build a JobCreate that re-runs an existing job (used for the overwrite retry).
function buildRetry(job: JobResponse): JobCreate {
  return {
    source: job.source,
    source_ref: job.source_ref,
    media_path: job.media_path,
    output_path: job.output_path,
    language_code: job.language_mode === "auto" ? null : job.language_code,
    language_mode: job.language_mode,
    output_format: job.output_format,
    overwrite: true,
  }
}

// Truncate a path for display
function truncatePath(path: string, maxLength: number = 50): string {
  if (path.length <= maxLength) return path
  const parts = path.split("/")
  const filename = parts[parts.length - 1]
  const dir = parts.slice(0, -1).join("/")
  if (filename.length > maxLength - 5) {
    return filename.slice(0, maxLength - 5) + "..."
  }
  if (dir.length > maxLength - filename.length - 5) {
    return ".../" + filename
  }
  return path
}

// Filter form component
interface HistoryFiltersProps {
  filters: HistoryFilters
  onChange: (filters: HistoryFilters) => void
}

function HistoryFiltersForm({ filters, onChange }: HistoryFiltersProps) {
  const [status, setStatus] = useState<JobStatus | "_all">(filters.status_filter?.[0] ?? "_all")
  const [source, setSource] = useState<JobSource | "_all">(filters.source_filter ?? "_all")
  const [language, setLanguage] = useState<string | undefined>(filters.language_filter)

  const handleApply = () => {
    onChange({
      ...filters,
      status_filter: status !== "_all" ? [status as JobStatus] : undefined,
      source_filter: source !== "_all" ? (source as JobSource) : undefined,
      language_filter: language || undefined,
      offset: 0,
    })
  }

  const handleReset = () => {
    setStatus("_all")
    setSource("_all")
    setLanguage(undefined)
    onChange({ offset: 0 })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Filters</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="status-filter">Status</Label>
          <Select
            value={status}
            onValueChange={(v) => setStatus(v as JobStatus | "_all")}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="_all">All statuses</SelectItem>
              {STATUS_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="source-filter">Source</Label>
          <Select
            value={source}
            onValueChange={(v) => setSource(v as JobSource | "_all")}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="All sources" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="_all">All sources</SelectItem>
              {SOURCE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="language-filter">Language</Label>
          <Input
            id="language-filter"
            type="text"
            placeholder="e.g. en, fr"
            value={language || ""}
            onChange={(e) => setLanguage(e.target.value)}
            className="w-full"
          />
        </div>

        <div className="flex gap-2">
          <Button size="sm" className="flex-1" onClick={handleApply}>
            <Filter className="h-4 w-4 mr-2" />
            Apply
          </Button>
          <Button size="sm" variant="outline" className="flex-1" onClick={handleReset}>
            Reset
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

// Stats display component
interface HistoryStatsProps {
  stats: HistoryResponse["stats"]
}

function HistoryStatsDisplay({ stats }: HistoryStatsProps) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Jobs</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{stats.total_jobs}</div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Cost</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{formatCost(stats.total_cost_usd)}</div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Runtime</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {formatDuration(stats.total_runtime_seconds)}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Audio Length</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {formatDuration(stats.total_audio_length_seconds)}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Avg Cost/Job</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{formatCost(stats.average_cost_usd)}</div>
        </CardContent>
      </Card>
    </div>
  )
}

// Correction dialog for a job's language, reachable via the review/mismatch
// icons in the Language column. Same field works for both cases: fixing an
// "und" auto-mode fallback, or overriding an explicit pick that Mistral's
// detection disagreed with.
interface LanguageReviewDialogProps {
  job: JobResponse | null
  onClose: () => void
}

function LanguageReviewDialog({ job, onClose }: LanguageReviewDialogProps) {
  const updateLanguage = useUpdateJobLanguage()
  const [code, setCode] = useState("")

  useEffect(() => {
    setCode(job?.mistral_detected_language || job?.language_code || "")
  }, [job])

  if (!job) return null

  const handleSave = () => {
    const trimmed = code.trim()
    if (!trimmed) return
    updateLanguage.mutate(
      { jobId: job.id, language_code: trimmed },
      {
        onSuccess: () => {
          toast.success("Language updated")
          onClose()
        },
        onError: () => {
          toast.error("Failed to update language")
        },
      },
    )
  }

  return (
    <Dialog open={!!job} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Set language</DialogTitle>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="review-language-code">Language code</Label>
          <Input
            id="review-language-code"
            placeholder="e.g. en, fr"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={updateLanguage.isPending || !code.trim()}
          >
            {updateLanguage.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Main HistoryPage component
export function HistoryPage() {
  const [filters, setFilters] = useState<HistoryFilters>({ offset: 0, limit: 20 })
  const [page, setPage] = useState(0)
  const [reviewJob, setReviewJob] = useState<JobResponse | null>(null)

  const { data, isLoading, isError } = useHistory(filters)
  const createJob = useCreateJob()
  // Live job state (queued/running), kept fresh app-wide by useJobsStream()
  // mounted in AppLayout. Used to gate the Retry button: a plain `failed`
  // job should only offer Retry while no active job already exists for its
  // (media_path, language_code) source.
  const liveJobs = useJobsStore((s) => s.jobs)

  const timezone = useTimezoneSetting()

  // Sync page state with filters
  useEffect(() => {
    const newPage = Math.floor((filters.offset || 0) / (filters.limit || 20))
    if (newPage !== page) setPage(newPage)
  }, [filters.offset, filters.limit, page])

  const handlePageChange = (newPage: number) => {
    setFilters({ ...filters, offset: newPage * (filters.limit || 20) })
  }

  const handleFiltersChange = (newFilters: HistoryFilters) => {
    setFilters(newFilters)
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <p>Loading history...</p>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-destructive">Failed to load history</p>
      </div>
    )
  }

  const response = data
  const jobs = response?.jobs || []
  const stats = response?.stats

  return (
    <div className="flex flex-col h-full p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">History</h1>
          <p className="text-sm text-muted-foreground">
            Completed transcription jobs
          </p>
        </div>
      </div>

      {/* Stats */}
      {stats && <HistoryStatsDisplay stats={stats} />}

      <div className="flex gap-4">
        {/* Filters sidebar */}
        <div className="w-full md:w-[250px] flex-none">
          <HistoryFiltersForm filters={filters} onChange={handleFiltersChange} />
        </div>

        {/* Jobs table */}
        <div className="flex-1 overflow-auto">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Jobs</CardTitle>
              <CardDescription>
                {response?.total || 0} total completed jobs
              </CardDescription>
            </CardHeader>
            <CardContent>
              {jobs.length === 0 ? (
                <p className="text-muted-foreground text-center py-8">
                  No jobs found matching your filters
                </p>
              ) : (
                <Table>
                  <TableHeader className="bg-muted/50">
                    <TableRow>
                      <TableHead className="p-3">Status</TableHead>
                      <TableHead className="p-3">Item</TableHead>
                      <TableHead className="p-3">Media Path</TableHead>
                      <TableHead className="p-3">Language</TableHead>
                      <TableHead className="p-3">Runtime</TableHead>
                      <TableHead className="p-3">Audio Length</TableHead>
                      <TableHead className="p-3">Cost</TableHead>
                      <TableHead className="p-3">Created</TableHead>
                      <TableHead className="p-3">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {jobs.map((job) => {
                      const headline = jobHeadline(job)
                      return (
                      <TableRow key={job.id} className="border-t hover:bg-muted/50">
                        <TableCell className="p-3">
                          <JobStatusIcon status={job.status} />
                        </TableCell>
                        <TableCell className="p-3 max-w-[260px]" title={jobFullLabel(job)}>
                          {/* The name and code are separate spans, not one truncated
                              string: a long series name should truncate on its own
                              without ever hiding the S/E code (see jobHeadline()'s
                              own comment in lib/jobLabels.ts). */}
                          <div className="flex items-baseline min-w-0">
                            <span className="truncate">{headline.name}</span>
                            {headline.code && (
                              <>
                                {" "}
                                <span className="flex-none tabular-nums">· {headline.code}</span>
                              </>
                            )}
                          </div>
                          <div className="text-muted-foreground text-xs flex items-center gap-1.5 mt-0.5">
                            <Badge variant="outline">
                              {job.source.replace("bazarr_", "")}
                            </Badge>
                            {bazarrRef(job) && <span>· {bazarrRef(job)}</span>}
                          </div>
                        </TableCell>
                        <TableCell className="p-3 max-w-[300px]">
                          <span className="truncate block" title={job.media_path}>
                            {truncatePath(job.media_path, 60)}
                          </span>
                        </TableCell>
                        <TableCell className="p-3">
                          <div className="flex items-center gap-1.5">
                            <span>{job.language_code || "—"}</span>
                            {job.needs_language_review && (
                              <Button
                                size="icon"
                                variant="ghost"
                                className="h-5 w-5"
                                title="Mistral couldn't detect a language; click to set it"
                                onClick={() => setReviewJob(job)}
                              >
                                <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                              </Button>
                            )}
                            {!job.needs_language_review &&
                              job.language_mode === "explicit" &&
                              job.mistral_detected_language &&
                              job.mistral_detected_language !== job.language_code && (
                                <Button
                                  size="icon"
                                  variant="ghost"
                                  className="h-5 w-5"
                                  title={`Selected "${job.language_code}", but Mistral detected "${job.mistral_detected_language}"`}
                                  onClick={() => setReviewJob(job)}
                                >
                                  <Info className="h-3.5 w-3.5 text-muted-foreground" />
                                </Button>
                              )}
                          </div>
                        </TableCell>
                        <TableCell className="p-3 whitespace-nowrap">{formatDuration(job.runtime_seconds)}</TableCell>
                        <TableCell className="p-3 whitespace-nowrap">{formatDuration(job.audio_duration_seconds)}</TableCell>
                        <TableCell className="p-3">{formatCost(job.estimated_cost_usd)}</TableCell>
                        <TableCell className="p-3 whitespace-nowrap">
                          {formatDateTime(job.created_at, timezone ?? undefined)}
                        </TableCell>
                        <TableCell className="p-3 whitespace-nowrap">
                          <div className="flex flex-wrap gap-2">
                            {/* M6.g: "Overwrite and retry" for jobs the worker
                                refused to overwrite. */}
                            {isOutputExists(job) && (
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={createJob.isPending}
                                onClick={() =>
                                  createJob.mutate(buildRetry(job), {
                                    onSuccess: () => toast.success("Job queued"),
                                    onError: (err) =>
                                      toast.error(
                                        err instanceof ApiError
                                          ? err.message
                                          : "Failed to queue job",
                                      ),
                                  })
                                }
                              >
                                {createJob.isPending ? "Queuing…" : "Overwrite & retry"}
                              </Button>
                            )}
                            {/* Rename the language when detection failed. */}
                            {needsLanguageRename(job) && (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setReviewJob(job)}
                              >
                                Rename
                              </Button>
                            )}
                            {/* M12: general Retry for an ordinary failed job -
                                distinct from the output_exists/language-review
                                cases above, which keep their own buttons.
                                Gated off live job state (not local component
                                state) so it correctly disappears once a retry
                                is in flight, and reappears if that retry
                                itself later fails. */}
                            {job.status === "failed" &&
                              !isOutputExists(job) &&
                              !needsLanguageRename(job) &&
                              !selectHasActiveJobForSource(
                                liveJobs,
                                job.media_path,
                                job.language_code,
                              ) && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={createJob.isPending}
                                  onClick={() =>
                                    createJob.mutate(buildRetry(job), {
                                      onSuccess: (newJob) => {
                                        useJobsStore.getState().merge([newJob])
                                        toast.success("Retry queued")
                                      },
                                      onError: () =>
                                        toast.error("Failed to queue retry"),
                                    })
                                  }
                                >
                                  {createJob.isPending ? "Queuing…" : "Retry"}
                                </Button>
                              )}
                          </div>
                        </TableCell>
                      </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              )}

              {/* Pagination */}
              {response && response.total > (filters.limit || 20) && (
                <div className="flex items-center justify-between pt-4">
                  <p className="text-sm text-muted-foreground">
                    Showing {(filters.offset || 0) + 1}-{Math.min(
                      (filters.offset || 0) + (filters.limit || 20),
                      response.total
                    )} of {response.total}
                  </p>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={filters.offset === 0}
                      onClick={() => handlePageChange(page - 1)}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <span className="text-sm">
                      Page {page + 1} of {Math.ceil(response.total / (filters.limit || 20))}
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={
                        filters.offset !== undefined &&
                        filters.offset + (filters.limit || 20) >= response.total
                      }
                      onClick={() => handlePageChange(page + 1)}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <LanguageReviewDialog job={reviewJob} onClose={() => setReviewJob(null)} />
    </div>
  )
}
