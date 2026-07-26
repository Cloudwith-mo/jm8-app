export type AccountPlanId =
  | "FREE"
  | "PRO";


export type AccountSubscriptionStatus =
  | "FREE"
  | "TRIALING"
  | "ACTIVE"
  | "PAST_DUE"
  | "CANCELED"
  | "EXPIRED";


export type AccountEntitlementSource =
  | "DEFAULT"
  | "SYSTEM"
  | "STRIPE";


export type AccountEntitlement = {
  accountEntitlementVersion: string;
  generatedAt: string;
  plan: {
    id: AccountPlanId;
    label: string;
  };
  subscription: {
    configuredPlan: AccountPlanId;
    status: AccountSubscriptionStatus;
    source: AccountEntitlementSource;
    cancelAtPeriodEnd: boolean;
  };
  access: {
    isPro: boolean;
    startsAt: string | null;
    endsAt: string | null;
  };
  limits: {
    askJm8: {
      monthly: number;
    };
    entryAnalysis: {
      monthly: number;
    };
  };
  updatedAt: string | null;
};


export type AccountEntitlementResponse = {
  entitlement: AccountEntitlement;
};
