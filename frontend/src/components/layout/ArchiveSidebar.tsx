import {
  Archive,
  BarChart3,
  CalendarDays,
  CloudUpload,
  FileText,
  History,
  ImagePlus,
  MessageCircle,
  Moon,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import type {
  LucideIcon,
} from "lucide-react";
import type { JournalEntry } from "../../types/journal";
import type { AuthUser } from "../../auth/cognito";

export type ArchiveSection =
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
  section?: ArchiveSection;
};

const navItems: SidebarNavItem[] = [
  {
    label: "Archive",
    icon: Archive,
    section: "archive",
  },
  {
    label: "Upload",
    icon: CloudUpload,
  },
  {
    label: "Timeline",
    icon: SlidersHorizontal,
  },
  {
    label: "Search",
    icon: Search,
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
  {
    label: "Settings",
    icon: Settings,
  },
];

function getDisplayName(user: AuthUser | null) {
  if (user?.name) return user.name;
  if (user?.email) return user.email.split("@")[0];
  return "Guest";
}

function getInitial(user: AuthUser | null) {
  return getDisplayName(user).charAt(0).toUpperCase();
}

function getYearCount(entries: JournalEntry[]) {
  const years = new Set(
    entries
      .map((entry) => entry.createdAt ? new Date(entry.createdAt).getFullYear() : null)
      .filter(Boolean)
  );

  return years.size || 0;
}

function getOcrPercent(entries: JournalEntry[]) {
  const imageEntries = entries.filter((entry) => entry.sourceType === "image");

  if (imageEntries.length === 0) return 0;

  const completed = imageEntries.filter((entry) => {
    return (
      entry.ocrStatus === "COMPLETED" ||
      entry.status === "OCR_COMPLETED" ||
      entry.status === "REVIEWED" ||
      entry.status === "ANALYZED"
    );
  });

  return Math.round((completed.length / imageEntries.length) * 100);
}

function getCurrentStreak(entries: JournalEntry[]) {
  const dateSet = new Set(
    entries
      .filter((entry) => entry.createdAt)
      .map((entry) => new Date(entry.createdAt as string).toISOString().slice(0, 10))
  );

  let streak = 0;
  const cursor = new Date();

  for (let i = 0; i < 365; i++) {
    const key = cursor.toISOString().slice(0, 10);

    if (!dateSet.has(key)) {
      break;
    }

    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }

  return streak;
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
  const emailOrHandle = user?.email || "@journalm8";
  const totalEntries = entries.length;
  const yearCount = getYearCount(entries);
  const streak = getCurrentStreak(entries);
  const ocrPercent = getOcrPercent(entries);
  const ocrJobs = entries.filter((entry) => entry.sourceType === "image").length;

  return (
    <aside className="archive-sidebar">
      <div className="brand-wordmark">JOURNALM8</div>

      <section className="profile-card">
        <div className="profile-avatar">{getInitial(user)}</div>
        <h2>{displayName}</h2>
        <p>{emailOrHandle} <span>{user ? "PRIVATE" : "SIGNED OUT"}</span></p>

        <div className="profile-stats">
          <div>
            <strong>{totalEntries}</strong>
            <small>Entries</small>
          </div>
          <div>
            <strong>{yearCount}</strong>
            <small>Years</small>
          </div>
          <div>
            <strong>{streak} 🔥</strong>
            <small>Streak</small>
          </div>
          <div>
            <strong>{ocrPercent}%</strong>
            <small>OCR Done</small>
          </div>
        </div>
      </section>

      <nav className="archive-nav">
        {navItems.map((item) => {
          const Icon = item.icon;

          const isActive =
            item.section ===
            activeSection;
          const isUploadAction =
            item.label === "Upload";

          return (
            <button
              key={item.label}
              className={
                isActive
                  ? "archive-nav-item active"
                  : "archive-nav-item"
              }
              onClick={() => {
                if (isUploadAction) {
                  onUpload();
                  return;
                }

                if (item.section) {
                  onNavigate(
                    item.section
                  );
                }
              }}
              aria-current={
                isActive
                  ? "page"
                  : undefined
              }
              aria-disabled={
                !item.section &&
                !isUploadAction
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

      <section className="reflection-note">
        <p>Today’s Reflection</p>
        <strong>“</strong>
        <span>
          Discipline is doing what needs to be done, even when you don’t feel like it.
        </span>
        <div className="signature">{getInitial(user)}</div>
      </section>

      <footer className="sidebar-bottom">
        <span>© 2026 JOURNALM8</span>
        <button>
          <Moon size={16} />
        </button>
      </footer>
    </aside>
  );
}
