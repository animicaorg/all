export async function compile(source: string) {
  return {
    bytecode: `0x${Buffer.from(source).toString("hex").slice(0, 256)}`,
    warnings: [],
    language: "animica"
  };
}
