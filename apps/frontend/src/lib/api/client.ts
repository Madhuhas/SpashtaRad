import type { AnalysisResult, EngineStatus, InstanceUids, SmartContext } from './types';

/** An HTTP error from the backend, carrying its `detail` message when present. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

let accessToken: string | null = null;

/**
 * Set the bearer token used for every backend call (OIDC mode). In development
 * (`SPASHTA_AUTH_MODE=dev`) no token is needed and this is never called.
 */
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function authHeaders(): Record<string, string> {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: { ...authHeaders(), ...init.headers },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') message = body.detail;
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    throw new ApiError(response.status, message);
  }
  return response;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await request(path, init)).json() as Promise<T>;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return json<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export const api = {
  engineStatus: () => json<EngineStatus>('/api/v1/ai/status'),

  analyzeInstance: (uids: InstanceUids) =>
    postJson<AnalysisResult>('/api/v1/ai/analyze', { ...uids, explain: true }),

  analyzeFile: (file: Blob) => {
    const form = new FormData();
    form.append('file', file, 'image.dcm');
    return json<AnalysisResult>('/api/v1/ai/analyze-upload', { method: 'POST', body: form });
  },

  exportFinding: (resultId: string, findingIndex: number) =>
    postJson<InstanceUids>(`/api/v1/ai/results/${resultId}/export`, {
      finding_index: findingIndex,
    }),

  /** Heatmaps need the auth header, so they are fetched and handed out as blobs. */
  heatmap: async (url: string) => (await request(url)).blob(),

  smartContext: () => json<SmartContext>('/api/v1/fhir/context'),

  endSmartSession: () => request('/api/v1/fhir/session', { method: 'DELETE' }),

  /** DICOMweb (QIDO-RS / WADO-RS metadata) through the backend gateway. */
  dicomweb: async <T>(path: string, params?: Record<string, string>): Promise<T> => {
    const query = params ? `?${new URLSearchParams(params)}` : '';
    const response = await request(`/dicomweb/${path}${query}`, {
      headers: { Accept: 'application/dicom+json' },
    });
    // QIDO-RS answers 204 when nothing matches.
    return (response.status === 204 ? [] : await response.json()) as T;
  },
};
