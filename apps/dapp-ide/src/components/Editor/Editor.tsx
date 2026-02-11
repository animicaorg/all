import { useEffect, useRef } from "react";
import MonacoEditor from "@monaco-editor/react";

interface EditorProps {
  filePath: string | null;
}

export default function Editor({ filePath }: EditorProps) {
  const editorRef = useRef<any>(null);

  const handleEditorDidMount = (editor: any, monaco: any) => {
    editorRef.current = editor;
    
    // Configure Monaco for Python
    monaco.languages.typescript.javascriptDefaults.setDiagnosticsOptions({
      noSemanticValidation: true,
      noSyntaxValidation: false,
    });
  };

  if (!filePath) {
    return (
      <div style={{ 
        display: "flex", 
        alignItems: "center", 
        justifyContent: "center",
        height: "100%",
        color: "#888"
      }}>
        Select a file to edit
      </div>
    );
  }

  const language = filePath.endsWith(".py") ? "python" : 
                   filePath.endsWith(".json") ? "json" : "text";

  return (
    <MonacoEditor
      height="100%"
      language={language}
      theme="vs-dark"
      defaultValue="# Write your contract code here\n"
      options={{
        minimap: { enabled: false },
        fontSize: 14,
        wordWrap: "on",
        lineNumbers: "on",
        renderWhitespace: "selection",
        scrollBeyondLastLine: false,
        automaticLayout: true,
      }}
      onMount={handleEditorDidMount}
    />
  );
}
