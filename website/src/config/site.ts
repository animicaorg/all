import { ENV } from '../env';

export type NavItem = {
  label: string;
  href: string;
  external?: boolean;
  rel?: string;
  target?: '_blank' | '_self';
};

export type NavSection = {
  title?: string;
  items: NavItem[];
};

export type Brand = {
  name: string;
  tagline: string;
  logo: {
    mark: string;
    wordmark: string;
  };
  theme: {
    color: string;
    bg: string;
  };
};

export type Social = {
  x?: string;
  github?: string;
  discord?: string;
  telegram?: string;
};

export type Contact = {
  email: string;
  securityTxt: string;
  securityPolicy: string;
  acknowledgments: string;
};

export type SiteConfig = {
  brand: Brand;
  urls: {
    site: string;
    docs: string;
    explorer: string;
    explorer2?: string;
    rpc: string;
    github: string;
    faucet?: string;
    pool?: string;
  };
  nav: {
    top: NavItem[];
    footer: NavSection[];
  };
  contact: Contact;
  social: Social;
  i18n: {
    defaultLocale: 'en';
    locales: Array<'en' | 'es'>;
  };
  meta: {
    title: string;
    description: string;
    ogImage: string;
  };
};

export const SITE: SiteConfig = {
  brand: {
    name: 'Animica',
    tagline: 'Post-quantum blockchain for verifiable, real-world compute.',
    logo: {
      mark: '/icons/logo.svg',
      wordmark: '/icons/wordmark.svg',
    },
    theme: {
      color: '#16d9c3',
      bg: '#070b16',
    },
  },

  urls: {
    site: (import.meta.env.SITE_URL as string) || 'https://animica.org',
    docs: ENV.DOCS_URL,
    explorer: ENV.EXPLORER_URL,
    explorer2: ENV.EXPLORER2_URL,
    rpc: ENV.RPC_URL,
    github: ENV.GITHUB_URL,
    faucet: ENV.FAUCET_URL,
    pool: ENV.POOL_URL,
  },

  nav: {
    top: [
      { label: 'ENA', href: '/ena' },
      { label: 'Developers', href: '/developers' },
      { label: 'Mine', href: '/mine' },
      { label: 'Wallet', href: '/wallet' },
      { label: 'Network', href: '/network' },
      { label: 'Docs', href: '/docs' },
      { label: 'Status', href: '/status' },
    ],
    footer: [
      {
        title: 'Platform',
        items: [
          { label: 'Developers', href: '/developers' },
          { label: 'Providers', href: '/providers' },
          { label: 'Wallet', href: '/wallet' },
          { label: 'Mine', href: '/mine' },
          { label: 'Downloads', href: '/downloads' },
          { label: 'Compute Pricing', href: '/compute-pricing' },
        ],
      },
      {
        title: 'Network',
        items: [
          { label: 'Network', href: '/network' },
          { label: 'Status', href: '/status' },
          { label: 'Explorer', href: '/explorer' },
          { label: 'RPC', href: '/developers#rpc' },
        ],
      },
      {
        title: 'Resources',
        items: [
          { label: 'Docs Hub', href: '/docs' },
          { label: 'About Animica', href: '/about' },
          { label: 'FAQ', href: '/faq' },
          { label: 'Support', href: '/support' },
        ],
      },
      {
        title: 'Legal',
        items: [
          { label: 'Privacy', href: '/privacy' },
          { label: 'Terms', href: '/terms' },
          { label: 'Security', href: '/security' },
          { label: 'GitHub', href: ENV.GITHUB_URL, external: true, target: '_blank', rel: 'noopener' },
        ],
      },
    ],
  },

  contact: {
    email: 'contact@animica.org',
    securityTxt: '/.well-known/security.txt',
    securityPolicy: '/security',
    acknowledgments: '/security/hall-of-fame',
  },

  social: {
    x: ENV.X_URL,
    github: ENV.GITHUB_URL,
    discord: ENV.DISCORD_URL,
    telegram: ENV.TELEGRAM_URL,
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'es'],
  },

  meta: {
    title: 'Animica — Post-Quantum Blockchain Infrastructure',
    description:
      'Animica is a production-ready post-quantum blockchain with useful-work consensus, deterministic Python contracts, node tooling, explorer infrastructure, and AI compute services.',
    ogImage: '/og/og-home.png',
  },
};

export const NAV = SITE.nav;
export const BRAND = SITE.brand;

export const site = {
  brand: SITE.brand.name,
  tagline: SITE.brand.tagline,
  description: SITE.meta.description,
  url: SITE.urls.site,
  contact: SITE.contact,
  links: SITE.social,
  meta: SITE.meta,
  nav: SITE.nav,
  theme: SITE.brand.theme,
};

export default site;
