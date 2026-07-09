import {
  AlertCircle,
  CheckCircle2,
  Info,
  Loader2,
  X,
} from "lucide-react";

export type ToastKind = "success" | "error" | "info" | "loading";

export type ToastMessage = {
  id: string;
  kind: ToastKind;
  title: string;
  message?: string;
};

type ToastStackProps = {
  toasts: ToastMessage[];
  onDismiss: (id: string) => void;
};

function getIcon(kind: ToastKind) {
  if (kind === "success") return <CheckCircle2 size={18} />;
  if (kind === "error") return <AlertCircle size={18} />;
  if (kind === "loading") return <Loader2 className="toast-spin" size={18} />;
  return <Info size={18} />;
}

export default function ToastStack({ toasts, onDismiss }: ToastStackProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="toast-stack" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => (
        <section key={toast.id} className={`toast-card ${toast.kind}`}>
          <div className="toast-icon">{getIcon(toast.kind)}</div>

          <div className="toast-content">
            <strong>{toast.title}</strong>
            {toast.message && <p>{toast.message}</p>}
          </div>

          <button onClick={() => onDismiss(toast.id)} aria-label="Dismiss notification">
            <X size={16} />
          </button>
        </section>
      ))}
    </div>
  );
}
