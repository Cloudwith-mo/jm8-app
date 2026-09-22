# JM8 development iOS prototype

This branch is a development build, not an App Store release. The bundle ID,
Cognito host/client and callback are explicitly restricted to the dev environment.

## Build and run

From `frontend`, use Node 24, `npm ci`, then:

```sh
node scripts/build_ios_dev.mjs
npx cap open ios
```

Select App and an iPhone simulator in Xcode and Run. The build script supplies
public dev identifiers and copies the compiled web assets into the native target.
It does not require a local Vite server. It does not change AWS.

## Development authentication configuration

The iOS session returns to `com.cloudwithmo.journalm8.dev://auth/callback`.
The app's API requests originate from `capacitor://localhost`.

Using the existing backend Python environment with boto3 installed, review:

```sh
python3 backend/bin/configure_dev_ios_auth.py
```

Then apply the reviewed additions with the same command plus `--apply`.
The helper checks the account and fixed dev resource identities, preserves
existing browser callbacks and client settings, and verifies callback/CORS readback.
It does not change staging or production. If interrupted after one API update,
rerun its review and apply; both additions are idempotent.

Before the next dev backend deployment, add `capacitor://localhost` to the existing
comma-separated `ALLOWED_ORIGINS` GitHub variable in `jm8-dev`. Preserve its current
origins. The backend environment-contract change in this branch must be merged
before a deployment can accept that value. Deployments from older commits may
remove the manually added API origin. Cognito branding/adoption helpers with an
exact browser callback contract also need review before rerunning them.

## Session behavior

- iOS uses ASWebAuthenticationSession with an ephemeral browser session and the
  existing S256 PKCE/state checks. Cancellation is displayed on the sign-in screen.
- Access/identity/refresh tokens are persisted only in a device-only, unlocked
  Keychain item. JavaScript keeps an in-memory copy for existing API calls.
- The PKCE verifier is in memory only. If iOS kills the app during sign-in, retry
  sign-in; the pending exchange is not resumed.
- Relaunch restores unexpired tokens. Expired sessions require sign-in again;
  automatic refresh is not implemented in this prototype.
- Logout clears memory and Keychain. A non-secret local tombstone prevents
  restoration after a failed Keychain deletion. Ephemeral authentication avoids
  retaining a shared browser SSO session.
- Native bridge logging is disabled so token payloads are not printed to Xcode.
- The native WebView frame respects safe-area bounds, including fixed web UI.

## Acceptance on the Mac

1. Launch: header below status bar; content and dialogs above the home indicator.
2. Sign in: Cognito opens as a system authentication session and returns to JM8.
3. Archive loads from the dev API; create/reload one synthetic entry.
4. Stop and relaunch: an unexpired session remains signed in.
5. Sign out, stop/relaunch: stays signed out. Cancel sign-in, then retry successfully.
6. Create-account route and account-deletion reauthentication return to JM8.
7. Repeat a browser sign-in to confirm web behavior is retained.

CI compiles an unsigned simulator build. Real-device testing, native session
refresh, mobile upload/export, subscription flows, release branding and App Store
submission remain separate work. AWS configuration and simulator acceptance must
be verified before this draft is considered complete.
