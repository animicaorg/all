import type { Metadata } from 'next';
import DevGate from './DevGate';

// Developer Center — the App Store publisher console. The gate (client) handles wallet
// challenge-login and only renders its children for a devportal-scoped session.

export const metadata: Metadata = {
  title: 'Developer Center — Animica App Store',
  description: 'Publish and monetize apps on the Animica App Store: listings, signed APK builds, prices in ANM, earnings, and API keys.',
};

export default function DevLayout({ children }: { children: React.ReactNode }) {
  return <DevGate>{children}</DevGate>;
}
