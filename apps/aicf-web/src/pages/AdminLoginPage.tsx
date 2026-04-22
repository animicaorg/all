import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

const DEFAULT_REDIRECT = '/admin';

function getSafeRedirect(search: string) {
  const params = new URLSearchParams(search);
  const redirect = params.get('redirect');
  if (!redirect) return DEFAULT_REDIRECT;
  return redirect.startsWith('/admin') ? redirect : DEFAULT_REDIRECT;
}

export function AdminLoginPage() {
  const { session, setSession } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const redirectTarget = useMemo(() => getSafeRedirect(location.search), [location.search]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (session?.user.role === 'admin') {
      navigate(redirectTarget, { replace: true });
    }
  }, [navigate, redirectTarget, session?.user.role]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage('');
    setIsSubmitting(true);

    try {
      const result = await aicfApi.login({ email: email.trim(), password });
      if (result.user.role !== 'admin') {
        setSession(null);
        setMessage('This account does not have admin access.');
        return;
      }

      setSession({ token: result.token, user: result.user });
      navigate(redirectTarget, { replace: true });
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="stack">
      <Panel title="Admin Login" subtitle="Sign in with an admin account to access platform controls.">
        <form className="stack" onSubmit={submit}>
          <label>
            Admin email
            <input
              autoComplete="username"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="admin@animica.org"
              required
              type="email"
              value={email}
            />
          </label>
          <label>
            Password
            <input
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              placeholder="password"
              required
              type="password"
              value={password}
            />
          </label>
          <button disabled={isSubmitting} type="submit">
            {isSubmitting ? 'Signing in...' : 'Sign in as admin'}
          </button>
          {message ? <p className="muted">{message}</p> : null}
          {session?.user.role && session.user.role !== 'admin' ? (
            <p className="muted">
              Current session role is <strong>{session.user.role}</strong>. Signing in here will replace your current session.
            </p>
          ) : null}
          <p className="muted">
            For developer or provider accounts, use <Link to="/app/onboarding">/app/onboarding</Link>.
          </p>
        </form>
      </Panel>
    </div>
  );
}
