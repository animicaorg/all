import { AlertTriangle } from 'lucide-react';
import { Seo } from '../components/Seo';
import { breadcrumbJsonLd } from '../lib/seo';

export default function LegalPage() {
  return (
    <div className="max-w-4xl mx-auto py-8">
      <Seo
        title="Legal Disclaimer and Risk Warning | Animica Exchange"
        description="Read the Animica Exchange legal disclaimer and digital asset risk warning before using ANM markets or exchange account features."
        path="/legal"
        structuredData={[
          breadcrumbJsonLd([
            { name: 'Home', path: '/' },
            { name: 'Legal Disclaimer', path: '/legal' },
          ]),
        ]}
      />

      <div className="bg-slate-800 rounded-lg shadow-xl p-8">
        <div className="flex items-center gap-4 mb-8">
          <AlertTriangle className="text-red-500" size={48} />
          <h1 className="text-3xl font-bold text-white">Legal Disclaimer</h1>
        </div>

        <div className="space-y-6 text-slate-300">
          {/* Primary Warning */}
          <div className="bg-red-900/30 border-2 border-red-500 rounded-lg p-6">
            <h2 className="text-xl font-bold text-red-400 mb-3">
              IMPORTANT: READ CAREFULLY
            </h2>
            <div className="space-y-3 text-lg">
              <p className="font-semibold text-white">
                Animica (ANM) has NO INTRINSIC VALUE and is subject to COMPLETE LOSS of any and all investment.
              </p>
              <p>
                By using this exchange and trading Animica tokens, you acknowledge and accept that you may lose your entire investment.
              </p>
            </div>
          </div>

          {/* Risk Warnings */}
          <section>
            <h2 className="text-2xl font-bold text-white mb-4">Risk Warnings</h2>
            <div className="space-y-3">
              <div className="border-l-4 border-yellow-500 pl-4">
                <h3 className="font-semibold text-yellow-400 mb-1">High Risk Investment</h3>
                <p>
                  Trading digital assets involves substantial risk of loss. The value of Animica can be highly volatile
                  and may fluctuate significantly in short periods of time.
                </p>
              </div>

              <div className="border-l-4 border-yellow-500 pl-4">
                <h3 className="font-semibold text-yellow-400 mb-1">No Guarantees</h3>
                <p>
                  There are no guarantees of profit or protection against losses. Past performance is not indicative
                  of future results.
                </p>
              </div>

              <div className="border-l-4 border-yellow-500 pl-4">
                <h3 className="font-semibold text-yellow-400 mb-1">Technical Risks</h3>
                <p>
                  Digital assets and blockchain technology involve technical risks including but not limited to:
                  software vulnerabilities, network failures, security breaches, and loss of private keys.
                </p>
              </div>

              <div className="border-l-4 border-yellow-500 pl-4">
                <h3 className="font-semibold text-yellow-400 mb-1">Regulatory Uncertainty</h3>
                <p>
                  The regulatory status of digital assets is uncertain and evolving. Changes in laws and regulations
                  may adversely affect the value or usability of Animica.
                </p>
              </div>
            </div>
          </section>

          {/* No Investment Advice */}
          <section>
            <h2 className="text-2xl font-bold text-white mb-4">No Investment Advice</h2>
            <p>
              Nothing on this platform constitutes investment, financial, legal, or tax advice. You should consult
              with qualified professionals before making any investment decisions.
            </p>
          </section>

          {/* No Warranties */}
          <section>
            <h2 className="text-2xl font-bold text-white mb-4">No Warranties</h2>
            <p>
              This exchange and all associated services are provided "AS IS" and "AS AVAILABLE" without warranties
              of any kind, either express or implied. We do not warrant that the service will be uninterrupted,
              secure, or error-free.
            </p>
          </section>

          {/* Limitation of Liability */}
          <section>
            <h2 className="text-2xl font-bold text-white mb-4">Limitation of Liability</h2>
            <p>
              To the fullest extent permitted by law, the operators of this exchange shall not be liable for any
              indirect, incidental, special, consequential, or punitive damages, or any loss of profits or revenues,
              whether incurred directly or indirectly, or any loss of data, use, goodwill, or other intangible losses.
            </p>
          </section>

          {/* User Responsibility */}
          <section>
            <h2 className="text-2xl font-bold text-white mb-4">User Responsibility</h2>
            <div className="space-y-2">
              <p>By using this exchange, you acknowledge and agree that:</p>
              <ul className="list-disc list-inside space-y-2 ml-4">
                <li>You are solely responsible for your investment decisions</li>
                <li>You have the financial capacity to bear the risk of total loss</li>
                <li>You understand the technical aspects and risks of digital assets</li>
                <li>You are responsible for the security of your account and private keys</li>
                <li>You comply with all applicable laws and regulations in your jurisdiction</li>
              </ul>
            </div>
          </section>

          {/* Age Restriction */}
          <section>
            <h2 className="text-2xl font-bold text-white mb-4">Age Restriction</h2>
            <p>
              You must be at least 18 years old (or the age of majority in your jurisdiction) to use this exchange.
            </p>
          </section>

          {/* Contact */}
          <section className="pt-6 border-t border-slate-700">
            <p className="text-sm text-slate-400">
              Last updated: {new Date().toLocaleDateString()}
            </p>
          </section>

          {/* Final Warning */}
          <div className="bg-red-900/30 border-2 border-red-500 rounded-lg p-6 mt-8">
            <p className="font-bold text-white text-center text-lg">
              DO NOT INVEST MORE THAN YOU CAN AFFORD TO LOSE
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
