async function parseApiError(r: Response): Promise<string> {
  const fallback = `${r.status} ${r.statusText || "Request failed"}`.trim();
  const body = (await r.json().catch(() => ({}))) as { detail?: unknown; message?: unknown };
  if (typeof body.detail === "string") return body.detail;
  if (body.detail && typeof body.detail === "object") return JSON.stringify(body.detail);
  if (typeof body.message === "string") return body.message;
  return fallback;
}

function authHeader(): Record<string, string> {
  const token = window.localStorage.getItem("n8n_editor_auth_token")?.trim();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {});
  for (const [k, v] of Object.entries(authHeader())) headers.set(k, v);
  let r: Response;
  try {
    r = await fetch(url, { ...init, headers });
  } catch (e) {
    throw new Error(`Network error: ${String(e)}`);
  }
  if (!r.ok) {
    throw new Error(await parseApiError(r));
  }
  return (await r.json()) as T;
}
