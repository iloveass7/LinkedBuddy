import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, type Job } from "@/lib/api"

const isFinished = (job: Job | undefined) => job?.status === "done" || job?.status === "failed"

/** Polls a background job until it finishes. Pass null when there is no job to watch. */
export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => (isFinished(query.state.data) ? false : 1500),
  })
}

/**
 * Tracks one background job at a time: `start(jobId)` begins polling and `onFinish` runs once
 * when the job is done or has failed.
 */
export function useTrackedJob(onFinish: (job: Job) => void) {
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useJob(jobId)
  const finish = useRef(onFinish)
  const handled = useRef<string | null>(null)
  useLayoutEffect(() => {
    finish.current = onFinish
  })

  const done = job.data !== undefined && job.data.id === jobId && isFinished(job.data)
  useEffect(() => {
    if (done && job.data && handled.current !== job.data.id) {
      handled.current = job.data.id
      finish.current(job.data)
    }
  }, [done, job.data])

  return { start: setJobId, running: jobId !== null && !done, status: job.data?.status }
}
