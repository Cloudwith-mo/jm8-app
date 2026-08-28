import PublicPolicyLayout from "../components/policy/PublicPolicyLayout";
import { POLICY_METADATA } from "../content/policyMetadata";

export default function TermsPage() {
  const [eea, uk] = POLICY_METADATA.excludedRegions;

  return (
    <PublicPolicyLayout
      title="Terms of Use"
      summary="These Terms govern access to the JM8 private beta, including account eligibility, journal features, AI output, and Free or Pro plan use."
    >
      <section aria-labelledby="terms-agreement">
        <h2 id="terms-agreement">Agreement and operator</h2>
        <p>
          By creating an account or using JM8, you agree to these Terms. JM8 is
          operated by {POLICY_METADATA.operatorName}. If you do not agree, do
          not use the service. Questions may be sent to{" "}
          <a href={`mailto:${POLICY_METADATA.contactEmail}`}>
            {POLICY_METADATA.contactEmail}
          </a>.
        </p>
      </section>

      <section aria-labelledby="terms-eligibility">
        <h2 id="terms-eligibility">Eligibility and account information</h2>
        <p>
          You must be at least {POLICY_METADATA.minimumAge} years old and able
          to agree to these Terms. You must provide truthful, current account
          information and keep your sign-in credentials secure. You are
          responsible for activity through your account.
        </p>
        <p>
          If you are under the age of majority where you live, your parent or
          legal guardian must review and agree to these Terms and authorize your
          use of JM8.
        </p>
        <p>
          During the private beta, JM8 is unavailable to residents of the{" "}
          {eea.name} ({eea.shortName}) and the {uk.name} ({uk.shortName}). You
          may not create or use an account if you reside in either region.
        </p>
      </section>

      <section aria-labelledby="terms-service">
        <h2 id="terms-service">JM8’s purpose</h2>
        <p>
          JM8 is a private journal archive and AI-analysis service. It lets you
          write entries, upload journal images, obtain OCR transcripts, request
          analysis, browse your archive, and ask questions about your journal
          content. The private beta may change as features are tested and
          improved.
        </p>
      </section>

      <section aria-labelledby="terms-content">
        <h2 id="terms-content">Your content and limited license</h2>
        <p>
          You retain ownership of journal entries, images, questions, and other
          content you submit to JM8. You grant the operator a limited,
          non-exclusive license to host, store, copy, transmit, process, OCR,
          and analyze that content only as needed to operate, secure, support,
          and provide the JM8 features you request.
        </p>
        <p>
          You represent that you have the rights needed to submit your content.
          This license ends when the content is deleted, subject to the backup,
          version-retention, security, and legal-retention periods described in
          the Privacy Policy.
        </p>
      </section>

      <section aria-labelledby="terms-misuse">
        <h2 id="terms-misuse">Prohibited use</h2>
        <p>You may not use JM8 to:</p>
        <ul>
          <li>submit unlawful content or violate another person’s rights;</li>
          <li>harass, exploit, threaten, impersonate, or defraud anyone;</li>
          <li>
            upload malware or attempt to disrupt, overload, probe, bypass, or
            gain unauthorized access to the service or another account;
          </li>
          <li>
            scrape, reverse engineer, resell, or automate access except where
            applicable law expressly permits it; or
          </li>
          <li>
            use AI features or output for unlawful, deceptive, or harmful
            activity.
          </li>
        </ul>
      </section>

      <section aria-labelledby="terms-ai">
        <h2 id="terms-ai">AI output and important limitations</h2>
        <p>
          AI-generated summaries, themes, answers, and other output may be
          incomplete, inaccurate, misleading, or unsuitable for your
          circumstances. Review important output and use your own judgment.
        </p>
        <p>
          JM8 is not medical, mental-health, legal, or emergency advice and is
          not a substitute for a qualified professional. Do not rely on JM8 to
          diagnose, treat, or respond to an urgent situation. If you or someone
          else may be in danger or experiencing a crisis or emergency, contact
          local emergency services or an appropriate local crisis service
          immediately.
        </p>
      </section>

      <section aria-labelledby="terms-plans">
        <h2 id="terms-plans">Free and Pro plans</h2>
        <h3>Free plan</h3>
        <p>
          The Free plan has no recurring subscription charge and includes the
          feature access and monthly AI allowances shown in JM8. Unused monthly
          allowances do not roll over. Free-plan features and limits may change
          as the private beta develops.
        </p>
        <h3>Pro plan</h3>
        <p>
          Pro is a $10 monthly recurring subscription that provides the
          expanded feature access and allowances shown in JM8. Stripe processes
          the payment method and recurring charge. The subscription renews
          monthly until canceled.
        </p>
        <p>
          You may cancel through the Stripe customer portal. Cancellation stops
          future renewal, and paid access continues through the current billing
          period. Payments are not prorated or refunded for a partial billing
          period except where required by law. If pricing changes, the new price
          will apply only to a future renewal after notice and any consent
          required by law.
        </p>
      </section>

      <section aria-labelledby="terms-availability">
        <h2 id="terms-availability">Availability and service changes</h2>
        <p>
          JM8 may be unavailable during maintenance, failures, provider outages,
          security events, or private-beta changes. Features may be added,
          changed, limited, or removed. The operator does not promise that JM8
          will always be available, uninterrupted, error-free, or compatible
          with every device.
        </p>
      </section>

      <section aria-labelledby="terms-suspension">
        <h2 id="terms-suspension">Suspension and termination</h2>
        <p>
          Access may be limited or suspended when reasonably necessary to
          address prohibited use, unlawful activity, security risk, nonpayment,
          material breach of these Terms, or harm to JM8 or others. The operator
          may terminate the private beta or an account after reasonable notice
          when practical, but may act immediately when needed for security,
          safety, or legal reasons. You may stop using JM8 and request account
          deletion at any time.
        </p>
      </section>

      <section aria-labelledby="terms-disclaimers">
        <h2 id="terms-disclaimers">Disclaimers</h2>
        <p>
          To the extent permitted by applicable law, JM8 is provided “as is” and
          “as available,” without warranties of uninterrupted availability,
          accuracy, fitness for a particular purpose, or non-infringement.
          Nothing in these Terms excludes warranties or rights that cannot
          legally be excluded.
        </p>
      </section>

      <section aria-labelledby="terms-liability">
        <h2 id="terms-liability">Limitation of liability</h2>
        <p>
          To the extent permitted by applicable law, the operator is not liable
          for indirect, incidental, special, consequential, or punitive damages,
          or for lost data, profits, opportunities, or goodwill arising from
          JM8. For other claims, total liability will not exceed the amount you
          paid for JM8 during the 12 months before the event giving rise to the
          claim. These limits do not apply where liability cannot legally be
          limited.
        </p>
      </section>

      <section aria-labelledby="terms-law">
        <h2 id="terms-law">Governing law</h2>
        <p>
          These Terms are governed by the laws of the State of Texas, United
          States, without regard to conflict-of-laws principles. Any dispute
          must be brought in a court with lawful jurisdiction in Texas, except
          where mandatory applicable law gives you the right to bring a claim
          elsewhere.
        </p>
      </section>

      <section aria-labelledby="terms-changes">
        <h2 id="terms-changes">Changes to these Terms</h2>
        <p>
          These Terms may be updated as JM8 changes. Revisions will be published
          with a new effective date. Material changes may also be communicated
          through the service, and continued use after the revised Terms take
          effect means you accept them where permitted by law.
        </p>
      </section>

      <section aria-labelledby="terms-contact">
        <h2 id="terms-contact">Contact</h2>
        <p>
          Contact {POLICY_METADATA.operatorName} at{" "}
          <a href={`mailto:${POLICY_METADATA.contactEmail}`}>
            {POLICY_METADATA.contactEmail}
          </a> with questions about these Terms.
        </p>
      </section>
    </PublicPolicyLayout>
  );
}
