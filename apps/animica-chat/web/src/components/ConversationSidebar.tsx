import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../lib/api';

interface Item {
  id: string;
  title: string;
  assistantMode: string;
  updatedAt: string;
}

interface Props {
  /** Mobile-drawer open state. On desktop the sidebar is always visible. */
  open?: boolean;
  /** Called when the user picks a conversation or the backdrop, so the
   *  parent can close the drawer on mobile. */
  onNavigate?: () => void;
}

export function ConversationSidebar({ open = false, onNavigate }: Props) {
  const { conversationId } = useParams();
  const [items, setItems] = useState<Item[]>([]);
  const [query, setQuery] = useState('');
  const nav = useNavigate();

  async function load() {
    const r = await api.get<{ conversations: Item[] }>('/api/conversations');
    setItems(r.conversations);
  }

  useEffect(() => {
    load();
  }, [conversationId]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((i) => i.title.toLowerCase().includes(q));
  }, [query, items]);

  async function rename(id: string) {
    const t = prompt('Rename conversation');
    if (t == null) return;
    await api.patch(`/api/conversations/${id}`, { title: t });
    load();
  }
  async function remove(id: string) {
    if (!confirm('Delete this conversation?')) return;
    await api.delete(`/api/conversations/${id}`);
    if (conversationId === id) nav('/chat');
    load();
  }

  return (
    <>
      {/* Mobile backdrop. Tapping closes the drawer; pointer-events:none
          on desktop where the sidebar is part of the layout. */}
      <div
        onClick={onNavigate}
        className={`fixed inset-0 z-30 bg-black/60 backdrop-blur-sm transition-opacity md:hidden ${
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        aria-hidden={!open}
      />

      <aside
        className={[
          // Mobile: slide-in fixed drawer.
          'fixed inset-y-0 left-0 z-40 w-72 max-w-[85vw] transform border-r border-white/5 bg-ink-950/95 backdrop-blur transition-transform duration-200 md:translate-x-0',
          // Desktop: static sidebar in the flex row.
          'md:static md:z-auto md:w-72 md:translate-x-0 md:bg-ink-950/60 md:backdrop-blur-none md:transition-none',
          open ? 'translate-x-0' : '-translate-x-full',
        ].join(' ')}
      >
        <div className="flex h-full flex-col">
          <div className="border-b border-white/5 p-3">
            <Link
              to="/chat"
              onClick={onNavigate}
              className="block rounded-lg border border-white/8 bg-white/[0.025] px-3 py-2.5 text-center text-sm text-ink-100 hover:bg-white/[0.05]"
            >
              + New chat
            </Link>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search"
              className="mt-2 w-full rounded-md border border-white/8 bg-ink-900/40 px-3 py-2 text-sm placeholder:text-ink-400 focus:border-accent-500 focus:outline-none"
            />
          </div>
          <div className="chat-scroll flex-1 overflow-y-auto p-2">
            {visible.map((c) => (
              <Link
                key={c.id}
                to={`/chat/${c.id}`}
                onClick={onNavigate}
                className={`group block rounded-lg px-3 py-2.5 text-sm ${
                  c.id === conversationId
                    ? 'bg-accent-700/20 text-ink-50'
                    : 'text-ink-300 hover:bg-white/[0.03] hover:text-ink-100'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate">{c.title}</span>
                  {/* Always-visible kebab on touch devices, hover-only on
                      pointer:fine. The CSS in styles/index.css handles
                      `pointer:coarse` to show by default. */}
                  <span className="conv-actions flex gap-2 opacity-0 group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={(e) => { e.preventDefault(); rename(c.id); }}
                      className="rounded p-1 text-ink-400 hover:text-ink-100"
                      aria-label="Rename"
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      onClick={(e) => { e.preventDefault(); remove(c.id); }}
                      className="rounded p-1 text-ink-400 hover:text-rose-300"
                      aria-label="Delete"
                    >
                      ✕
                    </button>
                  </span>
                </div>
              </Link>
            ))}
            {visible.length === 0 && (
              <p className="px-3 py-4 text-xs text-ink-500">No conversations yet.</p>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}
