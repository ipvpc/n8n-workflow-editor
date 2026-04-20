import ReactMarkdown from "react-markdown";

type ChatTurn = { role: "user" | "assistant"; content: string };

type Props = {
  chat: ChatTurn[];
  chatInput: string;
  chatBusy: boolean;
  onChatInputChange: (value: string) => void;
  onSend: () => void;
  onApplyJson: () => void;
  onClear: () => void;
};

export function ChatPanel(props: Props) {
  const {
    chat,
    chatInput,
    chatBusy,
    onChatInputChange,
    onSend,
    onApplyJson,
    onClear,
  } = props;

  return (
    <aside className="chat-panel">
      <div className="chat-header">Assistant</div>
      <div className="chat-messages">
        {chat.length === 0 && (
          <div className="muted">Ask about nodes, expressions, or edits. The current editor JSON is sent as context.</div>
        )}
        {chat.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.role === "assistant" ? <ReactMarkdown>{m.content}</ReactMarkdown> : m.content}
          </div>
        ))}
      </div>
      <div className="chat-input-row">
        <textarea
          placeholder="Message…"
          value={chatInput}
          onChange={(e) => onChatInputChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
        />
        <button type="button" className="primary" disabled={chatBusy} onClick={onSend}>
          Send
        </button>
      </div>
      <div style={{ padding: "0 0.65rem 0.65rem", display: "flex", gap: "0.5rem" }}>
        <button type="button" className="ghost" onClick={onApplyJson}>
          Apply JSON from last reply
        </button>
        <button type="button" className="ghost" onClick={onClear}>
          Clear chat
        </button>
      </div>
    </aside>
  );
}
