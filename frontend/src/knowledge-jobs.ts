// The API sorts recent jobs; active work takes precedence over a newer terminal
// task. Recovery is read-only: never resubmit an import after refresh.
export type KnowledgeImportIssue = { path: string; display_name: string; reason: string };
export type KnowledgeJob = {
  id: string;
  status: 'pending' | 'running' | 'complete' | 'partial' | 'failed' | 'cancelled';
  cancellation_supported?: boolean;
  stage: string;
  total_count: number;
  processed_count: number;
  imported_count: number;
  skipped_count: number;
  failed_count: number;
  current_file?: string;
  error_message?: string;
  result?: { skipped?: KnowledgeImportIssue[]; failed?: KnowledgeImportIssue[] };
};

export function isKnowledgeJob(value: unknown): value is KnowledgeJob {
  if (!value || typeof value !== 'object') return false;
  const row = value as Record<string, unknown>;
  return typeof row.id === 'string' && typeof row.stage === 'string'
    && ['pending', 'running', 'complete', 'partial', 'failed', 'cancelled'].includes(String(row.status))
    && ['total_count', 'processed_count', 'imported_count', 'skipped_count', 'failed_count']
      .every(key => typeof row[key] === 'number' && Number.isInteger(row[key]) && Number(row[key]) >= 0);
}

export function recentKnowledgeJob<T extends { status: string }>(jobs: T[]): T | null {
  return jobs.find((job) => job.status === 'pending' || job.status === 'running') ?? jobs[0] ?? null;
}
