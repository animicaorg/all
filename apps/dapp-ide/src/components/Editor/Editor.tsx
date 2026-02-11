import { useEffect, useRef } from "react";
import MonacoEditor from "@monaco-editor/react";
import { useProjectStore } from "../../app/store";

interface EditorProps {
  filePath?: string | null;
}

export default function Editor({ filePath }: EditorProps) {
  const editorRef = useRef<any>(null);
  const { files, activeFile, updateFile } = useProjectStore();
  
  const currentPath = filePath ?? activeFile;
  const currentFile = currentPath ? files.get(currentPath) : null;

  const handleEditorDidMount = (editor: any, monaco: any) => {
    editorRef.current = editor;

    // Configure Monaco
    monaco.editor.defineTheme("animica-dark", {
      base: "vs-dark",
      inherit: true,
      rules: [],
      colors: {
        "editor.background": "#1e1e1e",
      },
    });
    monaco.editor.setTheme("animica-dark");
  };

  const handleChange = (value: string | undefined) => {
    if (currentPath && value !== undefined) {
      updateFile(currentPath, value);
    }
  };

  if (!currentFile) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "#888",
          background: "#1e1e1e",
          flexDirection: "column",
          gap: "16px",
        }}
      >
        <div style={{ fontSize: "48px" }}>📝</div>
        <div>Select a file to edit</div>
        <div style={{ fontSize: "12px", color: "#666" }}>
          or create a new file from the file tree
        </div>
      </div>
    );
  }

  const getLanguage = (file: typeof currentFile) => {
    if (file.type === "python") return "python";
    if (file.type === "json") return "json";
    if (file.path.endsWith(".md")) return "markdown";
    return "text";
  };

  return (
    <MonacoEditor
      height="100%"
      language={getLanguage(currentFile)}
      value={currentFile.content}
      theme="animica-dark"
      options={{
        minimap: { enabled: true },
        fontSize: 14,
        wordWrap: "on",
        lineNumbers: "on",
        renderWhitespace: "selection",
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 4,
        insertSpaces: true,
        formatOnPaste: true,
        formatOnType: true,
      }}
      onMount={handleEditorDidMount}
      onChange={handleChange}
    />
  );
}
