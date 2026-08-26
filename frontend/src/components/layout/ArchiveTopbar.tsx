import { CalendarDays, Search, SlidersHorizontal, X } from "lucide-react";

type ArchiveTopbarProps = {
  searchQuery: string;
  sourceFilter: string;
  statusFilter: string;
  sortOrder: string;
  resultCount: number;
  totalCount: number;
  onSearchChange: (value: string) => void;
  onSourceFilterChange: (value: string) => void;
  onStatusFilterChange: (value: string) => void;
  onSortOrderChange: (value: string) => void;
  onClearFilters: () => void;
};

export default function ArchiveTopbar({
  searchQuery,
  sourceFilter,
  statusFilter,
  sortOrder,
  resultCount,
  totalCount,
  onSearchChange,
  onSourceFilterChange,
  onStatusFilterChange,
  onSortOrderChange,
  onClearFilters,
}: ArchiveTopbarProps) {
  const hasActiveFilters =
    searchQuery.trim() ||
    sourceFilter !== "all" ||
    statusFilter !== "all" ||
    sortOrder !== "newest";

  return (
    <header className="archive-topbar">
      <div className="archive-search">
        <Search size={20} />
        <input
          value={searchQuery}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search your archive…"
          aria-label="Search your archive"
        />
        {searchQuery ? (
          <button
            className="clear-search-button"
            onClick={() => onSearchChange("")}
            aria-label="Clear search"
          >
            <X size={16} />
          </button>
        ) : (
          <kbd>⌘ K</kbd>
        )}
      </div>

      <div className="archive-controls">
        <label className="filter-control">
          <CalendarDays size={18} />
          <select
            value={sourceFilter}
            onChange={(event) => onSourceFilterChange(event.target.value)}
          >
            <option value="all">All Sources</option>
            <option value="typed">Typed</option>
            <option value="image">Images</option>
          </select>
        </label>

        <label className="filter-control">
          <SlidersHorizontal size={18} />
          <select
            value={statusFilter}
            onChange={(event) => onStatusFilterChange(event.target.value)}
          >
            <option value="all">All Statuses</option>
            <option value="ANALYZED">Analyzed</option>
            <option value="REVIEWED">Reviewed</option>
            <option value="OCR_COMPLETED">OCR completed</option>
            <option value="OCR_FAILED">OCR failed</option>
            <option value="UPLOAD_URL_CREATED">Uploaded</option>
            <option value="NOT_ANALYZED">Not Analyzed</option>
          </select>
        </label>

        <label className="filter-control">
          <select
            value={sortOrder}
            onChange={(event) => onSortOrderChange(event.target.value)}
          >
            <option value="newest">Sort: Newest</option>
            <option value="oldest">Sort: Oldest</option>
          </select>
        </label>

        <div className="result-count-pill">
          {resultCount}/{totalCount}
        </div>

        {hasActiveFilters && (
          <button className="clear-filters-button" onClick={onClearFilters}>
            Clear
          </button>
        )}
      </div>
    </header>
  );
}
