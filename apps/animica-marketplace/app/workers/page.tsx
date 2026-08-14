import type { Metadata } from 'next';
import WorkersClient from './WorkersClient';

export const metadata: Metadata = {
  title: 'Animica Workers',
  description:
    'A normal agent responds when you ask. A Worker works on schedule. Autonomous AI Workers on Animica: scheduled prompts, webhooks, .anm publishing, execution history and hard safety rails.',
  alternates: { canonical: 'https://animica.dev/workers' },
  openGraph: {
    title: 'Animica Workers',
    description: 'A normal agent responds when you ask. A Worker works on schedule.',
    url: 'https://animica.dev/workers',
    siteName: 'Animica',
    type: 'website',
  },
};

export const dynamic = 'force-dynamic';

export default function WorkersPage() {
  return <WorkersClient />;
}
