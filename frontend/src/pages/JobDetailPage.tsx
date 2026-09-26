// Job detail page: shows a single job's status/progress and its scoped logs.
// Reached by clicking a job ID anywhere a job ID is shown (Logs, History, Queue).

import type { ReactNode } from "react"
import { ArrowLeft } from "lucide-react"
import { Link, useParams } from "@tanstack/react-router"

import { useJob, useJobLogs } from "@/hooks/useJobs"
import { useTimezoneSetting, formatDateTime } from "@/lib/datetime"
import { jobFullLabel, bazarrRef } from "@/lib/jobLabels"
import type { LogLevel, JobResponse } from "@/lib/types"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

function getLevelBadgeClass(level: LogLevel): string {
  switch (level) {
    case "debug":
      return "bg-log-debug text-primary-foreground border-transparent"
    case "info":
      return "bg-log-info text-primary-foreground border-transparent"
    case "warning":
      return "bg-log-warning text-log-warning-foreground border-transparent"
    case "error":
      return "bg-log-error text-primary-foreground border-transparent"
    default:
      return "bg-muted text-foreground border-transparent"
  }
}

// "Episode" / "Movie" / "Manual", derived from the job's source - the
// type line shown under the full label in the CardDescription.
function jobSourceType(job: JobResponse): string {
  switch (job.source) {
    case "bazarr_episode":
      return "Episode"
    case "bazarr_movie":
      return "Movie"
    default:
      return "Manual"
  }
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="break-words text-sm">{value ?? "—"}</dd>
    </div>
  )
}

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/layout/jobs/$jobId" })
  const timezone = useTimezoneSetting()
  const jobQuery = useJob(jobId)
  const logsQuery = useJobLogs(jobId)

  const job = jobQuery.data

  return (
    <div className="space-y-6">
      <div>
        <Link to="/logs">
          <Button variant="ghost" size="sm" className="gap-1">
            <ArrowLeft className="h-4 w-4" />
            Back to logs
          </Button>
        </Link>
      </div>

      <div>
        <h1 className="text-xl font-semibold">Job {jobId}</h1>
        <p className="text-sm text-muted-foreground">
          Details and activity for this transcription job.
        </p>
      </div>

      {jobQuery.isLoading && (
        <p className="text-sm text-muted-foreground">Loading job…</p>
      )}

      {jobQuery.isError && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Job not found. It may have been removed, or the ID is incorrect.
            </p>
          </CardContent>
        </Card>
      )}

      {job && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Badge variant="outline">{job.status}</Badge>
              <span className="text-muted-foreground">{jobFullLabel(job)}</span>
            </CardTitle>
            <CardDescription>
              {jobSourceType(job)}
              {bazarrRef(job) && <> · {bazarrRef(job)}</>}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field
                label="Progress"
                value={
                  job.status === "running" || job.status === "queued"
                    ? `${job.progress_percent}%${job.progress_stage ? ` · ${job.progress_stage}` : ""}`
                    : job.status
                }
              />
              <Field label="Language" value={job.language_code ?? "auto"} />
              <Field label="Output format" value={job.output_format} />
              <Field
                label="Created"
                value={formatDateTime(job.created_at, timezone ?? undefined)}
              />
              <Field
                label="Started"
                value={
                  job.started_at
                    ? formatDateTime(job.started_at, timezone ?? undefined)
                    : "—"
                }
              />
              <Field
                label="Finished"
                value={
                  job.finished_at
                    ? formatDateTime(job.finished_at, timezone ?? undefined)
                    : "—"
                }
              />
              <Field label="Audio length" value={job.audio_duration_seconds != null ? `${job.audio_duration_seconds}s` : "—"} />
              <Field
                label="Est. cost"
                value={
                  job.estimated_cost_usd != null
                    ? `$${job.estimated_cost_usd.toFixed(4)}`
                    : "—"
                }
              />
              <Field label="Worker" value={job.worker_id} />
              <Field
                label="Media path"
                value={<span className="block break-all">{job.media_path}</span>}
              />
              <Field
                label="Output path"
                value={
                  job.output_path ? (
                    <span className="block break-all">{job.output_path}</span>
                  ) : (
                    "—"
                  )
                }
              />
              {job.error_message && (
                <Field
                  label="Error"
                  value={
                    <span className="block break-all text-destructive">
                      {job.error_message}
                    </span>
                  }
                />
              )}
            </dl>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Job logs</CardTitle>
          <CardDescription>
            Activity entries scoped to this job.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {logsQuery.isLoading && (
            <p className="text-sm text-muted-foreground">Loading logs…</p>
          )}
          {logsQuery.data && logsQuery.data.logs.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No log entries for this job.
            </p>
          )}
          {logsQuery.data && logsQuery.data.logs.length > 0 && (
            <Table>
              <TableHeader className="bg-muted/50">
                <TableRow>
                  <TableHead className="p-3">Timestamp</TableHead>
                  <TableHead className="p-3">Level</TableHead>
                  <TableHead className="p-3 flex-1">Message</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logsQuery.data.logs.map((log) => (
                  <TableRow
                    key={log.id}
                    className="border-t hover:bg-muted/50"
                  >
                    <TableCell className="p-3 whitespace-nowrap">
                      {formatDateTime(log.ts, timezone ?? undefined)}
                    </TableCell>
                    <TableCell className="p-3">
                      <Badge
                        variant="outline"
                        className={getLevelBadgeClass(log.level)}
                      >
                        {log.level.toUpperCase()}
                      </Badge>
                    </TableCell>
                    <TableCell className="p-3 max-w-[400px] truncate">
                      <span className="block truncate" title={log.message}>
                        {log.message}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
