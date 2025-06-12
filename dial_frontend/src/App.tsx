import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Modal from './Modal';
import './App.css';

function App() {
  return (
    <Router basename="/dial">
      <Routes>
        <Route path="/*" element={<Modal />} />
      </Routes>
    </Router>
  );
}

export default App; 