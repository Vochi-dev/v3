import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import SchemaEditor from './SchemaEditor';
import SchemaList from './SchemaList';
import './App.css';

function App() {
  return (
    <BrowserRouter basename="/dial">
      <Routes>
        <Route path="/enterprise/:enterpriseId" element={<SchemaList />} />
        <Route path="/enterprise/:enterpriseId/schema/:schemaId" element={<SchemaEditor />} />
        {/* Redirect old or invalid URLs. 
            This assumes a default/known enterprise ID might exist, 
            or redirects to a placeholder. For now, just navigating away
            from the blank root. A better default could be a page 
            asking the user to select an enterprise.
        */}
        <Route path="/*" element={<Navigate to="/enterprise/0201" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
