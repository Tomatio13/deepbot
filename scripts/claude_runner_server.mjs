import http from "node:http";
import { spawn } from "node:child_process";

const PORT = Number(process.env.CLAUDE_RUNNER_PORT || "8787");
const HOST = process.env.CLAUDE_RUNNER_HOST || "0.0.0.0";
const TOKEN = process.env.CLAUDE_RUNNER_TOKEN || "";
const DEFAULT_WORKDIR = process.env.CLAUDE_RUNNER_WORKDIR || "/workspace";
const DEFAULT_TIMEOUT_MS = Number(process.env.CLAUDE_RUNNER_TIMEOUT_MS || "300000");
const COMMAND = process.env.CLAUDE_RUNNER_COMMAND || "claude";

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  res.end(body);
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk.toString("utf-8");
      if (raw.length > 1024 * 1024) {
        reject(new Error("request body too large"));
      }
    });
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch (err) {
        reject(new Error("invalid json"));
      }
    });
    req.on("error", reject);
  });
}

function runClaude({ task, resumeSessionId, model, skipPermissions }) {
  return new Promise((resolve, reject) => {
    const args = ["-p", "--output-format", "json"];
    if (skipPermissions) args.push("--dangerously-skip-permissions");
    if (typeof model === "string" && model.trim()) args.push("--model", model.trim());
    if (typeof resumeSessionId === "string" && resumeSessionId.trim()) {
      args.push("--resume", resumeSessionId.trim());
    }
    args.push(
      "--append-system-prompt",
      "You are a delegated sub-agent. Focus on executable implementation details and return concise results.",
      task
    );

    const childEnv = { ...process.env };
    if (!childEnv.ANTHROPIC_BASE_URL) {
      childEnv.ANTHROPIC_BASE_URL =
        childEnv.CLAUDE_CODE_ANTHROPIC_BASE_URL || "http://litellm:4000";
    }
    if (!childEnv.ANTHROPIC_AUTH_TOKEN) {
      childEnv.ANTHROPIC_AUTH_TOKEN =
        childEnv.CLAUDE_CODE_ANTHROPIC_AUTH_TOKEN || childEnv.OPENAI_API_KEY || "";
    }

    const proc = spawn(COMMAND, args, {
      cwd: DEFAULT_WORKDIR,
      stdio: ["ignore", "pipe", "pipe"],
      env: childEnv,
    });

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => {
      stdout += d.toString("utf-8");
    });
    proc.stderr.on("data", (d) => {
      stderr += d.toString("utf-8");
    });

    const timeout = setTimeout(() => {
      proc.kill("SIGKILL");
      reject(new Error(`timeout after ${DEFAULT_TIMEOUT_MS}ms`));
    }, DEFAULT_TIMEOUT_MS);

    proc.on("close", (code) => {
      clearTimeout(timeout);
      if (code !== 0) {
        reject(new Error(`claude exited with code ${code}: ${(stderr || stdout).trim()}`));
        return;
      }
      try {
        const parsed = JSON.parse(stdout.trim());
        resolve(parsed);
      } catch {
        reject(new Error(`non-json response: ${stdout.slice(0, 4000)}`));
      }
    });

    proc.on("error", (err) => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/healthz") {
    sendJson(res, 200, { ok: true });
    return;
  }
  if (req.method !== "POST" || req.url !== "/v1/run") {
    sendJson(res, 404, { error: "not found" });
    return;
  }

  if (TOKEN) {
    const auth = req.headers.authorization || "";
    if (auth !== `Bearer ${TOKEN}`) {
      sendJson(res, 401, { error: "unauthorized" });
      return;
    }
  }

  try {
    const body = await parseBody(req);
    const task = typeof body.task === "string" ? body.task.trim() : "";
    if (!task) {
      sendJson(res, 400, { error: "task is required" });
      return;
    }

    const payload = await runClaude({
      task,
      resumeSessionId: typeof body.resume_session_id === "string" ? body.resume_session_id : "",
      model: typeof body.model === "string" ? body.model : "",
      skipPermissions: Boolean(body.skip_permissions),
    });
    sendJson(res, 200, payload);
  } catch (err) {
    sendJson(res, 500, { error: err instanceof Error ? err.message : String(err) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[claude-runner] listening on http://${HOST}:${PORT}`);
});
