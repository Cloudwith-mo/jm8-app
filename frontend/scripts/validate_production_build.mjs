import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TEXT_EXTENSIONS = new Set([
  ".css",
  ".html",
  ".js",
  ".json",
  ".svg",
  ".txt",
  ".webmanifest",
  ".xml",
]);

const FORBIDDEN_ARTIFACT_PATTERNS = [
  { name: "localhost", pattern: /(?:localhost|127\.0\.0\.1)/i },
  {
    name: "demo or local authentication marker",
    pattern: /(?:x-user-id|demo[-_ ]?(?:mode|user|login|banner|route)|VITE_[A-Z0-9_]*(?:DEMO|MOCK|BYPASS))/i,
  },
  { name: "cross-stage resource", pattern: /journalm8-(?:dev|staging)(?:[-./:_]|$)/i },
  { name: "cross-stage URL", pattern: /https?:\/\/[^\s"']*(?:staging\.|dev\.)/i },
  { name: "Stripe secret", pattern: /(?:sk_(?:live|test)_|whsec_)[A-Za-z0-9]+/ },
  { name: "AWS access key", pattern: /AKIA[0-9A-Z]{16}/ },
  { name: "secret assignment", pattern: /(?:STRIPE_SECRET|AWS_SECRET_ACCESS_KEY)\s*[=:]/i },
];

function walkFiles(root) {
  const files = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const entryPath = path.join(root, entry.name);
    if (entry.isSymbolicLink()) {
      throw new Error("Production build must not contain symbolic links");
    }
    if (entry.isDirectory()) {
      files.push(...walkFiles(entryPath));
    } else if (entry.isFile()) {
      files.push(entryPath);
    }
  }
  return files;
}

export function validateProductionBuild(rootPath) {
  const root = path.resolve(rootPath);
  const indexPath = path.join(root, "index.html");
  const assetsPath = path.join(root, "assets");
  if (!fs.statSync(root, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error("Production build directory is missing");
  }
  if (!fs.statSync(indexPath, { throwIfNoEntry: false })?.isFile()) {
    throw new Error("Production build index.html is missing");
  }
  if (!fs.statSync(assetsPath, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error("Production build assets directory is missing");
  }

  const files = walkFiles(root);
  for (const filePath of files) {
    const relativePath = path.relative(root, filePath);
    if (filePath.endsWith(".map")) {
      throw new Error(`Production source map is forbidden: ${relativePath}`);
    }
    if (relativePath.startsWith(`assets${path.sep}`)) {
      const basename = path.basename(filePath);
      if (!/-[A-Za-z0-9_-]{8,}\.[^.]+$/.test(basename)) {
        throw new Error(`Production asset is not content-hashed: ${relativePath}`);
      }
    }
    if (!TEXT_EXTENSIONS.has(path.extname(filePath).toLowerCase())) {
      continue;
    }
    const contents = fs.readFileSync(filePath, "utf8");
    const forbidden = FORBIDDEN_ARTIFACT_PATTERNS.find(({ pattern }) =>
      pattern.test(contents)
    );
    if (forbidden) {
      throw new Error(
        `Production build contains forbidden ${forbidden.name}: ${relativePath}`
      );
    }
  }
  return { fileCount: files.length };
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const rootPath = process.argv[2];
  if (!rootPath) {
    console.error("Usage: node validate_production_build.mjs <dist-directory>");
    process.exit(1);
  }
  try {
    const result = validateProductionBuild(rootPath);
    console.log(`Production build validation passed (${result.fileCount} files).`);
  } catch (error) {
    console.error(String(error?.message || "Production build validation failed."));
    process.exit(1);
  }
}
