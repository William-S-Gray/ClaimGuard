// Thin fetch wrapper around the ClaimGuard backend.
// Unwraps the standard {success, message, data, metadata, errors} envelope,
// attaches the auth token, transparently refreshes an expired access token,
// and surfaces clean errors.
//
// SECURITY: PHI system. The short-lived access token lives in memory ONLY —
// never localStorage/sessionStorage — so it cannot be exfiltrated via XSS or
// read from disk. The long-lived refresh token is held by the browser in an
// httpOnly + Secure + SameSite cookie set by the backend; JavaScript can never
// read it, and it is sent automatically via `credentials: 'include'`.

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '/api/v1';

// ── Token storage (in-memory access token only) ───────────────────────────────
let accessToken: string | null = null;

export function getToken(): string | null {
  return accessToken;
}

export function setToken(token: string | null): void {
  accessToken = token;
}

/**
 * Store the access token. The refresh token argument is ignored on purpose —
 * refresh is handled entirely by the backend's httpOnly cookie. The second
 * parameter is kept for call-site compatibility.
 */
export function setTokens(access: string | null, _refresh?: string | null): void {
  accessToken = access;
}

export function clearTokens(): void {
  accessToken = null;
}

// ── Types ─────────────────────────────────────────────────────────────────────
export interface Envelope<T> {
  success: boolean;
  message: string;
  data: T;
  metadata: Record<string, unknown>;
  errors: Array<{ code: string; message: string; field?: string }>;
}

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, message: string, code = 'error') {
    super(message);
    this.status = status;
    this.code = code;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  /** Return null instead of throwing on 404 (used for get-by-id reads). */
  nullOn404?: boolean;
  signal?: AbortSignal;
  /** Internal: skip the auto-refresh retry (prevents loops). */
  _retried?: boolean;
}

function buildUrl(path: string, params?: RequestOptions['params']): string {
  const url = `${API_BASE_URL}${path}`;
  if (!params) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.append(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `${url}?${qs}` : url;
}

// ── Token refresh (deduped) ─────────────────────────────────────────────────────
// The refresh token travels in the httpOnly cookie, so we send no body and rely
// on `credentials: 'include'`. A fresh access token comes back in the response.
let refreshInFlight: Promise<boolean> | null = null;

export async function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: '{}',
      });
      if (!res.ok) throw new Error('refresh failed');
      const payload = (await res.json()) as Envelope<{ accessToken: string }>;
      if (!payload.success) throw new Error(payload.message);
      setToken(payload.data.accessToken);
      return true;
    } catch {
      clearTokens();
      // Let the app react (redirect to login).
      window.dispatchEvent(new Event('cg:logout'));
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

// ── Core request ────────────────────────────────────────────────────────────────
async function performFetch(path: string, options: RequestOptions): Promise<Response> {
  const { method = 'GET', body, params, signal, _retried } = options;
  const isAuthPath = path.startsWith('/auth/');

  const headers: Record<string, string> = { Accept: 'application/json' };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response: Response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
      // Send the httpOnly refresh cookie with every request (same-origin).
      credentials: 'include',
    });
  } catch {
    throw new ApiError(0, 'Network error — is the ClaimGuard API running?', 'network');
  }

  // Transparently refresh an expired access token and retry once. The refresh
  // cookie decides whether a session exists — we always attempt it.
  if (response.status === 401 && !isAuthPath && !_retried) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return performFetch(path, { ...options, _retried: true });
    }
  }
  return response;
}

/** Returns the full response envelope (data + metadata). Throws on failure. */
export async function requestEnvelope<T>(
  path: string,
  options: RequestOptions = {},
): Promise<Envelope<T>> {
  const response = await performFetch(path, options);
  let payload: Envelope<T> | null = null;
  try {
    payload = (await response.json()) as Envelope<T>;
  } catch {
    /* non-JSON response */
  }
  if (!response.ok || (payload && payload.success === false)) {
    const message = payload?.message ?? `Request failed (${response.status})`;
    const code = payload?.errors?.[0]?.code ?? 'error';
    throw new ApiError(response.status, message, code);
  }
  return (payload ?? { success: true, message: 'OK', data: null as T, metadata: {}, errors: [] });
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await performFetch(path, options);

  if (response.status === 404 && options.nullOn404) {
    return null as T;
  }

  let payload: Envelope<T> | null = null;
  try {
    payload = (await response.json()) as Envelope<T>;
  } catch {
    /* non-JSON response */
  }

  if (!response.ok || (payload && payload.success === false)) {
    const message = payload?.message ?? `Request failed (${response.status})`;
    const code = payload?.errors?.[0]?.code ?? 'error';
    throw new ApiError(response.status, message, code);
  }

  return payload ? payload.data : (null as T);
}

/** Fetch an authenticated file (e.g. a CSV report) and hand it to the browser. */
export async function downloadFile(
  path: string,
  filename: string,
  params?: RequestOptions['params'],
): Promise<void> {
  const response = await performFetch(path, { method: 'GET', params });
  if (!response.ok) throw new ApiError(response.status, `Download failed (${response.status})`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>(path, { ...opts, method: 'GET' }),
  getEnvelope: <T>(path: string, opts?: RequestOptions) =>
    requestEnvelope<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'PATCH', body }),
  del: <T>(path: string, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'DELETE' }),
};
