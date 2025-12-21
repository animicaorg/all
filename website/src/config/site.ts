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
    tagline: 'Post-quantum blockchain for durable, verifiable compute.',
    logo: {
      mark: '/icons/logo.svg',
      wordmark: '/icons/wordmark.svg',
    },
    theme: {
      color: '#0ea5e9',
      bg: '#0b0f1a',
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
      { label: 'Home', href: '/' },
      { label: 'Docs', href: '/docs' },
      { label: 'Explorer', href: '/explorer' },
      { label: 'Wallet', href: '/wallet' },
      { label: 'Run a Node', href: '/node' },
      { label: 'Mine', href: '/mine' },
      { label: 'Community', href: '/community' },
      { label: 'Status', href: '/status' },
      { label: 'Updates', href: '/updates' },
    ],
    footer: [
      {
        title: 'Network',
        items: [
          { label: 'Explorer', href: '/explorer' },
          ...(ENV.EXPLORER2_URL ? [{ label: 'Explorer 2', href: '/explorer#explorer-2' }] : []),
          { label: 'RPC', href: ENV.RPC_URL, external: true, target: '_blank', rel: 'noopener' },
          { label: 'Status', href: '/status' },
        ],
      },
      {
        title: 'Developers',
        items: [
          { label: 'Docs', href: '/docs' },
          { label: 'Run a Node', href: '/node' },
          { label: 'Mine', href: '/mine' },
          { label: 'GitHub', href: ENV.GITHUB_URL, external: true, target: '_blank', rel: 'noopener' },
        ],
      },
      {
        title: 'Community',
        items: [
          ...(ENV.DISCORD_URL ? [{ label: 'Discord', href: ENV.DISCORD_URL, external: true, target: '_blank', rel: 'noopener' }] : []),
          ...(ENV.TELEGRAM_URL ? [{ label: 'Telegram', href: ENV.TELEGRAM_URL, external: true, target: '_blank', rel: 'noopener' }] : []),
          ...(ENV.X_URL ? [{ label: 'X (Twitter)', href: ENV.X_URL, external: true, target: '_blank', rel: 'noopener' }] : []),
          { label: 'Updates', href: '/updates' },
        ],
      },
      {
        title: 'Legal',
        items: [
          { label: 'Privacy', href: '/privacy' },
          { label: 'Terms', href: '/terms' },
          { label: 'Security', href: '/security' },
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
    title: 'Animica — Post-quantum blockchain for real-world compute',
    description:
      'Animica is a post-quantum blockchain with useful-work consensus, deterministic Python VM, and production tooling for wallets, explorers, and node operators.',
    ogImage: '/images/og/landing-light.png',
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
