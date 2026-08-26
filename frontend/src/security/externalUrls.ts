export type StripeRedirectKind = "checkout" | "portal";

const STRIPE_HOSTS: Record<StripeRedirectKind, string> = {
  checkout: "checkout.stripe.com",
  portal: "billing.stripe.com",
};

export function isTrustedStripeRedirect(value: string, kind: StripeRedirectKind): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && url.hostname === STRIPE_HOSTS[kind]
      && url.port === ""
      && url.username === ""
      && url.password === ""
      && url.hash === "";
  } catch {
    return false;
  }
}
