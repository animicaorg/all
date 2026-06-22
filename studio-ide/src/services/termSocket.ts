// Terminal WebSocket client. Connects to the same-origin broker upgrade route
// /api/ide/term, which bridges to a `docker exec ... /bin/bash` PTY inside the
// per-user IDE container. Cookies (anm_sid) flow on the WS handshake, so the
// broker can gate the session the same way the HTTP routes do.
//
// Wire protocol (Inc 4-5 contract):
//   client -> server : text frames are stdin; a JSON control frame
//                       {type:'resize',cols,rows} resizes the pty.
//   server -> client : text frames are terminal output.

export interface TermSocketCallbacks {
  onData?: (data: string) => void;
  onOpen?: () => void;
  onClose?: (info: { code: number; reason: string }) => void;
  onError?: () => void;
}

export interface TermSocket {
  send: (data: string) => void;
  resize: (cols: number, rows: number) => void;
  close: () => void;
  readonly ready: boolean;
}

function termUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/ide/term`;
}

export function openTermSocket(cb: TermSocketCallbacks): TermSocket {
  const ws = new WebSocket(termUrl());
  let open = false;

  ws.onopen = () => {
    open = true;
    cb.onOpen?.();
  };
  ws.onmessage = (ev) => {
    // The broker only sends text frames, but be defensive about Blob payloads.
    if (typeof ev.data === "string") {
      cb.onData?.(ev.data);
    } else if (ev.data instanceof Blob) {
      void ev.data.text().then((t) => cb.onData?.(t));
    }
  };
  ws.onerror = () => {
    cb.onError?.();
  };
  ws.onclose = (ev) => {
    open = false;
    cb.onClose?.({ code: ev.code, reason: ev.reason });
  };

  return {
    send(data: string) {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    },
    resize(cols: number, rows: number) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    },
    close() {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    },
    get ready() {
      return open;
    },
  };
}
