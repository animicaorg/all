import { redirect } from 'next/navigation';

// The site now leads with Animica Python Cloud's app directory. The legacy AI marketplace
// home (/marketplace) is itself a permanent redirect here-adjacent — sending / straight to
// /apps skips the double hop.
export default function Home() {
  redirect('/apps');
}
