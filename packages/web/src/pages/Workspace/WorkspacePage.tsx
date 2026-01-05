import { useState } from 'react'
import { useParams } from 'react-router-dom'
import Editor from '@monaco-editor/react'
import { useWorkspaceStore } from '@/stores/workspaceStore'

export default function WorkspacePage() {
  const { projectId } = useParams()
  const {
    projects,
    activeProjectId,
    session,
    selectedFile,
    setActiveProject,
    setSelectedFile,
    updateFile,
  } = useWorkspaceStore()
  
  const [terminalOutput, setTerminalOutput] = useState('Welcome to Animica Workspace\n$ ')
  
  const activeProject = projects.find((p) => p.id === activeProjectId)
  
  const handleEditorChange = (value: string | undefined) => {
    if (selectedFile && value !== undefined) {
      updateFile(selectedFile.path, value)
    }
  }
  
  const handleRunCode = () => {
    setTerminalOutput((prev) => prev + '\nRunning code...\nCompilation successful!\n$ ')
  }
  
  return (
    <div className="flex h-full">
      {/* File Tree */}
      <div className="w-64 bg-slate-900 border-r border-slate-700 flex flex-col">
        <div className="p-4 border-b border-slate-700">
          <h2 className="text-lg font-semibold text-white">
            {activeProject?.name || 'Workspace'}
          </h2>
        </div>
        
        <div className="flex-1 overflow-y-auto p-2">
          {session?.files.map((file) => (
            <button
              key={file.path}
              onClick={() => setSelectedFile(file)}
              className={`
                w-full text-left p-2 rounded-lg mb-1 text-sm transition-colors
                ${file.path === selectedFile?.path
                  ? 'bg-slate-700 text-white'
                  : 'text-slate-300 hover:bg-slate-800'
                }
              `}
            >
              <div className="flex items-center">
                <span className="mr-2">📄</span>
                <span className="truncate">{file.path}</span>
                {file.modified && <span className="ml-auto text-primary-400">•</span>}
              </div>
            </button>
          ))}
          
          {(!session?.files || session.files.length === 0) && (
            <div className="text-center text-slate-500 mt-8">
              <div className="text-4xl mb-2">📁</div>
              <p className="text-sm">No files yet</p>
            </div>
          )}
        </div>
        
        <div className="p-4 border-t border-slate-700 space-y-2">
          <button className="w-full py-2 px-4 bg-slate-800 hover:bg-slate-700 text-white text-sm rounded-lg">
            + New File
          </button>
          <button className="w-full py-2 px-4 bg-slate-800 hover:bg-slate-700 text-white text-sm rounded-lg">
            🔗 Connect GitHub
          </button>
        </div>
      </div>
      
      {/* Main Editor Area */}
      <div className="flex-1 flex flex-col">
        {/* Editor Tabs */}
        {selectedFile && (
          <div className="flex items-center px-4 py-2 bg-slate-900 border-b border-slate-700">
            <span className="text-slate-300 text-sm">{selectedFile.path}</span>
            {selectedFile.modified && (
              <span className="ml-2 w-2 h-2 rounded-full bg-primary-400"></span>
            )}
          </div>
        )}
        
        {/* Editor */}
        <div className="flex-1">
          {selectedFile ? (
            <Editor
              height="100%"
              language={selectedFile.language}
              value={selectedFile.content}
              onChange={handleEditorChange}
              theme="vs-dark"
              options={{
                minimap: { enabled: false },
                fontSize: 14,
                lineNumbers: 'on',
                renderWhitespace: 'selection',
                scrollBeyondLastLine: false,
                automaticLayout: true,
              }}
            />
          ) : (
            <div className="h-full flex items-center justify-center text-slate-500">
              <div className="text-center">
                <div className="text-6xl mb-4">📝</div>
                <h2 className="text-2xl font-semibold mb-2">No file selected</h2>
                <p>Select a file from the tree to start coding</p>
              </div>
            </div>
          )}
        </div>
        
        {/* Terminal */}
        <div className="h-48 bg-slate-950 border-t border-slate-700 flex flex-col">
          <div className="flex items-center justify-between px-4 py-2 bg-slate-900 border-b border-slate-700">
            <span className="text-sm text-slate-300 font-medium">Terminal</span>
            <div className="flex space-x-2">
              <button
                onClick={handleRunCode}
                className="px-3 py-1 bg-primary-600 hover:bg-primary-700 text-white text-xs rounded"
              >
                ▶ Run
              </button>
              <button className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-white text-xs rounded">
                Clear
              </button>
            </div>
          </div>
          
          <div className="flex-1 overflow-y-auto p-4 font-mono text-sm text-slate-300">
            <pre>{terminalOutput}</pre>
          </div>
        </div>
      </div>
      
      {/* Right Panel - AI Assistant */}
      <div className="w-80 bg-slate-900 border-l border-slate-700 flex flex-col">
        <div className="p-4 border-b border-slate-700">
          <h2 className="text-lg font-semibold text-white">AI Assistant</h2>
        </div>
        
        <div className="flex-1 overflow-y-auto p-4">
          <div className="mb-4">
            <button className="w-full py-2 px-4 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg">
              🤖 Generate Code
            </button>
          </div>
          
          <div className="space-y-3">
            <SuggestionCard
              title="Fix syntax errors"
              description="Automatically detect and fix syntax issues"
            />
            <SuggestionCard
              title="Add documentation"
              description="Generate docstrings for functions"
            />
            <SuggestionCard
              title="Optimize performance"
              description="Suggest performance improvements"
            />
          </div>
        </div>
        
        <div className="p-4 border-t border-slate-700">
          <input
            type="text"
            placeholder="Ask AI anything..."
            className="w-full px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
        </div>
      </div>
    </div>
  )
}

function SuggestionCard({ title, description }: { title: string; description: string }) {
  return (
    <div className="p-3 bg-slate-800 rounded-lg border border-slate-700 cursor-pointer hover:bg-slate-750 transition-colors">
      <h4 className="text-white text-sm font-medium mb-1">{title}</h4>
      <p className="text-slate-400 text-xs">{description}</p>
    </div>
  )
}
