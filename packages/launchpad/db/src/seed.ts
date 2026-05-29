import { PrismaClient, ProjectStatus, RiskLevel, WalletType, ActivityType } from "@prisma/client";
import { sha3_256 } from "@noble/hashes/sha3";
import { CATEGORIES, encodeAnimicaAddress } from "@launchpad/shared";

const prisma = new PrismaClient();

// Deterministic Animica address from a label — produces a real, bech32m-valid
// address so the launchpad's schema validation accepts seeded records. Uses
// the SPHINCS+ alg id (0x1002), which is what the node CLI defaults to.
function addressFromLabel(label: string): string {
  const digest = sha3_256(new TextEncoder().encode(`launchpad-seed:${label}`));
  const payload = new Uint8Array(34);
  payload[0] = 0x10; // alg_id 0x1002 (sphincs_shake_128s), big-endian
  payload[1] = 0x02;
  payload.set(digest, 2);
  return encodeAnimicaAddress(payload);
}

const CREATOR_ADDRESS = addressFromLabel("animica-core");
const ADMIN_ADDRESS =
  process.env.ANIMICA_LAUNCHPAD_ADMIN_WALLET || addressFromLabel("animica-admin");

/**
 * Real Animica ecosystem projects shown on the homepage. All fields reflect
 * publicly documented sub-projects under animica.org — no invented names.
 */
const PROJECTS: Array<{
  name: string;
  symbol: string;
  description: string;
  categorySlug: string;
  websiteUrl?: string;
  twitterUrl?: string;
  githubUrl?: string;
  docsUrl?: string;
  risk: RiskLevel;
  verified: boolean;
  featured: boolean;
  status: ProjectStatus;
}> = [
  {
    name: "Ena AI",
    symbol: "ENA",
    description:
      "Multimodal Animica-native agent built on the AICF compute mesh. Pay per inference in ANM; receipts settle on-chain.",
    categorySlug: "ai",
    websiteUrl: "https://animica.org",
    docsUrl: "https://animica.org/docs",
    risk: "LOW",
    verified: true,
    featured: true,
    status: "LIVE",
  },
  {
    name: "AICF Compute",
    symbol: "AICF",
    description:
      "Permissionless AI compute fabric: providers earn ANM for serving inference receipts validated against the chain.",
    categorySlug: "infra",
    websiteUrl: "https://aicf.animica.org",
    risk: "LOW",
    verified: true,
    featured: true,
    status: "LIVE",
  },
  {
    name: "Animica Stratum Pool",
    symbol: "POOL",
    description:
      "Open Stratum-style mining pool aggregating CPU, GPU and quantum miners. Payouts on-chain in ANM.",
    categorySlug: "infra",
    websiteUrl: "https://pool.animica.org",
    risk: "LOW",
    verified: true,
    featured: false,
    status: "LIVE",
  },
  {
    name: "BANM Bridge",
    symbol: "BANM",
    description:
      "Bridge between Animica L1 and BSC — mint BANM (ERC-20 wrapped ANM) on BSC, redeem 1:1 for native ANM.",
    categorySlug: "infra",
    websiteUrl: "https://animica.org",
    docsUrl: "https://animica.org/docs",
    risk: "MEDIUM",
    verified: true,
    featured: false,
    status: "LIVE",
  },
  {
    name: "Animica Studio",
    symbol: "STUDIO",
    description:
      "On-chain creative studio for the Animica ecosystem — mint, license and remix audio/visual assets natively.",
    categorySlug: "media",
    websiteUrl: "https://studio.animica.org",
    risk: "LOW",
    verified: true,
    featured: true,
    status: "LIVE",
  },
  {
    name: "USDAN",
    symbol: "USDAN",
    description:
      "Animica-native USD-denominated unit, backed by a treasury and exposed via on-chain receipts.",
    categorySlug: "defi",
    websiteUrl: "https://animica.org",
    risk: "MEDIUM",
    verified: true,
    featured: false,
    status: "LIVE",
  },
  {
    name: "Buy.animica.org",
    symbol: "BUY",
    description:
      "Fiat → ANM OTC desk. Stripe/PayPal in, native ANM out, settled by the node's hot wallet.",
    categorySlug: "tools",
    websiteUrl: "https://buy.animica.org",
    risk: "LOW",
    verified: true,
    featured: false,
    status: "LIVE",
  },
  {
    name: "Perbug Labs",
    symbol: "PERBUG",
    description:
      "Indexer & monitoring swarm for the Animica network. Earn ANM for accurate alerts and uptime probes.",
    categorySlug: "tools",
    websiteUrl: "https://perbug.com",
    risk: "MEDIUM",
    verified: false,
    featured: false,
    status: "LIVE",
  }
];

async function main() {
  // Categories
  for (const c of CATEGORIES) {
    await prisma.category.upsert({
      where: { slug: c.slug },
      update: { name: c.name },
      create: { slug: c.slug, name: c.name }
    });
  }

  // Demo creator (a real bech32m address, but not a funded on-chain account)
  const creator = await prisma.user.upsert({
    where: { primaryWalletAddress: CREATOR_ADDRESS },
    update: {},
    create: {
      primaryWalletAddress: CREATOR_ADDRESS,
      primaryWalletType: WalletType.ANIMICA_EXTENSION,
      username: "animica-core",
      bio: "Curated by the Animica core team"
    }
  });

  // Admin
  await prisma.user.upsert({
    where: { primaryWalletAddress: ADMIN_ADDRESS },
    update: { role: "ADMIN" },
    create: {
      primaryWalletAddress: ADMIN_ADDRESS,
      primaryWalletType: WalletType.ANIMICA_EXTENSION,
      username: "admin",
      role: "ADMIN"
    }
  });

  for (const p of PROJECTS) {
    const cat = await prisma.category.findUnique({ where: { slug: p.categorySlug } });
    const slug = p.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    const existing = await prisma.project.findUnique({ where: { slug } });
    const data = {
      slug,
      name: p.name,
      symbol: p.symbol,
      description: p.description,
      websiteUrl: p.websiteUrl,
      twitterUrl: p.twitterUrl,
      githubUrl: p.githubUrl,
      docsUrl: p.docsUrl,
      categoryId: cat?.id,
      creatorWallet: creator.primaryWalletAddress,
      creatorUserId: creator.id,
      status: p.status,
      verified: p.verified,
      featured: p.featured,
      riskLevel: p.risk,
      holderCount: 0,
      commentCount: 0,
      followCount: 0,
      liquidityAnm: "0",
      volume24hAnm: "0",
      marketCap: "0",
      initialSupply: "1000000000",
      tags: []
    };
    const proj = existing
      ? await prisma.project.update({ where: { id: existing.id }, data })
      : await prisma.project.create({ data });

    await prisma.activity.create({
      data: {
        type: ActivityType.PROJECT_CREATED,
        projectId: proj.id,
        userId: creator.id,
        walletAddress: creator.primaryWalletAddress
      }
    });
    if (p.verified) {
      await prisma.activity.create({
        data: {
          type: ActivityType.VERIFIED,
          projectId: proj.id,
          userId: creator.id
        }
      });
    }
    if (p.featured) {
      await prisma.activity.create({
        data: {
          type: ActivityType.PROJECT_FEATURED,
          projectId: proj.id
        }
      });
    }
  }

  await prisma.platformSetting.upsert({
    where: { key: "launchFeeAnm" },
    update: {},
    create: { key: "launchFeeAnm", value: "0" }
  });
  await prisma.platformSetting.upsert({
    where: { key: "tradeFeeBps" },
    update: {},
    create: { key: "tradeFeeBps", value: "0" }
  });

  // eslint-disable-next-line no-console
  console.log(
    `Seeded ${PROJECTS.length} real Animica ecosystem projects.\n` +
      `  creator: ${CREATOR_ADDRESS}\n` +
      `  admin:   ${ADMIN_ADDRESS}`
  );
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
