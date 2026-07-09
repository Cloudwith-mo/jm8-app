import { CalendarDays, Search, SlidersHorizontal } from "lucide-react";

export default function ArchiveTopbar() {
  return (
    <header className="archive-topbar">
      <div className="archive-search">
        <Search size={20} />
        <input placeholder="Search entries, moods, themes, or keywords..." />
        <kbd>⌘ K</kbd>
      </div>

      <div className="archive-controls">
        <button>
          <CalendarDays size={18} />
          All time
        </button>

        <button>
          <SlidersHorizontal size={18} />
          Filters
        </button>

        <button>
          Sort: Newest
        </button>
      </div>
    </header>
  );
}
