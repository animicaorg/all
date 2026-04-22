import { useEffect, useState } from 'react';
import { useSession } from '../lib/session';
import { usdanApi } from '../lib/api';

export function CompliancePage() {
  const { session } = useSession();
  const [kyc, setKyc] = useState<any>(null);

  useEffect(() => {
    if (!session) return;
    usdanApi.getKycStatus(session).then(setKyc).catch(() => undefined);
  }, [session]);

  return (
    <section className="page">
      <h2>Compliance & Risk Controls</h2>
      <div className="grid two">
        <article className="card">
          <h3>Identity Controls</h3>
          <p>KYC status: {kyc?.status ?? 'unknown'}</p>
          <p>Bank verification required for buy/redeem</p>
          <p>Session wallet: {session?.walletAddress ?? 'not bound'}</p>
        </article>

        <article className="card">
          <h3>On-Chain Controls</h3>
          <ul>
            <li>Pause and freeze via compliance controller</li>
            <li>Allowlist/denylist transfer gating</li>
            <li>Mint and redemption nonce replay protection</li>
            <li>Auditable event emission for admin actions</li>
          </ul>
        </article>
      </div>
    </section>
  );
}
