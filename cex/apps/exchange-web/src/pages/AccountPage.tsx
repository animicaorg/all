import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDownToLine, ArrowUpFromLine, Copy, Loader2, Wallet, X } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { apiClient } from '../lib/api-client';
import type { Asset, AssetNetwork, DepositAddress } from '../types';

type TransferAction = {
  type: 'deposit' | 'withdraw';
  asset: Asset;
  network: AssetNetwork;
};

function formatAtoms(atoms: string, decimals: number): string {
  try {
    const value = BigInt(atoms || '0');
    const negative = value < 0n;
    const absolute = negative ? -value : value;
    const raw = absolute.toString().padStart(decimals + 1, '0');
    const integer = raw.slice(0, -decimals) || '0';
    const fraction = raw.slice(-decimals).replace(/0+$/, '');
    return `${negative ? '-' : ''}${integer}${fraction ? `.${fraction}` : ''}`;
  } catch {
    return '0';
  }
}

function decimalToAtoms(value: string, decimals: number): string {
  const normalized = value.trim();
  if (!/^\d+(\.\d+)?$/.test(normalized)) {
    throw new Error('Enter a valid amount');
  }

  const [whole, fraction = ''] = normalized.split('.');
  if (fraction.length > decimals) {
    throw new Error(`Amount supports up to ${decimals} decimal places`);
  }

  const atoms = BigInt(`${whole}${fraction.padEnd(decimals, '0')}`);
  if (atoms <= 0n) {
    throw new Error('Amount must be greater than zero');
  }

  return atoms.toString();
}

function formatBalance(value: number, asset: string): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: asset === 'BTC' || asset === 'ETH' ? 8 : asset === 'SOL' ? 6 : 2,
  });
}

function getErrorMessage(error: any): string {
  return error?.response?.data?.message || error?.response?.data?.error || error?.message || 'Request failed';
}

function RailStatus({ enabled }: { enabled: boolean }) {
  return (
    <span className={`inline-flex rounded px-2 py-1 text-xs font-medium ${enabled ? 'bg-green-500/10 text-green-300' : 'bg-slate-700 text-slate-300'}`}>
      {enabled ? 'Enabled' : 'Paused'}
    </span>
  );
}

function TransferButton({
  disabled,
  icon,
  label,
  onClick,
}: {
  disabled: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-2 rounded-md border border-slate-600 px-3 py-2 text-sm font-medium text-slate-100 transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {icon}
      {label}
    </button>
  );
}

function NetworkRows({
  asset,
  onDeposit,
  onWithdraw,
}: {
  asset: Asset;
  onDeposit: (asset: Asset, network: AssetNetwork) => void;
  onWithdraw: (asset: Asset, network: AssetNetwork) => void;
}) {
  return (
    <>
      {asset.networks.map((network) => (
        <tr key={network.assetNetworkId} className="hover:bg-slate-700 transition-colors">
          <td className="px-6 py-4">
            <div className="text-sm font-medium text-white">{asset.symbol}</div>
            <div className="text-xs text-slate-400">{network.name}</div>
          </td>
          <td className="px-6 py-4 text-sm text-slate-300">{network.provider}</td>
          <td className="px-6 py-4">
            <RailStatus enabled={network.depositsEnabled} />
          </td>
          <td className="px-6 py-4">
            <RailStatus enabled={network.withdrawalsEnabled} />
          </td>
          <td className="px-6 py-4 text-right text-sm text-white">
            {formatAtoms(network.withdrawalFeeAtoms, asset.decimals)} {asset.symbol}
          </td>
          <td className="px-6 py-4 text-right text-sm text-slate-300">
            {formatAtoms(network.minWithdrawalAtoms, asset.decimals)} {asset.symbol}
          </td>
          <td className="px-6 py-4">
            <div className="flex justify-end gap-2">
              <TransferButton
                disabled={!network.depositsEnabled}
                icon={<ArrowDownToLine size={16} />}
                label="Deposit"
                onClick={() => onDeposit(asset, network)}
              />
              <TransferButton
                disabled={!network.withdrawalsEnabled}
                icon={<ArrowUpFromLine size={16} />}
                label="Withdraw"
                onClick={() => onWithdraw(asset, network)}
              />
            </div>
          </td>
        </tr>
      ))}
    </>
  );
}

function ModalFrame({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 px-4 py-8">
      <div className="w-full max-w-lg rounded-lg border border-slate-700 bg-slate-800 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-700 px-5 py-4">
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <button type="button" onClick={onClose} className="rounded-md p-2 text-slate-400 hover:bg-slate-700 hover:text-white" title="Close">
            <X size={18} />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

function DepositModal({
  action,
  address,
  isLoading,
  error,
  onClose,
}: {
  action: TransferAction;
  address: DepositAddress | null;
  isLoading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  const copyAddress = async () => {
    if (!address?.address) return;
    await navigator.clipboard.writeText(address.address);
    toast.success('Address copied');
  };

  return (
    <ModalFrame title={`Deposit ${action.asset.symbol}`} onClose={onClose}>
      <div className="space-y-4">
        <div className="rounded-md border border-slate-700 bg-slate-900 px-4 py-3">
          <div className="text-xs uppercase tracking-wider text-slate-500">Network</div>
          <div className="mt-1 text-sm font-medium text-white">{action.network.name}</div>
        </div>

        {isLoading && (
          <div className="flex items-center gap-3 rounded-md border border-slate-700 bg-slate-900 px-4 py-6 text-slate-300">
            <Loader2 className="animate-spin" size={18} />
            Creating deposit address
          </div>
        )}

        {error && (
          <div className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {address && (
          <div className="space-y-3">
            <div className="rounded-md border border-slate-700 bg-slate-900 p-4">
              <div className="mb-2 text-xs uppercase tracking-wider text-slate-500">Address</div>
              <div className="break-all font-mono text-sm text-white">{address.address}</div>
              <button
                type="button"
                onClick={copyAddress}
                className="mt-4 inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-500"
              >
                <Copy size={16} />
                Copy
              </button>
            </div>

            {address.tag && (
              <div className="rounded-md border border-slate-700 bg-slate-900 p-4">
                <div className="mb-2 text-xs uppercase tracking-wider text-slate-500">Tag</div>
                <div className="break-all font-mono text-sm text-white">{address.tag}</div>
              </div>
            )}
          </div>
        )}
      </div>
    </ModalFrame>
  );
}

function WithdrawModal({
  action,
  available,
  isSubmitting,
  error,
  onClose,
  onSubmit,
}: {
  action: TransferAction;
  available?: number;
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (amount: string, destinationAddress: string, destinationTag?: string) => void;
}) {
  const [amount, setAmount] = useState('');
  const [destinationAddress, setDestinationAddress] = useState('');
  const [destinationTag, setDestinationTag] = useState('');

  return (
    <ModalFrame title={`Withdraw ${action.asset.symbol}`} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(amount, destinationAddress, destinationTag || undefined);
        }}
      >
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-md border border-slate-700 bg-slate-900 px-4 py-3">
            <div className="text-xs uppercase tracking-wider text-slate-500">Available</div>
            <div className="mt-1 text-sm font-medium text-white">
              {available === undefined ? '-' : formatBalance(available, action.asset.symbol)} {action.asset.symbol}
            </div>
          </div>
          <div className="rounded-md border border-slate-700 bg-slate-900 px-4 py-3">
            <div className="text-xs uppercase tracking-wider text-slate-500">Flat Fee</div>
            <div className="mt-1 text-sm font-medium text-white">
              {formatAtoms(action.network.withdrawalFeeAtoms, action.asset.decimals)} {action.asset.symbol}
            </div>
          </div>
        </div>

        <div className="rounded-md border border-slate-700 bg-slate-900 px-4 py-3">
          <div className="text-xs uppercase tracking-wider text-slate-500">Minimum</div>
          <div className="mt-1 text-sm font-medium text-white">
            {formatAtoms(action.network.minWithdrawalAtoms, action.asset.decimals)} {action.asset.symbol}
          </div>
        </div>

        <label className="block">
          <span className="mb-2 block text-sm font-medium text-slate-300">Amount</span>
          <input
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            inputMode="decimal"
            required
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
          />
        </label>

        <label className="block">
          <span className="mb-2 block text-sm font-medium text-slate-300">Destination Address</span>
          <input
            value={destinationAddress}
            onChange={(event) => setDestinationAddress(event.target.value)}
            required
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-sm text-white outline-none focus:border-blue-500"
          />
        </label>

        <label className="block">
          <span className="mb-2 block text-sm font-medium text-slate-300">Tag or Memo</span>
          <input
            value={destinationTag}
            onChange={(event) => setDestinationTag(event.target.value)}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-sm text-white outline-none focus:border-blue-500"
          />
        </label>

        {error && (
          <div className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={isSubmitting}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-3 text-sm font-semibold text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSubmitting && <Loader2 className="animate-spin" size={18} />}
          Submit Withdrawal
        </button>
      </form>
    </ModalFrame>
  );
}

export default function AccountPage() {
  const queryClient = useQueryClient();
  const [activeTransfer, setActiveTransfer] = useState<TransferAction | null>(null);
  const [depositAddress, setDepositAddress] = useState<DepositAddress | null>(null);
  const [transferError, setTransferError] = useState<string | null>(null);

  const { data: balances = [], isLoading } = useQuery({
    queryKey: ['balances'],
    queryFn: () => apiClient.getBalances(),
    refetchInterval: 5000,
  });

  const { data: assets = [], isLoading: assetsLoading } = useQuery({
    queryKey: ['assets'],
    queryFn: () => apiClient.getAssets(),
    refetchInterval: 15000,
  });

  const { data: withdrawals = [] } = useQuery({
    queryKey: ['withdrawals'],
    queryFn: () => apiClient.getWithdrawals(),
    refetchInterval: 10000,
  });

  const createDepositAddress = useMutation({
    mutationFn: (assetNetworkId: string) => apiClient.createDepositAddress(assetNetworkId),
    onSuccess: (address) => {
      setDepositAddress(address);
      queryClient.invalidateQueries({ queryKey: ['depositAddresses'] });
    },
    onError: (error) => {
      setTransferError(getErrorMessage(error));
    },
  });

  const createWithdrawal = useMutation({
    mutationFn: apiClient.createWithdrawal,
    onSuccess: () => {
      toast.success('Withdrawal submitted');
      setActiveTransfer(null);
      setTransferError(null);
      queryClient.invalidateQueries({ queryKey: ['balances'] });
      queryClient.invalidateQueries({ queryKey: ['withdrawals'] });
    },
    onError: (error) => {
      setTransferError(getErrorMessage(error));
    },
  });

  const assetMap = new Map(assets.map((asset) => [asset.symbol, asset]));
  const activeRailCount = assets.reduce(
    (count, asset) => count + asset.networks.filter((network) => network.depositsEnabled || network.withdrawalsEnabled).length,
    0
  );

  const openDeposit = (asset: Asset, network: AssetNetwork) => {
    setTransferError(null);
    setDepositAddress(null);
    setActiveTransfer({ type: 'deposit', asset, network });
    createDepositAddress.reset();
    createDepositAddress.mutate(network.assetNetworkId);
  };

  const openWithdraw = (asset: Asset, network: AssetNetwork) => {
    setTransferError(null);
    setDepositAddress(null);
    setActiveTransfer({ type: 'withdraw', asset, network });
    createWithdrawal.reset();
  };

  const closeModal = () => {
    setActiveTransfer(null);
    setTransferError(null);
    setDepositAddress(null);
  };

  const submitWithdrawal = (amount: string, destinationAddress: string, destinationTag?: string) => {
    if (!activeTransfer) return;

    try {
      const amountAtoms = decimalToAtoms(amount, activeTransfer.asset.decimals);
      createWithdrawal.mutate({
        assetNetworkId: activeTransfer.network.assetNetworkId,
        amountAtoms,
        destinationAddress,
        destinationTag,
      });
    } catch (error) {
      setTransferError(getErrorMessage(error));
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-slate-400">Loading account...</div>
      </div>
    );
  }

  const activeBalance = activeTransfer ? balances.find((balance) => balance.asset === activeTransfer.asset.symbol) : undefined;

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold text-white">Account</h1>

      <div className="bg-gradient-to-br from-blue-600 to-blue-800 rounded-lg p-6">
        <div className="flex items-center gap-2 text-blue-200 mb-2">
          <Wallet size={20} />
          <span className="text-sm font-medium">Portfolio</span>
        </div>
        <div className="text-4xl font-bold text-white mb-4">
          {balances.length.toLocaleString()} assets
        </div>
        <div className="flex flex-wrap gap-4 text-blue-200">
          <span className="inline-flex items-center gap-2 text-sm">
            <ArrowDownToLine size={16} />
            {activeRailCount.toLocaleString()} transfer rails
          </span>
          <span className="inline-flex items-center gap-2 text-sm">
            <ArrowUpFromLine size={16} />
            Flat withdrawal fees
          </span>
        </div>
      </div>

      <div className="bg-slate-800 rounded-lg overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-700">
          <h2 className="text-lg font-semibold text-white">Balances</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Asset</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">Total</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">Available</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">In Orders</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {balances.map((balance) => (
                <tr key={balance.asset} className="hover:bg-slate-700 transition-colors">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center">
                      <div className="w-8 h-8 bg-slate-600 rounded-full flex items-center justify-center text-sm font-bold text-white mr-3">
                        {balance.asset.charAt(0)}
                      </div>
                      <div>
                        <div className="text-sm font-medium text-white">{balance.asset}</div>
                        <div className="text-xs text-slate-400">{assetMap.get(balance.asset)?.name ?? balance.asset}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-white font-medium">
                    {formatBalance(balance.total, balance.asset)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-green-400">
                    {formatBalance(balance.available, balance.asset)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-yellow-400">
                    {formatBalance(balance.locked, balance.asset)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="bg-slate-800 rounded-lg overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-700">
          <h2 className="text-lg font-semibold text-white">Transfer Rails</h2>
        </div>
        {assetsLoading ? (
          <div className="p-6 text-slate-400">Loading transfer rails...</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-700">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Asset</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Provider</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Deposits</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Withdrawals</th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">Flat Fee</th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">Minimum</th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {assets.map((asset) => (
                  <NetworkRows key={asset.symbol} asset={asset} onDeposit={openDeposit} onWithdraw={openWithdraw} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="bg-slate-800 rounded-lg overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-700">
          <h2 className="text-lg font-semibold text-white">Recent Withdrawals</h2>
        </div>
        {withdrawals.length === 0 ? (
          <div className="p-6 text-sm text-slate-400">No withdrawals</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-700">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Status</th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">Amount</th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">Fee</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Destination</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {withdrawals.slice(0, 10).map((withdrawal) => {
                  const network = assets.flatMap((asset) => asset.networks.map((item) => ({ asset, network: item }))).find((item) => item.network.assetNetworkId === withdrawal.assetNetworkId);
                  const symbol = network?.asset.symbol ?? '';
                  const decimals = network?.asset.decimals ?? 0;
                  return (
                    <tr key={withdrawal.id} className="hover:bg-slate-700">
                      <td className="px-6 py-4 text-sm text-white">{withdrawal.status}</td>
                      <td className="px-6 py-4 text-right text-sm text-white">{formatAtoms(withdrawal.amount, decimals)} {symbol}</td>
                      <td className="px-6 py-4 text-right text-sm text-slate-300">{formatAtoms(withdrawal.feeAmount, decimals)} {symbol}</td>
                      <td className="max-w-sm truncate px-6 py-4 font-mono text-xs text-slate-300">{withdrawal.destinationAddress}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {activeTransfer?.type === 'deposit' && (
        <DepositModal
          action={activeTransfer}
          address={depositAddress}
          isLoading={createDepositAddress.isPending}
          error={transferError}
          onClose={closeModal}
        />
      )}

      {activeTransfer?.type === 'withdraw' && (
        <WithdrawModal
          action={activeTransfer}
          available={activeBalance?.available}
          isSubmitting={createWithdrawal.isPending}
          error={transferError}
          onClose={closeModal}
          onSubmit={submitWithdrawal}
        />
      )}
    </div>
  );
}
