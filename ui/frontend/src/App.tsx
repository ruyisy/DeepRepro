import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Toaster } from './components/common/Toaster';
import Layout from './components/layout/Layout';
import PaperToCodePage from './pages/PaperToCodePage';
import SettingsPage from './pages/SettingsPage';

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<PaperToCodePage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </Layout>
      <Toaster />
    </BrowserRouter>
  );
}

export default App;
