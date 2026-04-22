import { Panel } from '../components/Ui';

export function StaticMarketingPage({
  title,
  subtitle,
  bullets,
  cta
}: {
  title: string;
  subtitle: string;
  bullets: string[];
  cta?: string;
}) {
  return (
    <div className="stack">
      <Panel title={title} subtitle={subtitle}>
        <ul className="feature-list">
          {bullets.map((bullet) => (
            <li key={bullet}>{bullet}</li>
          ))}
        </ul>
        {cta ? <p className="muted">{cta}</p> : null}
      </Panel>
    </div>
  );
}
