import { NavLink } from "react-router-dom";

const links = [
  { to: "/bridge", label: "Bridge" },
  { to: "/proof-of-reserves", label: "Solvency" },
  { to: "/faq", label: "FAQ" },
  { to: "/risk", label: "Risk" },
  { to: "/terms", label: "Terms" }
];

export function Header() {
  return (
    <header className="top-nav">
      <div className="top-nav-inner">
        <NavLink to="/" style={{ fontWeight: 700, letterSpacing: 0 }}>
          BANM Custodial Bridge
        </NavLink>
        <nav className="nav-links">
          {links.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}

