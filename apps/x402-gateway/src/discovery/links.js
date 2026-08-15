'use strict';
/**
 * The identity facts every discovery surface repeats — catalog, landing
 * page, OpenAPI document, stats endpoint and the per-route 402 metadata —
 * derived from ONE place so they cannot drift apart.
 *
 * Nothing here is a claim: the network/asset facts are read out of the
 * gateway's own payment configuration (the same values that go into the
 * accepts array of every 402), and the URLs are built from
 * X402_RESOURCE_BASE_URL, which is the base a payer is already told to
 * trust (it is the host of every 402's resource.url).
 *
 * Prices live in the product registry and are NEVER restated here.
 */

const cfgMod = require('../config');

const PROVIDER = 'Animica';
const HOMEPAGE = 'https://animica.org';
const REPO = 'https://github.com/animicaorg/all';
// The dependency-free verifier the randomness products point at. Verified
// live (HTTP 200) on 2026-08-15 — it is the file the responses name in their
// own `verification.verifier` field, not a landing page for one.
const VERIFIER_URL = 'https://github.com/animicaorg/all/blob/main/randomness/beacon_api/static/verify.js';
const CONTACT = 'ai@3vdc.com';
const LICENSE = 'Apache-2.0';
const CATALOG_NAME = 'Animica x402';

/** `eip155:8453` -> 8453; null for anything that is not an eip155 CAIP-2 id. */
function chainIdOf(caip2) {
  const m = /^eip155:(\d+)$/.exec(String(caip2 || ''));
  return m ? Number(m[1]) : null;
}

/**
 * Network + asset facts for the lane this gateway actually offers. The asset
 * SYMBOL is only reported when the configured token address is the
 * allowlisted USDC contract for the configured chain — an operator who
 * points X402_USDC_ASSET at some other token gets `asset: null` rather than
 * a page that calls it USDC.
 */
function networkFacts(cfg) {
  const caip2 = cfg.networkEvm;
  const slug = cfgMod.V1_NETWORK_SLUGS[caip2] || null;
  const known = Object.values(cfgMod.EVM_NETWORKS).find((n) => n.caip2 === caip2) || null;
  const address = cfg.usdcAsset || null;
  const isKnownUsdc = Boolean(known && address
    && address.toLowerCase() === known.usdc.address.toLowerCase());
  return {
    payment_protocol: 'x402',
    x402_version: 2,
    scheme: 'exact',
    network: slug,
    network_caip2: caip2,
    chain_id: chainIdOf(caip2),
    asset: isKnownUsdc ? 'USDC' : null,
    asset_address: address,
    asset_decimals: isKnownUsdc ? known.usdc.decimals : null,
    pay_to: cfg.basePayTo || null,
    explorer_tx: known ? known.explorerTx : null,
  };
}

/** Human name for the configured chain; falls back to the CAIP-2 id. */
const NETWORK_DISPLAY = { base: 'Base', 'base-sepolia': 'Base Sepolia' };

function networkDisplayName(facts) {
  return NETWORK_DISPLAY[facts.network] || facts.network || facts.network_caip2;
}

/** Stable in-page anchor for a product (also the documentation URL fragment). */
function anchorFor(productId) {
  return `product-${String(productId).replace(/[^a-z0-9_-]/gi, '-')}`;
}

/**
 * Absolute, publicly meaningful URLs for every discovery surface. Built from
 * X402_RESOURCE_BASE_URL so the links a crawler follows are the same origin
 * the 402 offers name (production refuses to start with a loopback base —
 * see config.loadGatewayConfig).
 */
function links(cfg) {
  const base = String(cfg.resourceBaseUrl || '').replace(/\/+$/, '');
  return {
    base,
    landing: `${base}/x402`,
    catalog: `${base}/x402`,
    wellKnown: `${base}/.well-known/x402`,
    openapi: `${base}/x402/openapi.json`,
    stats: `${base}/x402/stats`,
    health: `${base}/x402/healthz`,
    docFor: (productId) => `${base}/x402#${anchorFor(productId)}`,
    urlFor: (routePath) => `${base}${routePath}`,
  };
}

/** The identity block shared by the catalog, the OpenAPI info and stats. */
function identity(cfg) {
  const l = links(cfg);
  return {
    name: CATALOG_NAME,
    provider: PROVIDER,
    service_name: cfg.serviceName,
    homepage: HOMEPAGE,
    gateway: l.landing,
    documentation: l.landing,
    repository: REPO,
    contact: CONTACT,
    license: LICENSE,
    discovery: {
      catalog: l.catalog,
      well_known: l.wellKnown,
      openapi: l.openapi,
      stats: l.stats,
    },
  };
}

module.exports = {
  PROVIDER, HOMEPAGE, REPO, CONTACT, LICENSE, CATALOG_NAME,
  chainIdOf, networkFacts, networkDisplayName, anchorFor, links, identity,
};
