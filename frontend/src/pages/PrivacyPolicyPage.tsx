import PublicPolicyLayout from "../components/policy/PublicPolicyLayout";
import { POLICY_METADATA } from "../content/policyMetadata";

export default function PrivacyPolicyPage() {
  const [eea, uk] = POLICY_METADATA.excludedRegions;

  return (
    <PublicPolicyLayout
      title="Privacy Policy"
      summary="This policy explains what JM8 collects, why it is used, where it is processed, and the choices available to you."
    >
      <section aria-labelledby="privacy-scope">
        <h2 id="privacy-scope">Who operates JM8</h2>
        <p>
          JM8 is operated by {POLICY_METADATA.operatorName}. Questions and
          privacy requests can be sent to{" "}
          <a href={`mailto:${POLICY_METADATA.contactEmail}`}>
            {POLICY_METADATA.contactEmail}
          </a>.
        </p>
        <p>
          During the private beta, JM8 is unavailable to residents of the{" "}
          {eea.name} ({eea.shortName}) and the {uk.name} ({uk.shortName}).
          Do not create or use an account if you reside in either excluded
          region.
        </p>
      </section>

      <section aria-labelledby="privacy-children">
        <h2 id="privacy-children">Age requirements</h2>
        <p>
          You must be at least {POLICY_METADATA.minimumAge} years old to use
          JM8. JM8 is not directed to children under{" "}
          {POLICY_METADATA.minimumAge}, and the operator does not knowingly
          collect their personal information.
        </p>
      </section>

      <section aria-labelledby="privacy-collection">
        <h2 id="privacy-collection">Information JM8 collects</h2>
        <ul>
          <li>
            <strong>Account data:</strong> Cognito account identifiers, email,
            display name when provided, authentication state, and account
            status.
          </li>
          <li>
            <strong>Journal data:</strong> typed journal entries, entry dates,
            titles or labels, review status, and other content you save.
          </li>
          <li>
            <strong>Uploaded-image and OCR data:</strong> journal-page images,
            filenames, upload metadata, OCR jobs, extracted text, corrections,
            and processing status.
          </li>
          <li>
            <strong>Analysis and Ask JM8 data:</strong> journal content sent for
            a requested analysis, generated themes, summaries, moods and other
            analysis results, Ask JM8 questions, relevant journal context, and
            generated answers and history.
          </li>
          <li>
            <strong>Usage data:</strong> monthly feature usage, allowances,
            reservations, job activity, and feature interactions needed to
            operate Free and Pro limits.
          </li>
          <li>
            <strong>Subscription data:</strong> plan, subscription status,
            Stripe customer references, billing-period dates, and payment or
            entitlement events. JM8 does not store complete card details.
          </li>
          <li>
            <strong>Support data:</strong> messages, attachments, and contact
            details you choose to provide when requesting help.
          </li>
          <li>
            <strong>Technical data:</strong> request identifiers, route and
            response status, timestamps, latency, IP address in API access
            logs, browser or device information made available by standard web
            requests, and security or diagnostic events.
          </li>
        </ul>
      </section>

      <section aria-labelledby="privacy-use">
        <h2 id="privacy-use">How information is used</h2>
        <p>
          JM8 uses this information to authenticate accounts, store and display
          private journals, upload images, perform OCR, provide requested AI
          features, answer Ask JM8 questions, enforce plan allowances, manage
          subscriptions, provide support, protect the service, diagnose
          failures, and improve reliability.
        </p>
        <p>
          JM8 does not sell personal information and does not use personal
          information for behavioral advertising. User journal content is not
          used by JM8 to train AI models.
        </p>
      </section>

      <section aria-labelledby="privacy-processors">
        <h2 id="privacy-processors">Service providers and processing</h2>
        <h3>AWS and Amazon Bedrock</h3>
        <p>
          AWS hosts and processes JM8 application data in the United States.
          AWS services support account authentication, application storage,
          uploaded images, OCR workflows, monitoring, and other infrastructure.
          Amazon Bedrock processes journal content and related context only
          when needed to provide AI features you request, such as analysis or
          Ask JM8.
        </p>
        <h3>Stripe</h3>
        <p>
          Stripe processes payments and subscription management. JM8 receives
          limited customer, subscription, and transaction status information,
          but does not store complete card details. Stripe handles payment
          information under its own privacy terms and may retain transaction
          records to meet its legal obligations.
        </p>
      </section>

      <section aria-labelledby="privacy-retention">
        <h2 id="privacy-retention">Retention and deletion timing</h2>
        <p>
          Active account and application data is retained while your account
          exists, unless a shorter period is required for a specific record or
          you request deletion.
        </p>
        <ul>
          <li>CloudWatch application logs are retained for 30 days.</li>
          <li>
            DynamoDB point-in-time backups may remain recoverable for up to 35
            days after active records are changed or deleted.
          </li>
          <li>
            Deleted S3 object versions may remain recoverable for up to 30 days
            under storage lifecycle rules.
          </li>
          <li>
            Stripe may retain transaction records under its legal obligations,
            even after a JM8 account is deleted.
          </li>
        </ul>
      </section>

      <section aria-labelledby="privacy-choices">
        <h2 id="privacy-choices">Your choices and requests</h2>
        <p>
          You may request access to, correction of, export of, or deletion of
          your JM8 personal information by emailing{" "}
          <a href={`mailto:${POLICY_METADATA.contactEmail}`}>
            {POLICY_METADATA.contactEmail}
          </a>. The operator may need to verify your identity before completing
          a request.
        </p>
        <p>
          In-app account export is available from the account panel. Automated
          in-app account deletion is planned for Phase 3C3 but is not yet
          available; deletion requests are currently handled by email.
        </p>
      </section>

      <section aria-labelledby="privacy-security">
        <h2 id="privacy-security">Security</h2>
        <p>
          JM8 uses authentication, access controls, encrypted AWS services,
          scoped infrastructure permissions, and operational monitoring to
          reduce privacy and security risk. These protections reduce risk but
          cannot guarantee absolute security.
        </p>
      </section>

      <section aria-labelledby="privacy-changes">
        <h2 id="privacy-changes">Changes to this policy</h2>
        <p>
          This Privacy Policy may be updated as JM8 changes. A revision will be
          published with a new effective date, and material changes may also be
          communicated through the service when appropriate.
        </p>
      </section>
    </PublicPolicyLayout>
  );
}
