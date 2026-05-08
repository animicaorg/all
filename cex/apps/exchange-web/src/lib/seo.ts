export const SITE_URL = 'https://trade.animica.org';
export const SITE_NAME = 'Animica Exchange';
export const DEFAULT_OG_IMAGE = `${SITE_URL}/og-default.svg`;

export const majorPairs = ['BTC-ANM', 'DOGE-ANM', 'LTC-ANM', 'ZEC-ANM'] as const;

export type MajorPair = (typeof majorPairs)[number];

export const pairLabels: Record<string, string> = {
  BNB: 'BNB',
  BTC: 'Bitcoin',
  DOGE: 'Dogecoin',
  LTC: 'Litecoin',
  USDT: 'Tether USD',
  ZEC: 'Zcash',
  ANM: 'Animica',
};

export function canonicalUrl(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${SITE_URL}${normalized === '/' ? '' : normalized}`;
}

export function pairMeta(symbol: string) {
  const normalized = symbol.toUpperCase();
  const [base = '', quote = 'ANM'] = normalized.split('-');

  return {
    title: `${base}/${quote} Trading | Trade ${base} on Animica Exchange`,
    description: `Trade ${base} against ${quote} on Animica Exchange. View the live ${base}/${quote} market, order book, recent trades, and account-ready trading tools.`,
    path: `/trade/${normalized}`,
  };
}

export function breadcrumbJsonLd(items: Array<{ name: string; path: string }>) {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name: item.name,
      item: canonicalUrl(item.path),
    })),
  };
}

export function faqJsonLd(faqs: Array<{ question: string; answer: string }>) {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faqs.map((faq) => ({
      '@type': 'Question',
      name: faq.question,
      acceptedAnswer: {
        '@type': 'Answer',
        text: faq.answer,
      },
    })),
  };
}

export function websiteJsonLd() {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    name: SITE_NAME,
    url: SITE_URL,
    potentialAction: {
      '@type': 'SearchAction',
      target: `${SITE_URL}/markets?search={search_term_string}`,
      'query-input': 'required name=search_term_string',
    },
  };
}

export function organizationJsonLd() {
  return {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    name: SITE_NAME,
    url: SITE_URL,
    logo: `${SITE_URL}/animica-mark.svg`,
  };
}
