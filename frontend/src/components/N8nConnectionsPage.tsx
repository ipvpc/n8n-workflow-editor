import { useCallback, useEffect, useState } from "react";
import { requestJson } from "../lib/api";
import type { N8nAccessVerification, N8nInstanceRow, Preferences } from "../types";

type Props = {
  onBack: () => void;
  onChanged: () => void;
};

function formatAccessVerification(result: N8nAccessVerification): string {
  if (result.ok) return "Read and write access verified.";
  const parts: string[] = [];
  if (result.read) parts.push("read OK");
  else parts.push(`read failed${result.read_error ? `: ${result.read_error}` : ""}`);
  if (result.write) parts.push("write OK");
  else parts.push(`write failed${result.write_error ? `: ${result.write_error}` : ""}`);
  return parts.join(" · ");
}

function buildTestPayload(
  editingId: string | null,
  baseUrl: string,
  apiKey: string,
  timeout: number,
  skipTls: boolean,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    base_url: baseUrl.trim(),
    http_timeout_seconds: timeout,
    skip_tls_verify: skipTls,
  };
  if (apiKey.trim()) payload.api_key = apiKey.trim();
  if (editingId) payload.instance_id = editingId;
  return payload;
}

export function N8nConnectionsPage({ onBack, onChanged }: Props) {
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [instances, setInstances] = useState<N8nInstanceRow[]>([]);
  const [prefs, setPrefs] = useState<Preferences | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingKeyMasked, setEditingKeyMasked] = useState<string | null>(null);

  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [name, setName] = useState("");
  const [timeout, setTimeout] = useState(60);
  const [skipTls, setSkipTls] = useState(false);

  const resetForm = () => {
    setEditingId(null);
    setEditingKeyMasked(null);
    setName("");
    setBaseUrl("");
    setApiKey("");
    setTimeout(60);
    setSkipTls(false);
  };

  const loadData = useCallback(async () => {
    const [i, pr] = await Promise.all([
      requestJson<N8nInstanceRow[]>("/api/n8n-instances"),
      requestJson<Preferences>("/api/preferences"),
    ]);
    setInstances(i);
    setPrefs(pr);
  }, []);

  useEffect(() => {
    setStatusMsg(null);
    void loadData().catch((e) => setStatusMsg(String(e)));
  }, [loadData]);

  const verifyConnection = async (
    payload: Record<string, unknown>,
    instanceId?: string,
  ): Promise<N8nAccessVerification> => {
    const url = instanceId
      ? `/api/n8n-instances/${encodeURIComponent(instanceId)}/test`
      : "/api/n8n/test";
    return requestJson<N8nAccessVerification>(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  };

  const testFormConnection = async () => {
    if (!baseUrl.trim()) {
      setStatusMsg("Base URL is required to test the connection.");
      return;
    }
    if (!editingId && !apiKey.trim()) {
      setStatusMsg("API key is required to test a new instance.");
      return;
    }
    setStatusMsg(null);
    setBusy(true);
    try {
      const result = await verifyConnection(buildTestPayload(editingId, baseUrl, apiKey, timeout, skipTls));
      setStatusMsg(formatAccessVerification(result));
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  const testConnection = async () => {
    setStatusMsg(null);
    setBusy(true);
    try {
      const result = await verifyConnection({});
      setStatusMsg(formatAccessVerification(result));
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  const testSavedInstance = async (instance: N8nInstanceRow) => {
    setStatusMsg(null);
    setBusy(true);
    try {
      const result = await verifyConnection({}, instance.id);
      setStatusMsg(`${instance.name}: ${formatAccessVerification(result)}`);
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (instance: N8nInstanceRow) => {
    setEditingId(instance.id);
    setEditingKeyMasked(instance.api_key_masked);
    setName(instance.name);
    setBaseUrl(instance.base_url);
    setApiKey("");
    setTimeout(instance.http_timeout_seconds);
    setSkipTls(instance.skip_tls_verify);
    setStatusMsg(null);
  };

  const saveInstance = async () => {
    if (!baseUrl.trim()) {
      setStatusMsg("Base URL is required.");
      return;
    }
    if (!editingId && !apiKey.trim()) {
      setStatusMsg("API key is required for new instances.");
      return;
    }
    setStatusMsg(null);
    setBusy(true);
    try {
      const payload = {
        name: name.trim() || "Unnamed",
        base_url: baseUrl.trim(),
        http_timeout_seconds: timeout,
        skip_tls_verify: skipTls,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      };

      if (editingId) {
        await requestJson(`/api/n8n-instances/${encodeURIComponent(editingId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const verify = await verifyConnection(
          buildTestPayload(editingId, baseUrl, apiKey, timeout, skipTls),
          editingId,
        );
        resetForm();
        await loadData();
        onChanged();
        setStatusMsg(`Instance updated. ${formatAccessVerification(verify)}`);
        return;
      }

      const created = await requestJson<{ ok: boolean; id: string }>("/api/n8n-instances", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, api_key: apiKey.trim() }),
      });
      await requestJson("/api/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          active_n8n_instance_id: created.id,
          active_llm_profile_id: prefs?.active_llm_profile_id ?? null,
        }),
      });
      const verify = await verifyConnection({}, created.id);
      resetForm();
      await loadData();
      onChanged();
      setStatusMsg(`Instance added and set as active. ${formatAccessVerification(verify)}`);
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  const setActive = async (id: string) => {
    if (!prefs) return;
    setStatusMsg(null);
    setBusy(true);
    try {
      await requestJson("/api/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          active_n8n_instance_id: id,
          active_llm_profile_id: prefs.active_llm_profile_id,
        }),
      });
      await loadData();
      onChanged();
      setStatusMsg("Active instance updated.");
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  const removeInstance = async (id: string) => {
    if (!window.confirm("Delete this n8n instance?")) return;
    setStatusMsg(null);
    setBusy(true);
    try {
      await requestJson(`/api/n8n-instances/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (editingId === id) resetForm();
      await loadData();
      onChanged();
      setStatusMsg("Instance removed.");
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="connections-page">
      <header className="connections-header">
        <div>
          <h1>n8n Connections</h1>
          <p className="muted">
            Add and manage multiple n8n instances. Select which one is active for workflows and the editor.
          </p>
        </div>
        <div className="connections-header-actions">
          {statusMsg && <span className="badge">{statusMsg}</span>}
          <button type="button" className="ghost" onClick={() => void testConnection()} disabled={busy}>
            Test active connection
          </button>
          <button type="button" className="ghost" onClick={onBack}>
            Back to editor
          </button>
        </div>
      </header>

      <div className="connections-grid">
        <section className="connections-card">
          <h2>Saved instances</h2>
          {instances.length === 0 && (
            <p className="muted">No instances yet. Add your first connection using the form.</p>
          )}
          <div className="instance-list">
            {instances.map((x) => {
              const active = prefs?.active_n8n_instance_id === x.id;
              const editing = editingId === x.id;
              return (
                <article key={x.id} className={`instance-card ${active ? "active" : ""} ${editing ? "editing" : ""}`}>
                  <div className="instance-card-head">
                    <div>
                      <div className="strong">{x.name}</div>
                      {active && <span className="badge ok">Active</span>}
                      {editing && <span className="badge">Editing</span>}
                    </div>
                    <div className="instance-card-actions">
                      {!editing && (
                        <button type="button" className="ghost" disabled={busy} onClick={() => void testSavedInstance(x)}>
                          Test
                        </button>
                      )}
                      {!editing && (
                        <button type="button" className="ghost" disabled={busy} onClick={() => startEdit(x)}>
                          Edit
                        </button>
                      )}
                      {!active && (
                        <button type="button" className="ghost" disabled={busy} onClick={() => void setActive(x.id)}>
                          Set active
                        </button>
                      )}
                      <button type="button" className="ghost danger-text" disabled={busy} onClick={() => void removeInstance(x.id)}>
                        Delete
                      </button>
                    </div>
                  </div>
                  <div className="muted small">{x.base_url}</div>
                  <div className="muted small">
                    {x.api_key_masked} · timeout {x.http_timeout_seconds}s
                    {x.skip_tls_verify ? " · TLS verify off" : ""}
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="connections-card">
          <h2>{editingId ? "Edit instance" : "Add instance"}</h2>

          <div className="field">
            <label htmlFor="connName">Display name</label>
            <input
              id="connName"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Production n8n"
              autoComplete="off"
            />
          </div>

          <div className="field">
            <label htmlFor="connBase">Base URL</label>
            <input
              id="connBase"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://n8n.example.com"
              autoComplete="off"
            />
            <span className="field-hint">Root URL of your n8n server (no trailing slash required).</span>
          </div>

          <div className="field">
            <label htmlFor="connKey">API key</label>
            <input
              id="connKey"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={editingId ? "Leave blank to keep current key" : "X-N8N-API-KEY"}
              autoComplete="off"
            />
            <span className="field-hint">
              {editingId
                ? `Current key: ${editingKeyMasked ?? "—"}. Leave blank to keep it.`
                : "Create under n8n Settings → API."}
            </span>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="connTimeout">HTTP timeout (seconds)</label>
              <input
                id="connTimeout"
                type="number"
                min={1}
                max={600}
                value={timeout}
                onChange={(e) => setTimeout(Number(e.target.value) || 60)}
              />
            </div>
            <label className="inline-check">
              <input type="checkbox" checked={skipTls} onChange={(e) => setSkipTls(e.target.checked)} />
              Skip TLS verify (lab only)
            </label>
          </div>

          <div className="connections-form-actions">
            <button type="button" className="ghost" disabled={busy} onClick={() => void testFormConnection()}>
              Test connection
            </button>
            <button type="button" className="primary" disabled={busy} onClick={() => void saveInstance()}>
              {editingId ? "Save changes" : "Add instance"}
            </button>
            {editingId && (
              <button type="button" className="ghost" disabled={busy} onClick={resetForm}>
                Cancel
              </button>
            )}
          </div>
        </section>

        <section className="connections-card connections-help">
          <h2>How to get your API key</h2>
          <ol className="help-list">
            <li>Open your n8n instance in the browser.</li>
            <li>Go to <strong>Settings → n8n API</strong>.</li>
            <li>Create an API key with workflow read and write permissions.</li>
            <li>Paste the base URL and key here, then test the connection before saving.</li>
          </ol>
        </section>
      </div>
    </div>
  );
}
