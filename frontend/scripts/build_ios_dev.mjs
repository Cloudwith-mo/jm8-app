import { spawnSync } from "node:child_process";

// Public development identifiers only. Native auth overrides the browser callback.
const env = { ...process.env,
  VITE_APP_STAGE: "dev",
  VITE_API_ENDPOINT: "https://u06tdrfsua.execute-api.us-east-1.amazonaws.com",
  VITE_COGNITO_ENABLED: "true",
  VITE_COGNITO_DOMAIN: "https://journalm8-dev-114743615542.auth.us-east-1.amazoncognito.com",
  VITE_COGNITO_CLIENT_ID: "4t37mcfdkg5gdvl7ev8vt91ojg",
  VITE_COGNITO_REDIRECT_URI: "http://localhost:5173/",
  VITE_COGNITO_LOGOUT_URI: "http://localhost:5173/",
};
for (const args of [["run", "build:dev"], ["exec", "--", "cap", "sync", "ios"]]) {
  const result = spawnSync("npm", args, { env, stdio: "inherit" });
  if (result.error || result.status !== 0) process.exit(result.status || 1);
}
