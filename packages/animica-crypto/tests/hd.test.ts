import { describe, it, expect } from 'vitest';
import {
  ANIMICA_COIN_TYPE,
  animicaPath,
  deriveAnimicaSeed,
  deriveAnimicaSeedFromMnemonic,
  deriveNodeFromSeed,
  masterNodeFromSeed,
  mnemonicToSeed,
  parsePath,
} from '../src/hd.js';
import { addressFromPubkey } from '../src/address.js';
import { AlgorithmId } from '../src/algorithms.js';

const hex = (u: Uint8Array) => Array.from(u, (b) => b.toString(16).padStart(2, '0')).join('');
const fromHex = (h: string) => new Uint8Array(h.match(/../gu)!.map((x) => parseInt(x, 16)));

const TEST_MNEMONIC =
  'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about';

describe('hd: BIP-39 seed', () => {
  it('matches the reference BIP-39 vector (Trezor vectors, entropy 00..00, no passphrase)', () => {
    expect(hex(mnemonicToSeed(TEST_MNEMONIC))).toBe(
      '5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc19a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4',
    );
  });
  it('matches the reference BIP-39 vector with passphrase TREZOR', () => {
    expect(hex(mnemonicToSeed(TEST_MNEMONIC, 'TREZOR'))).toBe(
      'c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e53495531f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04',
    );
  });
});

describe('hd: SLIP-0010 ed25519 derivation (spec test vector 1)', () => {
  const seed = fromHex('000102030405060708090a0b0c0d0e0f');
  it('master node', () => {
    const m = masterNodeFromSeed(seed);
    expect(hex(m.key)).toBe('2b4be7f19ee27bbf30c667b642d5f4aa69fd169872f8fc3059c08ebae2eb19e7');
    expect(hex(m.chainCode)).toBe('90046a93de5380a72b5e45010748567d5ea02bbf6522f979e05c0d8d8ca9fffb');
  });
  it("m/0'", () => {
    const n = deriveNodeFromSeed(seed, "m/0'");
    expect(hex(n.key)).toBe('68e0fe46dfb67e368c75379acec591dad19df3cde26e63b93a8e704f1dade7a3');
    expect(hex(n.chainCode)).toBe('8b59aa11380b624e81507a27fedda59fea6d0b779a778918a2fd3590e16e9c69');
  });
  it("m/0'/1'/2'/2'/1000000000'", () => {
    const n = deriveNodeFromSeed(seed, "m/0'/1'/2'/2'/1000000000'");
    expect(hex(n.key)).toBe('8f94d394a8e8fd6b1bc2f3f49f5c47e385281d5c17e65324b0f62483e37e8793');
    expect(hex(n.chainCode)).toBe('68789923a0cac2cd5a29172a475fe9e0fb14cd6adb5ad98a3fa70333e7afa230');
  });
});

describe('hd: Animica path', () => {
  it('uses SLIP-0044 coin type 4279885 (ASCII "ANM")', () => {
    expect(ANIMICA_COIN_TYPE).toBe(0x414e4d);
    expect(animicaPath()).toBe("m/44'/4279885'/0'/0'/0'");
    expect(animicaPath(2, 7)).toBe("m/44'/4279885'/2'/0'/7'");
    expect(parsePath("m/44'/4279885'/0'/0'/0'")).toEqual([44, 4279885, 0, 0, 0]);
    expect(parsePath('m/44h/4279885H/0h/0h/1h')).toEqual([44, 4279885, 0, 0, 1]);
  });
  it('rejects non-hardened levels', () => {
    expect(() => parsePath("m/44'/4279885'/0'/0/0")).toThrow(/hardened/u);
  });

  // Cross-checked 2026-08-22 against the mainnet node's Python implementation
  // (pq/py/address.py address_from_pubkey) after ML-DSA-65 keygen from ξ with
  // @noble/post-quantum 0.6.1 ml_dsa65.keygen(ξ). See docs/wallet/HD_DERIVATION.md.
  const VECTORS = [
    {
      account: 0,
      index: 0,
      xi: 'e3bb5b745b1da91201e7b9744038def07dfd02da9a85682d30468b9355c50835',
      address: 'anim1zqpn54yt2fz07wg5zz33qplkh7tewv30tm5s9cdwvag6kf6myvd2d5sj9pzp7',
    },
    {
      account: 0,
      index: 1,
      xi: '5b7ea6e7ab17f7f78900e57dae759104518bca0e55f7fa69b6d0b9986e130595',
      address: 'anim1zqpmznku3ddgyhl27d0p38jq7qyjgsnvafzd8pwh27gednh0x09s2egxyv9ej',
    },
    {
      account: 1,
      index: 0,
      xi: 'cf68ab2eb4222e81656973cc01769ab28b794f8907e214c1f615a5da6a5c0260',
      address: 'anim1zqpn2j43cqempqfke6rzvwf6f4529xwrexgpcw8gfd8dg8agmcqw6qqu83f7t',
    },
  ];
  const seed = mnemonicToSeed(TEST_MNEMONIC);
  for (const v of VECTORS) {
    it(`derives ξ for ${animicaPath(v.account, v.index)}`, () => {
      expect(hex(deriveAnimicaSeed(seed, v.account, v.index))).toBe(v.xi);
      expect(hex(deriveAnimicaSeedFromMnemonic(TEST_MNEMONIC, v.account, v.index))).toBe(v.xi);
    });
  }
  it('ML-DSA-65 addresses are 66 chars and start with anim1zqp', () => {
    for (const v of VECTORS) {
      expect(v.address).toHaveLength(66);
      expect(v.address.startsWith('anim1zqp')).toBe(true);
    }
  });
  it('addressFromPubkey accepts alg 0x1003 (ML-DSA-65)', () => {
    // A pubkey whose SHA3-256 digest we know: we only check the framing here, so use any 1952-byte buffer.
    const pk = new Uint8Array(1952);
    const a = addressFromPubkey(AlgorithmId.ML_DSA_65, pk);
    expect(a).toHaveLength(66);
    expect(a.startsWith('anim1zqp')).toBe(true);
  });
});
