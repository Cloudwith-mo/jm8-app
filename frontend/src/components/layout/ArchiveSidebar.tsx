import {
  Archive,
  BarChart3,
  CloudUpload,
  FileText,
  Home,
  ImagePlus,
  Moon,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";

const navItems = [
  { label: "Archive", icon: Archive, active: true },
  { label: "Upload", icon: CloudUpload },
  { label: "Timeline", icon: SlidersHorizontal },
  { label: "Search", icon: Search },
  { label: "Insights", icon: BarChart3 },
  { label: "Themes", icon: Sparkles },
  { label: "OCR Jobs", icon: FileText, badge: "3" },
  { label: "Settings", icon: Settings },
];

type ArchiveSidebarProps = {
  onNewEntry: () => void;
  onUpload: () => void;
};

export default function ArchiveSidebar({ onNewEntry, onUpload }: ArchiveSidebarProps) {
  return (
    <aside className="archive-sidebar">
      <div className="brand-wordmark">JOURNALM8</div>

      <section className="profile-card">
        <div className="profile-avatar">M</div>
        <h2>Muhammad Adeyemi</h2>
        <p>@journalm8 <span>PRIVATE</span></p>

        <div className="profile-stats">
          <div>
            <strong>1,284</strong>
            <small>Entries</small>
          </div>
          <div>
            <strong>6</strong>
            <small>Years</small>
          </div>
          <div>
            <strong>27 🔥</strong>
            <small>Streak</small>
          </div>
          <div>
            <strong>94%</strong>
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
              {item.badge && <em>{item.badge}</em>}
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
        <div className="signature">M</div>
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
