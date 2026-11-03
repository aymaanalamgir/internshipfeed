// Map current and compatibility URL paths to their job views.
import type { JobView } from '../types/jobs';
export function viewForPath(path: string): JobView | 'listings' {
  const route = path.replace(/\/$/, '') || '/';
  return route === '/listings' || route === '/feed' ? 'listings' : route === '/saved' ? 'saved' : route === '/applied' ? 'applied' : route === '/dismissed' ? 'dismissed' : 'recommended';
}
