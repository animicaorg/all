import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(__dirname, '..');
const distRoot = path.join(appRoot, 'dist');
const siteUrl = 'https://trade.animica.org';
const image = `${siteUrl}/og-default.svg`;

const pairPages = [
  ['BTC-ANM', 'Bitcoin'],
  ['DOGE-ANM', 'Dogecoin'],
  ['LTC-ANM', 'Litecoin'],
  ['ZEC-ANM', 'Zcash'],
].map(([symbol, assetName]) => {
  const [base, quote] = symbol.split('-');
  return {
    path: `/trade/${symbol}`,
    title: `${base}/${quote} Trading | Trade ${quote} on Animica Exchange`,
    description: `Trade ${quote} against ${assetName} on Animica Exchange. View the live ${base}/${quote} market, order book, recent trades, and account-ready trading tools.`,
    jsonLd: [
      breadcrumb([
        ['Home', '/'],
        ['Markets', '/markets'],
        [symbol, `/trade/${symbol}`],
      ]),
      faq([
        [
          `What is the ${base}/${quote} market?`,
          `${base}/${quote} is an Animica Exchange market for trading ANM against ${assetName}.`,
        ],
        [
          `Do I need an account to trade ${base}/${quote}?`,
          `You can view the ${base}/${quote} market without logging in. Creating orders, claiming ANM, deposits, withdrawals, and balances require an account.`,
        ],
      ]),
    ],
  };
});

const pages = [
  {
    path: '/',
    title: 'Animica Exchange | Trade ANM, Claim ANM, Explore Live Markets',
    description:
      'Animica Exchange is a live ANM exchange for BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM trading, ANM claims, deposits, API keys, and exchange tools.',
    jsonLd: [
      {
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        name: 'Animica Exchange',
        url: siteUrl,
        potentialAction: {
          '@type': 'SearchAction',
          target: `${siteUrl}/markets?search={search_term_string}`,
          'query-input': 'required name=search_term_string',
        },
      },
      {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        name: 'Animica Exchange',
        url: siteUrl,
        logo: `${siteUrl}/animica-mark.svg`,
      },
      faq([
        [
          'What is Animica Exchange?',
          'Animica Exchange is a live exchange interface for ANM markets, account balances, deposits, withdrawals, airdrop claims, API keys, and built-in trading bot controls.',
        ],
        ['Which ANM trading pairs are available?', 'Animica Exchange has public pages for BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM markets.'],
        ['Can I claim ANM?', 'The exchange includes an account-based ANM airdrop claim flow when the airdrop is enabled and funded.'],
      ]),
    ],
  },
  {
    path: '/markets',
    title: 'ANM Markets | Trade BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM',
    description:
      'Explore live Animica Exchange markets for ANM trading pairs including BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM.',
    jsonLd: [breadcrumb([['Home', '/'], ['Markets', '/markets']])],
  },
  {
    path: '/anm',
    title: 'ANM Exchange | Trade Animica on Live ANM Markets',
    description:
      'Explore Animica Exchange, a live ANM exchange where users can trade ANM against BTC, DOGE, LTC, and ZEC, claim ANM, and follow the Animica ecosystem.',
    jsonLd: infoJsonLd('ANM Exchange', '/anm'),
  },
  {
    path: '/anm-markets',
    title: 'ANM Markets | BTC, DOGE, LTC, and ZEC Trading Pairs',
    description:
      'View live ANM markets on Animica Exchange, including BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM trading pages.',
    jsonLd: infoJsonLd('ANM Markets', '/anm-markets'),
  },
  ...pairPages,
  {
    path: '/airdrop',
    title: 'Claim ANM | Animica Exchange Airdrop',
    description:
      'Claim ANM through the Animica Exchange airdrop flow, then explore live ANM markets and trading tools.',
    jsonLd: infoJsonLd('Claim ANM', '/airdrop'),
  },
  {
    path: '/fees',
    title: 'Animica Exchange Fees | Trading, Deposits, and Withdrawals',
    description:
      'Review how Animica Exchange presents trading fee estimates, deposits, withdrawals, and market-specific order settings.',
    jsonLd: infoJsonLd('Fees and Trading Costs', '/fees'),
  },
  {
    path: '/how-it-works',
    title: 'How Animica Exchange Works | Accounts, Markets, and ANM Trading',
    description:
      'Learn how to create an Animica Exchange account, claim ANM, deposit supported assets, and trade ANM markets.',
    jsonLd: infoJsonLd('How Animica Exchange Works', '/how-it-works'),
  },
  {
    path: '/security',
    title: 'Animica Exchange Security | Account and API Key Controls',
    description:
      'Understand Animica Exchange security controls including account sessions, scoped API keys, transfer rails, and authenticated trading actions.',
    jsonLd: infoJsonLd('Security at Animica Exchange', '/security'),
  },
  {
    path: '/about',
    title: 'About Animica Exchange | Live ANM Trading Platform',
    description:
      'Animica Exchange is a live exchange interface for ANM trading, claim flows, deposits, withdrawals, API keys, and automation tools.',
    jsonLd: infoJsonLd('About Animica Exchange', '/about'),
  },
  {
    path: '/legal',
    title: 'Legal Disclaimer and Risk Warning | Animica Exchange',
    description:
      'Read the Animica Exchange legal disclaimer and digital asset risk warning before using ANM markets or exchange account features.',
    jsonLd: [breadcrumb([['Home', '/'], ['Legal Disclaimer', '/legal']])],
  },
];

function canonical(routePath) {
  return `${siteUrl}${routePath === '/' ? '' : routePath}`;
}

function breadcrumb(items) {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map(([name, itemPath], index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name,
      item: canonical(itemPath),
    })),
  };
}

function faq(items) {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: items.map(([question, answer]) => ({
      '@type': 'Question',
      name: question,
      acceptedAnswer: {
        '@type': 'Answer',
        text: answer,
      },
    })),
  };
}

function infoJsonLd(name, itemPath) {
  return [
    breadcrumb([
      ['Home', '/'],
      [name, itemPath],
    ]),
  ];
}

function replaceTag(html, pattern, replacement) {
  return pattern.test(html) ? html.replace(pattern, replacement) : html.replace('</head>', `    ${replacement}\n  </head>`);
}

function renderPage(indexHtml, page) {
  const url = canonical(page.path);
  let html = indexHtml;
  html = html.replace(/<title>.*?<\/title>/, `<title>${escapeHtml(page.title)}</title>`);
  html = replaceTag(html, /<meta name="description" content="[^"]*"\s*\/?>/, `<meta name="description" content="${escapeAttr(page.description)}" />`);
  html = replaceTag(html, /<meta name="robots" content="[^"]*"\s*\/?>/, '<meta name="robots" content="index,follow" />');
  html = replaceTag(html, /<link rel="canonical" href="[^"]*"\s*\/?>/, `<link rel="canonical" href="${url}" />`);
  html = replaceTag(html, /<meta property="og:title" content="[^"]*"\s*\/?>/, `<meta property="og:title" content="${escapeAttr(page.title)}" />`);
  html = replaceTag(html, /<meta property="og:description" content="[^"]*"\s*\/?>/, `<meta property="og:description" content="${escapeAttr(page.description)}" />`);
  html = replaceTag(html, /<meta property="og:url" content="[^"]*"\s*\/?>/, `<meta property="og:url" content="${url}" />`);
  html = replaceTag(html, /<meta property="og:image" content="[^"]*"\s*\/?>/, `<meta property="og:image" content="${image}" />`);
  html = replaceTag(html, /<meta name="twitter:title" content="[^"]*"\s*\/?>/, `<meta name="twitter:title" content="${escapeAttr(page.title)}" />`);
  html = replaceTag(html, /<meta name="twitter:description" content="[^"]*"\s*\/?>/, `<meta name="twitter:description" content="${escapeAttr(page.description)}" />`);
  html = replaceTag(html, /<meta name="twitter:image" content="[^"]*"\s*\/?>/, `<meta name="twitter:image" content="${image}" />`);

  const jsonLd = page.jsonLd
    .map((entry) => `    <script type="application/ld+json">${JSON.stringify(entry)}</script>`)
    .join('\n');
  html = html.replace('</head>', `${jsonLd}\n  </head>`);
  return html;
}

function escapeHtml(value) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, '&quot;');
}

const indexHtml = await readFile(path.join(distRoot, 'index.html'), 'utf8');

for (const page of pages) {
  const output = renderPage(indexHtml, page);
  const target =
    page.path === '/'
      ? path.join(distRoot, 'index.html')
      : path.join(distRoot, page.path.replace(/^\//, ''), 'index.html');
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, output);
}

console.log(`Generated static metadata for ${pages.length} public routes.`);
