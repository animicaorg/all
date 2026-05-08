import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import axios from 'axios';
import { CheckCircle2, Mail, RefreshCw, ShieldCheck, XCircle } from 'lucide-react';
import { Seo } from '../components/Seo';
import { getApiBaseUrl } from '../lib/endpoints';

const API_URL = getApiBaseUrl();

type Status = 'idle' | 'verifying' | 'verified' | 'sent' | 'error';

export default function VerifyEmailPage() {
  const location = useLocation();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const token = params.get('token') || '';
  const statusParam = params.get('status');
  const [email, setEmail] = useState(params.get('email') || '');
  const [status, setStatus] = useState<Status>(statusParam === 'verified' ? 'verified' : token ? 'verifying' : params.get('sent') ? 'sent' : 'idle');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!token || statusParam === 'verified') return;

    let active = true;
    setStatus('verifying');
    axios
      .post(`${API_URL}/auth/verify-email`, { token }, { withCredentials: true })
      .then(() => {
        if (!active) return;
        setStatus('verified');
        setMessage('Your email is verified. You can sign in and use Animica Exchange.');
      })
      .catch((error) => {
        if (!active) return;
        setStatus('error');
        setMessage(error.response?.data?.message || 'Verification link could not be confirmed.');
      });

    return () => {
      active = false;
    };
  }, [statusParam, token]);

  const resend = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setMessage('');
    try {
      await axios.post(`${API_URL}/auth/resend-verification`, { email }, { withCredentials: true });
      setStatus('sent');
      setMessage('If that address has an unverified account, a new verification email has been sent.');
    } catch (error: any) {
      setStatus('error');
      setMessage(error.response?.data?.message || 'Verification email could not be sent.');
    } finally {
      setLoading(false);
    }
  };

  const icon =
    status === 'verified' ? (
      <CheckCircle2 className="text-emerald-400" size={34} />
    ) : status === 'error' ? (
      <XCircle className="text-red-400" size={34} />
    ) : (
      <ShieldCheck className="text-blue-400" size={34} />
    );

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center px-4 py-12">
      <Seo
        title="Verify Email | Animica Exchange"
        description="Verify your email address to activate your Animica Exchange account."
        path="/verify-email"
        noindex
      />

      <div className="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 p-8 shadow-2xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-slate-800">{icon}</div>
          <div>
            <h1 className="text-2xl font-bold text-white">Verify your email</h1>
            <p className="text-sm text-slate-400">Required before login, deposits, claims, and trading.</p>
          </div>
        </div>

        {status === 'verifying' && (
          <div className="rounded-lg border border-blue-500/40 bg-blue-500/10 p-4 text-sm text-blue-200">
            Confirming your verification link...
          </div>
        )}

        {status === 'verified' && (
          <div className="space-y-4">
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm text-emerald-200">
              {message || 'Your email is verified. You can sign in and use Animica Exchange.'}
            </div>
            <Link className="block rounded-lg bg-blue-600 px-4 py-3 text-center text-sm font-semibold text-white hover:bg-blue-500" to="/login">
              Sign in
            </Link>
          </div>
        )}

        {status !== 'verified' && (
          <form onSubmit={resend} className="space-y-4">
            {message && (
              <div className={`rounded-lg border p-4 text-sm ${status === 'error' ? 'border-red-500/40 bg-red-500/10 text-red-200' : 'border-blue-500/40 bg-blue-500/10 text-blue-200'}`}>
                {message}
              </div>
            )}
            <label className="block text-sm font-medium text-slate-300" htmlFor="verification-email">
              Email address
            </label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={18} />
              <input
                id="verification-email"
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 py-3 pl-10 pr-4 text-white outline-none focus:border-blue-500"
                placeholder="you@example.com"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-3 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-60"
            >
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
              Resend verification email
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
