import {
  Archive,
  BarChart3,
  CloudUpload,
  FileText,
  ImagePlus,
  Moon,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import type { JournalEntry } from "../../types/journal";
import type { AuthUser } from "../../auth/cognito";

type ArchiveSidebarProps = {
  user: AuthUser | null;
  entries: JournalEntry[];
  onNewEntry: () => void;
  onUpload: () => void;
};

const navItems = [
  { label: "Archive", icon: Archive, active: true },
  { label: "Upload", icon: CloudUpload },
  { label: "Timeline", icon: SlidersHorizontal },
  { label: "Search", icon: Search },
  { label: "Insights", icon: BarChart3 },
  { label: "Themes", icon: Sparkles },
  { label: "OCR Jobs", icon: FileText },
  { label: "Settings", icon: Settings },
];

function getDisplayName(user: AuthUser | null) {
  if (user?.name) return user.name;
  if (user?.email) return user.email.split("@")[0];
  return "Demo User";
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
        <p>{emailOrHandle} <span>{user ? "PRIVATE" : "DEMO"}</span></p>

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

          return (
            <button
              key={item.label}
              className={item.active ? "archive-nav-item active" : "archive-nav-item"}
            >
              <Icon size={19} />
              <span>{item.label}</span>
              {item.label === "OCR Jobs" && ocrJobs > 0 && <em>{ocrJobs}</em>}
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
