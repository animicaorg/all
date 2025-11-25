export type ClientOptions = { rpcUrl?: string };
export class AnimicaClient {
  #h = 0;
  constructor(public opts: ClientOptions = {}) {}
  async getChainId(){ return "0xa11ca"; }
  async getBlockNumber(){ return ++this.#h; }
}
export const createClient = (o?: ClientOptions) => new AnimicaClient(o);
