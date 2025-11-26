import { JsonRpcHttpClient } from "../rpc/http";

export interface RandomnessClientOptions {
  url: string;
  chainId?: number | string;
}

export class RandomnessClient {
  private rpc: JsonRpcHttpClient;

  constructor(opts: RandomnessClientOptions) {
    this.rpc = new JsonRpcHttpClient(opts.url);
  }

  async getRound(): Promise<any> {
    return this.rpc.request("rand.getRound");
  }

  async getBeacon(): Promise<any> {
    return this.rpc.request("rand.getBeacon");
  }

  async getHistory(params: { offset?: number; limit?: number }): Promise<any[]> {
    return this.rpc.request("rand.getHistory", [params]);
  }

  async commit(args: { from: string; salt: string; payload: string; signature?: string }): Promise<any> {
    return this.rpc.request("rand.commit", [args]);
  }

  async reveal(args: { from: string; salt: string; payload: string; signature?: string }): Promise<any> {
    return this.rpc.request("rand.reveal", [args]);
  }
}
