import { afterEach, describe, expect, it } from "vitest";
import { createServer } from "http";
import { getHostPort } from "../config/http.js";

const originalEnv = { ...process.env };

afterEach(() => {
  for (const key of Object.keys(process.env)) {
    if (!(key in originalEnv)) {
      delete process.env[key];
    }
  }
  Object.assign(process.env, originalEnv);
});

describe("getHostPort", () => {
  it("returns defaults when PORT is missing", () => {
    const result = getHostPort({}, { defaultPort: 4000 });
    expect(result).toEqual({ HOST: "0.0.0.0", PORT: 4000 });
  });

  it("honors PORT and HOST overrides", () => {
    const result = getHostPort({ HOST: "127.0.0.1", PORT: "5123" }, { defaultPort: 4000 });
    expect(result).toEqual({ HOST: "127.0.0.1", PORT: 5123 });
  });

  it("rejects invalid ports", () => {
    expect(() => getHostPort({ PORT: "0" }, { defaultPort: 4000 })).toThrow(
      /Invalid host\/port configuration/
    );
  });

  it("can start a server on an ephemeral port", async () => {
    const { HOST, PORT } = getHostPort({ PORT: "0" }, { defaultPort: 4000 });
    const server = createServer((_req, res) => {
      res.writeHead(200);
      res.end("ok");
    });

    await new Promise<void>((resolve) => {
      server.listen(PORT, HOST, () => resolve());
    });

    const address = server.address();
    expect(address && typeof address !== "string" && address.port).toBeGreaterThan(0);

    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  });
});
