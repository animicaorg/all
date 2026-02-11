import { useState, useEffect } from "react";

interface FileNode {
  name: string;
  path: string;
  type: "file" | "folder";
  children?: FileNode[];
}

interface FileTreeProps {
  onFileSelect: (path: string) => void;
}

export default function FileTree({ onFileSelect }: FileTreeProps) {
  const [tree, setTree] = useState<FileNode[]>([
    {
      name: "src",
      path: "src",
      type: "folder",
      children: [
        { name: "main.py", path: "src/main.py", type: "file" },
      ],
    },
    { name: "manifest.json", path: "manifest.json", type: "file" },
  ]);

  const [expanded, setExpanded] = useState<Set<string>>(new Set(["src"]));

  const toggleFolder = (path: string) => {
    const newExpanded = new Set(expanded);
    if (newExpanded.has(path)) {
      newExpanded.delete(path);
    } else {
      newExpanded.add(path);
    }
    setExpanded(newExpanded);
  };

  const renderNode = (node: FileNode, depth: number = 0) => {
    const isExpanded = expanded.has(node.path);
    
    return (
      <div key={node.path}>
        <div
          style={{
            paddingLeft: `${depth * 20}px`,
            cursor: "pointer",
            padding: "4px 8px",
            userSelect: "none",
          }}
          onClick={() => {
            if (node.type === "folder") {
              toggleFolder(node.path);
            } else {
              onFileSelect(node.path);
            }
          }}
        >
          {node.type === "folder" ? (isExpanded ? "📂" : "📁") : "📄"} {node.name}
        </div>
        {node.type === "folder" && isExpanded && node.children && (
          <div>
            {node.children.map((child) => renderNode(child, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div style={{ padding: "1rem" }}>
      <h3>Project Files</h3>
      <div style={{ marginTop: "0.5rem" }}>
        {tree.map((node) => renderNode(node))}
      </div>
    </div>
  );
}
