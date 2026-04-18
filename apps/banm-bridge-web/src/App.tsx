import { Navigate, Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { BridgePage } from "./pages/BridgePage";
import { FaqPage } from "./pages/FaqPage";
import { HomePage } from "./pages/HomePage";
import { RiskPage } from "./pages/RiskPage";
import { SolvencyPage } from "./pages/SolvencyPage";
import { StatusPage } from "./pages/StatusPage";
import { TermsPage } from "./pages/TermsPage";

export default function App() {
  return (
    <div className="app-shell">
      <Header />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/bridge" element={<BridgePage />} />
          <Route path="/status/:orderId" element={<StatusPage />} />
          <Route path="/faq" element={<FaqPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/terms" element={<TermsPage />} />
          <Route path="/proof-of-reserves" element={<SolvencyPage />} />
          <Route path="*" element={<Navigate to="/bridge" replace />} />
        </Routes>
      </main>
    </div>
  );
}

