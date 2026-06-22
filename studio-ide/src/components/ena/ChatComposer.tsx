import { useRef, useState } from "react";

interface Props {
  sending: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export function ChatComposer({ sending, onSend, onStop }: Props) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const t = text.trim();
    if (!t || sending) return;
    onSend(t);
    setText("");
    if (taRef.current) taRef.current.style.height = "auto";
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter to send on desktop; Shift+Enter for newline. (Mobile keyboards
    // generally insert a newline, which is fine.)
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const autosize = (el: HTMLTextAreaElement) => {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  };

  return (
    <div className="safe-b flex-none border-t border-border bg-surface p-2.5">
      <div className="flex items-end gap-2">
        <textarea
          ref={taRef}
          className="input max-h-40 flex-1 resize-none py-2.5 leading-snug"
          rows={1}
          placeholder="Ask ENA to read or change your code…"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            autosize(e.target);
          }}
          onKeyDown={onKeyDown}
        />
        {sending ? (
          <button className="btn-ghost flex-none" onClick={onStop} aria-label="Stop">
            <span className="h-3 w-3 rounded-[3px] bg-danger" />
          </button>
        ) : (
          <button
            className="btn-primary flex-none px-3"
            onClick={submit}
            disabled={!text.trim()}
            aria-label="Send"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}
