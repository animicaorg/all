import { useMutation } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";
import { launchToken, uploadMedia, uploadMetadata } from "../lib/api";
import { getWalletAccounts, watchTokenAsset } from "../lib/wallet";

const defaultForm = {
  name: "",
  symbol: "",
  decimals: 18,
  initialSupply: "1000000",
  maxSupply: "1000000",
  mintable: false,
  description: "",
  website: "",
  twitter: "",
  telegram: "",
  discord: "",
  github: "",
  creatorAddress: "",
  freezeAuthority: ""
};

export function LaunchPage() {
  const [form, setForm] = useState(defaultForm);
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>("");
  const [result, setResult] = useState<{ tokenAddress?: string; metadataUri?: string; imageUri?: string } | null>(null);

  const launchM = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Please select a logo or banner file.");
      if (!form.name.trim() || !form.symbol.trim()) throw new Error("Name and symbol are required.");

      let creator = form.creatorAddress.trim();
      if (!creator) {
        const accounts = await getWalletAccounts();
        creator = accounts[0] || "";
      }
      if (!creator) throw new Error("Creator address is required (connect wallet or type it manually).");

      setStatus("Uploading media...");
      const media = await uploadMedia(file);

      setStatus("Uploading metadata...");
      const metadata = await uploadMetadata({
        name: form.name,
        symbol: form.symbol,
        description: form.description,
        image: media.uri,
        website: form.website,
        twitter: form.twitter,
        telegram: form.telegram,
        discord: form.discord,
        github: form.github,
        creator,
        decimals: Number(form.decimals),
        total_supply: form.initialSupply
      });

      setStatus("Deploying token contract...");
      const launched = await launchToken({
        name: form.name,
        symbol: form.symbol,
        decimals: Number(form.decimals),
        initialSupply: form.initialSupply,
        maxSupply: form.maxSupply,
        mintable: form.mintable,
        metadataUri: metadata.uri,
        creatorAddress: creator,
        freezeAuthority: form.freezeAuthority.trim()
      });

      return {
        tokenAddress: launched.token.address,
        metadataUri: metadata.uri,
        imageUri: media.uri
      };
    },
    async onSuccess(data) {
      setResult(data);
      setStatus("Token launched successfully.");
      await watchTokenAsset({
        address: data.tokenAddress || "",
        symbol: form.symbol,
        decimals: Number(form.decimals),
        image: data.imageUri,
        name: form.name
      });
    },
    onError(error) {
      setStatus((error as Error).message);
    }
  });

  const supplyMode = useMemo(() => {
    if (!form.mintable && form.initialSupply === form.maxSupply) return "Fully Fixed";
    if (form.mintable) return "Owner Mintable with Cap";
    return "Fixed Supply";
  }, [form.initialSupply, form.maxSupply, form.mintable]);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    launchM.mutate();
  };

  return (
    <section className="stack-lg">
      <div className="card">
        <h2>Launch VM-PY Token</h2>
        <p className="muted">Creates a new standard `AnimicaTokenStandard` contract instance.</p>
      </div>

      <form className="card form-grid" onSubmit={onSubmit}>
        <label>
          Name
          <input value={form.name} onChange={(e) => setForm((v) => ({ ...v, name: e.target.value }))} required />
        </label>
        <label>
          Symbol
          <input value={form.symbol} onChange={(e) => setForm((v) => ({ ...v, symbol: e.target.value }))} required />
        </label>
        <label>
          Decimals
          <input type="number" min={0} max={255} value={form.decimals} onChange={(e) => setForm((v) => ({ ...v, decimals: Number(e.target.value) }))} />
        </label>
        <label>
          Initial Supply
          <input value={form.initialSupply} onChange={(e) => setForm((v) => ({ ...v, initialSupply: e.target.value }))} />
        </label>
        <label>
          Max Supply
          <input value={form.maxSupply} onChange={(e) => setForm((v) => ({ ...v, maxSupply: e.target.value }))} />
        </label>
        <label className="toggle-label">
          <input type="checkbox" checked={form.mintable} onChange={(e) => setForm((v) => ({ ...v, mintable: e.target.checked }))} />
          Mintable (owner can mint up to cap)
        </label>
        <label className="full-row">
          Description
          <textarea value={form.description} onChange={(e) => setForm((v) => ({ ...v, description: e.target.value }))} rows={3} />
        </label>
        <label>
          Website
          <input value={form.website} onChange={(e) => setForm((v) => ({ ...v, website: e.target.value }))} placeholder="https://..." />
        </label>
        <label>
          Twitter/X
          <input value={form.twitter} onChange={(e) => setForm((v) => ({ ...v, twitter: e.target.value }))} placeholder="https://x.com/..." />
        </label>
        <label>
          Telegram
          <input value={form.telegram} onChange={(e) => setForm((v) => ({ ...v, telegram: e.target.value }))} />
        </label>
        <label>
          Discord
          <input value={form.discord} onChange={(e) => setForm((v) => ({ ...v, discord: e.target.value }))} />
        </label>
        <label>
          GitHub
          <input value={form.github} onChange={(e) => setForm((v) => ({ ...v, github: e.target.value }))} />
        </label>
        <label>
          Creator Address
          <input value={form.creatorAddress} onChange={(e) => setForm((v) => ({ ...v, creatorAddress: e.target.value }))} placeholder="anim1..." />
        </label>
        <label>
          Freeze Authority (optional)
          <input value={form.freezeAuthority} onChange={(e) => setForm((v) => ({ ...v, freezeAuthority: e.target.value }))} placeholder="anim1..." />
        </label>
        <label className="full-row">
          Media (png/jpg/gif)
          <input
            type="file"
            accept="image/png,image/jpeg,image/jpg,image/gif"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            required
          />
        </label>

        <div className="preview full-row">
          <h3>Launch Preview</h3>
          <p>Supply model: {supplyMode}</p>
          <p>Deploying via standardized token contract with metadata URI pinned to IPFS.</p>
          {file ? <p>Selected file: {file.name}</p> : null}
        </div>

        <button className="btn-primary" type="submit" disabled={launchM.isPending}>
          {launchM.isPending ? "Launching..." : "Launch Token"}
        </button>
      </form>

      {status ? <p className="status">{status}</p> : null}
      {result?.tokenAddress ? (
        <div className="card">
          <h3>Launch Complete</h3>
          <p className="mono">Token: {result.tokenAddress}</p>
          <p className="mono">Metadata: {result.metadataUri}</p>
        </div>
      ) : null}
    </section>
  );
}
