import Editor from "@monaco-editor/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

type WorkflowRow = {
  id: string;
  name?: string;
  active?: boolean;
};

type ChatTurn = { role: "user" | "assistant"; content: string };

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

export default function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [baseUrlInput, setBaseUrlInput] = useState("");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [settingsMeta, setSettingsMeta] = useState<{
    base_url: string | null;
    api_key_masked: string | null;
    has_api_key: boolean;
    source: string;
  } | null>(null);

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

  const loadSettings = useCallback(async () => {
    const r = await fetch("/api/settings/n8n");
    if (!r.ok) return;
    const j = (await r.json()) as {
      base_url: string | null;
      api_key_masked: string | null;
      has_api_key: boolean;
      source: string;
    };
    setSettingsMeta(j);
    if (j.base_url) setBaseUrlInput(j.base_url);
  }, []);

  const loadAiStatus = useCallback(async () => {
    const r = await fetch("/api/ai/status");
    if (!r.ok) {
      setAiOk(false);
      return;
    }
    const j = (await r.json()) as { enabled?: boolean };
    setAiOk(!!j.enabled);
  }, []);

  const refreshWorkflows = useCallback(async () => {
    setStatusMsg(null);
    const r = await fetch("/api/workflows?limit=200");
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      setStatusMsg(typeof err?.detail === "object" ? JSON.stringify(err.detail) : r.statusText);
      setWorkflows([]);
      return;
    }
    const j = await r.json();
    setWorkflows(workflowsFromResponse(j));
  }, []);

  useEffect(() => {
    void loadSettings();
    void loadAiStatus();
    void refreshWorkflows();
  }, [loadSettings, loadAiStatus, refreshWorkflows]);

  const loadWorkflow = useCallback(
    async (id: string) => {
      setStatusMsg(null);
      const r = await fetch(`/api/workflows/${encodeURIComponent(id)}`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        setStatusMsg(typeof err?.detail === "object" ? JSON.stringify(err.detail) : r.statusText);
        return;
      }
      const data = await r.json();
      setEditorText(JSON.stringify(data, null, 2));
      setSelectedId(id);
      setDirty(false);
    },
    [],
  );

  const saveWorkflow = useCallback(async () => {
    if (!selectedId) return;
    let body: object;
    try {
      body = JSON.parse(editorText) as object;
    } catch (e) {
      setStatusMsg(`Invalid JSON: ${e}`);
      return;
    }
    setStatusMsg(null);
    const r = await fetch(`/api/workflows/${encodeURIComponent(selectedId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      setStatusMsg(typeof err?.detail === "object" ? JSON.stringify(err.detail) : r.statusText);
      return;
    }
    const data = await r.json();
    setEditorText(JSON.stringify(data, null, 2));
    setDirty(false);
    setStatusMsg("Saved.");
  }, [editorText, selectedId]);

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
    const r = await fetch("/api/n8n/test", { method: "POST" });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      setStatusMsg(typeof err?.detail === "object" ? JSON.stringify(err.detail) : r.statusText);
      return;
    }
    setStatusMsg("n8n connection OK.");
  }, []);

  const saveSettings = useCallback(async () => {
    setStatusMsg(null);
    const payload: { base_url: string; api_key?: string } = { base_url: baseUrlInput.trim() };
    const k = apiKeyInput.trim();
    if (k) payload.api_key = k;
    const r = await fetch("/api/settings/n8n", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      setStatusMsg(typeof err?.detail === "string" ? err.detail : JSON.stringify(err));
      return;
    }
    setApiKeyInput("");
    setSettingsOpen(false);
    await loadSettings();
    await refreshWorkflows();
    setStatusMsg("Settings saved.");
  }, [apiKeyInput, baseUrlInput, loadSettings, refreshWorkflows]);

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

  return (
    <div className="app-shell">
      <header className="topbar">
        <h1>n8n Workflow Editor</h1>
        <div className="topbar-actions">
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

      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="search">
            <input
              placeholder="Search workflows…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          {settingsMeta && (
            <div className="muted">
              {settingsMeta.base_url ?? "—"} · {settingsMeta.source}
            </div>
          )}
        </div>
        <div className="workflow-list">
          {filtered.map((w) => (
            <div
              key={w.id}
              className={`workflow-item ${selectedId === w.id ? "active" : ""}`}
              onClick={() => void loadWorkflow(w.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  void loadWorkflow(w.id);
                }
              }}
              role="button"
              tabIndex={0}
            >
              <div className="name">{w.name ?? "(unnamed)"}</div>
              <div className="meta">
                {w.id}
                {w.active === false ? " · inactive" : ""}
              </div>
            </div>
          ))}
          {filtered.length === 0 && <div className="muted" style={{ padding: "0.5rem" }}>No workflows.</div>}
        </div>
      </aside>

      <section className="editor-panel">
        <div className="editor-toolbar">
          <div className="title">{editorTitle}</div>
          <button type="button" className="primary" disabled={!selectedId || !dirty} onClick={() => void saveWorkflow()}>
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

      <aside className="chat-panel">
        <div className="chat-header">Assistant</div>
        <div className="chat-messages">
          {chat.length === 0 && (
            <div className="muted">Ask about nodes, expressions, or edits. The current editor JSON is sent as context.</div>
          )}
          {chat.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              {m.role === "assistant" ? (
                <ReactMarkdown>{m.content}</ReactMarkdown>
              ) : (
                m.content
              )}
            </div>
          ))}
        </div>
        <div className="chat-input-row">
          <textarea
            placeholder="Message…"
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendChat();
              }
            }}
          />
          <button type="button" className="primary" disabled={chatBusy} onClick={() => void sendChat()}>
            Send
          </button>
        </div>
        <div style={{ padding: "0 0.65rem 0.65rem", display: "flex", gap: "0.5rem" }}>
          <button type="button" className="ghost" onClick={applyJsonFromLastAssistant}>
            Apply JSON from last reply
          </button>
          <button type="button" className="ghost" onClick={() => setChat([])}>
            Clear chat
          </button>
        </div>
      </aside>

      {settingsOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setSettingsOpen(false)}>
          <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
            <h2>n8n connection</h2>
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
          </div>
        </div>
      )}
    </div>
  );
}
