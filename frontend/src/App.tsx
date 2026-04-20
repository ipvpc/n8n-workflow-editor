import Editor from "@monaco-editor/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { WorkflowSidebar } from "./components/WorkflowSidebar";

type WorkflowRow = {
  id: string;
  name?: string;
  active?: boolean;
};

type ChatTurn = { role: "user" | "assistant"; content: string };

type SettingsMeta = {
  base_url: string | null;
  api_key_masked: string | null;
  has_api_key: boolean;
  source: string;
  instance_id?: string | null;
  instance_name?: string | null;
};

type N8nInstanceRow = {
  id: string;
  name: string;
  base_url: string;
  api_key_masked: string;
  http_timeout_seconds: number;
  skip_tls_verify: boolean;
};

type LlmProfileRow = {
  id: string;
  name: string;
  provider: "azure_openai" | "openai_compatible";
  config_public: Record<string, unknown>;
};

type Preferences = {
  active_n8n_instance_id: string | null;
  active_llm_profile_id: string | null;
};

function workflowsFromResponse(j: unknown): WorkflowRow[] {
  if (Array.isArray(j)) return j as WorkflowRow[];
  if (j && typeof j === "object") {
    const o = j as Record<string, unknown>;
    if (Array.isArray(o.data)) return o.data as WorkflowRow[];
    if (Array.isArray(o.workflows)) return o.workflows as WorkflowRow[];
  }
  return [];
}

function formatJson(text: string): string {
  const parsed = JSON.parse(text);
  return JSON.stringify(parsed, null, 2);
}

function extractJsonFromMarkdown(md: string): string | null {
  const re = /```(?:json)?\s*([\s\S]*?)```/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(md)) !== null) {
    const inner = m[1]?.trim();
    if (!inner) continue;
    try {
      JSON.parse(inner);
      return inner;
    } catch {
      /* try next block */
    }
  }
  return null;
}

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

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
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

export default function App() {
  const [dbMode, setDbMode] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [baseUrlInput, setBaseUrlInput] = useState("");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [settingsMeta, setSettingsMeta] = useState<SettingsMeta | null>(null);

  const [instances, setInstances] = useState<N8nInstanceRow[]>([]);
  const [profiles, setProfiles] = useState<LlmProfileRow[]>([]);
  const [prefs, setPrefs] = useState<Preferences | null>(null);

  const [newN8nName, setNewN8nName] = useState("");
  const [newN8nBase, setNewN8nBase] = useState("");
  const [newN8nKey, setNewN8nKey] = useState("");
  const [newN8nTimeout, setNewN8nTimeout] = useState(60);
  const [newN8nSkipTls, setNewN8nSkipTls] = useState(false);

  const [newLlmName, setNewLlmName] = useState("");
  const [newLlmProvider, setNewLlmProvider] = useState<"azure_openai" | "openai_compatible">("openai_compatible");
  const [azureEp, setAzureEp] = useState("");
  const [azureKey, setAzureKey] = useState("");
  const [azureDep, setAzureDep] = useState("");
  const [azureVer, setAzureVer] = useState("2024-08-01-preview");
  const [oaiKey, setOaiKey] = useState("");
  const [oaiBase, setOaiBase] = useState("https://api.openai.com/v1");
  const [oaiModel, setOaiModel] = useState("gpt-4o-mini");

  const [workflows, setWorkflows] = useState<WorkflowRow[]>([]);
  const [filter, setFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editorText, setEditorText] = useState("{}");
  const [dirty, setDirty] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [aiOk, setAiOk] = useState<boolean | null>(null);

  const [chat, setChat] = useState<ChatTurn[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [loadingWorkflowId, setLoadingWorkflowId] = useState<string | null>(null);
  const [savingWorkflow, setSavingWorkflow] = useState(false);
  const workflowLoadSeq = useRef(0);

  const loadCapabilities = useCallback(async () => {
    try {
      const j = await requestJson<{ database?: boolean }>("/api/capabilities");
      setDbMode(!!j.database);
    } catch {
      setDbMode(false);
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const j = await requestJson<SettingsMeta>("/api/settings/n8n");
      setSettingsMeta(j);
      if (j.base_url) setBaseUrlInput(j.base_url);
    } catch {
      // Keep existing values when settings load fails.
    }
  }, []);

  const loadDbBundle = useCallback(async () => {
    try {
      const [i, p, pr] = await Promise.all([
        requestJson<N8nInstanceRow[]>("/api/n8n-instances"),
        requestJson<LlmProfileRow[]>("/api/llm-profiles"),
        requestJson<Preferences>("/api/preferences"),
      ]);
      setInstances(i);
      setProfiles(p);
      setPrefs(pr);
    } catch (e) {
      setStatusMsg(`Failed to load DB settings: ${String(e)}`);
    }
  }, []);

  const loadAiStatus = useCallback(async () => {
    try {
      const j = await requestJson<{ enabled?: boolean }>("/api/ai/status");
      setAiOk(!!j.enabled);
    } catch {
      setAiOk(false);
    }
  }, []);

  const refreshWorkflows = useCallback(async () => {
    setStatusMsg(null);
    try {
      const j = await requestJson<unknown>("/api/workflows?limit=200");
      setWorkflows(workflowsFromResponse(j));
    } catch (e) {
      setStatusMsg(String(e));
      setWorkflows([]);
    }
  }, []);

  useEffect(() => {
    void loadCapabilities();
  }, [loadCapabilities]);

  useEffect(() => {
    void loadSettings();
    void loadAiStatus();
    void refreshWorkflows();
  }, [loadSettings, loadAiStatus, refreshWorkflows]);

  useEffect(() => {
    if (settingsOpen && dbMode) void loadDbBundle();
  }, [settingsOpen, dbMode, loadDbBundle]);

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!dirty) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  const normId = (s: string | null | undefined) => (s && s.length ? s : null);

  const applyPreferences = useCallback(
    async (next: Preferences) => {
      setStatusMsg(null);
      try {
        await requestJson<{ ok: boolean }>("/api/preferences", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            active_n8n_instance_id: normId(next.active_n8n_instance_id ?? undefined),
            active_llm_profile_id: normId(next.active_llm_profile_id ?? undefined),
          }),
        });
        setPrefs({
          active_n8n_instance_id: normId(next.active_n8n_instance_id ?? undefined),
          active_llm_profile_id: normId(next.active_llm_profile_id ?? undefined),
        });
        await loadSettings();
        await loadAiStatus();
        await refreshWorkflows();
        setStatusMsg("Active targets updated.");
      } catch (e) {
        setStatusMsg(String(e));
      }
    },
    [loadSettings, loadAiStatus, refreshWorkflows],
  );

  const loadWorkflow = useCallback(async (id: string) => {
    if (dirty && selectedId && selectedId !== id) {
      const proceed = window.confirm("You have unsaved changes. Load another workflow and discard changes?");
      if (!proceed) return;
    }
    setStatusMsg(null);
    const seq = ++workflowLoadSeq.current;
    setLoadingWorkflowId(id);
    try {
      const data = await requestJson<unknown>(`/api/workflows/${encodeURIComponent(id)}`);
      if (seq !== workflowLoadSeq.current) return;
      setEditorText(JSON.stringify(data, null, 2));
      setSelectedId(id);
      setDirty(false);
    } catch (e) {
      if (seq !== workflowLoadSeq.current) return;
      setStatusMsg(String(e));
    } finally {
      if (seq === workflowLoadSeq.current) setLoadingWorkflowId(null);
    }
  }, [dirty, selectedId]);

  const saveWorkflow = useCallback(async () => {
    if (!selectedId || savingWorkflow) return;
    let body: object;
    try {
      body = JSON.parse(editorText) as object;
    } catch (e) {
      setStatusMsg(`Invalid JSON: ${e}`);
      return;
    }
    setStatusMsg(null);
    setSavingWorkflow(true);
    try {
      const data = await requestJson<unknown>(`/api/workflows/${encodeURIComponent(selectedId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setEditorText(JSON.stringify(data, null, 2));
      setDirty(false);
      setStatusMsg("Saved.");
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setSavingWorkflow(false);
    }
  }, [editorText, savingWorkflow, selectedId]);

  const onFormat = useCallback(() => {
    try {
      setEditorText(formatJson(editorText));
      setStatusMsg("Formatted.");
    } catch (e) {
      setStatusMsg(`Cannot format: ${e}`);
    }
  }, [editorText]);

  const testConnection = useCallback(async () => {
    setStatusMsg(null);
    try {
      await requestJson<{ ok: boolean }>("/api/n8n/test", { method: "POST" });
      setStatusMsg("n8n connection OK.");
    } catch (e) {
      setStatusMsg(String(e));
    }
  }, []);

  const saveSettings = useCallback(async () => {
    setStatusMsg(null);
    const payload: { base_url: string; api_key?: string } = { base_url: baseUrlInput.trim() };
    const k = apiKeyInput.trim();
    if (k) payload.api_key = k;
    try {
      await requestJson<{ ok: boolean }>("/api/settings/n8n", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setApiKeyInput("");
      setSettingsOpen(false);
      await loadSettings();
      await refreshWorkflows();
      setStatusMsg("Settings saved.");
    } catch (e) {
      setStatusMsg(String(e));
    }
  }, [apiKeyInput, baseUrlInput, loadSettings, refreshWorkflows]);

  const addN8nInstance = useCallback(async () => {
    setStatusMsg(null);
    try {
      await requestJson<{ ok: boolean }>("/api/n8n-instances", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newN8nName.trim() || "Unnamed",
          base_url: newN8nBase.trim(),
          api_key: newN8nKey.trim(),
          http_timeout_seconds: newN8nTimeout,
          skip_tls_verify: newN8nSkipTls,
        }),
      });
      setNewN8nName("");
      setNewN8nBase("");
      setNewN8nKey("");
      setNewN8nTimeout(60);
      setNewN8nSkipTls(false);
      await loadDbBundle();
      setStatusMsg("n8n remote added.");
    } catch (e) {
      setStatusMsg(String(e));
    }
  }, [newN8nBase, newN8nKey, newN8nName, newN8nSkipTls, newN8nTimeout, loadDbBundle]);

  const removeN8nInstance = useCallback(
    async (id: string) => {
      if (!confirm("Delete this n8n remote?")) return;
      setStatusMsg(null);
      try {
        await requestJson<{ ok: boolean }>(`/api/n8n-instances/${encodeURIComponent(id)}`, { method: "DELETE" });
        await loadDbBundle();
        await loadSettings();
        setStatusMsg("Remote removed.");
      } catch (e) {
        setStatusMsg(String(e));
      }
    },
    [loadDbBundle, loadSettings],
  );

  const addLlmProfile = useCallback(async () => {
    setStatusMsg(null);
    const config =
      newLlmProvider === "azure_openai"
        ? {
            azure_endpoint: azureEp.trim(),
            api_key: azureKey.trim(),
            azure_deployment: azureDep.trim(),
            api_version: azureVer.trim() || "2024-08-01-preview",
          }
        : {
            api_key: oaiKey.trim(),
            base_url: oaiBase.trim() || "https://api.openai.com/v1",
            model: oaiModel.trim() || "gpt-4o-mini",
          };
    try {
      await requestJson<{ ok: boolean }>("/api/llm-profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newLlmName.trim() || "Unnamed",
          provider: newLlmProvider,
          config,
        }),
      });
      setNewLlmName("");
      setAzureEp("");
      setAzureKey("");
      setAzureDep("");
      setOaiKey("");
      await loadDbBundle();
      await loadAiStatus();
      setStatusMsg("LLM profile added.");
    } catch (e) {
      setStatusMsg(String(e));
    }
  }, [azureDep, azureEp, azureKey, azureVer, loadAiStatus, loadDbBundle, newLlmName, newLlmProvider, oaiBase, oaiKey, oaiModel]);

  const removeLlmProfile = useCallback(
    async (id: string) => {
      if (!confirm("Delete this LLM profile?")) return;
      setStatusMsg(null);
      try {
        await requestJson<{ ok: boolean }>(`/api/llm-profiles/${encodeURIComponent(id)}`, { method: "DELETE" });
        await loadDbBundle();
        await loadAiStatus();
        setStatusMsg("Profile removed.");
      } catch (e) {
        setStatusMsg(String(e));
      }
    },
    [loadAiStatus, loadDbBundle],
  );

  const sendChat = useCallback(async () => {
    const text = chatInput.trim();
    if (!text || chatBusy) return;
    const next: ChatTurn[] = [...chat, { role: "user", content: text }];
    setChat(next);
    setChatInput("");
    setChatBusy(true);
    setStatusMsg(null);
    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: next.map((m) => ({ role: m.role, content: m.content })),
          workflow_id: selectedId,
          workflow_json: editorText,
        }),
      });
      const raw = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail =
          typeof raw?.detail === "string" ? raw.detail : JSON.stringify(raw?.detail ?? raw);
        setChat((c) => [...c, { role: "assistant", content: `Error: ${detail}` }]);
        return;
      }
      const answer = String((raw as { answer_markdown?: string }).answer_markdown ?? "");
      setChat((c) => [...c, { role: "assistant", content: answer || "(empty response)" }]);
    } catch (e) {
      setChat((c) => [...c, { role: "assistant", content: `Error: ${e}` }]);
    } finally {
      setChatBusy(false);
    }
  }, [chat, chatBusy, chatInput, editorText, selectedId]);

  const applyJsonFromLastAssistant = useCallback(() => {
    for (let i = chat.length - 1; i >= 0; i--) {
      if (chat[i].role !== "assistant") continue;
      const extracted = extractJsonFromMarkdown(chat[i].content);
      if (!extracted) {
        setStatusMsg("No valid JSON code block found in last assistant messages.");
        return;
      }
      try {
        setEditorText(formatJson(extracted));
        setDirty(true);
        setStatusMsg("Editor updated from assistant JSON. Review and Save.");
      } catch (e) {
        setStatusMsg(`Could not apply: ${e}`);
      }
      return;
    }
    setStatusMsg("No assistant message to pull JSON from.");
  }, [chat]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return workflows;
    return workflows.filter(
      (w) =>
        (w.name ?? "").toLowerCase().includes(q) ||
        String(w.id).toLowerCase().includes(q),
    );
  }, [filter, workflows]);

  const editorTitle = selectedId ? `Workflow ${selectedId}` : "No workflow selected";

  const sidebarHint = useMemo(() => {
    if (!settingsMeta) return null;
    if (settingsMeta.instance_name) {
      return `${settingsMeta.instance_name} · ${settingsMeta.base_url ?? "—"} · ${settingsMeta.source}`;
    }
    return `${settingsMeta.base_url ?? "—"} · ${settingsMeta.source}`;
  }, [settingsMeta]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <h1>n8n Workflow Editor</h1>
        <div className="topbar-actions">
          {dbMode && <span className="badge ok">PostgreSQL</span>}
          {aiOk === true && <span className="badge ok">AI ready</span>}
          {aiOk === false && <span className="badge warn">AI not configured</span>}
          {statusMsg && <span className="badge">{statusMsg}</span>}
          <button type="button" className="ghost" onClick={() => void refreshWorkflows()}>
            Refresh list
          </button>
          <button type="button" className="ghost" onClick={() => setSettingsOpen(true)}>
            Settings
          </button>
        </div>
      </header>

      <WorkflowSidebar
        filter={filter}
        onFilterChange={setFilter}
        sidebarHint={sidebarHint}
        workflows={filtered}
        selectedId={selectedId}
        loadingWorkflowId={loadingWorkflowId}
        onSelectWorkflow={(id) => void loadWorkflow(id)}
      />

      <section className="editor-panel">
        <div className="editor-toolbar">
          <div className="title">{editorTitle}</div>
          <button
            type="button"
            className="primary"
            disabled={!selectedId || !dirty || savingWorkflow}
            onClick={() => void saveWorkflow()}
          >
            Save to n8n
          </button>
          <button type="button" className="ghost" onClick={onFormat}>
            Format JSON
          </button>
        </div>
        <div className="monaco-wrap">
          <Editor
            key={selectedId ?? "none"}
            defaultLanguage="json"
            theme="vs-dark"
            value={editorText}
            onChange={(v) => {
              setEditorText(v ?? "");
              setDirty(true);
            }}
            options={{
              minimap: { enabled: true },
              fontSize: 13,
              wordWrap: "on",
              scrollBeyondLastLine: false,
              automaticLayout: true,
            }}
          />
        </div>
      </section>

      <ChatPanel
        chat={chat}
        chatInput={chatInput}
        chatBusy={chatBusy}
        onChatInputChange={setChatInput}
        onSend={() => void sendChat()}
        onApplyJson={applyJsonFromLastAssistant}
        onClear={() => setChat([])}
      />

      {settingsOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setSettingsOpen(false)}>
          <div
            className="modal"
            role="dialog"
            style={{ maxHeight: "90vh", overflowY: "auto", maxWidth: "720px" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2>Settings</h2>

            {dbMode ? (
              <>
                <p className="muted">
                  Connections and LLM credentials are stored in PostgreSQL. Pick the active n8n remote and LLM profile for
                  the workflow list, editor saves, and chat.
                </p>

                <h3>Active selection</h3>
                {!prefs && <div className="muted">Loading preferences…</div>}
                {prefs && (
                  <div className="field-row">
                    <div className="field">
                      <label htmlFor="selN8n">Active n8n remote</label>
                      <select
                        id="selN8n"
                        value={prefs.active_n8n_instance_id ?? ""}
                        onChange={(e) => {
                          const v = e.target.value || null;
                          void applyPreferences({
                            active_n8n_instance_id: v,
                            active_llm_profile_id: prefs.active_llm_profile_id,
                          });
                        }}
                      >
                        <option value="">— none —</option>
                        {instances.map((x) => (
                          <option key={x.id} value={x.id}>
                            {x.name} ({x.base_url})
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="selLlm">Active LLM profile</label>
                      <select
                        id="selLlm"
                        value={prefs.active_llm_profile_id ?? ""}
                        onChange={(e) => {
                          const v = e.target.value || null;
                          void applyPreferences({
                            active_n8n_instance_id: prefs.active_n8n_instance_id,
                            active_llm_profile_id: v,
                          });
                        }}
                      >
                        <option value="">— none —</option>
                        {profiles.map((x) => (
                          <option key={x.id} value={x.id}>
                            {x.name} ({x.provider})
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                )}

                <h3>n8n remotes</h3>
                <div className="table-like">
                  {instances.map((x) => (
                    <div key={x.id} className="table-row">
                      <div>
                        <div className="strong">{x.name}</div>
                        <div className="muted small">{x.base_url}</div>
                        <div className="muted small">
                          {x.api_key_masked} · timeout {x.http_timeout_seconds}s
                          {x.skip_tls_verify ? " · TLS verify off" : ""}
                        </div>
                      </div>
                      <button type="button" className="ghost" onClick={() => void removeN8nInstance(x.id)}>
                        Delete
                      </button>
                    </div>
                  ))}
                  {instances.length === 0 && <div className="muted">No remotes yet.</div>}
                </div>

                <h4>Add n8n remote</h4>
                <div className="field">
                  <label htmlFor="nnName">Name</label>
                  <input id="nnName" value={newN8nName} onChange={(e) => setNewN8nName(e.target.value)} autoComplete="off" />
                </div>
                <div className="field">
                  <label htmlFor="nnBase">Base URL</label>
                  <input
                    id="nnBase"
                    value={newN8nBase}
                    onChange={(e) => setNewN8nBase(e.target.value)}
                    placeholder="https://n8n.example.com"
                    autoComplete="off"
                  />
                </div>
                <div className="field">
                  <label htmlFor="nnKey">API key</label>
                  <input
                    id="nnKey"
                    type="password"
                    value={newN8nKey}
                    onChange={(e) => setNewN8nKey(e.target.value)}
                    autoComplete="off"
                  />
                </div>
                <div className="field-row">
                  <div className="field">
                    <label htmlFor="nnTo">HTTP timeout (s)</label>
                    <input
                      id="nnTo"
                      type="number"
                      min={1}
                      max={600}
                      value={newN8nTimeout}
                      onChange={(e) => setNewN8nTimeout(Number(e.target.value) || 60)}
                    />
                  </div>
                  <label className="inline-check">
                    <input type="checkbox" checked={newN8nSkipTls} onChange={(e) => setNewN8nSkipTls(e.target.checked)} />
                    Skip TLS verify (lab only)
                  </label>
                </div>
                <div className="modal-actions">
                  <button type="button" className="primary" onClick={() => void addN8nInstance()}>
                    Add remote
                  </button>
                  <button type="button" className="ghost" onClick={() => void testConnection()}>
                    Test active n8n
                  </button>
                </div>

                <h3>LLM profiles</h3>
                <div className="table-like">
                  {profiles.map((x) => (
                    <div key={x.id} className="table-row">
                      <div>
                        <div className="strong">{x.name}</div>
                        <div className="muted small">{x.provider}</div>
                        <div className="muted small">
                          {JSON.stringify(x.config_public)}
                        </div>
                      </div>
                      <button type="button" className="ghost" onClick={() => void removeLlmProfile(x.id)}>
                        Delete
                      </button>
                    </div>
                  ))}
                  {profiles.length === 0 && <div className="muted">No profiles yet.</div>}
                </div>

                <h4>Add LLM profile</h4>
                <div className="field">
                  <label htmlFor="llmName">Name</label>
                  <input id="llmName" value={newLlmName} onChange={(e) => setNewLlmName(e.target.value)} autoComplete="off" />
                </div>
                <div className="field">
                  <label htmlFor="llmProv">Provider</label>
                  <select
                    id="llmProv"
                    value={newLlmProvider}
                    onChange={(e) => setNewLlmProvider(e.target.value as "azure_openai" | "openai_compatible")}
                  >
                    <option value="openai_compatible">OpenAI-compatible</option>
                    <option value="azure_openai">Azure OpenAI</option>
                  </select>
                </div>

                {newLlmProvider === "azure_openai" ? (
                  <>
                    <div className="field">
                      <label>Azure endpoint</label>
                      <input value={azureEp} onChange={(e) => setAzureEp(e.target.value)} autoComplete="off" />
                    </div>
                    <div className="field">
                      <label>API key</label>
                      <input type="password" value={azureKey} onChange={(e) => setAzureKey(e.target.value)} autoComplete="off" />
                    </div>
                    <div className="field">
                      <label>Deployment</label>
                      <input value={azureDep} onChange={(e) => setAzureDep(e.target.value)} autoComplete="off" />
                    </div>
                    <div className="field">
                      <label>API version</label>
                      <input value={azureVer} onChange={(e) => setAzureVer(e.target.value)} autoComplete="off" />
                    </div>
                  </>
                ) : (
                  <>
                    <div className="field">
                      <label>API key</label>
                      <input type="password" value={oaiKey} onChange={(e) => setOaiKey(e.target.value)} autoComplete="off" />
                    </div>
                    <div className="field">
                      <label>Base URL</label>
                      <input value={oaiBase} onChange={(e) => setOaiBase(e.target.value)} autoComplete="off" />
                    </div>
                    <div className="field">
                      <label>Model</label>
                      <input value={oaiModel} onChange={(e) => setOaiModel(e.target.value)} autoComplete="off" />
                    </div>
                  </>
                )}
                <div className="modal-actions">
                  <button type="button" className="primary" onClick={() => void addLlmProfile()}>
                    Add LLM profile
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3>n8n connection</h3>
                <p className="muted">Saved to /data/n8n-connection.json in Docker. Overrides N8N_BASE_URL / N8N_API_KEY from env.</p>
                <div className="field">
                  <label htmlFor="baseUrl">Base URL</label>
                  <input
                    id="baseUrl"
                    value={baseUrlInput}
                    onChange={(e) => setBaseUrlInput(e.target.value)}
                    placeholder="https://n8n.example.com"
                    autoComplete="off"
                  />
                </div>
                <div className="field">
                  <label htmlFor="apiKey">API key (leave blank to keep current)</label>
                  <input
                    id="apiKey"
                    type="password"
                    value={apiKeyInput}
                    onChange={(e) => setApiKeyInput(e.target.value)}
                    placeholder={settingsMeta?.api_key_masked ?? "X-N8N-API-KEY"}
                    autoComplete="off"
                  />
                </div>
                <div className="modal-actions">
                  <button type="button" className="ghost" onClick={() => void testConnection()}>
                    Test connection
                  </button>
                  <button type="button" className="ghost" onClick={() => setSettingsOpen(false)}>
                    Cancel
                  </button>
                  <button type="button" className="primary" onClick={() => void saveSettings()}>
                    Save
                  </button>
                </div>
              </>
            )}

            <div className="modal-actions" style={{ marginTop: "1rem" }}>
              <button type="button" className="ghost" onClick={() => setSettingsOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
