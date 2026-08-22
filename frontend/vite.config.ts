import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";

import { assertFrontendEnv } from "./scripts/frontend_env_contract.mjs";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const legacyEnvLocalPath = path.join(process.cwd(), ".env.local");
  const hasLegacyEnvLocal = fs.existsSync(legacyEnvLocalPath);
  const hasProductionLocal = fs.existsSync(
    path.join(process.cwd(), ".env.production.local")
  );
  const hasProductionEnv = fs.existsSync(
    path.join(process.cwd(), ".env.production")
  );
  const hasGenericEnv = fs.existsSync(path.join(process.cwd(), ".env"));

  assertFrontendEnv(env, {
    mode,
    hasLegacyEnvLocal,
    hasProductionLocal,
    hasProductionEnv,
    hasGenericEnv,
  });

  return {
    plugins: [react()],
    build: {
      sourcemap: false,
    },
  };
});
