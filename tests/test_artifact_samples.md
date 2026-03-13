# Test Artifact Samples for New Renderers

This file contains sample artifact payloads to test the newly implemented renderers.

## 1. React/JSX Renderer Test

### Sample 1: Simple React Counter Component

```artifact
{
  "title": "React Counter Component",
  "entrypoint": "App.jsx",
  "files": [
    {
      "path": "App.jsx",
      "language": "jsx",
      "content": "function Counter() {\n  const [count, setCount] = React.useState(0);\n\n  return (\n    <div style={{ padding: '40px', textAlign: 'center' }}>\n      <h1 style={{ color: '#5681ff' }}>React Counter</h1>\n      <p style={{ fontSize: '48px', margin: '20px 0' }}>{count}</p>\n      <button \n        onClick={() => setCount(count + 1)}\n        style={{\n          padding: '12px 24px',\n          fontSize: '18px',\n          background: 'linear-gradient(135deg, #64b0ff, #7c5cff)',\n          color: 'white',\n          border: 'none',\n          borderRadius: '8px',\n          cursor: 'pointer',\n          margin: '0 8px'\n        }}\n      >\n        Increment\n      </button>\n      <button \n        onClick={() => setCount(count - 1)}\n        style={{\n          padding: '12px 24px',\n          fontSize: '18px',\n          background: '#4a5568',\n          color: 'white',\n          border: 'none',\n          borderRadius: '8px',\n          cursor: 'pointer',\n          margin: '0 8px'\n        }}\n      >\n        Decrement\n      </button>\n      <button \n        onClick={() => setCount(0)}\n        style={{\n          padding: '12px 24px',\n          fontSize: '18px',\n          background: '#2d3748',\n          color: 'white',\n          border: 'none',\n          borderRadius: '8px',\n          cursor: 'pointer',\n          margin: '0 8px'\n        }}\n      >\n        Reset\n      </button>\n    </div>\n  );\n}\n\nconst root = ReactDOM.createRoot(document.getElementById('root'));\nroot.render(<Counter />);"
    }
  ]
}
```

### Sample 2: React Todo List

```artifact
{
  "title": "React Todo List",
  "entrypoint": "TodoApp.jsx",
  "files": [
    {
      "path": "TodoApp.jsx",
      "language": "jsx",
      "content": "function TodoApp() {\n  const [todos, setTodos] = React.useState([]);\n  const [input, setInput] = React.useState('');\n\n  const addTodo = () => {\n    if (input.trim()) {\n      setTodos([...todos, { id: Date.now(), text: input, done: false }]);\n      setInput('');\n    }\n  };\n\n  const toggleTodo = (id) => {\n    setTodos(todos.map(todo => \n      todo.id === id ? { ...todo, done: !todo.done } : todo\n    ));\n  };\n\n  const deleteTodo = (id) => {\n    setTodos(todos.filter(todo => todo.id !== id));\n  };\n\n  return (\n    <div style={{ padding: '40px', maxWidth: '600px', margin: '0 auto' }}>\n      <h1 style={{ color: '#5681ff', marginBottom: '24px' }}>Todo List</h1>\n      <div style={{ display: 'flex', gap: '8px', marginBottom: '24px' }}>\n        <input\n          type=\"text\"\n          value={input}\n          onChange={(e) => setInput(e.target.value)}\n          onKeyPress={(e) => e.key === 'Enter' && addTodo()}\n          placeholder=\"Add a new task...\"\n          style={{\n            flex: 1,\n            padding: '12px',\n            fontSize: '16px',\n            border: '1px solid rgba(255,255,255,0.2)',\n            borderRadius: '8px',\n            background: 'rgba(255,255,255,0.05)',\n            color: '#f4f4f4'\n          }}\n        />\n        <button\n          onClick={addTodo}\n          style={{\n            padding: '12px 24px',\n            background: 'linear-gradient(135deg, #64b0ff, #7c5cff)',\n            color: 'white',\n            border: 'none',\n            borderRadius: '8px',\n            cursor: 'pointer',\n            fontSize: '16px'\n          }}\n        >\n          Add\n        </button>\n      </div>\n      <div>\n        {todos.map(todo => (\n          <div\n            key={todo.id}\n            style={{\n              display: 'flex',\n              alignItems: 'center',\n              gap: '12px',\n              padding: '12px',\n              marginBottom: '8px',\n              background: 'rgba(255,255,255,0.05)',\n              borderRadius: '8px',\n              border: '1px solid rgba(255,255,255,0.1)'\n            }}\n          >\n            <input\n              type=\"checkbox\"\n              checked={todo.done}\n              onChange={() => toggleTodo(todo.id)}\n              style={{ width: '20px', height: '20px', cursor: 'pointer' }}\n            />\n            <span\n              style={{\n                flex: 1,\n                textDecoration: todo.done ? 'line-through' : 'none',\n                opacity: todo.done ? 0.6 : 1,\n                fontSize: '16px'\n              }}\n            >\n              {todo.text}\n            </span>\n            <button\n              onClick={() => deleteTodo(todo.id)}\n              style={{\n                padding: '6px 12px',\n                background: '#ff6b6b',\n                color: 'white',\n                border: 'none',\n                borderRadius: '4px',\n                cursor: 'pointer',\n                fontSize: '14px'\n              }}\n            >\n              Delete\n            </button>\n          </div>\n        ))}\n      </div>\n      {todos.length > 0 && (\n        <p style={{ marginTop: '16px', color: '#888', fontSize: '14px' }}>\n          {todos.filter(t => !t.done).length} of {todos.length} tasks remaining\n        </p>\n      )}\n    </div>\n  );\n}\n\nconst root = ReactDOM.createRoot(document.getElementById('root'));\nroot.render(<TodoApp />);"
    }
  ]
}
```

## 2. SVG Renderer Test

### Sample 1: Simple SVG Icon

```artifact
{
  "title": "Heart Icon SVG",
  "entrypoint": "heart.svg",
  "files": [
    {
      "path": "heart.svg",
      "language": "svg",
      "content": "<svg width=\"200\" height=\"200\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">\n  <path d=\"M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z\" fill=\"#ff6b6b\"/>\n</svg>"
    }
  ]
}
```

### Sample 2: Animated SVG Chart

```artifact
{
  "title": "SVG Bar Chart",
  "entrypoint": "chart.svg",
  "files": [
    {
      "path": "chart.svg",
      "language": "svg",
      "content": "<svg width=\"400\" height=\"300\" xmlns=\"http://www.w3.org/2000/svg\">\n  <defs>\n    <linearGradient id=\"grad1\" x1=\"0%\" y1=\"0%\" x2=\"0%\" y2=\"100%\">\n      <stop offset=\"0%\" style=\"stop-color:#7c5cff;stop-opacity:1\" />\n      <stop offset=\"100%\" style=\"stop-color:#64b0ff;stop-opacity:1\" />\n    </linearGradient>\n  </defs>\n  \n  <!-- Chart Title -->\n  <text x=\"200\" y=\"30\" text-anchor=\"middle\" fill=\"#f4f4f4\" font-size=\"20\" font-family=\"Arial\">Monthly Sales</text>\n  \n  <!-- Bars -->\n  <rect x=\"50\" y=\"200\" width=\"60\" height=\"50\" fill=\"url(#grad1)\" opacity=\"0.8\">\n    <animate attributeName=\"height\" from=\"0\" to=\"50\" dur=\"0.5s\" fill=\"freeze\"/>\n    <animate attributeName=\"y\" from=\"250\" to=\"200\" dur=\"0.5s\" fill=\"freeze\"/>\n  </rect>\n  <rect x=\"130\" y=\"150\" width=\"60\" height=\"100\" fill=\"url(#grad1)\" opacity=\"0.8\">\n    <animate attributeName=\"height\" from=\"0\" to=\"100\" dur=\"0.5s\" begin=\"0.1s\" fill=\"freeze\"/>\n    <animate attributeName=\"y\" from=\"250\" to=\"150\" dur=\"0.5s\" begin=\"0.1s\" fill=\"freeze\"/>\n  </rect>\n  <rect x=\"210\" y=\"120\" width=\"60\" height=\"130\" fill=\"url(#grad1)\" opacity=\"0.8\">\n    <animate attributeName=\"height\" from=\"0\" to=\"130\" dur=\"0.5s\" begin=\"0.2s\" fill=\"freeze\"/>\n    <animate attributeName=\"y\" from=\"250\" to=\"120\" dur=\"0.5s\" begin=\"0.2s\" fill=\"freeze\"/>\n  </rect>\n  <rect x=\"290\" y=\"180\" width=\"60\" height=\"70\" fill=\"url(#grad1)\" opacity=\"0.8\">\n    <animate attributeName=\"height\" from=\"0\" to=\"70\" dur=\"0.5s\" begin=\"0.3s\" fill=\"freeze\"/>\n    <animate attributeName=\"y\" from=\"250\" to=\"180\" dur=\"0.5s\" begin=\"0.3s\" fill=\"freeze\"/>\n  </rect>\n  \n  <!-- Labels -->\n  <text x=\"80\" y=\"270\" text-anchor=\"middle\" fill=\"#f4f4f4\" font-size=\"14\">Jan</text>\n  <text x=\"160\" y=\"270\" text-anchor=\"middle\" fill=\"#f4f4f4\" font-size=\"14\">Feb</text>\n  <text x=\"240\" y=\"270\" text-anchor=\"middle\" fill=\"#f4f4f4\" font-size=\"14\">Mar</text>\n  <text x=\"320\" y=\"270\" text-anchor=\"middle\" fill=\"#f4f4f4\" font-size=\"14\">Apr</text>\n  \n  <!-- Axis -->\n  <line x1=\"40\" y1=\"260\" x2=\"360\" y2=\"260\" stroke=\"rgba(255,255,255,0.2)\" stroke-width=\"2\"/>\n  <line x1=\"40\" y1=\"60\" x2=\"40\" y2=\"260\" stroke=\"rgba(255,255,255,0.2)\" stroke-width=\"2\"/>\n</svg>"
    }
  ]
}
```

## 3. Mermaid Diagram Renderer Test

### Sample 1: Flowchart

```artifact
{
  "title": "User Authentication Flow",
  "entrypoint": "auth-flow.mmd",
  "files": [
    {
      "path": "auth-flow.mmd",
      "language": "mermaid",
      "content": "graph TD\n    A[User Visits App] --> B{Logged In?}\n    B -->|Yes| C[Show Dashboard]\n    B -->|No| D[Show Login Page]\n    D --> E{Valid Credentials?}\n    E -->|Yes| F[Create Session]\n    E -->|No| G[Show Error]\n    F --> C\n    G --> D\n    C --> H{User Action}\n    H -->|Logout| I[Destroy Session]\n    H -->|Continue| C\n    I --> D"
    }
  ]
}
```

### Sample 2: Sequence Diagram

```artifact
{
  "title": "API Request Sequence",
  "entrypoint": "sequence.mmd",
  "files": [
    {
      "path": "sequence.mmd",
      "language": "mermaid",
      "content": "sequenceDiagram\n    participant User\n    participant Browser\n    participant Server\n    participant Database\n    participant AI\n\n    User->>Browser: Enter prompt\n    Browser->>Server: POST /chat/messages\n    Server->>Database: Validate session\n    Database-->>Server: Session valid\n    Server->>AI: Send prompt\n    AI-->>Server: Return response\n    Server->>Database: Save message\n    Database-->>Server: Success\n    Server-->>Browser: Return response\n    Browser-->>User: Display message"
    }
  ]
}
```

### Sample 3: Class Diagram

```artifact
{
  "title": "Artifact System Classes",
  "entrypoint": "classes.mmd",
  "files": [
    {
      "path": "classes.mmd",
      "language": "mermaid",
      "content": "classDiagram\n    class ArtifactRouter {\n        +route(structured, prompt)\n        -detect_preference()\n        -contains_code_block()\n    }\n\n    class ArtifactStore {\n        -db: Firestore\n        +persist_decision()\n        +get_artifact()\n        +list_artifacts()\n        -create_artifact()\n        -append_version()\n    }\n\n    class ArtifactPanel {\n        -currentArtifact\n        -currentVersion\n        -versionCache\n        +openArtifact()\n        +renderVersion()\n        +attachPreview()\n    }\n\n    class ArtifactDecision {\n        +render_mode: str\n        +chat_payload: str\n        +artifact_payload: dict\n        +contains_code: bool\n        +error_message: str\n    }\n\n    ArtifactRouter --> ArtifactDecision : creates\n    ArtifactStore --> Firestore : uses\n    ArtifactPanel --> ArtifactStore : fetches from"
    }
  ]
}
```

### Sample 4: State Diagram

```artifact
{
  "title": "Artifact Lifecycle States",
  "entrypoint": "states.mmd",
  "files": [
    {
      "path": "states.mmd",
      "language": "mermaid",
      "content": "stateDiagram-v2\n    [*] --> Parsing\n    Parsing --> Validated: Valid JSON\n    Parsing --> Error: Invalid JSON\n    Validated --> Persisting\n    Persisting --> Persisted: Success\n    Persisting --> Draft: Offline/Failed\n    Persisted --> Rendering\n    Draft --> Rendering\n    Rendering --> Displayed\n    Displayed --> Editing: User edits\n    Editing --> Versioning\n    Versioning --> Persisted\n    Displayed --> [*]: Panel closed\n    Error --> [*]"
    }
  ]
}
```

## 4. Error Message Testing

### Test Invalid JSON

Ask the AI: "Create an artifact with invalid JSON to test error handling"

Expected error payload:
```artifact
{
  "title": "Broken Artifact",
  "files": [
    {"path": "test.txt", "content": "test"}  // Missing closing bracket
```

Expected user-friendly error: "The artifact contains invalid JSON. Please check for missing commas, quotes, or brackets."

### Test Duplicate Paths

```artifact
{
  "title": "Duplicate Paths Test",
  "files": [
    {"path": "test.txt", "language": "text", "content": "First file"},
    {"path": "test.txt", "language": "text", "content": "Second file"}
  ]
}
```

Expected error: "Two or more files have the same path. Each file must have a unique path."

### Test File Too Large

```artifact
{
  "title": "Large File Test",
  "files": [
    {
      "path": "large.txt",
      "language": "text",
      "content": "[imagine 300KB of text here - exceeds 200KB limit]"
    }
  ]
}
```

Expected error: "One or more files are too large. Each file must be less than 200 KB."

### Test Too Many Files

```artifact
{
  "title": "Too Many Files",
  "files": [
    ... 41+ files ...
  ]
}
```

Expected error: "The artifact has too many files. The maximum is 40 files per artifact."

## Testing Instructions

1. **React Renderer:**
   - Ask AI: "Create a React counter component"
   - Verify: Component renders, buttons work, state updates

2. **SVG Renderer:**
   - Ask AI: "Create an SVG heart icon"
   - Verify: SVG displays centered, scales properly

3. **Mermaid Renderer:**
   - Ask AI: "Create a flowchart showing user login process"
   - Verify: Diagram renders with dark theme, is readable

4. **Error Messages:**
   - Send malformed artifact JSON
   - Verify: User-friendly error appears in chat
   - Verify: No artifact pill/panel opens

## Success Criteria

- [ ] React components render and execute
- [ ] SVG graphics display properly
- [ ] Mermaid diagrams render with dark theme
- [ ] Error messages are user-friendly
- [ ] No console errors
- [ ] Panel resizes correctly for all types
- [ ] Copy/download works for all renderers
