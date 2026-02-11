import { useState } from "react";
import { useProjectStore, type ProjectFile } from "../../app/store";
import { getContractTemplate } from "../../project/templates";

export default function FileTree() {
  const { files, activeFile, createFile, deleteFile, setActiveFile } = useProjectStore();
  const [showNewFileDialog, setShowNewFileDialog] = useState(false);
  const [newFileName, setNewFileName] = useState("");
  const [newFileType, setNewFileType] = useState<ProjectFile["type"]>("python");

  const handleCreateFile = () => {
    if (!newFileName) return;

    const path = newFileName;
    let content = "";
    
    // Get template if it's a Python file
    if (newFileType === "python") {
      content = getContractTemplate("counter");
    } else if (newFileType === "json" && path.includes("manifest")) {
      // Create basic manifest
      content = JSON.stringify(
        {
          manifestVersion: "1.0.0",
          encoding: "animica-manifest/1",
          package: {
            name: "Contract",
            version: "0.1.0",
          },
          target: {
            vm: "python",
            vmVersion: ">=1.0.0 <2.0.0",
            abiVersion: "1.0.0",
          },
          entrypoint: "contract.py",
          code: {},
          abi: {},
          capabilities: {
            required: [],
            optional: [],
          },
          integrity: {
            codeHash: "",
            abiHash: "",
          },
        },
        null,
        2
      );
    }

    createFile(path, content, newFileType);
    setShowNewFileDialog(false);
    setNewFileName("");
  };

  const handleDelete = (path: string) => {
    if (confirm(`Delete ${path}?`)) {
      deleteFile(path);
    }
  };

  const getFileIcon = (file: ProjectFile) => {
    if (file.type === "python") return "🐍";
    if (file.type === "json") return "📋";
    return "��";
  };

  const fileList = Array.from(files.values()).sort((a, b) =>
    a.path.localeCompare(b.path)
  );

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <div
        style={{
          padding: "12px",
          borderBottom: "1px solid #ddd",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h3 style={{ margin: 0, fontSize: "14px", fontWeight: 600 }}>Files</h3>
        <button
          onClick={() => setShowNewFileDialog(true)}
          style={{
            padding: "4px 8px",
            background: "#007bff",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: "pointer",
            fontSize: "12px",
          }}
          title="New File"
        >
          + New
        </button>
      </div>

      {/* File list */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {fileList.length === 0 ? (
          <div
            style={{
              padding: "20px",
              textAlign: "center",
              color: "#888",
              fontSize: "13px",
            }}
          >
            <div style={{ fontSize: "32px", marginBottom: "8px" }}>📁</div>
            <div>No files yet</div>
            <div style={{ fontSize: "11px", marginTop: "4px" }}>
              Click "New" to create a file
            </div>
          </div>
        ) : (
          fileList.map((file) => (
            <div
              key={file.path}
              onClick={() => setActiveFile(file.path)}
              style={{
                padding: "8px 12px",
                cursor: "pointer",
                background: activeFile === file.path ? "#e3f2fd" : "transparent",
                borderLeft:
                  activeFile === file.path ? "3px solid #007bff" : "3px solid transparent",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                fontSize: "13px",
                transition: "background 0.2s",
              }}
              onMouseEnter={(e) => {
                if (activeFile !== file.path) {
                  e.currentTarget.style.background = "#f5f5f5";
                }
              }}
              onMouseLeave={(e) => {
                if (activeFile !== file.path) {
                  e.currentTarget.style.background = "transparent";
                }
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span>{getFileIcon(file)}</span>
                <span>{file.path}</span>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(file.path);
                }}
                style={{
                  padding: "2px 6px",
                  background: "transparent",
                  border: "1px solid #ddd",
                  borderRadius: "3px",
                  cursor: "pointer",
                  fontSize: "10px",
                  color: "#666",
                }}
                title="Delete file"
              >
                ✕
              </button>
            </div>
          ))
        )}
      </div>

      {/* New file dialog */}
      {showNewFileDialog && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0, 0, 0, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          onClick={() => setShowNewFileDialog(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "white",
              padding: "24px",
              borderRadius: "8px",
              minWidth: "400px",
              boxShadow: "0 4px 6px rgba(0, 0, 0, 0.1)",
            }}
          >
            <h3 style={{ margin: "0 0 16px 0" }}>Create New File</h3>

            <div style={{ marginBottom: "16px" }}>
              <label
                style={{
                  display: "block",
                  marginBottom: "4px",
                  fontSize: "13px",
                  fontWeight: 600,
                }}
              >
                File Name
              </label>
              <input
                type="text"
                value={newFileName}
                onChange={(e) => setNewFileName(e.target.value)}
                placeholder="e.g., contract.py"
                autoFocus
                onKeyPress={(e) => {
                  if (e.key === "Enter") handleCreateFile();
                }}
                style={{
                  width: "100%",
                  padding: "8px",
                  border: "1px solid #ddd",
                  borderRadius: "4px",
                  fontSize: "14px",
                }}
              />
            </div>

            <div style={{ marginBottom: "20px" }}>
              <label
                style={{
                  display: "block",
                  marginBottom: "4px",
                  fontSize: "13px",
                  fontWeight: 600,
                }}
              >
                File Type
              </label>
              <select
                value={newFileType}
                onChange={(e) => setNewFileType(e.target.value as ProjectFile["type"])}
                style={{
                  width: "100%",
                  padding: "8px",
                  border: "1px solid #ddd",
                  borderRadius: "4px",
                  fontSize: "14px",
                }}
              >
                <option value="python">Python (.py)</option>
                <option value="json">JSON (.json)</option>
                <option value="text">Text</option>
              </select>
            </div>

            <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
              <button
                onClick={() => setShowNewFileDialog(false)}
                style={{
                  padding: "8px 16px",
                  background: "#f5f5f5",
                  border: "1px solid #ddd",
                  borderRadius: "4px",
                  cursor: "pointer",
                  fontSize: "14px",
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleCreateFile}
                disabled={!newFileName}
                style={{
                  padding: "8px 16px",
                  background: newFileName ? "#007bff" : "#ccc",
                  color: "white",
                  border: "none",
                  borderRadius: "4px",
                  cursor: newFileName ? "pointer" : "not-allowed",
                  fontSize: "14px",
                  fontWeight: 600,
                }}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
