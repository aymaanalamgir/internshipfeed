// Provide typed JSON requests and consistent API error handling.
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | undefined;

  constructor(status: number, detail?: string) {
    super('Request failed');
    this.status = status;
    this.detail = detail;
  }
}

let sessionReady: Promise<void> | undefined;

export function ensureSession(): Promise<void> {
  if (!sessionReady) {
    sessionReady = fetch('/api/session', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(response => { if (!response.ok) throw new ApiError(response.status); })
      .catch(error => { sessionReady = undefined; throw error; });
  }
  return sessionReady;
}

export async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json', ...options.headers },
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
      ? body.detail : undefined;
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}
