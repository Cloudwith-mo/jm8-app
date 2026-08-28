export const POLICY_METADATA = {
  operatorName: "Muhammad Adeyemi",
  contactEmail: "muhammadadeyemi.it@outlook.com",
  effectiveDate: "August 28, 2026",
  minimumAge: 13,
  excludedRegions: [
    {
      name: "European Economic Area",
      shortName: "EEA",
    },
    {
      name: "United Kingdom",
      shortName: "UK",
    },
  ],
} as const;

export const EXCLUDED_REGION_SHORT_NAMES =
  POLICY_METADATA.excludedRegions
    .map((region) => region.shortName)
    .join(" or ");
