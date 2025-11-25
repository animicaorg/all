/**
 * Project slice — manages the in-memory project:
 * - Files map (path → file)
 * - Open tabs, active file
 * - Dirty flags & persistence to localStorage
 * - Tree builder for sidebar
 *
 * Paths are canonicalized to forward-slash form without leading slash.
 */

import { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';

export type Lang =
  | 'python'
  | 'json'
  | 'yaml'
  | 'ts'
  | 'tsx'
  | 'md'
  | 'cddl'
  | 'txt';

export interface ProjectFile {
  path: string;          // normalized (no leading slash)
  content: string;
  language: Lang;
  readonly?: boolean;
  dirty: boolean;
  createdAt: number;     // epoch ms
  updatedAt: number;     // epoch ms
}

export type TreeKind = 'dir' | 'file';
export interface TreeNode {
  name: string;
  path: string;          // '' for root
  kind: TreeKind;
  children?: TreeNode[]; // present iff dir
}

export type ProjectSnapshot = {
  files: Record<string, ProjectFile>;
  open: string[];
  active?: string;
};

export interface ProjectSlice {
  // state
  files: Record<string, ProjectFile>;
  open: string[];     // ordered tab list (paths)
  active?: string;    // active file path
  lastSavedAt?: number;
  lastError?: string;

  // selectors / derived
  tree(): TreeNode;
  activeFile(): ProjectFile | undefined;
  openFiles(): ProjectFile[];

  // file ops
  createFile(path: string, content?: string, opts?: { readonly?: boolean; language?: Lang; activate?: boolean; }): ProjectFile;
  updateFile(path: string, content: string): void;
  touch(path: string): void;
  renameFile(oldPath: string, newPath: string): void;
  deleteFile(path: string): void;
  setLanguage(path: string, language: Lang): void;
  markSaved(path?: string): void; // mark one or all as clean

  // tabs
  openFile(path: string): void;
  closeFile(path: string): void;
  setActive(path?: string): void;

  // bulk/project
  setFiles(list: Array<{ path: string; content: string; language?: Lang; readonly?: boolean }>, opts?: { activate?: string }): void;
  importFiles(list: Array<{ path: string; content: string; language?: Lang; readonly?: boolean }>, opts?: { activateFirst?: boolean }): void;
  exportSnapshot(): ProjectSnapshot;
  loadSnapshot(s: ProjectSnapshot): void;
  resetProject(): void;

  // persistence
  saveToStorage(): void;
  loadFromStorage(): boolean;
}

const STORAGE_KEY = 'animica.studio.project.v1';

function now(): number {
  return Date.now();
}

function normPath(p: string): string {
  let s = String(p || '').replace(/\\/g, '/').replace(/\/{2,}/g, '/').trim();
  if (s.startsWith('/')) s = s.slice(1);
  // collapse './' segments
  s = s.split('/').filter((seg) => seg !== '.' && seg.length > 0).join('/');
  return s;
}

function guessLang(p: string): Lang {
  const m = /\.([A-Za-z0-9_+-]+)$/.exec(p);
  const ext = (m?.[1] || '').toLowerCase();
  switch (ext) {
    case 'py': return 'python';
    case 'json': return 'json';
    case 'yaml':
    case 'yml': return 'yaml';
    case 'ts': return 'ts';
    case 'tsx': return 'tsx';
    case 'md': return 'md';
    case 'cddl': return 'cddl';
    case 'txt':
    case 'log': return 'txt';
    default: {
      // heuristics by folder
      if (p.includes('/manifest') || p.endsWith('manifest.json')) return 'json';
      if (p.endsWith('.schema.json')) return 'json';
      return 'txt';
    }
  }
}

function sortNodes(a: TreeNode, b: TreeNode): number {
  if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1; // dirs first
  return a.name.localeCompare(b.name);
}

function buildTree(files: Record<string, ProjectFile>): TreeNode {
  const root: TreeNode = { name: '', path: '', kind: 'dir', children: [] };
  const dirs = new Map<string, TreeNode>([['', root]]);
  const ensureDir = (dirPath: string): TreeNode => {
    const p = normPath(dirPath);
    if (dirs.has(p)) return dirs.get(p)!;
    const parentPath = p.split('/').slice(0, -1).join('/');
    const parent = ensureDir(parentPath);
    const node: TreeNode = { name: p.split('/').pop() || '', path: p, kind: 'dir', children: [] };
    parent.children!.push(node);
    dirs.set(p, node);
    return node;
  };

  for (const f of Object.values(files)) {
    const parts = f.path.split('/');
    const dir = parts.slice(0, -1).join('/');
    const base = parts[parts.length - 1];
    const dirNode = ensureDir(dir);
    dirNode.children!.push({ name: base, path: f.path, kind: 'file' });
  }

  // recursively sort
  const walk = (n: TreeNode) => {
    if (n.children) {
      n.children.sort(sortNodes);
      n.children.forEach(walk);
    }
  };
  walk(root);
  return root;
}

const projectSlice: SliceCreator<ProjectSlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => ({
  files: Object.create(null),
  open: [],
  active: undefined,
  lastSavedAt: undefined,
  lastError: undefined,

  tree(): TreeNode {
    return buildTree((get() as unknown as ProjectSlice).files);
  },

  activeFile(): ProjectFile | undefined {
    const s = get() as unknown as ProjectSlice;
    return s.active ? s.files[s.active] : undefined;
  },

  openFiles(): ProjectFile[] {
    const s = get() as unknown as ProjectSlice;
    return s.open.map((p) => s.files[p]).filter(Boolean);
  },

  createFile(path: string, content = '', opts?): ProjectFile {
    const s = get() as unknown as ProjectSlice;
    const p = normPath(path);
    if (!p) throw new Error('Invalid path');
    if (s.files[p]) throw new Error(`File exists: ${p}`);
    const language = opts?.language ?? guessLang(p);
    const file: ProjectFile = {
      path: p,
      content,
      language,
      readonly: !!opts?.readonly,
      dirty: true,
      createdAt: now(),
      updatedAt: now(),
    };
    set({
      files: { ...s.files, [p]: file },
      open: opts?.activate === false ? s.open : [...new Set([...s.open, p])],
      active: opts?.activate === false ? s.active : p,
      lastError: undefined,
    } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
    return file;
  },

  updateFile(path: string, content: string): void {
    const s = get() as unknown as ProjectSlice;
    const p = normPath(path);
    const prev = s.files[p];
    if (!prev) throw new Error(`No such file: ${p}`);
    if (prev.readonly) throw new Error(`Readonly file: ${p}`);
    if (prev.content === content) return;
    const updated: ProjectFile = { ...prev, content, dirty: true, updatedAt: now() };
    set({ files: { ...s.files, [p]: updated } } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  touch(path: string): void {
    const s = get() as unknown as ProjectSlice;
    const p = normPath(path);
    const prev = s.files[p];
    if (!prev) return;
    set({ files: { ...s.files, [p]: { ...prev, updatedAt: now() } } } as Partial<StoreState>);
  },

  renameFile(oldPath: string, newPath: string): void {
    const s = get() as unknown as ProjectSlice;
    const from = normPath(oldPath);
    const to = normPath(newPath);
    if (!s.files[from]) throw new Error(`No such file: ${from}`);
    if (s.files[to]) throw new Error(`Target exists: ${to}`);
    const f = { ...s.files[from], path: to, updatedAt: now(), dirty: true };
    const files = { ...s.files };
    delete files[from];
    files[to] = f;
    const open = s.open.map((p) => (p === from ? to : p));
    const active = s.active === from ? to : s.active;
    set({ files, open, active } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  deleteFile(path: string): void {
    const s = get() as unknown as ProjectSlice;
    const p = normPath(path);
    if (!s.files[p]) return;
    const files = { ...s.files };
    delete files[p];
    const open = s.open.filter((x) => x !== p);
    const active = s.active === p ? open[open.length - 1] : s.active;
    set({ files, open, active } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  setLanguage(path: string, language: Lang): void {
    const s = get() as unknown as ProjectSlice;
    const p = normPath(path);
    const f = s.files[p];
    if (!f) throw new Error(`No such file: ${p}`);
    set({ files: { ...s.files, [p]: { ...f, language, updatedAt: now() } } } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  markSaved(path?: string): void {
    const s = get() as unknown as ProjectSlice;
    if (path) {
      const p = normPath(path);
      const f = s.files[p];
      if (!f) return;
      if (!f.dirty) return;
      set({ files: { ...s.files, [p]: { ...f, dirty: false, updatedAt: now() } }, lastSavedAt: now() } as Partial<StoreState>);
    } else {
      const files: Record<string, ProjectFile> = {};
      for (const [k, v] of Object.entries(s.files)) files[k] = { ...v, dirty: false };
      set({ files, lastSavedAt: now() } as Partial<StoreState>);
    }
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  openFile(path: string): void {
    const s = get() as unknown as ProjectSlice;
    const p = normPath(path);
    if (!s.files[p]) throw new Error(`No such file: ${p}`);
    set({ open: [...new Set([...s.open, p])], active: p } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  closeFile(path: string): void {
    const s = get() as unknown as ProjectSlice;
    const p = normPath(path);
    const open = s.open.filter((x) => x !== p);
    const active = s.active === p ? open[open.length - 1] : s.active;
    set({ open, active } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  setActive(path?: string): void {
    const s = get() as unknown as ProjectSlice;
    if (!path) {
      set({ active: undefined } as Partial<StoreState>);
      return;
    }
    const p = normPath(path);
    if (!s.files[p]) throw new Error(`No such file: ${p}`);
    set({ active: p, open: [...new Set([...s.open, p])] } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  setFiles(list, opts): void {
    const files: Record<string, ProjectFile> = {};
    const t = now();
    for (const item of list) {
      const p = normPath(item.path);
      files[p] = {
        path: p,
        content: item.content,
        language: item.language ?? guessLang(p),
        readonly: !!item.readonly,
        dirty: true,
        createdAt: t,
        updatedAt: t,
      };
    }
    set({
      files,
      open: [],
      active: opts?.activate ? normPath(opts.activate) : undefined,
      lastError: undefined,
    } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
    if (opts?.activate) (get() as unknown as ProjectSlice).openFile(opts.activate);
  },

  importFiles(list, opts): void {
    const s = get() as unknown as ProjectSlice;
    const files = { ...s.files };
    const t = now();
    for (const item of list) {
      const p = normPath(item.path);
      files[p] = {
        path: p,
        content: item.content,
        language: item.language ?? guessLang(p),
        readonly: !!item.readonly,
        dirty: true,
        createdAt: files[p]?.createdAt ?? t,
        updatedAt: t,
      };
    }
    const first = list[0]?.path ? normPath(list[0].path) : undefined;
    set({
      files,
      open: opts?.activateFirst && first ? [...new Set([...s.open, first])] : s.open,
      active: opts?.activateFirst && first ? first : s.active,
    } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  exportSnapshot(): ProjectSnapshot {
    const s = get() as unknown as ProjectSlice;
    return {
      files: s.files,
      open: s.open,
      active: s.active,
    };
  },

  loadSnapshot(snap: ProjectSnapshot): void {
    set({
      files: snap.files ?? Object.create(null),
      open: Array.isArray(snap.open) ? [...snap.open] : [],
      active: snap.active,
    } as Partial<StoreState>);
    (get() as unknown as ProjectSlice).saveToStorage();
  },

  resetProject(): void {
    set({
      files: Object.create(null),
      open: [],
      active: undefined,
      lastError: undefined,
    } as Partial<StoreState>);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch { /* ignore */ }
  },

  saveToStorage(): void {
    const s = get() as unknown as ProjectSlice;
    try {
      const snapshot: ProjectSnapshot = { files: s.files, open: s.open, active: s.active };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
      set({ lastSavedAt: now() } as Partial<StoreState>);
    } catch (err: any) {
      set({ lastError: `persist: ${String(err?.message ?? err)}` } as Partial<StoreState>);
    }
  },

  loadFromStorage(): boolean {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return false;
      const snap = JSON.parse(raw) as ProjectSnapshot;
      if (!snap || typeof snap !== 'object' || !snap.files) return false;
      (get() as unknown as ProjectSlice).loadSnapshot(snap);
      return true;
    } catch {
      return false;
    }
  },
});

registerSlice<ProjectSlice>(projectSlice);

export default undefined;
