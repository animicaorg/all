import { describe, expect, it, vi, beforeEach } from "vitest";
import axios from "axios";
import { BitGoClient } from "../bitgo/client.js";
import { createMockLogger } from "./helpers.js";

vi.mock("axios", () => ({
  default: {
    request: vi.fn(),
  },
}));

describe("BitGoClient", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each([
    ["btc", "wallet-btc"],
    ["ltc", "wallet-ltc"],
    ["doge", "wallet-doge"],
    ["zec", "wallet-zec"],
  ])("uses the coin-scoped sendcoins endpoint for %s", async (coin, walletId) => {
    vi.mocked(axios.request).mockResolvedValueOnce({
      status: 200,
      data: {
        transfer: {
          id: `transfer-${coin}`,
          coin,
          wallet: walletId,
          state: "signed",
          value: "1000",
          valueString: "1000",
          entries: [],
          createdDate: new Date().toISOString(),
        },
      },
    });

    const client = new BitGoClient(
      {
        getConfig: async () => ({
          baseUrl: "https://app.bitgo-test.com",
          expressUrl: "http://127.0.0.1:3080",
          accessToken: "token",
          walletPassphrase: "passphrase",
        }),
      },
      createMockLogger()
    );

    await client.createTransfer(coin, walletId, {
      amount: "1000",
      address: "destination",
      sequenceId: `withdrawal-${coin}`,
    });

    expect(axios.request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "post",
        baseURL: "http://127.0.0.1:3080",
        url: `/api/v2/${coin}/wallet/${walletId}/sendcoins`,
        data: expect.objectContaining({
          amount: "1000",
          address: "destination",
          sequenceId: `withdrawal-${coin}`,
          walletPassphrase: "passphrase",
        }),
      })
    );
  });

  it("fails clearly if sendcoins is pointed at the hosted BitGo API", async () => {
    const client = new BitGoClient(
      {
        getConfig: async () => ({
          baseUrl: "https://app.bitgo.com",
          accessToken: "token",
        }),
      },
      createMockLogger()
    );

    await expect(
      client.createTransfer("ltc", "wallet-ltc", {
        amount: "1000",
        address: "destination",
      })
    ).rejects.toThrow("BITGO_EXPRESS_URL is required for BitGo withdrawals");

    expect(axios.request).not.toHaveBeenCalled();
  });

  it("uses the coin-scoped transfer lookup endpoint", async () => {
    vi.mocked(axios.request).mockResolvedValueOnce({
      status: 200,
      data: {
        transfer: {
          id: "transfer-1",
          coin: "ltc",
          wallet: "wallet-ltc",
          state: "confirmed",
          value: "1000",
          valueString: "1000",
          entries: [],
          createdDate: new Date().toISOString(),
        },
      },
    });

    const client = new BitGoClient(
      {
        getConfig: async () => ({
          baseUrl: "https://bitgo.test",
          accessToken: "token",
        }),
      },
      createMockLogger()
    );

    await client.getTransfer("ltc", "wallet-ltc", "transfer-1");

    expect(axios.request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "get",
        baseURL: "https://bitgo.test",
        url: "/api/v2/ltc/wallet/wallet-ltc/transfer/transfer-1",
      })
    );
  });

  it("uses the coin-scoped transfer cancel endpoint", async () => {
    vi.mocked(axios.request).mockResolvedValueOnce({
      status: 204,
      data: undefined,
    });

    const client = new BitGoClient(
      {
        getConfig: async () => ({
          baseUrl: "https://bitgo.test",
          accessToken: "token",
        }),
      },
      createMockLogger()
    );

    await client.cancelTransfer("zec", "wallet-zec", "transfer-1");

    expect(axios.request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "delete",
        baseURL: "https://bitgo.test",
        url: "/api/v2/zec/wallet/wallet-zec/transfer/transfer-1",
      })
    );
  });
});
