declare module "*.mjs" {
  export function assertFrontendEnv(
    env: Record<string, string | undefined>,
    options?: {
      mode?: string;
      hasLegacyEnvLocal?: boolean;
    }
  ): void;
}
