const API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT as string;
const APP_STAGE = import.meta.env.VITE_APP_STAGE as string;
const COGNITO_DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN as string;
const COGNITO_CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID as string;
const COGNITO_REDIRECT_URI = import.meta.env.VITE_COGNITO_REDIRECT_URI as string;
const COGNITO_LOGOUT_URI = import.meta.env.VITE_COGNITO_LOGOUT_URI as string;

export const frontendEnv = {
  appStage: APP_STAGE,
  apiEndpoint: API_ENDPOINT,
  demoUserId: (import.meta.env.VITE_DEMO_USER_ID as string | undefined) || "demo-user",
  cognitoEnabled: import.meta.env.VITE_COGNITO_ENABLED === "true",
  cognitoDomain: COGNITO_DOMAIN,
  cognitoClientId: COGNITO_CLIENT_ID,
  cognitoRedirectUri: COGNITO_REDIRECT_URI,
  cognitoLogoutUri: COGNITO_LOGOUT_URI,
};
