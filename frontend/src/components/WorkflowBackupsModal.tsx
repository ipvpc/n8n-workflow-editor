import { useCallback, useEffect, useState } from "react";
import { requestJson } from "../lib/api";
import type { WorkflowBackupRow } from "../types";

type Props = {
  workflowId: string;
  onClose: () => void;
  onRestored: () => void;
};

export function WorkflowBackupsModal({ workflowId, onClose, onRestored }: Props) {
  const [backups, setBackups] = useState<WorkflowBackupRow[]>([]);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pushOnRestore, setPushOnRestore] = useState(false);

  const loadBackups = useCallback(async () => {
    const rows = await requestJson<WorkflowBackupRow[]>(
      `/api/workflows/local/${encodeURIComponent(workflowId)}/backups`,
    );
    setBackups(rows);
  }, [workflowId]);

  useEffect(() => {
    void loadBackups().catch((e) => setStatusMsg(String(e)));
  }, [loadBackups]);

  const restore = async (backupId: string) => {
    if (!window.confirm("Restore this backup into the local copy?")) return;
    setBusy(true);
    setStatusMsg(null);
    try {
      await requestJson(
        `/api/workflows/local/${encodeURIComponent(workflowId)}/backups/${encodeURIComponent(backupId)}/restore`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ push: pushOnRestore }),
        },
      );
      onRestored();
      onClose();
    } catch (e) {
      setStatusMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal" role="dialog" onClick={(e) => e.stopPropagation()}>
        <h2>Workflow backups</h2>
        <p className="muted small">Workflow ID: {workflowId}</p>
        {statusMsg && <p className="muted">{statusMsg}</p>}
        <label className="inline-check">
          <input type="checkbox" checked={pushOnRestore} onChange={(e) => setPushOnRestore(e.target.checked)} />
          Push to n8n after restore
        </label>
        <div className="table-like" style={{ marginTop: "0.75rem" }}>
          {backups.map((b) => (
            <div key={b.id} className="table-row">
              <div>
                <div className="strong">{b.label ?? b.name ?? "Backup"}</div>
                <div className="muted small">
                  {b.source} · {b.created_at ?? "unknown time"}
                </div>
              </div>
              <button type="button" className="ghost" disabled={busy} onClick={() => void restore(b.id)}>
                Restore
              </button>
            </div>
          ))}
          {backups.length === 0 && <div className="muted" style={{ padding: "0.55rem" }}>No backups yet.</div>}
        </div>
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
