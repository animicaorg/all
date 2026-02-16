function encodeText(value: string) {
  const hex = Buffer.from(value, "utf8").toString("hex");
  const len = hex.length / 2;
  const prefix = len < 24 ? (0x60 + len).toString(16) : `78${len.toString(16).padStart(2, "0")}`;
  return `${prefix}${hex}`;
}

function encodeUInt(value: number) {
  if (value < 24) return value.toString(16).padStart(2, "0");
  return `1a${value.toString(16).padStart(8, "0")}`;
}

function encodeBytes(hexValue: string) {
  const hex = hexValue.startsWith("0x") ? hexValue.slice(2) : hexValue;
  const len = hex.length / 2;
  const prefix = len < 24 ? (0x40 + len).toString(16) : `58${len.toString(16).padStart(2, "0")}`;
  return `${prefix}${hex}`;
}

function encodeMap(entries: Array<[string, string]>) {
  const prefix = (0xa0 + entries.length).toString(16);
  return `${prefix}${entries.map(([k, v]) => `${encodeText(k)}${v}`).join("")}`;
}

export type DeployDraft = {
  chainId: number;
  nonce: number;
  gasLimit: number;
  fee: number;
  from: string;
  bytecode: string;
  args?: string;
};

export function buildDeployCborTx(draft: DeployDraft) {
  const mapHex = encodeMap([
    ["type", encodeText("deploy")],
    ["chainId", encodeUInt(draft.chainId)],
    ["nonce", encodeUInt(draft.nonce)],
    ["gasLimit", encodeUInt(draft.gasLimit)],
    ["fee", encodeUInt(draft.fee)],
    ["from", encodeText(draft.from)],
    ["bytecode", encodeBytes(draft.bytecode)],
    ["args", encodeText(draft.args ?? "")]
  ]);

  return `0x${mapHex}`;
}
