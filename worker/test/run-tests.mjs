/**
 * Node-based end-to-end tests for the WhatsApp Worker (src/index.js).
 * No wrangler needed: KV is mocked, outbound fetch is stubbed, ctx.waitUntil is captured.
 *
 * Run: node test/run-tests.mjs
 */

import { strict as assert } from "node:assert";
import crypto from "node:crypto";
import handler from "../src/index.js";

const APP_SECRET = "test-app-secret";
const GRAPH = "https://graph.facebook.com";

/* ----------------------------- shared stub state ---------------------------- */

let llmReplyText = "Hi there!";
let llmShouldFail = false;
let sends = []; // Graph API send calls captured during the current webhook

function installFetchStub() {
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    if (u.startsWith("https://llm.example/")) {
      if (llmShouldFail) return { ok: false, status: 503, text: async () => "llm down" };
      return {
        ok: true, status: 200,
        json: async () => ({ choices: [{ message: { content: llmReplyText } }] }),
      };
    }
    if (u.startsWith(GRAPH)) {
      sends.push(JSON.parse(init.body));
      return { ok: true, status: 200, text: async () => "ok", json: async () => ({}) };
    }
    throw new Error("unexpected fetch to " + u);
  };
}

/* ---------------------------------- fakes ---------------------------------- */

function makeKV() {
  const store = new Map();
  return {
    get: async (k) => (store.has(k) ? store.get(k) : null),
    put: async (k, v) => { store.set(k, v); },
    delete: async (k) => { store.delete(k); },
    store,
  };
}

function makeEnv(extra = {}) {
  return {
    VERIFY_TOKEN: "verify-me",
    APP_SECRET,
    KV: makeKV(),
    WHATSAPP_TOKEN: "wa-token",
    PHONE_NUMBER_ID: "1234567890",
    LLM_API_KEY: "llm-key",
    LLM_BASE_URL: "https://llm.example/v1",
    LLM_MODEL: "test-model",
    SYSTEM_PROMPT: "be nice",
    ALLOWED_NUMBERS: "972501234567",
    HISTORY_TURNS: "2",
    ...extra,
  };
}

function sign(raw, secret) {
  return "sha256=" + crypto.createHmac("sha256", secret).update(raw).digest("hex");
}

function waMessage(id, from, body) {
  return { id, from, type: "text", timestamp: "1760000000", text: { body } };
}

function waPayload(messages) {
  return { entry: [{ changes: [{ value: { messages } }] }] };
}

/**
 * POST to /webhook and (like the worker does) flush ctx.waitUntil tasks.
 * Returns { res, sends } where sends are the Graph API send calls.
 */
async function postWebhook(env, payloadObj, { withSignature = true, sigHeader } = {}) {
  const raw = JSON.stringify(payloadObj);
  const headers = { "content-type": "application/json" };
  if (withSignature) headers["x-hub-signature-256"] = sigHeader ?? sign(raw, APP_SECRET);

  const req = new Request("https://agent.example/webhook", { method: "POST", headers, body: raw });
  const ctx = { tasks: [], waitUntil(p) { this.tasks.push(p); } };
  const res = await handler.fetch(req, env, ctx);
  await Promise.all(ctx.tasks.map((p) => p.catch((e) => e)));
  return { res, sends };
}

/* ---------------------------------- tests ---------------------------------- */

const tests = [];
function test(name, fn) { tests.push([name, fn]); }

test("GET /webhook wrong token -> 403", async () => {
  const req = new Request("https://x/webhook?hub.mode=subscribe&hub.verify_token=WRONG&hub.challenge=1234");
  const res = await handler.fetch(req, makeEnv(), { waitUntil() {} });
  assert.equal(res.status, 403);
});

test("GET /webhook correct token -> echoes challenge", async () => {
  const req = new Request("https://x/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=987654");
  const res = await handler.fetch(req, makeEnv(), { waitUntil() {} });
  assert.equal(res.status, 200);
  assert.equal(await res.text(), "987654");
});

test("root path -> 200 running", async () => {
  const res = await handler.fetch(new Request("https://x/health"), makeEnv(), { waitUntil() {} });
  assert.equal(res.status, 200);
});

test("POST bad signature -> 401, nothing sent", async () => {
  const { res, sends: s } = await postWebhook(makeEnv(),
    waPayload([waMessage("m1", "972501234567", "hi")]), { sigHeader: "sha256=deadbeef" });
  assert.equal(res.status, 401);
  assert.equal(s.length, 0);
});

test("POST without APP_SECRET -> 401 fail-closed", async () => {
  // REGRESSION (round 1): a missing secret used to accept unsigned traffic.
  const env = makeEnv({ APP_SECRET: undefined });
  const { res, sends: s } = await postWebhook(env,
    waPayload([waMessage("m0", "972501234567", "hi")]));
  assert.equal(res.status, 401);
  assert.equal(s.length, 0);
});

test("ALLOW_UNSIGNED=true and no APP_SECRET -> accepted", async () => {
  const env = makeEnv({ APP_SECRET: undefined, ALLOW_UNSIGNED: "true" });
  const { res, sends: s } = await postWebhook(env,
    waPayload([waMessage("m9", "972501234567", "hello")]));
  assert.equal(res.status, 200);
  assert.equal(s.length, 1);
  assert.equal(s[0].text.body, "Hi there!");
});

test("happy path: replies via Graph API and stores history", async () => {
  const env = makeEnv();
  const { res, sends: s } = await postWebhook(env, waPayload([waMessage("m1", "972501234567", "hello")]));
  assert.equal(res.status, 200);
  assert.equal(s.length, 1);
  assert.equal(s[0].messaging_product, "whatsapp");
  assert.equal(s[0].to, "972501234567");
  assert.equal(s[0].text.body, "Hi there!");
  const hist = JSON.parse(await env.KV.get("hist:972501234567"));
  assert.equal(hist.length, 2);
  assert.equal(hist[0].role, "user");
  assert.equal(hist[1].content, "Hi there!");
  assert.ok(await env.KV.get("seen:m1"), "seen marker persisted");
});

test("idempotency: replayed message id is not processed twice", async () => {
  const env = makeEnv();
  await postWebhook(env, waPayload([waMessage("m1", "972501234567", "hello")]));
  const before = sends.length;
  await postWebhook(env, waPayload([waMessage("m1", "972501234567", "hello")]));
  const after = sends.length;
  assert.equal(after, before, `replayed message must not trigger another reply (sent ${after - before} extra)`);
});

test("allowlist: stranger gets no reply", async () => {
  const { sends: s } = await postWebhook(makeEnv(), waPayload([waMessage("m2", "972999999999", "hi")]));
  assert.equal(s.length, 0);
});

test("non-text message gets the fallback ask", async () => {
  const { sends: s } = await postWebhook(makeEnv(),
    waPayload([{ id: "m3", from: "972501234567", type: "image" }]));
  assert.equal(s.length, 1);
  assert.match(s[0].text.body, /only read text/i);
});

test("reset clears history", async () => {
  const env = makeEnv();
  await postWebhook(env, waPayload([waMessage("m1", "972501234567", "hello")]));
  const before = sends.length;
  const { sends: s } = await postWebhook(env, waPayload([waMessage("m4", "972501234567", "reset")]));
  assert.equal(sends.length - before, 1, "reset must produce exactly one reply");
  const fresh = s[s.length - 1];
  assert.match(fresh.text.body, /Fresh start/);
  const hist = await env.KV.get("hist:972501234567");
  assert.ok(hist === null || JSON.parse(hist).length === 0, "history cleared");
});

test("LLM failure falls back to apology message", async () => {
  const env = makeEnv();
  await postWebhook(env, waPayload([waMessage("m1", "972501234567", "hello")])); // success first
  llmShouldFail = true;
  const before = sends.length;
  const { sends: s } = await postWebhook(env, waPayload([waMessage("m5", "972501234567", "hello again")]));
  llmShouldFail = false;
  assert.equal(sends.length - before, 1, "exactly one fallback reply");
  assert.match(s[s.length - 1].text.body, /trouble thinking/i);
});

test("long reply is split into WhatsApp-sized chunks", async () => {
  const env = makeEnv();
  await postWebhook(env, waPayload([waMessage("m1", "972501234567", "hello")]));
  llmReplyText = "x".repeat(8000);
  const before = sends.length;
  await postWebhook(env, waPayload([waMessage("m6", "972501234567", "give me a long one")]));
  llmReplyText = "Hi there!";
  const chunks = sends.slice(before);
  const totalLength = chunks.reduce((acc, s) => acc + s.text.body.length, 0);
  assert.ok(chunks.length >= 2, `expected multiple chunks, got ${chunks.length}`);
  assert.equal(totalLength, 8000, "chunking must not lose content");
  for (const s2 of chunks) assert.ok(s2.text.body.length <= 3500, "chunk over hard limit");
});

test("history webhooks carry no messages -> still 200", async () => {
  const { res, sends: s } = await postWebhook(makeEnv(),
    { entry: [{ changes: [{ value: { statuses: [{ status: "delivered" }] } }] }] });
  assert.equal(res.status, 200);
  assert.equal(s.length, 0);
});

/* --------------------------------- runner ---------------------------------- */

installFetchStub();
llmReplyText = "Hi there!";

let failures = 0;
for (const [name, fn] of tests) {
  sends = []; // clear captured sends for each test
  try {
    await fn();
    console.log("PASS", name);
  } catch (e) {
    failures++;
    console.log("FAIL", name);
    console.log("   ", e.message.split("\n")[0]);
  }
}
console.log(`\n${tests.length - failures}/${tests.length} worker tests passed`);
process.exit(failures ? 1 : 0);
