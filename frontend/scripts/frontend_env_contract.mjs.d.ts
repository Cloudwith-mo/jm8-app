export type FrontendEnv = Record<string, string | undefined>;

export function getApprovedModeLocalFiles(mode: string): string[];

export function isApprovedModeSpecificEnvSource(
  source: string,
  mode: string
): boolean;

export function validateFrontendEnv(
  env: FrontendEnv,
  options?: {
    mode?: string;
    hasLegacyEnvLocal?: boolean;
  }
): { mode: string; stage: string };

export function assertFrontendEnv(
  env: FrontendEnv,
  options?: {
    mode?: string;
    hasLegacyEnvLocal?: boolean;
  }
): void;
