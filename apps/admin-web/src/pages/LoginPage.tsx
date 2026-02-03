/**
 * Login Page
 * Admin authentication with email, password, and TOTP
 */

import { useState } from 'react';
import { useAuth } from '../contexts/AuthContext';

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [totpToken, setTotpToken] = useState('');
  const [bootstrapSecret, setBootstrapSecret] = useState('');
  const [showBootstrap, setShowBootstrap] = useState(false);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const { login } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      await login({
        email,
        password,
        totpToken: totpToken || undefined,
        bootstrapSecret: showBootstrap ? bootstrapSecret || undefined : undefined,
      });
    } catch (err: any) {
      setError(err.response?.data?.message || 'Login failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8">
        <div>
          <h1 className="text-center text-3xl font-extrabold text-gray-900">
            Animica Admin Console
          </h1>
          <p className="mt-2 text-center text-sm text-gray-600">
            Secure Operations & Administration
          </p>
        </div>

        <form className="mt-8 space-y-6 bg-white p-8 rounded-lg shadow" onSubmit={handleSubmit}>
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
              {error}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-gray-700">
                Email Address
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                placeholder="admin@example.com"
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-gray-700">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                placeholder="••••••••"
              />
            </div>

            <div>
              <label htmlFor="totp" className="block text-sm font-medium text-gray-700">
                2FA Code (Optional)
              </label>
              <input
                id="totp"
                type="text"
                value={totpToken}
                onChange={(e) => setTotpToken(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                placeholder="123456"
                maxLength={6}
              />
            </div>
            <div>
              <button
                type="button"
                onClick={() => setShowBootstrap((prev) => !prev)}
                className="text-sm text-blue-600 hover:text-blue-700"
              >
                {showBootstrap ? 'Hide first-time setup' : 'First-time setup'}
              </button>
            </div>
            {showBootstrap && (
              <div>
                <label htmlFor="bootstrapSecret" className="block text-sm font-medium text-gray-700">
                  Bootstrap Secret
                </label>
                <input
                  id="bootstrapSecret"
                  type="password"
                  value={bootstrapSecret}
                  onChange={(e) => setBootstrapSecret(e.target.value)}
                  className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  placeholder="Enter bootstrap secret"
                />
                <p className="mt-1 text-xs text-gray-500">
                  Required only to initialize the first admin account.
                </p>
              </div>
            )}
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <div className="text-center text-xs text-gray-500">
          Protected by enterprise-grade security
        </div>
      </div>
    </div>
  );
}
