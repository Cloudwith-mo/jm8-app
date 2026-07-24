export type UsageOperation = {
  used: number;
  reserved: number;
  failed: number;
  limit: number;
  remaining: number;
  allowed: boolean;
};

export type UsagePeriod = {
  key: string;
  startsAt: string;
  endsAt: string;
  resetsAt: string;
};

export type UsagePlan = {
  id: "FREE" | "PRO";
  label: string;
};

export type UsageSnapshot = {
  usageVersion: string;
  generatedAt: string;
  period: UsagePeriod;
  plan: UsagePlan;
  operations: {
    askJm8: UsageOperation;
    entryAnalysis: UsageOperation;
  };
};

export type UsageResponse = {
  usage: UsageSnapshot;
};
