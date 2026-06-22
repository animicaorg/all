import { create } from "zustand";
import { streamChat, approve, type DiffFile, type StreamHandle } from "@/services/enaApi";
import { useFilesStore } from "@/state/files";
import { useScmStore } from "@/state/scm";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  error?: boolean;
}

export interface ToolEvent {
  name: string;
  status: "start" | "done";
  summary?: string;
}

export interface Proposal {
  id: string;
  files: DiffFile[];
  status: "pending" | "accepted" | "rejected";
}

interface ChatState {
  messages: ChatMessage[];
  sending: boolean;
  proposals: Proposal[];
  tools: ToolEvent[];
  statusPhase: string | null;

  send: (message: string) => void;
  stop: () => void;
  appendToken: (delta: string) => void;
  addProposal: (id: string, files: DiffFile[]) => void;
  resolveProposal: (id: string, accept: boolean) => Promise<void>;
  reset: () => void;
}

let activeStream: StreamHandle | null = null;

const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  sending: false,
  proposals: [],
  tools: [],
  statusPhase: null,

  send: (message: string) => {
    const text = message.trim();
    if (!text || get().sending) return;

    const userMsg: ChatMessage = { id: uid(), role: "user", content: text };
    const asstId = uid();
    const asstMsg: ChatMessage = { id: asstId, role: "assistant", content: "", streaming: true };

    // History = prior turns (exclude the just-added pair).
    const history = get().messages.map((m) => ({ role: m.role, content: m.content }));

    set((st) => ({
      messages: [...st.messages, userMsg, asstMsg],
      sending: true,
      tools: [],
      statusPhase: null,
    }));

    const finishAssistant = (patch: Partial<ChatMessage>) =>
      set((st) => ({
        messages: st.messages.map((m) => (m.id === asstId ? { ...m, streaming: false, ...patch } : m)),
        sending: false,
        statusPhase: null,
      }));

    activeStream = streamChat(text, history, {
      onToken: (delta) => get().appendToken(delta),
      onTool: (t) =>
        set((st) => {
          // collapse a done over its matching start
          if (t.status === "done") {
            const idx = [...st.tools].reverse().findIndex((x) => x.name === t.name && x.status === "start");
            if (idx !== -1) {
              const real = st.tools.length - 1 - idx;
              const next = [...st.tools];
              next[real] = { ...t };
              return { tools: next };
            }
          }
          return { tools: [...st.tools, t] };
        }),
      onDiff: (d) => get().addProposal(d.id, d.files),
      onStatus: (s) => set({ statusPhase: s.message || s.phase || null }),
      onDone: (d) => {
        // If the agent produced no streamed text but returned a summary, show it.
        const cur = get().messages.find((m) => m.id === asstId);
        finishAssistant(d?.summary && !cur?.content ? { content: d.summary } : {});
      },
      onError: (msg) =>
        set((st) => {
          const target = st.messages.find((m) => m.id === asstId);
          const hadContent = !!target?.content;
          return {
            messages: st.messages.map((m) =>
              m.id === asstId
                ? {
                    ...m,
                    streaming: false,
                    error: true,
                    content: hadContent ? m.content + "\n\n" + msg : msg,
                  }
                : m,
            ),
            sending: false,
            statusPhase: null,
          };
        }),
    });
  },

  stop: () => {
    activeStream?.abort();
    activeStream = null;
    set((st) => ({
      sending: false,
      statusPhase: null,
      messages: st.messages.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    }));
  },

  appendToken: (delta: string) =>
    set((st) => {
      const idx = st.messages.findIndex((m) => m.role === "assistant" && m.streaming);
      if (idx === -1) return st;
      const next = [...st.messages];
      next[idx] = { ...next[idx], content: next[idx].content + delta };
      return { messages: next };
    }),

  addProposal: (id, files) =>
    set((st) => {
      if (st.proposals.some((p) => p.id === id)) return st;
      return { proposals: [...st.proposals, { id, files, status: "pending" }] };
    }),

  resolveProposal: async (id, accept) => {
    const prop = get().proposals.find((p) => p.id === id);
    if (!prop || prop.status !== "pending") return;
    // Optimistically mark resolved.
    set((st) => ({
      proposals: st.proposals.map((p) =>
        p.id === id ? { ...p, status: accept ? "accepted" : "rejected" } : p,
      ),
    }));
    try {
      await approve(id, accept);
    } catch (e) {
      // Revert on failure.
      set((st) => ({
        proposals: st.proposals.map((p) => (p.id === id ? { ...p, status: "pending" } : p)),
      }));
      throw e;
    }

    if (accept) {
      // The sidecar applied the writes; refresh tree, open buffers, and SCM.
      const files = useFilesStore.getState();
      await files.loadTree();
      for (const f of prop.files) {
        const entry = files.byPath[f.path];
        if (entry && entry.content !== undefined && !entry.dirty) {
          // Reflect the applied new_text in the open buffer.
          useFilesStore.setState((st) => ({
            byPath: {
              ...st.byPath,
              [f.path]: { ...st.byPath[f.path], content: f.new_text, dirty: false },
            },
          }));
        }
      }
      void useScmStore.getState().refresh();
    }
  },

  reset: () => {
    activeStream?.abort();
    activeStream = null;
    set({ messages: [], sending: false, proposals: [], tools: [], statusPhase: null });
  },
}));
