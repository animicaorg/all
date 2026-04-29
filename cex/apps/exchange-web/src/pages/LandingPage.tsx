import { Link } from 'react-router-dom';
import { TrendingUp, Shield, Zap, Globe, ArrowRight, CheckCircle2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { apiClient } from '../lib/api-client';
import type { PlatformStats } from '../types';

export default function LandingPage() {
  const [stats, setStats] = useState<PlatformStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const data = await apiClient.getStats();
        setStats(data);
      } catch (error) {
        console.error('Failed to load platform stats:', error);
        // Keep stats null to show fallback message
      } finally {
        setStatsLoading(false);
      }
    };

    fetchStats();
  }, []);

  const formatVolume = (volume: number) => {
    if (volume >= 1_000_000_000) {
      return `$${(volume / 1_000_000_000).toFixed(1)}B`;
    } else if (volume >= 1_000_000) {
      return `$${(volume / 1_000_000).toFixed(1)}M`;
    } else if (volume >= 1_000) {
      return `$${(volume / 1_000).toFixed(1)}K`;
    } else if (volume > 0) {
      return `$${volume.toFixed(0)}`;
    } else {
      return 'Coming Soon';
    }
  };

  const formatTraders = (traders: number) => {
    if (traders >= 1_000_000) {
      return `${(traders / 1_000_000).toFixed(1)}M+`;
    } else if (traders >= 1_000) {
      return `${(traders / 1_000).toFixed(1)}K+`;
    } else if (traders > 0) {
      return `${traders}+`;
    } else {
      return 'Growing';
    }
  };

  const formatUptime = (uptime: number | null | undefined) => {
    if (typeof uptime !== 'number' || !Number.isFinite(uptime)) {
      return 'N/A';
    }
    return `${uptime.toFixed(1)}%`;
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-blue-950">
      {/* Animated background elements */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-blue-500/5 rounded-full blur-3xl animate-pulse"></div>
        <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/5 rounded-full blur-3xl animate-pulse delay-1000"></div>
      </div>

      {/* Navigation */}
      <nav className="relative z-10 border-b border-slate-800/50 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center shadow-lg">
                <span className="text-xl font-bold text-white">A</span>
              </div>
              <span className="text-xl font-bold bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent">
                Animica Exchange
              </span>
            </div>
            <div className="flex items-center gap-4">
              <Link
                to="/login"
                className="px-4 py-2 text-slate-300 hover:text-white transition-colors"
              >
                Sign In
              </Link>
              <Link
                to="/register"
                className="px-6 py-2 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white font-medium rounded-lg shadow-lg shadow-blue-500/30 transition-all transform hover:scale-105"
              >
                Get Started
              </Link>
            </div>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="relative z-10 pt-20 pb-32 px-4 sm:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center space-y-8">
            <h1 className="text-5xl sm:text-6xl md:text-7xl font-bold">
              <span className="block text-white mb-2">Trade Animica</span>
              <span className="block bg-gradient-to-r from-blue-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
                The Future of Digital Assets
              </span>
            </h1>
            
            <p className="text-xl text-slate-400 max-w-3xl mx-auto">
              Experience lightning-fast trading with institutional-grade security. 
              Buy, sell, and trade Animica tokens with confidence on our cutting-edge platform.
            </p>

            <div className="flex flex-col sm:flex-row items-center justify-center gap-4 pt-8">
              <Link
                to="/register"
                className="w-full sm:w-auto px-8 py-4 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white text-lg font-medium rounded-xl shadow-2xl shadow-blue-500/40 transition-all transform hover:scale-105 flex items-center justify-center gap-2"
              >
                Start Trading Now
                <ArrowRight size={20} />
              </Link>
              <Link
                to="/legal"
                className="w-full sm:w-auto px-8 py-4 bg-slate-800/50 hover:bg-slate-800 text-white text-lg font-medium rounded-xl border border-slate-700 transition-all flex items-center justify-center gap-2"
              >
                Learn More
              </Link>
            </div>

            {/* Trust Indicators */}
            <div className="flex flex-wrap items-center justify-center gap-8 pt-12 text-sm text-slate-400">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="text-green-500" size={20} />
                <span>Secure Trading</span>
              </div>
              <div className="flex items-center gap-2">
                <CheckCircle2 className="text-green-500" size={20} />
                <span>24/7 Support</span>
              </div>
              <div className="flex items-center gap-2">
                <CheckCircle2 className="text-green-500" size={20} />
                <span>Fast Execution</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="relative z-10 py-20 px-4 sm:px-6 lg:px-8 bg-slate-900/30 backdrop-blur-sm border-y border-slate-800/50">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-4xl font-bold text-white mb-4">
              Why Choose Animica Exchange?
            </h2>
            <p className="text-xl text-slate-400">
              Built for traders who demand the best
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-8">
            {/* Feature 1 */}
            <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-6 hover:bg-slate-800/50 transition-all group">
              <div className="w-12 h-12 rounded-xl bg-blue-500/10 flex items-center justify-center mb-4 group-hover:bg-blue-500/20 transition-all">
                <TrendingUp className="text-blue-400" size={24} />
              </div>
              <h3 className="text-xl font-bold text-white mb-2">Advanced Trading</h3>
              <p className="text-slate-400">
                Professional trading tools with real-time charts and order books
              </p>
            </div>

            {/* Feature 2 */}
            <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-6 hover:bg-slate-800/50 transition-all group">
              <div className="w-12 h-12 rounded-xl bg-purple-500/10 flex items-center justify-center mb-4 group-hover:bg-purple-500/20 transition-all">
                <Shield className="text-purple-400" size={24} />
              </div>
              <h3 className="text-xl font-bold text-white mb-2">Bank-Grade Security</h3>
              <p className="text-slate-400">
                Multi-layer security with cold storage and 2FA protection
              </p>
            </div>

            {/* Feature 3 */}
            <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-6 hover:bg-slate-800/50 transition-all group">
              <div className="w-12 h-12 rounded-xl bg-green-500/10 flex items-center justify-center mb-4 group-hover:bg-green-500/20 transition-all">
                <Zap className="text-green-400" size={24} />
              </div>
              <h3 className="text-xl font-bold text-white mb-2">Lightning Fast</h3>
              <p className="text-slate-400">
                Ultra-low latency matching engine for instant order execution
              </p>
            </div>

            {/* Feature 4 */}
            <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-6 hover:bg-slate-800/50 transition-all group">
              <div className="w-12 h-12 rounded-xl bg-pink-500/10 flex items-center justify-center mb-4 group-hover:bg-pink-500/20 transition-all">
                <Globe className="text-pink-400" size={24} />
              </div>
              <h3 className="text-xl font-bold text-white mb-2">Global Access</h3>
              <p className="text-slate-400">
                Trade from anywhere with our web and mobile platforms
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Stats Section */}
      <section className="relative z-10 py-20 px-4 sm:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="grid md:grid-cols-3 gap-8 text-center">
            <div className="space-y-2">
              <div className="text-5xl font-bold bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent">
                {statsLoading ? '...' : stats ? formatVolume(stats.volume24h) : 'N/A'}
              </div>
              <div className="text-slate-400">24h Trading Volume</div>
            </div>
            <div className="space-y-2">
              <div className="text-5xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
                {statsLoading ? '...' : stats ? formatTraders(stats.activeTraders) : 'N/A'}
              </div>
              <div className="text-slate-400">Active Traders</div>
            </div>
            <div className="space-y-2">
              <div className="text-5xl font-bold bg-gradient-to-r from-pink-400 to-blue-400 bg-clip-text text-transparent">
                {statsLoading ? '...' : stats ? formatUptime(stats.uptimePercentage) : 'N/A'}
              </div>
              <div className="text-slate-400">Uptime</div>
            </div>
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="relative z-10 py-20 px-4 sm:px-6 lg:px-8 bg-gradient-to-r from-blue-900/20 to-purple-900/20 border-t border-slate-800/50">
        <div className="max-w-4xl mx-auto text-center space-y-8">
          <h2 className="text-4xl md:text-5xl font-bold text-white">
            Ready to Start Trading?
          </h2>
          <p className="text-xl text-slate-400">
            Join thousands of traders already using Animica Exchange
          </p>
          <Link
            to="/register"
            className="inline-flex items-center gap-2 px-8 py-4 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white text-lg font-medium rounded-xl shadow-2xl shadow-blue-500/40 transition-all transform hover:scale-105"
          >
            Create Your Account
            <ArrowRight size={20} />
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 border-t border-slate-800/50 py-12 px-4 sm:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="grid md:grid-cols-4 gap-8 mb-8">
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center">
                  <span className="text-sm font-bold text-white">A</span>
                </div>
                <span className="font-bold text-white">Animica</span>
              </div>
              <p className="text-sm text-slate-400">
                The next generation cryptocurrency exchange
              </p>
            </div>
            
            <div>
              <h4 className="font-semibold text-white mb-4">Products</h4>
              <ul className="space-y-2 text-sm text-slate-400">
                <li><Link to="/markets" className="hover:text-white transition-colors">Markets</Link></li>
                <li><Link to="/trade/ANIMICA-USDT" className="hover:text-white transition-colors">Trading</Link></li>
              </ul>
            </div>
            
            <div>
              <h4 className="font-semibold text-white mb-4">Company</h4>
              <ul className="space-y-2 text-sm text-slate-400">
                <li><Link to="/legal" className="hover:text-white transition-colors">About Us</Link></li>
                <li><Link to="/legal" className="hover:text-white transition-colors">Legal</Link></li>
              </ul>
            </div>
            
            <div>
              <h4 className="font-semibold text-white mb-4">Support</h4>
              <ul className="space-y-2 text-sm text-slate-400">
                <li><Link to="/legal" className="hover:text-white transition-colors">Help Center</Link></li>
                <li><Link to="/legal" className="hover:text-white transition-colors">Contact Us</Link></li>
              </ul>
            </div>
          </div>
          
          <div className="pt-8 border-t border-slate-800/50 text-center text-sm text-slate-500">
            <p>&copy; 2026 Animica Exchange. All rights reserved.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
