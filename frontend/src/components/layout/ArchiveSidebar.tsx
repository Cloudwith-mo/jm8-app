import {
  Archive,
  BarChart3,
  CalendarDays,
  ChevronDown,
  CloudUpload,
  FileText,
  History,
  House,
  ImagePlus,
  MessageCircle,
  Sparkles,
} from "lucide-react";
import type {
  LucideIcon,
} from "lucide-react";
import type { JournalEntry } from "../../types/journal";
import type { AuthUser } from "../../auth/cognito";
import { BrandMark } from "../ui/V2Primitives";

export type ArchiveSection =
  | "home"
  | "archive"
  | "insights"
  | "insightsTrends"
  | "reports"
  | "askJm8"
  | "ocrJobs"
  | "analysisJobs";

type ArchiveSidebarProps = {
  user: AuthUser | null;
  entries: JournalEntry[];
  activeSection: ArchiveSection;
  onNavigate: (
    section: ArchiveSection
  ) => void;
  onNewEntry: () => void;
  onUpload: () => void;
};

type SidebarNavItem = {
  label: string;
  icon: LucideIcon;
  section: ArchiveSection;
};

const navItems: SidebarNavItem[] = [
  {
    label: "Home",
    icon: House,
    section: "home",
  },
  {
    label: "Archive",
    icon: Archive,
    section: "archive",
  },
  {
    label: "Insights",
    icon: BarChart3,
    section: "insights",
  },
  {
    label: "Themes",
    icon: Sparkles,
    section: "insightsTrends",
  },
  {
    label: "Reports",
    icon: CalendarDays,
    section: "reports",
  },
  {
    label: "Ask JM8",
    icon: MessageCircle,
    section: "askJm8",
  },
  {
    label: "OCR Jobs",
    icon: FileText,
    section: "ocrJobs",
  },
  {
    label: "Analysis Jobs",
    icon: History,
    section: "analysisJobs",
  },
];

function getDisplayName(user: AuthUser | null) {
  if (user?.name?.trim()) return user.name.trim();
  if (user?.email) return user.email;
  return "JM8 member";
}

function getInitial(user: AuthUser | null) {
  return getDisplayName(user).charAt(0).toUpperCase();
}

export default function ArchiveSidebar({
  user,
  entries,
  activeSection,
  onNavigate,
  onNewEntry,
  onUpload,
}: ArchiveSidebarProps) {
  const displayName = getDisplayName(user);
  const emailOrHandle = user?.name && user.email ? user.email : "Authenticated account";
  const ocrJobs = entries.filter((entry) => entry.sourceType === "image").length;

  return (
    <aside className="archive-sidebar">
      <div className="brand-wordmark">
        <BrandMark large />
      </div>

      <nav className="archive-nav">
        {navItems.map((item) => {
          const Icon = item.icon;

          const isActive =
            item.section ===
            activeSection;
          return (
            <button
              key={item.label}
              className={
                isActive
                  ? "archive-nav-item active"
                  : "archive-nav-item"
              }
              onClick={() => {
                onNavigate(
                  item.section
                );
              }}
              aria-current={
                isActive
                  ? "page"
                  : undefined
              }
            >
              <Icon size={19} />
              <span>{item.label}</span>

              {item.label ===
                "OCR Jobs" &&
                ocrJobs > 0 && (
                  <em>{ocrJobs}</em>
                )}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-actions">
        <button className="primary-sidebar-action" onClick={onNewEntry}>
          <ImagePlus size={18} />
          New Entry
        </button>

        <button className="secondary-sidebar-action" onClick={onUpload}>
          <CloudUpload size={18} />
          Upload Journal Page
        </button>
      </div>

      <footer className="sidebar-identity" aria-label={`Signed in as ${displayName}`}>
        <div className="profile-avatar" aria-hidden="true">{getInitial(user)}</div>
        <div className="profile-copy">
          <strong>{displayName}</strong>
          <span>{emailOrHandle}</span>
        </div>
        <ChevronDown size={16} aria-hidden="true" />
      </footer>
    </aside>
  );
}
