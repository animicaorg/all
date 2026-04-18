import { FormEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { WalletConnectPanel } from "../components/WalletConnectPanel";
import {
  attachAnimicaDeposit,
  attachEvmDeposit,
  confirmClaimCode,
  createOrder,
  verifySignature
} from "../lib/api";
import { connectWallet, ensureBnbChain, getInjectedProvider, signOrderTypedData, submitRouterDeposit } from "../lib/wallet";

type Direction = "ANM_TO_BANM" | "BANM_TO_ANM";

export function BridgePage() {
  const [direction, setDirection] = useState<Direction>("ANM_TO_BANM");
  const [evmChain, setEvmChain] = useState<"BNB">("BNB");
  const [amount, setAmount] = useState("1");
  const [animicaSource, setAnimicaSource] = useState("");
  const [animicaDestination, setAnimicaDestination] = useState("");
  const [txHashInput, setTxHashInput] = useState("");
  const [claimCodeInput, setClaimCodeInput] = useState("");
  const [wallet, setWallet] = useState<{ account: string | null; chainId: number | null; isMetaMask: boolean }>({
    account: null,
    chainId: null,
    isMetaMask: false
  });
  const [provider, setProvider] = useState<any>(null);
  const [created, setCreated] = useState<any>(null);
  const [orderState, setOrderState] = useState<any>(null);
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const providerDetected = Boolean(getInjectedProvider());
  const warningList = useMemo(
    () => [
      "Custodial bridge: settlement is operator-managed.",
      "MetaMask signature proves only EVM address control.",
      "Destination and amount are immutable after order creation."
    ],
    []
  );
  const sourceChain = direction === "ANM_TO_BANM" ? "ANIMICA" : evmChain;
  const destinationChain = direction === "ANM_TO_BANM" ? evmChain : "ANIMICA";

  async function onConnectWallet() {
    try {
      const state = await connectWallet();
      setProvider(state.provider);
      setWallet({
        account: state.account,
        chainId: state.chainId,
        isMetaMask: state.isMetaMask
      });
      setError("");
    } catch (err: any) {
      setError(err?.message || "Wallet connection failed");
    }
  }

  async function onCreateOrder(event: FormEvent) {
    event.preventDefault();
    if (!wallet.account) {
      setError("Connect MetaMask before creating an order.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload: any = {
        direction,
        connected_evm_address: wallet.account,
        amount,
        source_chain: sourceChain,
        destination_chain: destinationChain,
        chain_id: wallet.chainId || 97
      };
      if (direction === "ANM_TO_BANM") {
        payload.source_address = animicaSource;
      } else {
        payload.destination_address = animicaDestination;
      }
      const next = await createOrder(payload);
      setCreated(next);
      setOrderState(next.order);
      setTxHashInput("");
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Order creation failed");
    } finally {
      setBusy(false);
    }
  }

  async function onSignOrder() {
    if (!created?.challenge?.typed_data || !provider) {
      setError("Create order and connect wallet first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const signature = await signOrderTypedData(provider, created.challenge.typed_data);
      const verified = await verifySignature(created.order.order_id, signature, "EIP712");
      setOrderState(verified.order);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Signature verification failed");
    } finally {
      setBusy(false);
    }
  }

  async function onAttachDepositTx() {
    if (!created?.order?.order_id || !txHashInput) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result =
        direction === "ANM_TO_BANM"
          ? await attachAnimicaDeposit(created.order.order_id, txHashInput)
          : await attachEvmDeposit(created.order.order_id, txHashInput);
      setOrderState(result.order);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Unable to attach deposit tx");
    } finally {
      setBusy(false);
    }
  }

  async function onMetaMaskDeposit() {
    if (direction !== "BANM_TO_ANM" || !provider || !created?.ui?.deposit_router || !created?.ui?.banm_token) return;
    setBusy(true);
    setError("");
    try {
      await ensureBnbChain(provider, 97);
      const result = await submitRouterDeposit(
        provider,
        created.ui.deposit_router,
        created.ui.banm_token,
        created.order.order_id,
        BigInt(created.ui.exact_banm_amount_wei)
      );
      setTxHashInput(result.depositTxHash);
      const attached = await attachEvmDeposit(created.order.order_id, result.depositTxHash);
      setOrderState(attached.order);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "MetaMask deposit failed");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmClaimCode() {
    if (!created?.order?.order_id || !claimCodeInput) return;
    setBusy(true);
    setError("");
    try {
      const result = await confirmClaimCode(created.order.order_id, claimCodeInput);
      setOrderState(result.order);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Claim code confirmation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid">
      <section className="sub-hero">
        <h1 style={{ margin: 0 }}>Bridge</h1>
      </section>

      <WalletConnectPanel
        account={wallet.account}
        onConnect={onConnectWallet}
        providerDetected={providerDetected}
        isMetaMask={wallet.isMetaMask}
      />

      <section className="section">
        <form onSubmit={onCreateOrder} className="grid">
          <div className="row">
            <button
              type="button"
              className={`btn ${direction === "ANM_TO_BANM" ? "primary" : "secondary"}`}
              onClick={() => setDirection("ANM_TO_BANM")}
            >
              ANM -&gt; BANM
            </button>
            <button
              type="button"
              className={`btn ${direction === "BANM_TO_ANM" ? "primary" : "secondary"}`}
              onClick={() => setDirection("BANM_TO_ANM")}
            >
              BANM -&gt; ANM
            </button>
          </div>
          <div className="grid two">
            <div className="field">
              <label>Source chain</label>
              <select value={sourceChain} disabled>
                <option value={sourceChain}>{sourceChain === "BNB" ? "BNB Chain" : "Animica"}</option>
              </select>
            </div>
            <div className="field">
              <label>Destination chain</label>
              <select value={destinationChain} onChange={() => setEvmChain("BNB")} disabled>
                <option value={destinationChain}>{destinationChain === "BNB" ? "BNB Chain" : "Animica"}</option>
              </select>
            </div>
          </div>
          <div className="grid two">
            <div className="field">
              <label>Amount</label>
              <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="1.0" />
            </div>
            {direction === "ANM_TO_BANM" ? (
              <div className="field">
                <label>Animica source address</label>
                <input
                  value={animicaSource}
                  onChange={(e) => setAnimicaSource(e.target.value)}
                  placeholder="anim1..."
                />
              </div>
            ) : (
              <div className="field">
                <label>Animica destination address</label>
                <input
                  value={animicaDestination}
                  onChange={(e) => setAnimicaDestination(e.target.value)}
                  placeholder="anim1..."
                />
              </div>
            )}
          </div>
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? "Processing..." : "Create Immutable Order"}
          </button>
        </form>
        <div style={{ marginTop: 12 }} className="warning">
          {warningList.join(" ")}
        </div>
        {error && (
          <div style={{ marginTop: 12 }} className="warning">
            {error}
          </div>
        )}
      </section>

      {created && (
        <section className="section">
          <h2 style={{ marginTop: 0 }}>Order Result</h2>
          <div className="grid two">
            <div>
              <div style={{ fontSize: 12, color: "#486079" }}>Order ID</div>
              <div className="mono">{created.order.order_id}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "#486079" }}>Current State</div>
              <div>{orderState?.status || created.order.status}</div>
            </div>
          </div>

          {direction === "ANM_TO_BANM" ? (
            <div className="grid two" style={{ marginTop: 12 }}>
              <Data label="Destination EVM Address" value={created.ui.destination_evm_address} />
              <Data label="Exact ANM Deposit Amount (base units)" value={String(created.ui.exact_anm_amount)} />
              <Data label="Animica Deposit Address" value={created.ui.deposit_address} />
              <Data label="Immutable Reference" value={created.ui.deposit_reference} />
              <Data label="Forward Fee (bps)" value={String(created.ui.fee_bps)} />
              <Data label="Estimated Net BANM Out (wei)" value={String(created.ui.net_output_wei)} />
              <Data label="Order Expiry" value={new Date(created.order.expires_at).toLocaleString()} />
            </div>
          ) : (
            <div className="grid two" style={{ marginTop: 12 }}>
              <Data label="Source EVM Address" value={created.ui.source_evm_address} />
              <Data label="Destination Animica Address" value={created.ui.destination_animica_address} />
              <Data label="Exact BANM Amount (wei)" value={String(created.ui.exact_banm_amount_wei)} />
              <Data label="Router Contract" value={created.ui.deposit_router} />
              <Data label="BANM Token Contract" value={created.ui.banm_token} />
              <Data label="Reverse Fee (bps)" value={String(created.ui.fee_bps)} />
              <Data label="Estimated Net ANM Out (base units)" value={String(created.ui.net_output_anm)} />
              <Data label="Order Expiry" value={new Date(created.order.expires_at).toLocaleString()} />
            </div>
          )}

          {created.warnings?.length > 0 && (
            <div className="warning" style={{ marginTop: 12 }}>
              {created.warnings.join(" ")}
            </div>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={onSignOrder} disabled={busy}>
              Sign Order Challenge (MetaMask)
            </button>
            {direction === "BANM_TO_ANM" && (
              <button className="btn secondary" onClick={onMetaMaskDeposit} disabled={busy}>
                Deposit BANM via MetaMask
              </button>
            )}
            <Link className="btn secondary" to={`/status/${created.order.order_id}`}>
              Open Status Page
            </Link>
          </div>

          {direction === "BANM_TO_ANM" && created.ui.claim_code && (
            <div className="mini-box" style={{ marginTop: 12 }}>
              <div style={{ fontSize: 12, color: "#486079" }}>Claim Code</div>
              <div className="mono" style={{ margin: "4px 0 10px" }}>
                {created.ui.claim_code}
              </div>
              <div className="row">
                <input
                  style={{ flex: 1, minWidth: 220 }}
                  value={claimCodeInput}
                  onChange={(e) => setClaimCodeInput(e.target.value)}
                  placeholder="Confirm claim code before release"
                />
                <button className="btn secondary" onClick={onConfirmClaimCode} disabled={busy}>
                  Confirm Claim Code
                </button>
              </div>
            </div>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            <input
              style={{ flex: 1, minWidth: 220 }}
              value={txHashInput}
              onChange={(e) => setTxHashInput(e.target.value)}
              placeholder={direction === "ANM_TO_BANM" ? "Animica tx hash" : "BNB tx hash"}
            />
            <button className="btn secondary" onClick={onAttachDepositTx} disabled={busy}>
              Attach Deposit TX
            </button>
          </div>
        </section>
      )}
    </div>
  );
}

function Data({ label, value }: { label: string; value: string }) {
  return (
    <div className="mini-box">
      <div style={{ fontSize: 12, color: "#486079" }}>{label}</div>
      <div className="mono" style={{ marginTop: 4 }}>
        {value}
      </div>
    </div>
  );
}
