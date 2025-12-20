import { Link, Route, Routes } from 'react-router-dom'
import HomePage from './pages/HomePage'
import BlocksPage from './pages/BlocksPage'
import BlockDetailPage from './pages/BlockDetailPage'
import TxDetailPage from './pages/TxDetailPage'
import AddressPage from './pages/AddressPage'
import MempoolPage from './pages/MempoolPage'
import SearchBar from './components/SearchBar'

export default function App() {
  return (
    <div className="min-h-screen bg-night-950 text-slate-100">
      <header className="border-b border-night-800 bg-night-900/80">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Link to="/" className="text-xl font-semibold text-animica-400">
              Animica Explorer 2
            </Link>
            <nav className="flex gap-4 text-sm text-slate-300">
              <Link className="hover:text-animica-400" to="/blocks">
                Blocks
              </Link>
              <Link className="hover:text-animica-400" to="/mempool">
                Mempool
              </Link>
            </nav>
          </div>
          <SearchBar />
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl px-4 py-8">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/blocks" element={<BlocksPage />} />
          <Route path="/block/:hashOrHeight" element={<BlockDetailPage />} />
          <Route path="/tx/:hash" element={<TxDetailPage />} />
          <Route path="/address/:address" element={<AddressPage />} />
          <Route path="/mempool" element={<MempoolPage />} />
          <Route
            path="*"
            element={
              <div className="rounded-xl border border-night-800 bg-night-900 p-6">
                <h2 className="text-lg font-semibold">Page not found</h2>
                <p className="mt-2 text-sm text-slate-400">
                  The page you requested does not exist.
                </p>
              </div>
            }
          />
        </Routes>
      </main>
    </div>
  )
}
