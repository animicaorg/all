import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="card">
      <h2>Page Not Found</h2>
      <Link to="/">Back to Overview</Link>
    </section>
  );
}
