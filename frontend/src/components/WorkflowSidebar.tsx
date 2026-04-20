type WorkflowRow = {
  id: string;
  name?: string;
  active?: boolean;
};

type Props = {
  filter: string;
  onFilterChange: (value: string) => void;
  sidebarHint: string | null;
  workflows: WorkflowRow[];
  selectedId: string | null;
  loadingWorkflowId: string | null;
  onSelectWorkflow: (id: string) => void;
};

export function WorkflowSidebar(props: Props) {
  const {
    filter,
    onFilterChange,
    sidebarHint,
    workflows,
    selectedId,
    loadingWorkflowId,
    onSelectWorkflow,
  } = props;

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="search">
          <input
            placeholder="Search workflows…"
            value={filter}
            onChange={(e) => onFilterChange(e.target.value)}
          />
        </div>
        {sidebarHint && <div className="muted">{sidebarHint}</div>}
      </div>
      <div className="workflow-list">
        {workflows.map((w) => (
          <div
            key={w.id}
            className={`workflow-item ${selectedId === w.id ? "active" : ""}`}
            onClick={() => onSelectWorkflow(w.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelectWorkflow(w.id);
              }
            }}
            role="button"
            tabIndex={0}
            aria-busy={loadingWorkflowId === w.id}
          >
            <div className="name">{w.name ?? "(unnamed)"}</div>
            <div className="meta">
              {w.id}
              {w.active === false ? " · inactive" : ""}
              {loadingWorkflowId === w.id ? " · loading..." : ""}
            </div>
          </div>
        ))}
        {workflows.length === 0 && <div className="muted" style={{ padding: "0.5rem" }}>No workflows.</div>}
      </div>
    </aside>
  );
}
