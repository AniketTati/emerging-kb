"use client";

import {
  MessageSquare,
  Upload,
  Search,
  Layers,
  FlaskConical,
  SlidersHorizontal,
  LayoutDashboard,
  ScrollText,
  Settings,
  HelpCircle,
  type LucideIcon,
} from "lucide-react";

type NavKey =
  | "chat"
  | "upload"
  | "explore"
  | "schema"
  | "extraction"
  | "playground"
  | "dashboard"
  | "audit"
  | "settings";

type NavItem = { key: NavKey; href: string; icon: LucideIcon; label: string };

const PRIMARY: NavItem[] = [
  { key: "chat", href: "/chat", icon: MessageSquare, label: "Chat" },
  { key: "upload", href: "/upload", icon: Upload, label: "Upload" },
  { key: "explore", href: "/explore", icon: Search, label: "Explore" },
];

const STUDIO: NavItem[] = [
  { key: "schema", href: "/schema-studio", icon: Layers, label: "Schema" },
  { key: "extraction", href: "/extraction-studio", icon: FlaskConical, label: "Extraction" },
  { key: "playground", href: "/playground", icon: SlidersHorizontal, label: "Playground" },
];

const ADMIN: NavItem[] = [
  { key: "dashboard", href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { key: "audit", href: "/audit", icon: ScrollText, label: "Audit" },
];

function NavLink({ item, current }: { item: NavItem; current?: string }) {
  const active = item.key === current;
  const Icon = item.icon;
  return (
    <a
      href={item.href}
      className={`flex items-center gap-3 px-2.5 py-1.5 rounded-md text-sm transition-colors ${
        active
          ? "bg-zinc-100 text-zinc-900"
          : "text-zinc-700 hover:bg-zinc-50 hover:text-zinc-900"
      }`}
    >
      <Icon className="w-4 h-4 flex-shrink-0" strokeWidth={1.75} aria-hidden />
      <span className="label opacity-0 group-hover:opacity-100 transition-opacity duration-150 whitespace-nowrap">
        {item.label}
      </span>
    </a>
  );
}

function NavGroup({
  title,
  items,
  current,
}: {
  title: string;
  items: NavItem[];
  current?: string;
}) {
  return (
    <>
      <div className="px-3 mt-4 mb-1 text-[10px] uppercase tracking-wider text-zinc-400 opacity-0 group-hover:opacity-100 transition-opacity duration-150 whitespace-nowrap">
        {title}
      </div>
      <nav className="px-2 space-y-0.5">
        {items.map((item) => (
          <NavLink key={item.key} item={item} current={current} />
        ))}
      </nav>
    </>
  );
}

/**
 * 56px collapsed sidebar that expands to 220px on hover.
 * The `group` class on the <aside> drives `group-hover:opacity-100` on the
 * label spans so labels fade in only while the sidebar is wide.
 */
export function Sidebar({ current = "upload" }: { current?: string }) {
  return (
    <aside className="group sidebar w-[56px] hover:w-[220px] flex-shrink-0 bg-white border-r border-zinc-200 flex flex-col py-2 overflow-hidden transition-[width] duration-200 ease-out">
      {/* Logo */}
      <a href="/" className="px-3 mb-3 mt-1 flex items-center gap-3" aria-label="Emerging KB home">
        <div className="w-7 h-7 rounded-md bg-zinc-900 flex items-center justify-center text-white text-[12px] font-semibold tracking-tight flex-shrink-0">
          K
        </div>
        <div className="opacity-0 group-hover:opacity-100 transition-opacity duration-150 text-sm font-medium text-zinc-900 whitespace-nowrap">
          Emerging KB
        </div>
      </a>

      <NavGroup title="Primary" items={PRIMARY} current={current} />
      <NavGroup title="Studio" items={STUDIO} current={current} />
      <NavGroup title="Admin" items={ADMIN} current={current} />

      <div className="mt-auto px-2 pb-2 space-y-0.5">
        <a
          href="https://github.com/AniketTati/emerging-kb#readme"
          target="_blank"
          rel="noopener noreferrer"
          className="w-full flex items-center gap-3 px-2.5 py-1.5 rounded-md text-sm text-zinc-700 hover:bg-zinc-50 hover:text-zinc-900"
          aria-label="Help (opens README)"
        >
          <HelpCircle className="w-4 h-4 flex-shrink-0" strokeWidth={1.75} aria-hidden />
          <span className="opacity-0 group-hover:opacity-100 transition-opacity duration-150 whitespace-nowrap">
            Help
          </span>
        </a>
        <a
          href="/settings"
          className={`w-full flex items-center gap-3 px-2.5 py-1.5 rounded-md text-sm transition-colors ${
            current === "settings"
              ? "bg-zinc-100 text-zinc-900"
              : "text-zinc-700 hover:bg-zinc-50 hover:text-zinc-900"
          }`}
          aria-label="Settings"
        >
          <Settings className="w-4 h-4 flex-shrink-0" strokeWidth={1.75} aria-hidden />
          <span className="opacity-0 group-hover:opacity-100 transition-opacity duration-150 whitespace-nowrap">
            Settings
          </span>
        </a>
      </div>
    </aside>
  );
}
