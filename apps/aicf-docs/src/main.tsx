import React from 'react';
import ReactDOM from 'react-dom/client';
import { docs } from './content/docs';

function App() {
  return (
    <main style={{ maxWidth: 920, margin: '0 auto', padding: 24, fontFamily: 'ui-sans-serif, system-ui, sans-serif' }}>
      <h1>AICF Docs</h1>
      <p>ANM-native decentralized AI compute cloud documentation.</p>
      {docs.map((doc) => (
        <article key={doc.id} style={{ border: '1px solid #ddd', borderRadius: 10, padding: 14, marginBottom: 12 }}>
          <h2>{doc.title}</h2>
          <ul>
            {doc.content.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </article>
      ))}
    </main>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
