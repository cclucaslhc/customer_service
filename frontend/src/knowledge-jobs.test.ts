import { describe, it, expect } from 'vitest';
import { recentKnowledgeJob, isKnowledgeJob } from './knowledge-jobs';

describe('knowledge task refresh recovery', () => {
  it('distinguishes asynchronous document receipts from synchronous document results', () => {
    expect(isKnowledgeJob({id:'doc',version:1,status:'ready'})).toBe(false);
    expect(isKnowledgeJob({id:'job',status:'pending',stage:'pending',total_count:1,
      processed_count:0,imported_count:0,skipped_count:0,failed_count:0})).toBe(true);
    expect(isKnowledgeJob({id:'job',status:'pending',stage:'pending'})).toBe(false);
    expect(isKnowledgeJob({id:'job',status:'cancelled',stage:'cancelled',total_count:1,
      processed_count:0,imported_count:0,skipped_count:0,failed_count:0})).toBe(true);
  });
  it('has no task when the authorized list is empty', () => {
    expect(recentKnowledgeJob([])).toBeNull();
  });
  it('recovers active work before a more recent terminal job without mutating history', () => {
    const jobs = [{ id: 'new', status: 'complete' }, { id: 'active', status: 'running' }, { id: 'older', status: 'pending' }];
    expect(recentKnowledgeJob(jobs)).toBe(jobs[1]);
    expect(jobs.map((job) => job.id)).toEqual(['new', 'active', 'older']);
  });
  it('shows the latest terminal result rather than starting another command', () => {
    const jobs = [{ id: 'failed', status: 'failed' }, { id: 'old', status: 'complete' }];
    expect(recentKnowledgeJob(jobs)).toBe(jobs[0]);
  });
});
