import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AdminPage } from "./pages/AdminPage";
import { CreatePairPage } from "./pages/CreatePairPage";
import { DocsPage } from "./pages/DocsPage";
import { FaqPage } from "./pages/FaqPage";
import { HomePage } from "./pages/HomePage";
import { LaunchPage } from "./pages/LaunchPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PoolDetailPage } from "./pages/PoolDetailPage";
import { PoolsPage } from "./pages/PoolsPage";
import { PortfolioPage } from "./pages/PortfolioPage";
import { SwapPage } from "./pages/SwapPage";
import { TokenDetailPage } from "./pages/TokenDetailPage";
import { TokensPage } from "./pages/TokensPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/launch" element={<LaunchPage />} />
        <Route path="/tokens" element={<TokensPage />} />
        <Route path="/tokens/:tokenId" element={<TokenDetailPage />} />
        <Route path="/dex" element={<Navigate to="/dex/swap" replace />} />
        <Route path="/dex/swap" element={<SwapPage />} />
        <Route path="/dex/pools" element={<PoolsPage />} />
        <Route path="/dex/pools/:pairId" element={<PoolDetailPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/create-pair" element={<CreatePairPage />} />
        <Route path="/docs" element={<DocsPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/faq" element={<FaqPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
