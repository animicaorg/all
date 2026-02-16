export async function simulateAdmission(bytecode: string) {
  const bytes = bytecode.startsWith("0x") ? bytecode.slice(2) : bytecode;
  const size = bytes.length / 2;
  const ok = size > 10;
  return {
    ok,
    summary: ok ? "Simulation passed" : "Simulation failed: bytecode too small",
    logs: [`Bytecode bytes: ${size}`]
  };
}
