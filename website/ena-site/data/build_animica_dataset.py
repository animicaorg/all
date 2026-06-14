#!/usr/bin/env python3
"""Build the Animica knowledge instruction dataset and register it as the ENA
starter dataset. Run with the project venv:

    /root/animica/.venv/bin/python build_animica_dataset.py
"""
from __future__ import annotations
import json, os, sqlite3, urllib.request
from pathlib import Path

PAIRS: list[tuple[str, str]] = [
    # --- Overview ---
    ("What is Animica?",
     "Animica is a post-quantum Layer-1 blockchain combined with an open AI compute network. It pairs a Proof-of-Work chain secured by post-quantum signatures with ENA and AICF, which let people contribute compute to run AI inference and training and earn the native token, ANM."),
    ("What is the native token of Animica?",
     "ANM. It has 9 decimals, so 1 ANM equals 1,000,000,000 base (nano) units. ANM pays transaction fees, rewards miners and AI contributors, and funds AI jobs."),
    ("What makes Animica post-quantum?",
     "Account signatures use post-quantum schemes instead of ECDSA: ML-DSA-65 (FIPS 204), Dilithium3, and SPHINCS+ (SHAKE-128s). This keeps accounts secure against attacks from large quantum computers."),
    ("What problem does Animica solve?",
     "It provides a quantum-resistant settlement layer and turns blockchain compute into useful AI work — inference and model training — rather than only hashing, so contributors earn ANM for productive work."),
    # --- Chain fundamentals ---
    ("What is Animica's chain ID?",
     "The Animica mainnet chain ID is 1."),
    ("What consensus does Animica use?",
     "Animica uses Proof-of-Work. Blocks are secured by SHA3-based hashing, and block times depend on the current network hashrate."),
    ("What hash function does Animica use?",
     "SHA3-256 (the NIST standard, not Keccak) is used for addresses, transaction hashing, and receipts."),
    ("How are Animica blocks and messages encoded?",
     "With canonical CBOR. Floating-point values are rejected at the wire and consensus layers so every node encodes state deterministically."),
    ("Does Animica have data availability?",
     "Yes. Animica has a data-availability (DA) layer for publishing and retrieving blobs. From the CLI you can use `animica da submit` and `animica da retrieve`."),
    ("Does Animica support smart contracts?",
     "Yes. Animica supports contracts via a Python-based VM. Use the `animica contract` command group to deploy and call contracts."),
    # --- Addresses ---
    ("What does an Animica address look like?",
     "Animica addresses are bech32m strings beginning with `anim1`, for example anim1zqp.... They encode a 2-byte algorithm id plus the 32-byte SHA3-256 digest of the public key."),
    ("How is an Animica address derived?",
     "address = bech32m(\"anim\", u16be(alg_id) || sha3_256(public_key)). The 34-byte payload is a 2-byte big-endian algorithm id followed by the 32-byte SHA3-256 digest of the raw public key."),
    ("What are the Animica signature algorithm ids?",
     "ml_dsa_65 = 0x1003 (4099), dilithium3 = 0x1001 (4097), and sphincs_shake_128s = 0x1002 (4098). Contract addresses use alg_id 0x0000."),
    ("Why must Animica addresses use bech32m and not bech32?",
     "bech32m (BIP-350, checksum constant 0x2bc830a3) and plain bech32 differ only in the 6-character checksum. Using plain bech32 yields a valid-looking address with the wrong tail, so cross-client validation silently fails. Animica addresses must use bech32m."),
    # --- Transactions ---
    ("What fields does an Animica transaction have?",
     "A transaction body has: chain_id, nonce, from_addr (32 bytes), to_addr (32 bytes), value (in base units), fee, data (calldata), and an optional memo (up to 256 bytes)."),
    ("What is the transaction memo used for?",
     "The memo is an optional field (up to 256 bytes). ENA uses it to bind a payment to a job by putting the job_hash in the memo, so one payment funds exactly one job."),
    ("How do I send ANM from the CLI?",
     "Use: animica tx send --from <addr-or-index> --to anim1... --value 1.5 . The node signs with the sender's post-quantum key and broadcasts the transaction."),
    ("How do I check an account balance?",
     "Use the RPC method state.getBalance (or account.getBalance), or check an address on explorer.animica.org."),
    ("How can I look up a transaction?",
     "Call the RPC method tx.getTransaction with the transaction hash, or use tx.getTransactionStatus for confirmation status. The explorer also shows transactions."),
    # --- CLI ---
    ("How do I install the Animica CLI?",
     "Run: pip install --upgrade animica . For AI training extras (PyTorch, transformers, PEFT, TRL, bitsandbytes), install: pip install --upgrade \"animica[gpu]\"."),
    ("What command groups does the animica CLI have?",
     "Major groups include node, wallet, key, tx, chain, rpc, mempool, miner, da, network, peer, sync, contract, ena, aicf, and bittensor. Run `animica --help` to see them all."),
    ("How do I start an Animica node?",
     "Run: animica node up (mainnet by default). Check it with `animica node status` and `animica sync status`."),
    ("How do I check sync status?",
     "Run: animica sync status . To force a resync, run: animica sync force ."),
    ("How do I switch networks in the CLI?",
     "Use the global flag `animica --network <name> <command>`, set ANIMICA_NETWORK, or run `animica network set <name>`. Valid networks include mainnet, testnet, devnet, and local-devnet."),
    # --- Wallet ---
    ("What wallets can I use with Animica?",
     "A browser extension (injects window.animica), a Qt desktop wallet for Windows/macOS/Linux, an Android wallet, and the web wallet at wallet.animica.org. Get them from animica.org/wallet."),
    ("How does a website connect to the Animica browser wallet?",
     "The extension injects a provider at window.animica and fires an `animica#initialized` event when ready. A dApp connects with window.animica.request({ method: 'animica_requestAccounts' }) and sends value with 'animica_sendTransaction'."),
    ("How do I create a new wallet from the CLI?",
     "Run: animica wallet new (or `animica key` commands to manage keys). List accounts with `animica wallet list`."),
    # --- Mining ---
    ("How do I mine on Animica?",
     "Install the CLI and run the miner against the pool, for example: animica miner dual-mine <your-anim1-address> . Pool details and downloads are at pool.animica.org."),
    ("What is dual mining on Animica?",
     "Dual mining runs the Animica ANM miner and a Monero (XMR) miner together, splitting effort so you earn ANM on Animica while also mining XMR. The CLI auto-resolves the required miner binaries."),
    ("Where is the Animica mining pool?",
     "At pool.animica.org. It supports ANM and XMR with per-miner stats; the stratum endpoint listens on port 3333."),
    ("What is useful-work mining on Animica?",
     "Beyond hashing, Animica lets contributors do useful AI work — data curation, evaluation, embeddings, and model training — through ENA, earning ANM for productive compute instead of only Proof-of-Work."),
    # --- ENA ---
    ("What is ENA?",
     "ENA is Animica's open AI training and inference network. Contributors run real AI jobs — data curation, evaluation, retrieval, and fine-tuning — and earn ANM. Every verified job mints an on-chain receipt. The site is ena.animica.org."),
    ("How do I contribute compute to ENA?",
     "Install the CLI and run: animica ena worker start --worker-id <id> --endpoint https://ena.animica.org/api . Your machine claims jobs, runs them, and earns ANM per verified job."),
    ("What ENA job types can run on a CPU?",
     "extract, chunk, embed, index, summarize, label, dataset_clean, training_records, eval, train_prepare, and scrape — all run on ordinary CPU machines."),
    ("What training methods does ENA support?",
     "Supervised fine-tuning (SFT), LoRA, QLoRA (4-bit), DPO, and CPU distillation, via the python_transformers backend. There is also a `command` backend to launch an external trainer."),
    ("How do I run a fine-tune with ENA?",
     "Install animica[gpu], then: animica ena train prepare --dataset data/train.jsonl --out manifests/run.json --base-model <model> --backend python_transformers --method lora --auto-split ; then `animica ena train run --manifest manifests/run.json`."),
    ("How do contributors get paid on ENA?",
     "Each verified job produces a deterministic SHA3 receipt carrying a credit event. That credit event settles in ANM; training jobs additionally anchor a TrainingReceipt with dataset hash, checkpoint hash, and GPU-hours."),
    ("How do I request an AI job on ENA?",
     "On ena.animica.org, connect your Animica wallet, choose a job type, and pay ANM to fund it. The payment is bound to the job on-chain (job_hash in the memo); the contributor fleet then runs it."),
    ("What is a TrainingReceipt in ENA?",
     "An on-chain anchor for a completed training run. It records the task id, job type (e.g. ena.train.sft), dataset hash, model checkpoint hash, epochs, samples processed, and GPU-hours."),
    ("How do I contribute training data to ENA?",
     "On ena.animica.org paste JSONL instruction pairs in the Training Data section, or from the CLI run: animica ena datasets contribute rows.jsonl . Data is normalized, deduplicated, and registered for training jobs."),
    ("Is ENA inference OpenAI-compatible?",
     "Yes. ENA serves OpenAI-style endpoints and supports pluggable model and embedding providers — a deterministic offline fallback, OpenAI-compatible HTTP APIs, and Ollama."),
    ("How are ENA payments verified on-chain?",
     "The coordinator looks up the funding transaction via the node RPC and checks that the recipient is the ENA treasury, the memo equals the job_hash, the amount meets the reward, and the transaction is confirmed. The txid is de-duplicated so it can fund only one job."),
    # --- AICF ---
    ("What is AICF?",
     "AICF, the AI Compute Fund, is Animica's off-chain compute coordination layer. It matches AI and quantum jobs to registered providers, ties completed work to on-chain proofs, and prices and settles rewards under transparent policy and SLA rules."),
    ("How does AICF prevent fake compute results?",
     "Through provider stake and registry gating, attestation checks, and the rule that results only pay once a proof is finalized on-chain. Deterministic task ids and nullifiers prevent replay and double-claims, and SLA violations are slashed."),
    # --- Products / subdomains ---
    ("What are Animica's main websites?",
     "animica.org (main), explorer.animica.org (block explorer), rpc.animica.org (JSON-RPC), wallet.animica.org (web wallet), pool.animica.org (mining pool), ena.animica.org (open AI network), aicf.animica.org (compute fund), and studio.animica.org."),
    ("Where is the Animica block explorer?",
     "At explorer.animica.org — browse blocks, transactions, and addresses on mainnet."),
    ("What is the Animica public RPC endpoint?",
     "rpc.animica.org/rpc serves JSON-RPC 2.0 over HTTPS via POST. The mainnet chain id is 1. Common methods include chain.getHead, tx.send, tx.getTransaction, and state.getBalance."),
    # --- Bittensor ---
    ("How does Animica relate to Bittensor?",
     "Animica operates as a compute and demand layer around Bittensor: it resells subsidized inference (e.g. via Chutes) and can back GPU subnets such as SN51 with contributor hardware, funneling the economics back to ANM."),
    # --- RPC examples ---
    ("How do I get the current chain head over RPC?",
     "POST to the RPC endpoint with method chain.getHead, e.g. {\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"chain.getHead\",\"params\":{}}. It returns the latest height and block hash."),
    ("What RPC method submits a transaction?",
     "tx.send (node-side signing) or tx.sendRawTransaction / tx.submitRawTransaction for a pre-signed transaction. Check inclusion with tx.getTransactionStatus."),
    # --- Misc ---
    ("Why does Animica reject floats in consensus encoding?",
     "Floating-point representations are not byte-for-byte deterministic across platforms. To keep every node's canonical CBOR identical, Animica rounds or rejects floats before encoding consensus data."),
    ("What is the difference between ENA and AICF?",
     "AICF is the accounting and coordination layer — the queue, economics, proofs, and settlement. ENA is the execution and orchestration layer — the agent, retrieval, useful-work, and training that actually runs the jobs and produces receipts AICF settles."),
    ("Is Animica's software open source?",
     "Yes. The full monorepo is published publicly on GitHub under the animicaorg organization (animicaorg/all), with a trimmed node-only mirror in animicaorg/animica-core."),
    ("How do I check the mempool from the CLI?",
     "Use animica mempool list to see pending transactions and animica mempool stats for mempool statistics."),
    ("What units should I send when paying ANM programmatically?",
     "Base (nano) units. Because ANM has 9 decimals, 1 ANM is 1,000,000,000 nano. The wallet's send-transaction provider call expects the value in base units."),
]


def main() -> None:
    base = os.environ.get("ENA_API", "http://127.0.0.1:8791")
    home = os.environ.get("ANIMICA_ENA_HOME", "/var/lib/animica-ena")
    rows = [{"prompt": p, "response": r} for p, r in PAIRS]

    out = Path(__file__).resolve().parent / "animica-knowledge.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} pairs -> {out}")

    # Clear prior datasets so this is the single starter dataset.
    db = Path(home) / "ena.db"
    if db.is_file():
        con = sqlite3.connect(str(db), timeout=15)
        con.execute("PRAGMA busy_timeout=15000")
        con.execute("DELETE FROM datasets")
        con.commit(); con.close()
        print("cleared prior datasets")

    body = json.dumps({"name": "animica-knowledge", "kind": "knowledge",
                       "rows": rows, "contributor": "animica", "curate": True}).encode()
    req = urllib.request.Request(base + "/datasets/contribute", data=body, method="POST",
                                 headers={"content-type": "application/json"})
    res = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print("registered:", {k: res.get(k) for k in ("name", "kind", "row_count", "sha256")})


if __name__ == "__main__":
    main()
