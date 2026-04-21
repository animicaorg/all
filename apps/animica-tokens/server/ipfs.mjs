import fs from "node:fs";
import path from "node:path";
import { DATA_DIR } from "./store.mjs";
import { sha256Hex } from "./util.mjs";

const IPFS_DIR = path.join(DATA_DIR, "ipfs");
const UPLOADS_DIR = path.join(DATA_DIR, "uploads");

function ensureDirs() {
  fs.mkdirSync(IPFS_DIR, { recursive: true });
  fs.mkdirSync(UPLOADS_DIR, { recursive: true });
}

function gatewayForCid(cid) {
  const base = process.env.IPFS_GATEWAY_BASE || "https://ipfs.io/ipfs/";
  const normalized = base.endsWith("/") ? base : `${base}/`;
  return `${normalized}${cid}`;
}

async function pinFilePinata(buffer, filename, mimeType) {
  const jwt = process.env.PINATA_JWT;
  if (!jwt) return null;

  const form = new FormData();
  form.set("file", new Blob([buffer], { type: mimeType }), filename);
  form.set("pinataMetadata", JSON.stringify({ name: filename }));

  const response = await fetch("https://api.pinata.cloud/pinning/pinFileToIPFS", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`
    },
    body: form
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Pinata file pin failed (${response.status}): ${text}`);
  }

  const json = await response.json();
  return json.IpfsHash;
}

async function pinJsonPinata(payload) {
  const jwt = process.env.PINATA_JWT;
  if (!jwt) return null;

  const response = await fetch("https://api.pinata.cloud/pinning/pinJSONToIPFS", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ pinataContent: payload })
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Pinata JSON pin failed (${response.status}): ${text}`);
  }

  const json = await response.json();
  return json.IpfsHash;
}

export async function persistMediaFile(buffer, filename, mimeType) {
  ensureDirs();

  const digest = sha256Hex(buffer);
  const localCid = `local-${digest}`;
  let cid = localCid;
  let pinned = false;

  const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, "_");

  try {
    const remoteCid = await pinFilePinata(buffer, safeName, mimeType);
    if (remoteCid) {
      cid = remoteCid;
      pinned = true;
    }
  } catch {
    pinned = false;
  }

  const dataPath = path.join(IPFS_DIR, cid);
  if (!fs.existsSync(dataPath)) {
    fs.writeFileSync(dataPath, buffer);
  }
  const uploadPath = path.join(UPLOADS_DIR, `${cid}-${safeName}`);
  if (!fs.existsSync(uploadPath)) {
    fs.writeFileSync(uploadPath, buffer);
  }

  return {
    cid,
    uri: `ipfs://${cid}`,
    gatewayUrl: gatewayForCid(cid),
    pinned
  };
}

export async function persistMetadataJson(payload) {
  ensureDirs();

  const raw = Buffer.from(JSON.stringify(payload, null, 2), "utf-8");
  const digest = sha256Hex(raw);
  const localCid = `local-${digest}`;
  let cid = localCid;
  let pinned = false;

  try {
    const remoteCid = await pinJsonPinata(payload);
    if (remoteCid) {
      cid = remoteCid;
      pinned = true;
    }
  } catch {
    pinned = false;
  }

  const dataPath = path.join(IPFS_DIR, cid);
  if (!fs.existsSync(dataPath)) {
    fs.writeFileSync(dataPath, raw);
  }

  return {
    cid,
    uri: `ipfs://${cid}`,
    gatewayUrl: gatewayForCid(cid),
    pinned,
    raw
  };
}

export function localIpfsPath(cid) {
  ensureDirs();
  return path.join(IPFS_DIR, cid);
}
