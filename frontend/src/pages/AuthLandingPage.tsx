import {
  ArrowRight,
  LockKeyhole,
  ShieldCheck,
  UserPlus,
} from "lucide-react";
import {
  BrandMark,
  Button,
  LoadingState,
  Surface,
} from "../components/ui/V2Primitives";
import {
  EXCLUDED_REGION_SHORT_NAMES,
  POLICY_METADATA,
} from "../content/policyMetadata";

type AuthLandingPageProps = {
  isReady: boolean;
  onSignIn: () => void;
  onCreateAccount: () => void;
  acknowledgement?: string;
  error?: string;
};

export default function AuthLandingPage({
  isReady,
  onSignIn,
  onCreateAccount,
  acknowledgement,
  error,
}: AuthLandingPageProps) {
  if (!isReady) {
    return (
      <main className="jm8-auth-loading">
        <BrandMark />
        <LoadingState label="Checking your secure session…" />
      </main>
    );
  }

  return (
    <main className="jm8-auth-landing">
      {error && <p role="alert">{error}</p>}
      <section className="jm8-auth-story" aria-labelledby="jm8-auth-story-title">
        <BrandMark large />

        <div className="jm8-auth-story-copy">
          <h1 id="jm8-auth-story-title">
            <span>Your life.</span>
            <span>Remembered.</span>
          </h1>
          <p>
            Turn years of handwritten journals into a private, searchable
            intelligence archive that grows with you.
          </p>
        </div>

        <div className="jm8-journal-collage" aria-hidden="true">
          <div className="jm8-journal-sheet sheet-one">
            <span className="journal-date">May 17, 2019</span>
            <i /><i /><i /><i /><i /><i />
          </div>
          <div className="jm8-journal-sheet sheet-two">
            <span className="journal-date">July 16, 2020</span>
            <i /><i /><i /><i /><i /><i />
          </div>
          <div className="jm8-journal-sheet sheet-three">
            <span className="journal-date">March 3, 2021</span>
            <i /><i /><i /><i /><i /><i /><i />
          </div>
          <div className="jm8-archive-preview">
            <div className="archive-preview-search">Search your journal… <span>⌕</span></div>
            <div className="archive-preview-entry">
              <b>May 17, 2019</b>
              <span>Grateful for the small wins today.</span>
            </div>
            <div className="archive-preview-entry">
              <b>July 16, 2020</b>
              <span>Discipline is doing what needs to be done.</span>
            </div>
            <div className="archive-preview-entry">
              <b>March 3, 2021</b>
              <span>Big plans ahead. One step at a time.</span>
            </div>
          </div>
        </div>

        <p className="jm8-auth-privacy">
          <LockKeyhole size={17} aria-hidden="true" />
          Private by design <span aria-hidden="true">·</span> Your journal belongs to you
        </p>
      </section>

      <section className="jm8-auth-action" aria-labelledby="jm8-auth-title">
        <BrandMark compact className="jm8-auth-mobile-brand" />
        <Surface className="jm8-auth-card">
          <h2 id="jm8-auth-title">Welcome back</h2>
          <p className="jm8-auth-card-lede">Continue your journal.</p>
          <p className="jm8-auth-card-support">
            Your entries stay private and are available only through your
            secure JM8 account.
          </p>

          {acknowledgement ? (
            <p className="jm8-auth-security-note" role="status">
              <ShieldCheck size={15} aria-hidden="true" />
              {acknowledgement}
            </p>
          ) : null}

          <div className="jm8-auth-actions">
            <Button
              type="button"
              fullWidth
              onClick={onSignIn}
              trailingIcon={<ArrowRight size={18} aria-hidden="true" />}
            >
              Sign in
            </Button>
            <div className="jm8-auth-divider" aria-hidden="true"><span>or</span></div>
            <Button
              type="button"
              variant="secondary"
              fullWidth
              onClick={onCreateAccount}
              leadingIcon={<UserPlus size={18} aria-hidden="true" />}
            >
              Create account
            </Button>
          </div>

          <p className="jm8-auth-security-note">
            <ShieldCheck size={15} aria-hidden="true" />
            Secure sign-in with authorization code and PKCE
          </p>

          <p className="jm8-auth-eligibility">
            By continuing, you confirm that you are at least{" "}
            {POLICY_METADATA.minimumAge}, do not reside in the{" "}
            {EXCLUDED_REGION_SHORT_NAMES}, and agree to the{" "}
            <a href="/terms">Terms</a> and{" "}
            <a href="/privacy">Privacy Policy</a>. If you are under the age of
            majority where you live, you confirm that your parent or legal
            guardian has authorized your use of JM8.
          </p>
        </Surface>

        <footer className="jm8-auth-footer">
          <nav aria-label="Legal policies">
            <a href="/privacy">Privacy</a>
            <a href="/terms">Terms</a>
          </nav>
        </footer>
      </section>
    </main>
  );
}
