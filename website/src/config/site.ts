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
    mark: string;     // /icons/logo.svg
    wordmark: string; // /icons/wordmark.svg
  };
  theme: {
    color: string;    // brand hex for meta/theme
    bg: string;       // background color
  };
};

export type Social = {
  twitter?: string;
  github?: string;
  discord?: string;
  youtube?: string;
  linkedin?: string;
};

export type Contact = {
  email: string;
  securityTxt: string;  // /.well-known/security.txt
  securityPolicy: string;
  acknowledgments: string;
};

export type SiteConfig = {
  brand: Brand;
  urls: {
    site: string;       // set via SITE_URL in env at build (astro.config)
    studio: string;
    explorer: string;
    docs: string;
    rpc: string;
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
    tagline: 'Post-quantum chain • Useful work • Python VM',
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
    studio: ENV.STUDIO_URL,
    explorer: ENV.EXPLORER_URL,
    docs: ENV.DOCS_URL,
    rpc: ENV.RPC_URL,
  },

  nav: {
    top: [
      { label: 'Home', href: '/' },
      { label: 'Developers', href: '/developers' },
      { label: 'Wallet', href: '/wallet' },
      { label: 'Explorer', href: ENV.EXPLORER_URL, external: true, target: '_blank', rel: 'noopener' },
      { label: 'Studio', href: ENV.STUDIO_URL, external: true, target: '_blank', rel: 'noopener' },
      { label: 'Docs', href: ENV.DOCS_URL, external: true, target: '_blank', rel: 'noopener' }
    ],
    footer: [
      {
        title: 'Build',
        items: [
          { label: 'Studio', href: ENV.STUDIO_URL, external: true, target: '_blank', rel: 'noopener' },
          { label: 'SDKs', href: '/sdks' },
          { label: 'OpenRPC', href: '/openrpc' }
        ]
      },
      {
        title: 'Network',
        items: [
          { label: 'Explorer', href: ENV.EXPLORER_URL, external: true, target: '_blank', rel: 'noopener' },
          { label: 'Status', href: '/status' },
          { label: 'RPC', href: ENV.RPC_URL, external: true }
        ]
      },
      {
        title: 'Company',
        items: [
          { label: 'About', href: '/about' },
          { label: 'Careers', href: '/careers' },
          { label: 'Press', href: '/press' }
        ]
      },
      {
        title: 'Legal',
        items: [
          { label: 'Privacy', href: '/privacy' },
          { label: 'Terms', href: '/terms' },
          { label: 'Security', href: '/security' }
        ]
      }
    ]
  },

  contact: {
    email: 'contact@animica.org',
    securityTxt: '/.well-known/security.txt',
    securityPolicy: '/security',
    acknowledgments: '/security/hall-of-fame'
  },

  social: {
    twitter: 'https://x.com/animica',
    github: 'https://github.com/animica-labs',
    discord: 'https://discord.gg/animica',
    youtube: 'https://www.youtube.com/@animica'
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'es']
  },

  meta: {
    title: 'Animica — Post-quantum chain with useful work & Python VM',
    description:
      'Animica is a post-quantum blockchain with Useful-Work consensus, Python smart contracts, and tooling for builders: wallet, explorer, studio, and SDKs.',
    ogImage: '/images/og/landing-light.png'
  }
};

export const NAV = SITE.nav;
export const BRAND = SITE.brand;

// Backward-compatible shape consumed by pages/components
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
