import fs from 'fs';
import path from 'path';

function parse(src) {
  const result = {};
  if (!src) return result;

  const lines = src.split(/\r?\n/);
  for (const line of lines) {
    if (!line || /^\s*#/.test(line)) continue;
    const match = line.match(/^(\w+)\s*=\s*(.*)$/);
    if (!match) continue;
    const key = match[1];
    let value = match[2] ?? '';
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    result[key] = value;
  }

  return result;
}

function config(options = {}) {
  const envPath = options.path || path.resolve(process.cwd(), '.env');
  const encoding = options.encoding || 'utf8';

  if (!fs.existsSync(envPath)) {
    return { parsed: undefined }; // mimic dotenv behavior when file missing
  }

  const parsed = parse(fs.readFileSync(envPath, { encoding }));

  for (const [key, value] of Object.entries(parsed)) {
    if (process.env[key] === undefined) {
      process.env[key] = value;
    }
  }

  return { parsed };
}

export { config, parse };
export default { config, parse };
