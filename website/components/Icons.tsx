type IconProps = { size?: number; className?: string }

const Svg = ({ size = 18, className, children }: IconProps & { children: React.ReactNode }) => (
  <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
)

export const ArrowUpRight = (props: IconProps) => <Svg {...props}><path d="M7 17 17 7M7 7h10v10" /></Svg>
export const ArrowRight = (props: IconProps) => <Svg {...props}><path d="M5 12h14M13 6l6 6-6 6" /></Svg>
export const Check = (props: IconProps) => <Svg {...props}><path d="m5 12 4 4L19 6" /></Svg>
export const Lock = (props: IconProps) => <Svg {...props}><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></Svg>
export const Terminal = (props: IconProps) => <Svg {...props}><path d="m4 6 5 5-5 5M11 18h9"/></Svg>
export const GitBranch = (props: IconProps) => <Svg {...props}><circle cx="6" cy="5" r="2"/><circle cx="18" cy="7" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10M8 7h4a6 6 0 0 1 6 6v-4"/></Svg>
export const Gauge = (props: IconProps) => <Svg {...props}><path d="M4.9 19a9 9 0 1 1 14.2 0"/><path d="m12 12 4-4"/><path d="M12 4v2M4 13H2M22 13h-2"/></Svg>
export const Layers = (props: IconProps) => <Svg {...props}><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></Svg>
export const Eye = (props: IconProps) => <Svg {...props}><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></Svg>
export const Github = (props: IconProps) => <Svg {...props}><path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3.2-.4 6.5-1.6 6.5-7A5.5 5.5 0 0 0 19 3.7 5.1 5.1 0 0 0 18.9 0S17.7-.4 15 1.5a13.4 13.4 0 0 0-7 0C5.3-.4 4.1 0 4.1 0A5.1 5.1 0 0 0 4 3.7a5.5 5.5 0 0 0-1.5 3.8c0 5.4 3.3 6.6 6.5 7A4.8 4.8 0 0 0 8 18v4"/><path d="M8 19c-3 .9-3-1.5-4-2"/></Svg>
export const Book = (props: IconProps) => <Svg {...props}><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></Svg>
export const ZoomIn = (props: IconProps) => <Svg {...props}><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M11 8v6M8 11h6"/></Svg>
export const X = (props: IconProps) => <Svg {...props}><path d="M18 6 6 18M6 6l12 12"/></Svg>
export const Copy = (props: IconProps) => <Svg {...props}><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></Svg>
