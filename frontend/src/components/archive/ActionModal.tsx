import { useEffect, useRef, useState } from "react";
import { FileText, ImagePlus, PenLine, X } from "lucide-react";
import type { JournalEntry } from "../../types/journal";

type ModalMode = "write" | "upload" | "review";

type ActionModalProps = {
  mode: ModalMode | null;
  entry?: JournalEntry | null;
  isBusy: boolean;
  onClose: () => void;
  onCreateText: (text: string) => Promise<void>;
  onUploadImage: (file: File) => Promise<void>;
  onSaveReview: (entryId: string, cleanText: string) => Promise<void>;
};

export default function ActionModal({
  mode,
  entry,
  isBusy,
  onClose,
  onCreateText,
  onUploadImage,
  onSaveReview,
}: ActionModalProps) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const onCloseRef = useRef(onClose);
  const isBusyRef = useRef(isBusy);

  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useEffect(() => { isBusyRef.current = isBusy; }, [isBusy]);

  useEffect(() => {
    if (mode === "review") {
      setText(entry?.cleanText || entry?.rawText || "");
    }

    if (mode === "write") {
      setText("");
    }

    if (mode === "upload") {
      setFile(null);
    }
  }, [mode, entry]);

  useEffect(() => {
    if (!mode) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const frame = window.requestAnimationFrame(() => {
      const preferred = dialogRef.current?.querySelector<HTMLElement>(
        "textarea, input:not([type='hidden'])"
      );
      (preferred || closeButtonRef.current)?.focus();
    });

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (!isBusyRef.current) onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), textarea:not([disabled]), input:not([disabled])"
      ));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [mode]);

  if (!mode) return null;

  async function handleSubmit() {
    if (mode === "write") {
      await onCreateText(text);
      return;
    }

    if (mode === "upload" && file) {
      await onUploadImage(file);
      return;
    }

    if (mode === "review" && entry) {
      await onSaveReview(entry.entryId, text);
    }
  }

  const title =
    mode === "write"
      ? "Write a Thought"
      : mode === "upload"
        ? "Upload Journal Page"
        : "Review OCR Transcript";

  const Icon =
    mode === "write" ? PenLine : mode === "upload" ? ImagePlus : FileText;

  return (
    <div className="modal-backdrop" role="presentation">
      <section
        ref={dialogRef}
        className="action-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="action-modal-title"
        aria-describedby="action-modal-description"
        aria-busy={isBusy}
      >
        <header className="action-modal-header">
          <div>
            <div className="modal-icon">
              <Icon size={20} />
            </div>
            <h2 id="action-modal-title">{title}</h2>
          </div>

          <button ref={closeButtonRef} type="button" onClick={onClose} disabled={isBusy} aria-label="Close dialog">
            <X size={20} />
          </button>
        </header>

        {mode === "write" && (
          <>
            <p className="modal-helper" id="action-modal-description">
              Save a typed journal entry directly into your timeline.
            </p>

            <label className="action-modal-field-label" htmlFor="jm8-entry-text">Journal text</label>
            <textarea
              id="jm8-entry-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="What are you reflecting on today?"
              rows={10}
            />
          </>
        )}

        {mode === "upload" && (
          <>
            <p className="modal-helper" id="action-modal-description">
              Upload a journal image. JM8 will send it to S3, run OCR, and save the
              transcript to your archive.
            </p>

            <label className="file-drop">
              <ImagePlus size={28} />
              <strong>{file ? file.name : "Choose journal image"}</strong>
              <span>JPEG or PNG works best for now.</span>
              <input
                type="file"
                accept="image/jpeg,image/png"
                onChange={(event) => setFile(event.target.files?.[0] || null)}
              />
            </label>
          </>
        )}

        {mode === "review" && (
          <>
            <p className="modal-helper" id="action-modal-description">
              Correct OCR mistakes before analysis. This keeps your insights grounded
              in the reviewed transcript.
            </p>

            <label className="action-modal-field-label" htmlFor="jm8-review-text">Reviewed transcript</label>
            <textarea
              id="jm8-review-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={14}
            />
          </>
        )}

        <footer className="action-modal-footer">
          <button type="button" className="ghost-modal-button" onClick={onClose} disabled={isBusy}>
            Cancel
          </button>

          <button
            type="button"
            className="primary-modal-button"
            onClick={handleSubmit}
            disabled={
              isBusy ||
              (mode === "write" && !text.trim()) ||
              (mode === "upload" && !file) ||
              (mode === "review" && !text.trim())
            }
          >
            {isBusy
              ? "Working..."
              : mode === "write"
                ? "Save Entry"
                : mode === "upload"
                  ? "Upload + OCR"
                  : "Save Review"}
          </button>
        </footer>
      </section>
    </div>
  );
}
