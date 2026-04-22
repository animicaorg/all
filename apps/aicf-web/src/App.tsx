import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { AdminDashboardPage } from './pages/AdminDashboardPage';
import { AdminLoginPage } from './pages/AdminLoginPage';
import { AgentTaskDetailPage } from './pages/AgentTaskDetailPage';
import { AgentTasksPage } from './pages/AgentTasksPage';
import { ApiKeysPage } from './pages/ApiKeysPage';
import { AppOverviewPage } from './pages/AppOverviewPage';
import { BillingPage } from './pages/BillingPage';
import { ContractDetailPage } from './pages/ContractDetailPage';
import { ContractJobDetailPage } from './pages/ContractJobDetailPage';
import { ContractJobsPage } from './pages/ContractJobsPage';
import { ContractsPage } from './pages/ContractsPage';
import { DisputesPage } from './pages/DisputesPage';
import { GpuNetworkPage } from './pages/GpuNetworkPage';
import { JobsPage } from './pages/JobsPage';
import { MiningContributePage } from './pages/MiningContributePage';
import { ModelsPage } from './pages/ModelsPage';
import { NewContractJobPage } from './pages/NewContractJobPage';
import { OnboardingPage } from './pages/OnboardingPage';
import { ProjectDetailPage } from './pages/ProjectDetailPage';
import { ProjectsPage } from './pages/ProjectsPage';
import { ProviderContractJobDetailPage } from './pages/ProviderContractJobDetailPage';
import { ProviderContractJobsPage } from './pages/ProviderContractJobsPage';
import { ProviderDashboardPage } from './pages/ProviderDashboardPage';
import { StudioPage } from './pages/StudioPage';
import { StaticMarketingPage } from './pages/StaticMarketingPage';
import { StatusPage } from './pages/StatusPage';
import { TextOpsPage } from './pages/TextOpsPage';
import { TrainingPage } from './pages/TrainingPage';
import { UsagePage } from './pages/UsagePage';
import { WalletPage } from './pages/WalletPage';
import { useSession } from './lib/session';

function HomePage() {
  return (
    <StaticMarketingPage
      title="AICF Platform"
      subtitle="Decentralized AI compute cloud where developers spend ANM and providers earn ANM"
      bullets={[
        'OpenAI-compatible APIs for aicf-chat-1 and aicf-embed-1',
        'ANM-native project balances, escrow, settlement, and treasury subsidies',
        'Provider registry with benchmark-first routing, stake, and reputation',
        'Async jobs for inference, embeddings, training, retrieval, and agent workloads',
        'Cross-platform provider worker bundles with benchmark and startup flows'
      ]}
      cta="Use /app for developer dashboard, /provider for provider dashboard, and /downloads for worker bundles."
    />
  );
}

function RequireAdmin({ children }: { children: JSX.Element }) {
  const { session } = useSession();
  const location = useLocation();

  if (!session || session.user.role !== 'admin') {
    const redirectPath = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to={`/admin/login?redirect=${encodeURIComponent(redirectPath)}`} replace />;
  }

  return children;
}

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route
          path="/developers"
          element={
            <StaticMarketingPage
              title="AICF Developers"
              subtitle="OpenAI-compatible APIs, ANM-native billing, and contract-driven AI jobs"
              bullets={[
                'Chat and embeddings endpoints with project-level API keys',
                'Budget caps and escrow settlement in ANM nanos',
                'Async jobs for inference, training, retrieval, and agents',
                'Contracts can fund and trigger workloads with dispute controls'
              ]}
            />
          }
        />
        <Route
          path="/pricing"
          element={
            <StaticMarketingPage
              title="ANM Pricing"
              subtitle="No fiat rails. ANM-only billing and settlement."
              bullets={[
                'Pay per request and per token/vector in ANM nanos',
                'Per-job budget caps with escrow reservations',
                'Provider reward share + treasury share transparent on each receipt',
                'Optional subsidies and grants from AICF treasury'
              ]}
            />
          }
        />
        <Route
          path="/docs"
          element={
            <StaticMarketingPage
              title="Developer Docs"
              subtitle="Quickstart, API references, wallet flows, provider onboarding, contracts, and ops"
              bullets={[
                'Quickstart and authentication',
                'OpenAI-compatible API examples',
                'Smart contracts calling LLMs/agents via deterministic on-chain + off-chain execution pattern',
                'ANM funding and escrow flows',
                'Provider daemon install and benchmarking',
                'Governance, staking, slashing, and disputes'
              ]}
              cta="See repo docs under docs/aicf-cloud and apps/aicf-docs."
            />
          }
        />
        <Route
          path="/network"
          element={<GpuNetworkPage />}
        />
        <Route
          path="/providers"
          element={
            <StaticMarketingPage
              title="Provider Network"
              subtitle="Contribute GPU/CPU compute and earn ANM"
              bullets={[
                'Register provider and nodes with wallet-backed identity',
                'Publish capabilities and benchmark receipts',
                'Receive scheduled jobs and submit execution receipts',
                'Claim ANM rewards and maintain reputation'
              ]}
              cta="Download worker bundles and provider setup instructions at /downloads."
            />
          }
        />
        <Route path="/downloads" element={<MiningContributePage />} />
        <Route path="/contribute/mining" element={<MiningContributePage />} />
        <Route
          path="/ecosystem"
          element={
            <StaticMarketingPage
              title="Ecosystem"
              subtitle="Treasury grants, subsidized workloads, and model ecosystem growth"
              bullets={[
                'Treasury-funded grants in ANM',
                'Subsidies for strategic workloads',
                'Model marketplace extension path',
                'Provider incentive bootstrapping'
              ]}
            />
          }
        />
        <Route
          path="/about"
          element={
            <StaticMarketingPage
              title="About AICF"
              subtitle="Animica-native decentralized compute infrastructure"
              bullets={[
                'Designed for practical AI workloads, not synthetic DeFi volume',
                'Contracts coordinate escrow, staking, slashing, and rewards',
                'Providers execute off-chain while settlement stays ANM-native'
              ]}
            />
          }
        />
        <Route
          path="/faq"
          element={
            <StaticMarketingPage
              title="FAQ"
              subtitle="Common questions on wallet flows, ANM billing, and provider earnings"
              bullets={[
                'Why ANM-only? To keep settlement natively on Animica.',
                'Can developers use email auth? No. Developer/provider login is wallet-only.',
                'How are providers paid? Rewards accrue per settlement and are claimable in ANM.',
                'How are disputes handled? Challenge window + dispute manager + slash hooks.'
              ]}
            />
          }
        />
        <Route
          path="/support"
          element={
            <StaticMarketingPage
              title="Support"
              subtitle="Operational channels and incident escalation"
              bullets={[
                'API support for failed jobs or billing mismatches',
                'Provider onboarding and daemon troubleshooting',
                'Dispute escalation with settlement references'
              ]}
            />
          }
        />
        <Route path="/status" element={<StatusPage />} />

        <Route path="/app" element={<AppOverviewPage />} />
        <Route path="/app/dashboard" element={<AppOverviewPage />} />
        <Route path="/app/onboarding" element={<OnboardingPage />} />
        <Route path="/app/projects" element={<ProjectsPage />} />
        <Route path="/app/projects/:id" element={<ProjectDetailPage />} />
        <Route path="/app/api-keys" element={<ApiKeysPage />} />
        <Route path="/app/models" element={<ModelsPage />} />
        <Route path="/app/studio" element={<StudioPage />} />
        <Route path="/app/jobs" element={<JobsPage />} />
        <Route path="/app/contract-jobs" element={<ContractJobsPage />} />
        <Route path="/app/contract-jobs/new" element={<NewContractJobPage />} />
        <Route path="/app/contract-jobs/:id" element={<ContractJobDetailPage />} />
        <Route path="/app/contracts" element={<ContractsPage />} />
        <Route path="/app/contracts/:address" element={<ContractDetailPage />} />
        <Route path="/app/agent-tasks" element={<AgentTasksPage />} />
        <Route path="/app/agent-tasks/:id" element={<AgentTaskDetailPage />} />
        <Route path="/app/disputes" element={<DisputesPage />} />
        <Route path="/app/provider-jobs" element={<ProviderContractJobsPage />} />
        <Route path="/app/provider-jobs/:id" element={<ProviderContractJobDetailPage />} />
        <Route path="/app/training" element={<TrainingPage />} />
        <Route path="/app/usage" element={<UsagePage />} />
        <Route path="/app/wallet" element={<WalletPage />} />
        <Route path="/app/billing" element={<BillingPage />} />
        <Route
          path="/app/logs"
          element={
            <TextOpsPage
              title="Request Logs"
              subtitle="Per-project request logs, provider routing, and traceability"
              lines={[
                'Model request IDs and timing',
                'Provider assignment and fallback trace',
                'Escrow and settlement IDs',
                'Webhook delivery attempts'
              ]}
            />
          }
        />
        <Route
          path="/app/settings"
          element={
            <TextOpsPage
              title="Project Settings"
              subtitle="Webhook, callback, security, and usage policy controls"
              lines={[
                'Project webhook configuration',
                'Allowed callback domains',
                'Rate limits and budget guardrails',
                'Model policy and region preferences'
              ]}
            />
          }
        />

        <Route path="/provider" element={<ProviderDashboardPage section="overview" />} />
        <Route path="/provider/onboarding" element={<ProviderDashboardPage section="onboarding" />} />
        <Route path="/provider/hardware" element={<ProviderDashboardPage section="hardware" />} />
        <Route path="/provider/benchmarks" element={<ProviderDashboardPage section="benchmarks" />} />
        <Route path="/provider/jobs" element={<ProviderDashboardPage section="jobs" />} />
        <Route path="/provider/earnings" element={<ProviderDashboardPage section="earnings" />} />
        <Route path="/provider/reputation" element={<ProviderDashboardPage section="reputation" />} />
        <Route path="/provider/staking" element={<ProviderDashboardPage section="staking" />} />
        <Route path="/provider/wallet" element={<ProviderDashboardPage section="wallet" />} />
        <Route path="/provider/settings" element={<ProviderDashboardPage section="settings" />} />
        <Route path="/provider/mining" element={<MiningContributePage />} />
        <Route path="/provider/downloads" element={<MiningContributePage />} />

        <Route path="/admin/login" element={<AdminLoginPage />} />
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="overview" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/providers"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="providers" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/jobs"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="jobs" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/releases"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="releases" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/disputes"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="disputes" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/treasury"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="treasury" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/model-routing"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="model-routing" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/feature-flags"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="feature-flags" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/escrow"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="escrow" />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/rewards"
          element={
            <RequireAdmin>
              <AdminDashboardPage section="rewards" />
            </RequireAdmin>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
