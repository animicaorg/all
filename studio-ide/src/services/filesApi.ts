// Thin wrapper over the same-origin broker /api/ide/fs/* routes (Contract 2).
import { api } from "@/services/api";

export type TreeNode = {
  name: string;
  path: string;
  type: "file" | "dir";
  children?: TreeNode[];
};

export interface ReadResult {
  path: string;
  content: string;
  truncated: boolean;
}

export const filesApi = {
  tree: (): Promise<{ tree: TreeNode }> => api.get("/api/ide/fs/tree"),
  read: (path: string): Promise<ReadResult> =>
    api.get(`/api/ide/fs/read?path=${encodeURIComponent(path)}`),
  write: (path: string, content: string): Promise<{ ok: true }> =>
    api.put("/api/ide/fs/write", { path, content }),
};
