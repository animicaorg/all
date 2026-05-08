import { useEffect } from 'react';
import { DEFAULT_OG_IMAGE, SITE_NAME, canonicalUrl } from '../lib/seo';

type SeoProps = {
  title: string;
  description: string;
  path: string;
  type?: string;
  image?: string;
  noindex?: boolean;
  structuredData?: object[];
};

function upsertMeta(selector: string, attrs: Record<string, string>, content: string) {
  let element = document.head.querySelector<HTMLMetaElement>(selector);
  if (!element) {
    element = document.createElement('meta');
    Object.entries(attrs).forEach(([key, value]) => element?.setAttribute(key, value));
    document.head.appendChild(element);
  }
  element.setAttribute('content', content);
}

function upsertCanonical(url: string) {
  let element = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!element) {
    element = document.createElement('link');
    element.setAttribute('rel', 'canonical');
    document.head.appendChild(element);
  }
  element.setAttribute('href', url);
}

export function Seo({
  title,
  description,
  path,
  type = 'website',
  image = DEFAULT_OG_IMAGE,
  noindex = false,
  structuredData = [],
}: SeoProps) {
  useEffect(() => {
    const url = canonicalUrl(path);
    document.title = title;

    upsertMeta('meta[name="description"]', { name: 'description' }, description);
    upsertMeta('meta[name="robots"]', { name: 'robots' }, noindex ? 'noindex,nofollow' : 'index,follow');
    upsertMeta('meta[property="og:site_name"]', { property: 'og:site_name' }, SITE_NAME);
    upsertMeta('meta[property="og:type"]', { property: 'og:type' }, type);
    upsertMeta('meta[property="og:title"]', { property: 'og:title' }, title);
    upsertMeta('meta[property="og:description"]', { property: 'og:description' }, description);
    upsertMeta('meta[property="og:url"]', { property: 'og:url' }, url);
    upsertMeta('meta[property="og:image"]', { property: 'og:image' }, image);
    upsertMeta('meta[name="twitter:card"]', { name: 'twitter:card' }, 'summary_large_image');
    upsertMeta('meta[name="twitter:title"]', { name: 'twitter:title' }, title);
    upsertMeta('meta[name="twitter:description"]', { name: 'twitter:description' }, description);
    upsertMeta('meta[name="twitter:image"]', { name: 'twitter:image' }, image);
    upsertCanonical(url);

    document.head.querySelectorAll('script[data-seo-jsonld="true"]').forEach((node) => node.remove());
    structuredData.forEach((entry) => {
      const script = document.createElement('script');
      script.type = 'application/ld+json';
      script.dataset.seoJsonld = 'true';
      script.text = JSON.stringify(entry);
      document.head.appendChild(script);
    });
  }, [description, image, noindex, path, structuredData, title, type]);

  return null;
}
