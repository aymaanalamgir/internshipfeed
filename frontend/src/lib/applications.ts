// Define concise labels for the application's five supported progression statuses.
import type { ApplicationStatus } from '../types/jobs';
export const APPLICATION_STATUSES: readonly (readonly [ApplicationStatus, string])[] = [
  ['applied', 'Applied'], ['interview', 'Interview'], ['rejected', 'Rejected'], ['ghosted', 'Ghosted'], ['offered', 'Offered'],
];
