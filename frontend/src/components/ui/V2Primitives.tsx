import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  ReactNode,
} from "react";
import { forwardRef } from "react";
import {
  AlertTriangle,
  BookOpenText,
  LoaderCircle,
} from "lucide-react";

type BrandMarkProps = {
  compact?: boolean;
  large?: boolean;
  className?: string;
};

export function BrandMark({ compact = false, large = false, className = "" }: BrandMarkProps) {
  const sizeClass = compact ? " compact" : large ? " large" : "";

  return (
    <span
      className={`jm8-brand${sizeClass}${className ? ` ${className}` : ""}`}
      role="img"
      aria-label="JM8, Journalm8"
    >
      <span className="jm8-brand-monogram" aria-hidden="true">
        <span className="jm8-brand-letters">JM</span>
        <span className="jm8-brand-eight">8</span>
      </span>
      <span className="jm8-brand-descriptor" aria-hidden="true">JOURNALM8</span>
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet" | "danger";
  fullWidth?: boolean;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
};

export function Button({
  variant = "primary",
  fullWidth = false,
  leadingIcon,
  trailingIcon,
  className = "",
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`jm8-button ${variant}${fullWidth ? " full-width" : ""} ${className}`.trim()}
      {...props}
    >
      {leadingIcon}
      <span>{children}</span>
      {trailingIcon}
    </button>
  );
}

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  icon: ReactNode;
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, className = "", ...props },
  ref,
) {
  return (
    <span className="jm8-tooltip-wrap">
      <button
        ref={ref}
        className={`jm8-icon-button ${className}`.trim()}
        aria-label={label}
        {...props}
      >
        {icon}
      </button>
      <span className="jm8-tooltip" role="tooltip">{label}</span>
    </span>
  );
});

type SurfaceProps = HTMLAttributes<HTMLElement> & {
  as?: "section" | "article" | "div";
};

export function Surface({ as: Element = "section", className = "", ...props }: SurfaceProps) {
  return <Element className={`jm8-surface ${className}`.trim()} {...props} />;
}

export function StatusBadge({ children, tone = "brand" }: {
  children: ReactNode;
  tone?: "brand" | "success" | "warning" | "danger" | "neutral";
}) {
  return <span className={`jm8-status-badge ${tone}`}>{children}</span>;
}

export function PageHeader({ eyebrow, title, description, actions }: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="jm8-page-header">
      <div>
        {eyebrow && <div className="jm8-page-eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="jm8-page-actions">{actions}</div>}
    </header>
  );
}

function StateLayout({ className, icon, title, description, action }: {
  className: string;
  icon: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <section className={`jm8-state ${className}`} role={className === "error" ? "alert" : "status"}>
      <span className="jm8-state-icon">{icon}</span>
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {action}
    </section>
  );
}

export function LoadingState({ label = "Preparing your private archive…" }: { label?: string }) {
  return (
    <StateLayout
      className="loading"
      icon={<LoaderCircle size={24} aria-hidden="true" />}
      title={label}
    />
  );
}

export function EmptyState({ title, description, action }: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <StateLayout
      className="empty"
      icon={<BookOpenText size={24} aria-hidden="true" />}
      title={title}
      description={description}
      action={action}
    />
  );
}

export function ErrorState({ title = "Something went wrong", description, action }: {
  title?: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <StateLayout
      className="error"
      icon={<AlertTriangle size={24} aria-hidden="true" />}
      title={title}
      description={description}
      action={action}
    />
  );
}
