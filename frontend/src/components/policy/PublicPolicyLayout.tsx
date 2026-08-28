import type { ReactNode } from "react";
import { BrandMark } from "../ui/V2Primitives";
import { POLICY_METADATA } from "../../content/policyMetadata";
import "./PublicPolicyLayout.css";

type PublicPolicyLayoutProps = {
  title: string;
  summary: string;
  children: ReactNode;
};

export default function PublicPolicyLayout({
  title,
  summary,
  children,
}: PublicPolicyLayoutProps) {
  return (
    <div className="jm8-policy-shell">
      <header className="jm8-policy-header">
        <a href="/" aria-label="JM8 home">
          <BrandMark compact />
        </a>

        <nav aria-label="Legal policies">
          <a href="/privacy">Privacy</a>
          <a href="/terms">Terms</a>
        </nav>
      </header>

      <main className="jm8-policy-main">
        <article className="jm8-policy-document">
          <header className="jm8-policy-title">
            <p>JM8 legal</p>
            <h1>{title}</h1>
            <p>{summary}</p>
            <dl>
              <div>
                <dt>Effective date</dt>
                <dd>{POLICY_METADATA.effectiveDate}</dd>
              </div>
              <div>
                <dt>Operator</dt>
                <dd>{POLICY_METADATA.operatorName}</dd>
              </div>
            </dl>
          </header>

          <div className="jm8-policy-content">{children}</div>
        </article>
      </main>

      <footer className="jm8-policy-footer">
        <span>© {new Date().getFullYear()} JM8</span>
        <a href={`mailto:${POLICY_METADATA.contactEmail}`}>
          {POLICY_METADATA.contactEmail}
        </a>
        <a href="/privacy">Privacy Policy</a>
        <a href="/terms">Terms</a>
      </footer>
    </div>
  );
}
