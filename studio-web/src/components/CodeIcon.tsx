import * as React from "react";

export type IconProps = React.SVGProps<SVGSVGElement> & {
  size?: number | string;
  title?: string;
  strokeWidth?: number;
};

/**
 * Base SVG wrapper used by all icons. Inherits currentColor.
 */
const IconBase = React.forwardRef<SVGSVGElement, IconProps>(
  ({ size = 20, className, title, strokeWidth = 1.75, children, ...rest }, ref) => (
    <svg
      ref={ref}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      role={title ? "img" : "presentation"}
      aria-label={title}
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...rest}
    >
      {children}
    </svg>
  )
);
IconBase.displayName = "IconBase";

/* Core UI icons */
export const Check = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}><path d="M20 6 9 17l-5-5"/></IconBase>
));
Check.displayName = "Check";

export const X = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}><path d="M18 6 6 18M6 6l12 12"/></IconBase>
));
X.displayName = "X";

export const Plus = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}><path d="M12 5v14M5 12h14"/></IconBase>
));
Plus.displayName = "Plus";

export const Minus = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}><path d="M5 12h14"/></IconBase>
));
Minus.displayName = "Minus";

export const Play = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}><path d="M7 5v14l12-7-12-7z"/></IconBase>
));
Play.displayName = "Play";

export const Pause = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}><path d="M8 5h3v14H8zM13 5h3v14h-3z"/></IconBase>
));
Pause.displayName = "Pause";

export const Refresh = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4"/>
  </IconBase>
));
Refresh.displayName = "Refresh";

export const Search = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <circle cx="11" cy="11" r="7"/><path d="M21 21l-3.6-3.6"/>
  </IconBase>
));
Search.displayName = "Search";

export const Copy = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <rect x="9" y="9" width="10" height="10" rx="2"/><rect x="5" y="5" width="10" height="10" rx="2"/>
  </IconBase>
));
Copy.displayName = "Copy";

export const ExternalLink = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <path d="M7 7h4M10 4h7v7M10 14l7-7"/><path d="M7 12v7h10"/>
  </IconBase>
));
ExternalLink.displayName = "ExternalLink";

export const ChevronRight = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}><path d="M9 6l6 6-6 6"/></IconBase>
));
ChevronRight.displayName = "ChevronRight";

export const ChevronDown = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}><path d="M6 9l6 6 6-6"/></IconBase>
));
ChevronDown.displayName = "ChevronDown";

/* Domain-specific icons */
export const Blocks = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <rect x="3" y="3" width="8" height="8" rx="1.5"/>
    <rect x="13" y="3" width="8" height="8" rx="1.5"/>
    <rect x="3" y="13" width="8" height="8" rx="1.5"/>
    <rect x="13" y="13" width="8" height="8" rx="1.5"/>
  </IconBase>
));
Blocks.displayName = "Blocks";

export const Tx = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <path d="M4 7h10M9 3l4 4-4 4"/><rect x="4" y="14" width="16" height="6" rx="2"/>
  </IconBase>
));
Tx.displayName = "Tx";

export const Wallet = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <rect x="3" y="6" width="18" height="12" rx="3"/>
    <path d="M16 12h4"/>
  </IconBase>
));
Wallet.displayName = "Wallet";

export const Contract = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>
    <path d="M10 14h6M10 18h6"/>
  </IconBase>
));
Contract.displayName = "Contract";

export const ABI = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <circle cx="7.5" cy="8" r="2.5"/><circle cx="16.5" cy="8" r="2.5"/><path d="M5 16h14"/>
  </IconBase>
));
ABI.displayName = "ABI";

export const AI = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <circle cx="12" cy="12" r="4.5"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3.5 3.5M15.5 15.5 19 19M19 5l-3.5 3.5M5 19l3.5-3.5"/>
  </IconBase>
));
AI.displayName = "AI";

export const Quantum = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <ellipse cx="12" cy="12" rx="8" ry="3.5"/>
    <ellipse cx="12" cy="12" rx="3.5" ry="8" transform="rotate(45 12 12)"/>
    <ellipse cx="12" cy="12" rx="3.5" ry="8" transform="rotate(-45 12 12)"/>
    <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>
  </IconBase>
));
Quantum.displayName = "Quantum";

export const DataAvailability = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <rect x="3" y="5" width="18" height="6" rx="2"/>
    <rect x="3" y="13" width="18" height="6" rx="2"/>
    <path d="M8 8h.01M12 8h.01M16 8h.01M8 16h.01M12 16h.01M16 16h.01"/>
  </IconBase>
));
DataAvailability.displayName = "DataAvailability";

export const Beacon = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <path d="M12 20v-6"/><circle cx="12" cy="8" r="2"/>
    <path d="M5 18a9 9 0 0 1 14 0M3 14a12 12 0 0 1 18 0"/>
  </IconBase>
));
Beacon.displayName = "Beacon";

export const Upload = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <path d="M12 16V4M7 9l5-5 5 5"/><rect x="4" y="16" width="16" height="4" rx="1.5"/>
  </IconBase>
));
Upload.displayName = "Upload";

export const Download = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <path d="M12 8v12M7 15l5 5 5-5"/><rect x="4" y="4" width="16" height="4" rx="1.5"/>
  </IconBase>
));
Download.displayName = "Download";

export const Link = React.forwardRef<SVGSVGElement, IconProps>((props, ref) => (
  <IconBase ref={ref} {...props}>
    <path d="M10 13a5 5 0 0 1 0-7l1.5-1.5a5 5 0 0 1 7 7L17 13"/>
    <path d="M14 11a5 5 0 0 1 0 7L12.5 19.5a5 5 0 0 1-7-7L7 11"/>
  </IconBase>
));
Link.displayName = "Link";

/** Optional map for dynamic usage */
export const Icons = {
  Check,
  X,
  Plus,
  Minus,
  Play,
  Pause,
  Refresh,
  Search,
  Copy,
  ExternalLink,
  ChevronRight,
  ChevronDown,
  Blocks,
  Tx,
  Wallet,
  Contract,
  ABI,
  AI,
  Quantum,
  DataAvailability,
  Beacon,
  Upload,
  Download,
  Link,
};

export default Icons;
