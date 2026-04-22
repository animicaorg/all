import { Panel } from '../components/Ui';

export function TextOpsPage({
  title,
  subtitle,
  lines
}: {
  title: string;
  subtitle: string;
  lines: string[];
}) {
  return (
    <Panel title={title} subtitle={subtitle}>
      <ul className="feature-list">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </Panel>
  );
}
