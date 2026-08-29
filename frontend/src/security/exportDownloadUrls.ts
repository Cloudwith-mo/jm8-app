const JM8_AWS_ACCOUNT_ID = "114743615542";
const JM8_AWS_REGION = "us-east-1";

export function expectedExportDownloadHostname(stage: string): string {
  if (!new Set(["dev", "staging", "prod"]).has(stage)) {
    return "";
  }
  return `journalm8-${stage}-exports-${JM8_AWS_ACCOUNT_ID}.s3.${JM8_AWS_REGION}.amazonaws.com`;
}

export function isTrustedExportDownloadUrl(
  value: string,
  expectedHostname: string
): boolean {
  if (!expectedHostname) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && url.hostname === expectedHostname
      && url.port === ""
      && url.username === ""
      && url.password === "";
  } catch {
    return false;
  }
}
