import Editor from "@monaco-editor/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { WorkflowBackupsModal } from "./components/WorkflowBackupsModal";
import { ChatPanel } from "./components/ChatPanel";
import { N8nConnectionsPage } from "./components/N8nConnectionsPage";
import { WorkflowSidebar } from "./components/WorkflowSidebar";
import { requestJson } from "./lib/api";
import type { LlmProfileRow, Preferences, SettingsMeta, WorkflowRow } from "./types";

type AppView = "editor" | "connections";

type ChatTurn = { role: "user" | "assistant"; content: string };

function workflowBodyForEditor(data: unknown): string {
  if (!data || typeof data !== "object") return "{}";
  const o = { ...(data as Record<string, unknown>) };
  delete o._local;
  delete o._backup;
  return JSON.stringify(o, null, 2);
}

function parseEditorBody(text: string): Record<string, unknown> {
  const parsed = JSON.parse(text) as Record<string, unknown>;
  delete parsed._local;
  delete parsed._backup;
  return parsed;
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

export default function App() {
  const [view, setView] = useState<AppView>("editor");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsMeta, setSettingsMeta] = useState<SettingsMeta | null>(null);

  const [profiles, setProfiles] = useState<LlmProfileRow[]>([]);
  const [prefs, setPrefs] = useState<Preferences | null>(null);

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
  const [syncBusy, setSyncBusy] = useState(false);
  const [backupsOpen, setBackupsOpen] = useState(false);
  const workflowLoadSeq = useRef(0);

  const loadSettings = useCallback(async () => {
    try {
      const j = await requestJson<SettingsMeta>("/api/settings/n8n");
      setSettingsMeta(j);
    } catch {
      // Keep existing values when settings load fails.
    }
  }, []);

  const loadLlmBundle = useCallback(async () => {
    try {
      const [p, pr] = await Promise.all([
        requestJson<LlmProfileRow[]>("/api/llm-profiles"),
        requestJson<Preferences>("/api/preferences"),
      ]);
      setProfiles(p);
      setPrefs(pr);
    } catch (e) {
      setStatusMsg(`Failed to load LLM settings: ${String(e)}`);
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
      const rows = await requestJson<WorkflowRow[]>("/api/workflows/local");
      setWorkflows(rows);
    } catch (e) {
      setStatusMsg(String(e));
      setWorkflows([]);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
    void loadAiStatus();
    void refreshWorkflows();
  }, [loadSettings, loadAiStatus, refreshWorkflows]);

  useEffect(() => {
    if (settingsOpen) void loadLlmBundle();
  }, [settingsOpen, loadLlmBundle]);

  const onConnectionChanged = useCallback(async () => {
    await loadSettings();
    await refreshWorkflows();
  }, [loadSettings, refreshWorkflows]);

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

  const applyLlmPreference = useCallback(
    async (activeLlmProfileId: string | null) => {
      if (!prefs) return;
      setStatusMsg(null);
      try {
        await requestJson<{ ok: boolean }>("/api/preferences", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            active_n8n_instance_id: prefs.active_n8n_instance_id,
            active_llm_profile_id: normId(activeLlmProfileId ?? undefined),
          }),
        });
        setPrefs({
          active_n8n_instance_id: prefs.active_n8n_instance_id,
          active_llm_profile_id: normId(activeLlmProfileId ?? undefined),
        });
        await loadAiStatus();
        setStatusMsg("Active LLM profile updated.");
      } catch (e) {
        setStatusMsg(String(e));
      }
    },
    [loadAiStatus, prefs],
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
      const data = await requestJson<unknown>(`/api/workflows/local/${encodeURIComponent(id)}`);
      if (seq !== workflowLoadSeq.current) return;
      setEditorText(workflowBodyForEditor(data));
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
    let body: Record<string, unknown>;
    try {
      body = parseEditorBody(editorText);
    } catch (e) {
      setStatusMsg(`Invalid JSON: ${e}`);
      return;
    }
    setStatusMsg(null);
    setSavingWorkflow(true);
    try {
      const data = await requestJson<unknown>(`/api/workflows/local/${encodeURIComponent(selectedId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setEditorText(workflowBodyForEditor(data));
      setDirty(false);
      await refreshWorkflows();
      setStatusMsg("Saved locally.");
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setSavingWorkflow(false);
    }
  }, [editorText, refreshWorkflows, savingWorkflow, selectedId]);

  const syncAllFromN8n = useCallback(async () => {
    if (syncBusy) return;
    setSyncBusy(true);
    setStatusMsg(null);
    try {
      const stats = await requestJson<{ created: number; updated: number; skipped: number }>(
        "/api/workflows/sync?force=false",
        { method: "POST" },
      );
      await refreshWorkflows();
      if (selectedId) {
        const data = await requestJson<unknown>(`/api/workflows/local/${encodeURIComponent(selectedId)}`);
        setEditorText(workflowBodyForEditor(data));
        setDirty(false);
      }
      setStatusMsg(
        `Synced from n8n: ${stats.created} new, ${stats.updated} updated, ${stats.skipped} skipped.`,
      );
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setSyncBusy(false);
    }
  }, [refreshWorkflows, selectedId, syncBusy]);

  const pullFromN8n = useCallback(async () => {
    if (!selectedId || syncBusy) return;
    if (dirty) {
      const proceed = window.confirm("Discard local changes and pull from n8n?");
      if (!proceed) return;
    }
    setSyncBusy(true);
    setStatusMsg(null);
    try {
      const data = await requestJson<unknown>(
        `/api/workflows/local/${encodeURIComponent(selectedId)}/pull?force=true`,
        { method: "POST" },
      );
      setEditorText(workflowBodyForEditor(data));
      setDirty(false);
      await refreshWorkflows();
      setStatusMsg("Pulled from n8n.");
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setSyncBusy(false);
    }
  }, [dirty, refreshWorkflows, selectedId, syncBusy]);

  const pushToN8n = useCallback(async () => {
    if (!selectedId || syncBusy) return;
    if (dirty) {
      setStatusMsg("Save locally before pushing to n8n.");
      return;
    }
    setSyncBusy(true);
    setStatusMsg(null);
    try {
      const data = await requestJson<unknown>(
        `/api/workflows/local/${encodeURIComponent(selectedId)}/push`,
        { method: "POST" },
      );
      setEditorText(workflowBodyForEditor(data));
      setDirty(false);
      await refreshWorkflows();
      setStatusMsg("Pushed to n8n.");
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setSyncBusy(false);
    }
  }, [dirty, refreshWorkflows, selectedId, syncBusy]);

  const backupWorkflow = useCallback(async () => {
    if (!selectedId || syncBusy) return;
    const label = window.prompt("Backup label (optional)", "Manual backup") ?? "Manual backup";
    setSyncBusy(true);
    setStatusMsg(null);
    try {
      await requestJson(`/api/workflows/local/${encodeURIComponent(selectedId)}/backups`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label }),
      });
      setStatusMsg("Backup created.");
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setSyncBusy(false);
    }
  }, [selectedId, syncBusy]);

  const onFormat = useCallback(() => {
    try {
      setEditorText(formatJson(editorText));
      setStatusMsg("Formatted.");
    } catch (e) {
      setStatusMsg(`Cannot format: ${e}`);
    }
  }, [editorText]);

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
      await loadLlmBundle();
      await loadAiStatus();
      setStatusMsg("LLM profile added.");
    } catch (e) {
      setStatusMsg(String(e));
    }
  }, [azureDep, azureEp, azureKey, azureVer, loadAiStatus, loadLlmBundle, newLlmName, newLlmProvider, oaiBase, oaiKey, oaiModel]);

  const removeLlmProfile = useCallback(
    async (id: string) => {
      if (!confirm("Delete this LLM profile?")) return;
      setStatusMsg(null);
      try {
        await requestJson<{ ok: boolean }>(`/api/llm-profiles/${encodeURIComponent(id)}`, { method: "DELETE" });
        await loadLlmBundle();
        await loadAiStatus();
        setStatusMsg("Profile removed.");
      } catch (e) {
        setStatusMsg(String(e));
      }
    },
    [loadAiStatus, loadLlmBundle],
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
        setStatusMsg("Editor updated from assistant JSON. Save locally, then push to n8n.");
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
    <>
      {view === "connections" ? (
        <N8nConnectionsPage
          onBack={() => setView("editor")}
          onChanged={() => void onConnectionChanged()}
        />
      ) : (
    <div className="app-shell">
      <header className="topbar">
        <h1>n8n Workflow Editor</h1>
        <div className="topbar-actions">
          {aiOk === true && <span className="badge ok">AI ready</span>}
          {aiOk === false && <span className="badge warn">AI not configured</span>}
          {statusMsg && <span className="badge">{statusMsg}</span>}
          <button type="button" className="ghost" disabled={syncBusy} onClick={() => void syncAllFromN8n()}>
            Sync from n8n
          </button>
          <button type="button" className="ghost" onClick={() => void refreshWorkflows()}>
            Refresh list
          </button>
          <button type="button" className="ghost" onClick={() => setView("connections")}>
            Connections
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
          <div className="title">{editorTitle}{dirty ? " · modified" : ""}</div>
          <button
            type="button"
            className="primary"
            disabled={!selectedId || !dirty || savingWorkflow}
            onClick={() => void saveWorkflow()}
          >
            Save locally
          </button>
          <button
            type="button"
            className="ghost"
            disabled={!selectedId || syncBusy}
            onClick={() => void pushToN8n()}
          >
            Push to n8n
          </button>
          <button
            type="button"
            className="ghost"
            disabled={!selectedId || syncBusy}
            onClick={() => void pullFromN8n()}
          >
            Pull from n8n
          </button>
          <button
            type="button"
            className="ghost"
            disabled={!selectedId || syncBusy}
            onClick={() => void backupWorkflow()}
          >
            Backup
          </button>
          <button
            type="button"
            className="ghost"
            disabled={!selectedId}
            onClick={() => setBackupsOpen(true)}
          >
            Restore
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

      {backupsOpen && selectedId && (
        <WorkflowBackupsModal
          workflowId={selectedId}
          onClose={() => setBackupsOpen(false)}
          onRestored={() => {
            void refreshWorkflows();
            void loadWorkflow(selectedId);
          }}
        />
      )}

      {settingsOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setSettingsOpen(false)}>
          <div
            className="modal"
            role="dialog"
            style={{ maxHeight: "90vh", overflowY: "auto", maxWidth: "720px" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2>Settings</h2>
            <p className="muted">
              Manage LLM profiles for the assistant. n8n connections are configured on the{" "}
              <button type="button" className="link-button" onClick={() => { setSettingsOpen(false); setView("connections"); }}>
                Connections
              </button>{" "}
              page.
            </p>

            <h3>Active LLM profile</h3>
            {!prefs && <div className="muted">Loading preferences…</div>}
            {prefs && (
              <div className="field">
                <label htmlFor="selLlm">Active LLM profile</label>
                <select
                  id="selLlm"
                  value={prefs.active_llm_profile_id ?? ""}
                  onChange={(e) => {
                    const v = e.target.value || null;
                    void applyLlmPreference(v);
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
            )}

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

            <div className="modal-actions" style={{ marginTop: "1rem" }}>
              <button type="button" className="ghost" onClick={() => setSettingsOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
      )}
    </>
  );
}
