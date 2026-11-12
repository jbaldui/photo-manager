import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

const STATUS_KEYS = new Set(["scan-status", "removal-status", "auto-status", "enhance-status", "organize-status"]);

/**
 * Polls a background job's status. When the job finishes, every query derived from the library is
 * refetched, so pages never show stale groups or counts. With `live`, counts are also refreshed while
 * the job runs (e.g. duplicates disappearing as they are moved to quarantine).
 */
export function useJobWatcher<T extends { running: boolean; finished?: number | null }>(key: string, fetcher: () => Promise<T>, live = false) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: [key],
    queryFn: fetcher,
    refetchInterval: (query) => (query.state.data?.running ? 1000 : 4000),
  });
  const running = !!q.data?.running;
  const finished = q.data?.finished ?? null;
  const wasRunning = useRef(false);
  const lastFinished = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    // A job can start and finish between two polls (moving a few files takes milliseconds), so a new
    // `finished` timestamp counts as "a job just ended" too, not only a running -> idle transition.
    const ended = (wasRunning.current && !running) || (lastFinished.current !== undefined && !!finished && finished !== lastFinished.current);
    if (ended) qc.invalidateQueries({ predicate: (x) => !STATUS_KEYS.has(String(x.queryKey[0])) });
    wasRunning.current = running;
    if (q.data) lastFinished.current = finished;
  }, [running, finished, q.data, qc]);
  useEffect(() => {
    if (!live || !running) return;
    const t = setInterval(() => {
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["groups"] });
    }, 3000);
    return () => clearInterval(t);
  }, [live, running, qc]);
  return q;
}
