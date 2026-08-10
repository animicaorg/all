import { Link, Route, Routes } from 'react-router-dom'
import HomePage from './pages/HomePage'
import BlocksPage from './pages/BlocksPage'
import BlockDetailPage from './pages/BlockDetailPage'
import TxDetailPage from './pages/TxDetailPage'
import AddressPage from './pages/AddressPage'
import MempoolPage from './pages/MempoolPage'
import DiagnosticsPage from './pages/DiagnosticsPage'
import ContractsPage from './pages/ContractsPage'
import TokensPage from './pages/TokensPage'
import TokenDetailPage from './pages/TokenDetailPage'
import { RichListPage } from './pages/RichListPage'
import AICFPage from './pages/AICFPage'
import MiningPage from './pages/MiningPage'
import DAPage from './pages/DAPage'
import QuantumPage from './pages/QuantumPage'
import RpcInspectorPage from './pages/RpcInspectorPage'
import DebugBundlePage from './pages/DebugBundlePage'
import SearchBar from './components/SearchBar'
import ThemeToggle from './components/ThemeToggle'
import NetworkHealthBanner from './components/NetworkHealthBanner'

export default function App() {
  return (
    <div className="min-h-screen bg-day-50 text-gray-900 transition-colors dark:bg-night-950 dark:text-slate-100">
      <header className="sticky top-0 z-10 border-b border-day-200 bg-white/80 backdrop-blur-sm dark:border-night-800 dark:bg-night-900/80">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Link to="/" className="flex items-center gap-2 text-xl font-semibold text-animica-600 dark:text-animica-400">
              <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
              <span className="hidden sm:inline">Animica Explorer</span>
              <span className="sm:hidden">Explorer</span>
            </Link>
            <div className="flex items-center gap-2 sm:gap-3">
              <span dangerouslySetInnerHTML={{ __html: '<anm-price-ticker></anm-price-ticker>' }} />
              <nav className="flex flex-wrap gap-2 text-sm text-gray-600 dark:text-slate-300 sm:gap-3">
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/blocks">
                  Blocks
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/richlist">
                  Rich List
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/mempool">
                  Mempool
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/contracts">
                  Contracts
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/tokens">
                  Tokens
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/aicf">
                  AICF
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/mining">
                  Mining
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/da">
                  DA
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/quantum">
                  Quantum
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/rpc-inspector">
                  RPC
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/diagnostics">
                  Diagnostics
                </Link>
                <Link className="hover:text-animica-600 dark:hover:text-animica-400" to="/debug">
                  Debug
                </Link>
              </nav>
              <ThemeToggle />
            </div>
          </div>
          <SearchBar />
        </div>
      </header>

      <NetworkHealthBanner />

      <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/blocks" element={<BlocksPage />} />
          <Route path="/block/:hashOrHeight" element={<BlockDetailPage />} />
          <Route path="/tx/:hash" element={<TxDetailPage />} />
          <Route path="/address/:address" element={<AddressPage />} />
          <Route path="/richlist" element={<RichListPage />} />
          <Route path="/mempool" element={<MempoolPage />} />
          <Route path="/contracts" element={<ContractsPage />} />
          <Route path="/tokens" element={<TokensPage />} />
          <Route path="/token/:address" element={<TokenDetailPage />} />
          <Route path="/aicf" element={<AICFPage />} />
          <Route path="/mining" element={<MiningPage />} />
          <Route path="/da" element={<DAPage />} />
          <Route path="/quantum" element={<QuantumPage />} />
          <Route path="/rpc-inspector" element={<RpcInspectorPage />} />
          <Route path="/diagnostics" element={<DiagnosticsPage />} />
          <Route path="/debug" element={<DebugBundlePage />} />
          <Route
            path="*"
            element={
              <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
                <h2 className="text-lg font-semibold">Page not found</h2>
                <p className="mt-2 text-sm text-gray-600 dark:text-slate-400">
                  The page you requested does not exist.
                </p>
              </div>
            }
          />
        </Routes>
      </main>

      <footer className="mt-12 border-t border-day-200 bg-white dark:border-night-800 dark:bg-night-900">
        <div className="mx-auto max-w-7xl px-4 py-6 text-center text-sm text-gray-600 dark:text-slate-400 sm:px-6 lg:px-8">
          <p>Animica Explorer — powered by the Animica blockchain</p>
          <p className="mt-2 flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
            <a href="https://nonkyc.io/market/ANM_USDT" target="_blank" rel="noreferrer" className="hover:text-animica-600 dark:hover:text-animica-400">Buy / Trade ANM</a>
            <a href="https://nonkyc.io/pool/ANM_USDT" target="_blank" rel="noreferrer" className="hover:text-animica-600 dark:hover:text-animica-400">Liquidity Pool</a>
            <a href="https://animica.org" target="_blank" rel="noreferrer" className="hover:text-animica-600 dark:hover:text-animica-400">animica.org</a>
          </p>
        </div>
      </footer>
    </div>
  )
}
