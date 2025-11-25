import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ProjectFile } from "../../types";
import { listTemplates, loadTemplate } from "../../services/templates";
import { useProjectStore } from "../../state/project";
import { bytesToHuman } from "../../utils/format";

type TemplateMeta = {
  id: string;
  title: string;
  description?: string;
  tags?: string[];
  estimatedSizeBytes?: number;
};

type LoadedTemplate = {
  meta: TemplateMeta;
  files: ProjectFile[];
  main?: string; // suggested file to open first
};

const BTN: React.CSSProperties = {
  padding: "10px 14px",
  borderRadius: 10,
  border: "1px solid var(--border,#e5e7eb)",
  background: "var(--btn-bg,#fff)",
  cursor: "pointer",
  fontWeight: 600,
  fontSize: 14,
};

const SecondaryBtn: React.CSSProperties = {
  ...BTN,
  background: "transparent",
};

const Card: React.CSSProperties = {
  border: "1px solid var(--card-border,#e5e7eb)",
  borderRadius: 12,
  background: "var(--card-bg,#fff)",
};

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: 0.3,
        background: "var(--chip-bg,#eef2ff)",
        color: "var(--chip-fg,#3730a3)",
      }}
    >
      {children}
    </span>
  );
}

function FileIcon({ path }: { path: string }) {
  const ext = path.split(".").pop()?.toLowerCase();
  const color = useMemo(() => {
    if (!ext) return "#6b7280";
    if (["ts", "tsx", "js"].includes(ext)) return "#2563eb";
    if (["py"].includes(ext)) return "#22c55e";
    if (["json"].includes(ext)) return "#f59e0b";
    if (["md"].includes(ext)) return "#a855f7";
    return "#6b7280";
  }, [ext]);
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden focusable="false" style={{ color }}>
      <path
        fill="currentColor"
        d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12V8zm1 1.5L18.5 7H15z"
      />
    </svg>
  );
}

export const ScaffoldPage: React.FC = () => {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<TemplateMeta[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [loaded, setLoaded] = useState<LoadedTemplate | null>(null);
  const [busy, setBusy] = useState(false);
  const [projectName, setProjectName] = useState<string>("my-project");
  const [withTests, setWithTests] = useState<boolean>(true);
  const [withComments, setWithComments] = useState<boolean>(true);

  // Zustand store (hook created via create()). The hook itself has .getState() in Zustand.
  const projectStore = useProjectStore as any;

  useEffect(() => {
    (async () => {
      const metas = await listTemplates();
      setTemplates(metas);
      // Preselect the first item (e.g., "counter")
      if (metas.length && !selectedId) {
        setSelectedId(metas[0].id);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    setBusy(true);
    setLoaded(null);
    (async () => {
      const tpl = await loadTemplate(selectedId);
      setLoaded(tpl);
      // suggest a project name from template title
      if (tpl?.meta?.title && !projectName) {
        setProjectName(slugify(tpl.meta.title));
      }
      setBusy(false);
    })();
  }, [selectedId]);

  const derivedStats = useMemo(() => {
    const files = loaded?.files || [];
    const count = files.length;
    const bytes = files.reduce((n, f) => n + (f.content?.length || 0), 0);
    return { count, bytes };
  }, [loaded]);

  const finalFiles = useMemo<ProjectFile[]>(() => {
    if (!loaded) return [];
    let files = [...loaded.files];

    // Optional transformations:
    if (!withComments) {
      files = files.map(stripComments);
    }
    if (!withTests) {
      files = files.filter((f) => !/(\.test\.(ts|tsx|py)|\/tests\/)/.test(f.path));
    }

    // Ensure a README exists with scaffold metadata
    const hasReadme = files.some((f) => f.path.toLowerCase().endsWith("readme.md"));
    if (!hasReadme) {
      files.push({
        path: "README.md",
        content: makeReadme(loaded.meta, projectName),
      });
    }
    // Replace placeholder project name tokens
    files = files.map((f) => ({
      ...f,
      content: f.content?.replace(/\{\{PROJECT_NAME\}\}/g, projectName) ?? f.content,
    }));
    return files;
  }, [loaded, withComments, withTests, projectName]);

  async function handleCreateInIDE() {
    if (!loaded || !finalFiles.length) return;
    setBusy(true);
    try {
      // Prefer a canonical "replaceProject" if the store provides one
      const api = projectStore.getState?.() || {};
      const main = loaded.main || pickMainFile(finalFiles) || finalFiles[0]?.path;

      if (api.replaceProject) {
        api.replaceProject(finalFiles, main);
      } else if (api.reset) {
        api.reset(finalFiles);
        if (api.openFile && main) api.openFile(main);
      } else if (api.setFiles) {
        api.setFiles(finalFiles);
        if (api.setActiveFile && main) api.setActiveFile(main);
      } else {
        // As a last resort, try common write patterns
        api.files = finalFiles;
        api.activeFile = main;
      }

      navigate("/edit");
    } finally {
      setBusy(false);
    }
  }

  function handleCopyManifest() {
    if (!loaded) return;
    const manifest = finalFiles.find((f) => /manifest\.json$/i.test(f.path));
    if (!manifest) return;
    navigator.clipboard?.writeText(manifest.content || "");
  }

  return (
    <div style={{ display: "grid", gap: 16, padding: 16 }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Scaffold a Project</h1>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            style={SecondaryBtn}
            onClick={() => navigate("/edit")}
            title="Back to Editor"
          >
            Back to Editor
          </button>
        </div>
      </header>

      {/* Template chooser */}
      <section style={{ ...Card, padding: 12, display: "grid", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>Choose a template</div>
          <div style={{ fontSize: 12, color: "#6b7280" }}>
            {templates.length} available
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {templates.map((t) => (
            <label
              key={t.id}
              style={{
                ...Card,
                padding: 12,
                cursor: "pointer",
                borderColor: selectedId === t.id ? "#c7d2fe" : "var(--card-border,#e5e7eb)",
                background: selectedId === t.id ? "var(--sel-bg,#eef2ff)" : "var(--card-bg,#fff)",
              }}
            >
              <input
                type="radio"
                name="template"
                value={t.id}
                checked={selectedId === t.id}
                onChange={() => setSelectedId(t.id)}
                style={{ display: "none" }}
              />
              <div style={{ display: "grid", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
                  <div style={{ fontWeight: 800 }}>{t.title}</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {(t.tags || []).slice(0, 3).map((tag) => (
                      <Tag key={tag}>{tag}</Tag>
                    ))}
                  </div>
                </div>
                {t.description && (
                  <div style={{ fontSize: 13, color: "#374151" }}>{t.description}</div>
                )}
                <div style={{ fontSize: 12, color: "#6b7280" }}>
                  {(t.estimatedSizeBytes && bytesToHuman(BigInt(t.estimatedSizeBytes))) || ""}
                </div>
              </div>
            </label>
          ))}
        </div>
      </section>

      {/* Options */}
      <section style={{ ...Card, padding: 12, display: "grid", gap: 12 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>Options</div>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(200px, 320px) 1fr", gap: 12, alignItems: "center" }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#667085" }}>Project name</div>
          <input
            value={projectName}
            onChange={(e) => setProjectName(slugify(e.target.value))}
            placeholder="my-project"
            style={{
              padding: "10px 12px",
              border: "1px solid var(--border,#e5e7eb)",
              borderRadius: 10,
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            }}
          />

          <div style={{ fontSize: 12, fontWeight: 700, color: "#667085" }}>Include tests</div>
          <label style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <input type="checkbox" checked={withTests} onChange={(e) => setWithTests(e.target.checked)} />
            <span style={{ fontSize: 13 }}>Add sample unit tests</span>
          </label>

          <div style={{ fontSize: 12, fontWeight: 700, color: "#667085" }}>Keep comments</div>
          <label style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <input type="checkbox" checked={withComments} onChange={(e) => setWithComments(e.target.checked)} />
            <span style={{ fontSize: 13 }}>Preserve explanatory comments</span>
          </label>
        </div>
      </section>

      {/* Preview */}
      <section style={{ ...Card, overflow: "hidden" }}>
        <div
          style={{
            padding: 12,
            borderBottom: "1px solid var(--card-border,#e5e7eb)",
            background: "var(--card-head,#f9fafb)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ fontWeight: 700, fontSize: 14 }}>Preview</div>
          <div style={{ fontSize: 12, color: "#6b7280" }}>
            {derivedStats.count} files · {bytesToHuman(BigInt(derivedStats.bytes))}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 0 }}>
          <div style={{ maxHeight: 280, overflow: "auto", borderRight: "1px solid var(--row,#eee)" }}>
            <ul style={{ margin: 0, padding: 12, listStyle: "none", display: "grid", gap: 8 }}>
              {finalFiles.map((f) => (
                <li key={f.path} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                  <FileIcon path={f.path} />
                  <code>{f.path}</code>
                </li>
              ))}
            </ul>
          </div>
          <div style={{ maxHeight: 280, overflow: "auto" }}>
            <pre
              style={{
                margin: 0,
                padding: 12,
                fontSize: 12.5,
                lineHeight: 1.45,
                tabSize: 2,
                background: "var(--code-bg,#0b1020)",
                color: "var(--code-fg,#e6edf3)",
                minHeight: 280,
              }}
            >
{loaded?.main
  ? <code>{previewFile(finalFiles, loaded.main)}</code>
  : <code>{previewFile(finalFiles, pickMainFile(finalFiles) || finalFiles[0]?.path)}</code>}
            </pre>
          </div>
        </div>
      </section>

      {/* Actions */}
      <section style={{ display: "flex", gap: 10, alignItems: "center", justifyContent: "flex-end" }}>
        <button
          style={SecondaryBtn}
          disabled={!loaded || busy}
          onClick={handleCopyManifest}
          title="Copy manifest.json to clipboard"
        >
          Copy manifest
        </button>
        <button
          style={{ ...BTN, opacity: busy ? 0.7 : 1 }}
          disabled={!loaded || !finalFiles.length || busy}
          onClick={handleCreateInIDE}
        >
          {busy ? "Creating..." : "Create in IDE"}
        </button>
      </section>
    </div>
  );
};

// ------------- helpers -------------

function slugify(s: string): string {
  return (s || "")
    .toLowerCase()
    .replace(/[^a-z0-9-_]+/g, "-")
    .replace(/--+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

function stripComments(file: ProjectFile): ProjectFile {
  const path = file.path.toLowerCase();
  const c = file.content || "";
  if (path.endsWith(".ts") || path.endsWith(".tsx") || path.endsWith(".js")) {
    // Remove // line comments and /* */ block comments
    const without = c
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|\s)\/\/.*$/gm, "");
    return { ...file, content: without.trim() + "\n" };
  }
  if (path.endsWith(".py")) {
    const without = c
      .replace(/(^|\s)#.*$/gm, "");
    return { ...file, content: without.trim() + "\n" };
  }
  return file;
}

function makeReadme(meta: TemplateMeta, projectName: string): string {
  return `# ${projectName || meta.title || "New Project"}

Scaffolded from **${meta.title}** template.

## Quickstart

- Open the project in the **Edit** tab.
- Inspect \`manifest.json\` and the contract sources.
- Use the **Simulate** panel to test calls.
- Head to **Deploy** to publish on-chain.

> Generated by Studio – safe by design (no server-side signing).

`;
}

function pickMainFile(files: ProjectFile[] = []): string | undefined {
  const candidates = [
    "src/contract.py",
    "contract.py",
    "manifest.json",
    "src/index.ts",
    "src/main.ts",
    "README.md",
  ];
  for (const c of candidates) {
    const hit = files.find((f) => normalizePath(f.path) === c);
    if (hit) return hit.path;
  }
  return files[0]?.path;
}

function normalizePath(p: string): string {
  return p.replace(/\\/g, "/");
}

function previewFile(files: ProjectFile[], path?: string): string {
  if (!files.length) return "// No files.";
  if (!path) return (files[0].content || "").slice(0, 5000);
  const f = files.find((x) => x.path === path);
  return (f?.content || files[0].content || "").slice(0, 5000);
}

export default ScaffoldPage;
