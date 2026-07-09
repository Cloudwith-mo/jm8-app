import { useEffect, useState } from "react";
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
    <div className="modal-backdrop">
      <section className="action-modal">
        <header className="action-modal-header">
          <div>
            <div className="modal-icon">
              <Icon size={20} />
            </div>
            <h2>{title}</h2>
          </div>

          <button onClick={onClose} disabled={isBusy}>
            <X size={20} />
          </button>
        </header>

        {mode === "write" && (
          <>
            <p className="modal-helper">
              Save a typed journal entry directly into your timeline.
            </p>

            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="What are you reflecting on today?"
              rows={10}
            />
          </>
        )}

        {mode === "upload" && (
          <>
            <p className="modal-helper">
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
            <p className="modal-helper">
              Correct OCR mistakes before analysis. This keeps your insights grounded
              in the reviewed transcript.
            </p>

            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={14}
            />
          </>
        )}

        <footer className="action-modal-footer">
          <button className="ghost-modal-button" onClick={onClose} disabled={isBusy}>
            Cancel
          </button>

          <button
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
