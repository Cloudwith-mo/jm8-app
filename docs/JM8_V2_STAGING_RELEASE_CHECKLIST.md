# JM8 V2 staging release checklist

Use this checklist on the release candidate commit in a new private-browser session. Never paste credentials, authorization codes, tokens, journal text, Stripe secrets, Basic Auth passwords, or API response bodies into this document, screenshots, tickets, or console notes. Use synthetic, non-sensitive journal content and record only pass/fail plus sanitized request IDs when needed.

## Stop and rollback conditions

Stop testing and roll back to the last verified staging commit if protected data appears while signed out, a 401 leaves journal or account data visible, callback parameters remain in the address bar, another user's data appears, a webhook is accepted without a valid Stripe signature, production resources appear in staging traffic, secrets appear in UI/console/artifacts, or any create/update/delete targets an unexpected account or stage. Do not continue billing tests after an unexpected charge or live-mode redirect.

## Fourteen-step journey

| # | Check | Coverage | Expected result | Stop condition |
|---|---|---|---|---|
| 1 | Open staging unauthenticated in a private window | Manual | CloudFront responds over HTTPS and prompts for staging Basic Auth before JM8 loads. | Missing Basic Auth, redirect to another stage, or console secret. |
| 2 | Pass Basic Auth | Manual | The signed-out JM8 landing renders; no protected request succeeds. | Archive/account data is visible. |
| 3 | Start sign-in and, separately, account creation | Automated contract + manual | Cognito Classic Hosted UI v1 opens with the staging pool/client and JM8 branding. | Wrong pool/domain/client or untrusted redirect. |
| 4 | Complete authorization-code/PKCE callback | Automated contract + manual | Callback state matches, code exchange succeeds, and invalid/replayed state fails closed. | Implicit tokens, missing PKCE, or accepted state mismatch. |
| 5 | Land on Home | Automated contract + manual | Home renders authenticated data and the URL contains no `code`, `state`, `error`, or stale callback value. Browser console is clean. | Callback material remains or a raw API response is shown. |
| 6 | Create a typed entry | Manual | One synthetic entry is saved, appears in shared Archive/Home state, and opens by deep link. | Duplicate/fabricated entry or another account sees it. |
| 7 | Upload a synthetic journal image | Manual | Presigned PUT succeeds, OCR is queued, and no file bytes or signed URL are logged. | Upload targets an unexpected bucket/account or signed URL is exposed. |
| 8 | Observe OCR and retry one eligible staging failure | Automated API tests + manual | Pending/completed/failed states are truthful; retry is offered only when authorized and eligible. | Stale filter results, cross-entry retry, or suppressed failure. |
| 9 | Review transcript in Entry Detail | Automated accessibility contract + manual | Back/deep-link works; modal traps focus, closes with Escape when idle, restores focus, and saved review updates shared state. | Unlabeled editor, lost focus, stale transcript, or wrong entry update. |
| 10 | Request analysis | Automated API tests + manual | Pending/completed/failed and usage states are safe; analysis remains tied to the selected entry. | Fabricated analysis, stale entry replacement, or raw provider error. |
| 11 | Exercise Archive | Automated route contract + manual | Search/filter/sort/refresh/deep-link and Back/Forward remain coherent using real entries. | Duplicate/invalid query parameters select unintended content. |
| 12 | Exercise Insights, Themes, and Ask JM8 | Automated contract + manual | Only authenticated, validated API data renders; source provenance and sparse/error states remain explicit. | Sample data, stale response, evidence mismatch, or unauthenticated success. |
| 13 | Exercise weekly/monthly reports | Automated contract + manual | Current/historical/empty windows survive refresh and Back/Forward; unsupported modules remain absent. | Old report is relabeled as the new period or invalid window is accepted. |
| 14 | Open plan/usage, test safe billing action, then logout | Automated security contract + manual | Use Stripe test mode only in staging; opening Checkout/Portal may be tested, but do not complete a charge. Logout clears protected UI immediately, Cognito logout completes, and a direct unauthenticated API request returns 401/403. | Live-mode charge surface, untrusted redirect, protected data after logout, or unauthenticated 2xx. |

## Required viewport pass

Repeat the signed-out landing and every authenticated destination at 1440×900, 1024×768, 820×1180, 390×844, and 320×568. At each width verify no horizontal page overflow, clipped controls, overlapping sticky/fixed elements, unreadable chart/table content, inaccessible modal content, or unreachable mobile navigation. Check keyboard-only operation, visible focus, reduced-motion preference, 200% zoom, long synthetic content wrapping, and 44px touch targets.

Destinations: Home, Archive, Entry Detail, Insights, Themes, Ask JM8, Weekly Reports, Monthly Reports, OCR Jobs, Analysis Jobs, account/plan/usage, typed-entry dialog, and upload dialog.

## Final evidence and rollback reference

- Record the exact release candidate commit and the immediately preceding verified staging commit; the latter is the rollback reference.
- Record automated command totals without copying secrets or payloads.
- Confirm a clean browser console on desktop and mobile.
- Confirm staging artifacts contain no production credentials and production artifacts contain no staging, localhost, demo, mock, bypass, source-map, or secret markers.
- Finish signed out. Reopen the application and call one protected API route without credentials; require 401 or 403.
- Manual items remain `NOT RUN` until a human performs them. Automated checks do not substitute for Basic Auth, Cognito, S3 upload, Stripe redirect, real viewport, or logout verification in staging.
