// Wanted page: search, tabs (All/Movies/Series), scope selector
// (All/Missing/No subs), missing-lang filter, table, live active-job
// indicator, Transcribe dialog.

import { useState, useEffect, useMemo } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2, Search, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useWanted } from "@/hooks/useWanted"
import { useCreateJob } from "@/hooks/useJobs"
import { useWantedRefresh } from "@/hooks/useWantedRefresh"
import { useJobsStore } from "@/lib/jobsStore"
import { ApiError } from "@/lib/api"
import { naturalCompare } from "@/lib/utils"
import { itemName, episodeCode, fullLabel } from "@/lib/wantedLabels"
import { Progress } from "@/components/ui/progress"
import type {
  JobConflictDetail,
  JobCreate,
  MissingSubtitle,
  OutputFormat,
  WantedItem,
  WantedScope,
} from "@/lib/types"

type ItemType = "all" | "movie" | "episode"

// Derive a clean language label from a MissingSubtitle
function langLabel(ms: MissingSubtitle): string {
  let label = ms.name ?? ms.code2
  if (ms.hi) label += " HI"
  if (ms.forced) label += " Forced"
  return label
}

// ── Transcribe dialog ─────────────────────────────────────────────────────────

interface TranscribeDialogProps {
  item: WantedItem | null
  onClose: () => void
}

const FORMAT_OPTIONS: OutputFormat[] = ["srt", "vtt", "webvtt", "sbv"]

// Sentinels for the language Select (Radix disallows empty-string values,
// same reason the "_all" sentinel exists in the language filter below).
const AUTO_LANGUAGE = "_auto"
const OTHER_LANGUAGE = "_other"

function TranscribeDialog({ item, onClose }: TranscribeDialogProps) {
  const createJob = useCreateJob()
  const [language, setLanguage] = useState<string>(AUTO_LANGUAGE)
  const [manualCode, setManualCode] = useState<string>("")
  const [format, setFormat] = useState<OutputFormat>("srt")
  // M6.g: when a subtitle already exists at the resolved output path, hold the
  // intended submission and the conflict detail so the user can confirm an
  // explicit overwrite instead of being silently blocked.
  const [pendingSubmit, setPendingSubmit] = useState<JobCreate | null>(null)
  const [conflict, setConflict] = useState<JobConflictDetail | null>(null)

  // Default to the item's first reported audio language if Bazarr knows it;
  // otherwise only Auto-detect makes sense (missing_subtitles is not used
  // here - it describes what's missing, not what's spoken).
  useEffect(() => {
    const firstAudioLang = item?.audio_language.find((al) => al.code2)
    setLanguage(firstAudioLang ? firstAudioLang.code2 : AUTO_LANGUAGE)
    setManualCode("")
    setFormat("srt")
    setPendingSubmit(null)
    setConflict(null)
  }, [item])

  if (!item) return null

  const buildSubmit = (): JobCreate => ({
    source: item.kind === "movie" ? "bazarr_movie" : "bazarr_episode",
    source_ref: String(item.ext_id),
    media_path: item.media_path,
    language_mode: language === AUTO_LANGUAGE ? "auto" : "explicit",
    language_code: language === AUTO_LANGUAGE
      ? null
      : language === OTHER_LANGUAGE
        ? manualCode.trim()
        : language,
    output_format: format,
  })

  const submit = (payload: JobCreate) => {
    createJob.mutate(payload, {
      onSuccess: () => {
        toast.success("Job queued")
        setPendingSubmit(null)
        setConflict(null)
        onClose()
      },
      onError: (err) => {
        const c = err instanceof ApiError ? err.conflict : null
        if (c?.code === "subtitle_exists") {
          // Defer to the confirm dialog instead of erroring out.
          setPendingSubmit(payload)
          setConflict(c)
          return
        }
        if (err instanceof ApiError && err.status === 409) {
          toast.error("A job for this item is already active")
        } else {
          toast.error(err instanceof ApiError ? err.message : "Failed to queue job")
        }
        setPendingSubmit(null)
        setConflict(null)
        onClose()
      },
    })
  }

  const handleSubmit = () => {
    submit(buildSubmit())
  }

  const confirmOverwrite = () => {
    if (pendingSubmit) submit({ ...pendingSubmit, overwrite: true })
  }

  return (
    <>
      <Dialog open={!!item} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Transcribe</DialogTitle>
        </DialogHeader>
        <div className="space-y-1 text-sm text-muted-foreground">
          <span className="font-medium text-foreground">{fullLabel(item)}</span>
        </div>

        <div className="space-y-4 pt-2">
          {/* Language */}
          <div className="space-y-1.5">
            <Label>Language</Label>
            <Select value={language} onValueChange={setLanguage}>
              <SelectTrigger>
                <SelectValue placeholder="Select language" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO_LANGUAGE}>Auto-detect</SelectItem>
                {item.audio_language
                  .filter((al) => al.code2)
                  .map((al) => (
                    <SelectItem key={al.code2} value={al.code2}>
                      {langLabel(al)}
                    </SelectItem>
                  ))}
                <SelectItem value={OTHER_LANGUAGE}>Other (manual code)</SelectItem>
              </SelectContent>
            </Select>
            {language === OTHER_LANGUAGE && (
              <Input
                placeholder="e.g. en"
                value={manualCode}
                onChange={(e) => setManualCode(e.target.value)}
              />
            )}
          </div>

          {/* Format */}
          <div className="space-y-1.5">
            <Label>Output format</Label>
            <Select
              value={format}
              onValueChange={(v) => setFormat(v as OutputFormat)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FORMAT_OPTIONS.map((f) => (
                  <SelectItem key={f} value={f}>
                    {f.toUpperCase()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={
              createJob.isPending ||
              (language === OTHER_LANGUAGE && !manualCode.trim())
            }
          >
            {createJob.isPending ? "Queuing…" : "Queue job"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    {/* M6.g: overwrite confirmation when a subtitle already exists. */}
    <Dialog
      open={conflict !== null}
      onOpenChange={(open) => {
        if (!open) {
          setConflict(null)
          setPendingSubmit(null)
        }
      }}
    >
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Subtitle already exists</DialogTitle>
        </DialogHeader>
        <div className="space-y-1 text-sm text-muted-foreground">
          <p>
            A {conflict?.language_code ?? ""} subtitle already exists at this
            location{conflict?.existing_mtime ? ` (last modified ${conflict.existing_mtime})` : ""}.
            Overwrite it and re-transcribe?
          </p>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              setConflict(null)
              setPendingSubmit(null)
            }}
          >
            Cancel
          </Button>
          <Button onClick={confirmOverwrite} disabled={createJob.isPending}>
            {createJob.isPending ? "Queuing…" : "Overwrite and retry"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  )
}

// ── WantedPage ────────────────────────────────────────────────────────────────

export function WantedPage() {
  const queryClient = useQueryClient()
  const [itemType, setItemType] = useState<ItemType>("all")
  const [search, setSearch] = useState("")
  // Display-only filter over the already-synced full library - selecting a
  // scope never triggers or narrows a re-sync (see handleRefresh below,
  // which is keyed only on itemType).
  const [scope, setScope] = useState<WantedScope>("all")
  const [langFilter, setLangFilter] = useState<string>("")
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [transcribeItem, setTranscribeItem] = useState<WantedItem | null>(null)

  // Refresh lifecycle (subscribe-first + SSE progress + persisted-state
  // fallback) is encapsulated in useWantedRefresh. The button and progress
  // bar below read directly off `refresh.progress`; finalization (toast,
  // table invalidate, 3s reset) happens inside the hook.
  const refresh = useWantedRefresh()

  // Invalidate wanted query when a job finishes
  const lastTerminalJobId = useJobsStore((s) => s.lastTerminalJobId)
  useEffect(() => {
    if (lastTerminalJobId) {
      void queryClient.invalidateQueries({ queryKey: ["wanted"] })
    }
  }, [lastTerminalJobId, queryClient])

  // Refresh wanted list handler
  const handleRefresh = () => {
    refresh.start({ item_type: itemType })
  }

  const { data, isLoading } = useWanted({
    item_type: itemType,
    language: langFilter || undefined,
    search: search || undefined,
    scope,
    page,
    page_size: pageSize,
  })

  // Server now applies search + type + language + scope filtering AND
  // sorting (series name, then season, then episode - article/case/accent-
  // insensitive, natural numbers; see audio_to_subs/api/routes/wanted.py),
  // so the client just surfaces the returned items as-is. Do NOT re-sort
  // here - the returned order is authoritative.
  const items = data?.items ?? []

  // Options for the language filter dropdown come from a dedicated query that
  // omits `language`, so picking a language doesn't shrink `data.items` to
  // only that language and make every other option disappear from the list.
  const { data: langOptionsData } = useWanted({
    item_type: itemType,
    search: search || undefined,
    scope,
    page: 1,
    page_size: 1000,
  })

  // Derive language options (keep the full MissingSubtitle so we can render
  // the real language name, like the "Missing" column does, rather than the
  // bare two-letter code).
  const langOptions = useMemo(() => {
    if (!langOptionsData?.items) return []
    const byCode = new Map<string, MissingSubtitle>()
    for (const item of langOptionsData.items) {
      for (const ms of item.missing_subtitles) {
        if (!byCode.has(ms.code2)) byCode.set(ms.code2, ms)
      }
    }
    return [...byCode.values()].sort((a, b) =>
      naturalCompare(a.name ?? a.code2, b.name ?? b.code2),
    )
  }, [langOptionsData?.items])

  const totalPages = data ? Math.ceil(data.total / pageSize) : 1

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex flex-wrap gap-3 items-center border-b px-4 py-3 bg-muted/30">
        {/* Search */}
        <div className="relative flex-1 min-w-[180px] max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search title or S01E02…"
            className="pl-8"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
          />
        </div>

        {/* Tabs */}
        <Tabs
          value={itemType}
          onValueChange={(v) => {
            setItemType(v as ItemType)
            setPage(1)
          }}
        >
          <TabsList aria-label="Type">
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="movie">Movies</TabsTrigger>
            <TabsTrigger value="episode">Series</TabsTrigger>
          </TabsList>
        </Tabs>

        {/* Scope: display-only filter over the synced library. Does NOT
            trigger or narrow a re-sync - only the Movies/Series tab above
            scopes what Refresh re-syncs. */}
        <Tabs
          value={scope}
          onValueChange={(v) => {
            setScope(v as WantedScope)
            setPage(1)
          }}
        >
          <TabsList aria-label="Scope">
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="missing">Missing</TabsTrigger>
            <TabsTrigger value="no_subs">No subs</TabsTrigger>
          </TabsList>
        </Tabs>

        {/* Language filter */}
        {langOptions.length > 0 && (
          <Select
            value={langFilter || "_all"}
            onValueChange={(v) => {
              setLangFilter(v === "_all" ? "" : v)
              setPage(1)
            }}
          >
            <SelectTrigger className="w-[160px]">
              <SelectValue placeholder="Language" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="_all">All</SelectItem>
              {langOptions.map((ms) => (
                <SelectItem key={ms.code2} value={ms.code2}>
                  {langLabel(ms)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {/* Refresh button — scoped to the current Movies/Series tab selection */}
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={refresh.progress.active}
          >
            {refresh.progress.active ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Refreshing...
              </>
            ) : (
              <>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </>
            )}
          </Button>

          {refresh.progress.active && (
            <div className="flex flex-col gap-1 min-w-[200px]">
              <div className="flex items-center gap-2">
                <Progress
                  value={
                    refresh.progress.total != null
                      ? refresh.progress.percent
                      : undefined
                  }
                  className="h-2 w-32"
                />
                <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                  {refresh.progress.total != null
                    ? `${refresh.progress.processed} / ${refresh.progress.total}`
                    : `${refresh.progress.processed} items`}
                </span>
              </div>
              {/* Surfaces what's actually happening (e.g. "waiting for
                  another sync to finish", "syncing episodes") instead of
                  leaving an unexplained, possibly-static count as the only
                  signal - especially important while blocked behind another
                  in-progress sync, where processed/total can't move yet. */}
              {refresh.progress.stage && (
                <span className="text-[11px] text-muted-foreground capitalize whitespace-nowrap">
                  {refresh.progress.stage}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            No items found.
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[200px]">Series / Movie</TableHead>
                <TableHead className="w-[100px]">S/E</TableHead>
                <TableHead className="w-[100px]">Type</TableHead>
                <TableHead className="w-[280px]">Missing</TableHead>
                <TableHead className="w-[110px] text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <WantedRow
                  key={item.id}
                  item={item}
                  onTranscribe={() => setTranscribeItem(item)}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Pagination */}
      {data && data.total > 0 && (
        <div className="flex items-center justify-between border-t px-4 py-2 text-sm text-muted-foreground">
          <span>
            Page {page} of {totalPages} — {data?.total ?? 0} items
          </span>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Label htmlFor="page-size" className="text-sm">
                Rows per page
              </Label>
              <Select
                value={String(pageSize)}
                onValueChange={(v) => {
                  setPageSize(Number(v))
                  setPage(1)
                }}
              >
                <SelectTrigger id="page-size" className="w-[80px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10</SelectItem>
                  <SelectItem value="50">50</SelectItem>
                  <SelectItem value="100">100</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </div>
      )}

      <TranscribeDialog
        item={transcribeItem}
        onClose={() => setTranscribeItem(null)}
      />
    </div>
  )
}

// ── WantedRow ─────────────────────────────────────────────────────────────────

interface WantedRowProps {
  item: WantedItem
  onTranscribe: () => void
}

function WantedRow({ item, onTranscribe }: WantedRowProps) {
  // Pull live active-job info from the SSE store (may be more up-to-date than the API)
  const liveJob = useJobsStore((s) =>
    item.active_job_id ? s.jobs[item.active_job_id] : null,
  )
  const hasActiveJob =
    liveJob != null || (item.active_job_id != null && item.active_job_status != null)

  const code = episodeCode(item)

  return (
    <TableRow>
      <TableCell className="font-medium">
        <span className="flex items-center gap-1.5">
          {hasActiveJob && (
            <Loader2
              className="h-3.5 w-3.5 animate-spin text-muted-foreground flex-none"
              aria-label="Job active"
            />
          )}
          {itemName(item)}
        </span>
      </TableCell>
      <TableCell>
        {code ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="tabular-nums text-sm">
                {code}
              </span>
            </TooltipTrigger>
            <TooltipContent>{item.title}</TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground capitalize">
        {item.kind}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1">
          {item.missing_subtitles.map((ms) => (
            <Badge key={ms.code2} variant="outline" className="text-xs">
              {langLabel(ms)}
            </Badge>
          ))}
          {item.missing_subtitles.length === 0 && (
            <span className="text-xs text-muted-foreground">—</span>
          )}
        </div>
      </TableCell>
      <TableCell className="text-right">
        <Button
          size="sm"
          variant="outline"
          onClick={onTranscribe}
          disabled={hasActiveJob}
        >
          Transcribe
        </Button>
      </TableCell>
    </TableRow>
  )
}
