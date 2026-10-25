/**
 * Free WhatsApp agent — Meta WhatsApp Cloud API + any OpenAI-compatible LLM.
 *
 * Flow: WhatsApp -> webhook here -> LLM -> Cloud API reply.
 * Runs on Cloudflare Workers free plan (100k requests/day, no cold-start server to babysit).
 */

const GRAPH = "https://graph.facebook.com";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname !== "/webhook") {
      return new Response("WhatsApp agent is running.\n", { status: 200 });
    }

    // 1. Meta webhook verification handshake.
    if (request.method === "GET") {
      const mode = url.searchParams.get("hub.mode");
      const token = url.searchParams.get("hub.verify_token");
      const challenge = url.searchParams.get("hub.challenge");
      if (mode === "subscribe" && env.VERIFY_TOKEN && timingSafeEqual(token ?? "", env.VERIFY_TOKEN)) {
        return new Response(challenge ?? "", { status: 200 });
      }
      return new Response("Forbidden", { status: 403 });
    }

    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    // 2. Verify the payload really came from Meta (cheap, prevents fake traffic).
    const raw = await request.text();
    if (!(await validSignature(request, raw, env))) {
      return new Response("Bad signature", { status: 401 });
    }

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return new Response("Bad JSON", { status: 400 });
    }

    // 3. Acknowledge immediately, do the slow work after the response.
    ctx.waitUntil(handlePayload(payload, env).catch((err) => console.error("handler failed", err)));
    return new Response("ok", { status: 200 });
  },
};

/* ------------------------------------------------------------------ */
/* Message handling                                                    */
/* ------------------------------------------------------------------ */

async function handlePayload(payload, env) {
  const messages = [];
  for (const entry of payload.entry ?? []) {
    for (const change of entry.changes ?? []) {
      const value = change.value ?? {};
      // Ignore delivery/read "statuses" updates: they carry no user text.
      for (const message of value.messages ?? []) messages.push(message);
    }
  }

  // Process concurrently; each message is independent.
  await Promise.all(messages.map((m) => handleMessage(m, env).catch((e) => console.error(m.id, e))));
}

async function handleMessage(message, env) {
  const from = message.from; // e.g. "972501234567"
  if (!from || message.type === "reaction" || message.type === "system") return;

  // Optional allowlist so strangers can't burn your free quota.
  const allow = (env.ALLOWED_NUMBERS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  if (allow.length > 0 && !allow.includes(from)) {
    console.log("ignoring non-allowlisted sender", from);
    return;
  }

  // Idempotency: Meta retries webhooks when your endpoint is slow.
  const seenKey = `seen:${message.id}`;
  if (await env.KV.get(seenKey)) return;
  await env.KV.put(seenKey, "1", { expirationTtl: 3600 });

  const text = extractText(message);
  if (!text) {
    await sendText(env, from, "I can only read text messages right now — could you type your question?");
    return;
  }

  const historyKey = `hist:${from}`;
  let history = [];
  try {
    history = JSON.parse((await env.KV.get(historyKey)) ?? "[]");
  } catch {
    history = [];
  }

  // Small escape hatch so users can start over without clearing KV by hand.
  if (/^(\/reset|reset|התחל מחדש)$/i.test(text.trim())) {
    await env.KV.delete(historyKey);
    await sendText(env, from, "Fresh start. What would you like to know?");
    return;
  }

  let reply;
  try {
    reply = await askLLM(env, history, text);
  } catch (err) {
    console.error("LLM error", err);
    reply = env.FALLBACK_REPLY ?? "Sorry, I'm having trouble thinking right now. Please try again in a minute.";
  }

  await sendText(env, from, reply);

  const nextHistory = [
    ...history,
    { role: "user", content: text },
    { role: "assistant", content: reply },
  ].slice(-Number(env.HISTORY_TURNS ?? 12) * 2);

  await env.KV.put(historyKey, JSON.stringify(nextHistory), { expirationTtl: 60 * 60 * 24 * 7 });
}

function extractText(message) {
  if (message.type === "text") return message.text?.body ?? "";
  // Button / list replies are common in guided flows.
  if (message.type === "interactive") {
    return message.interactive?.button_reply?.title ?? message.interactive?.list_reply?.title ?? "";
  }
  if (message.type === "button") return message.button?.text ?? "";
  return "";
}

async function askLLM(env, history, userText) {
  const base = (env.LLM_BASE_URL ?? "https://api.groq.com/openai/v1").replace(/\/+$/, "");
  const res = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${env.LLM_API_KEY}`,
    },
    body: JSON.stringify({
      model: env.LLM_MODEL ?? "llama-3.3-70b-versatile",
      temperature: Number(env.LLM_TEMPERATURE ?? 0.6),
      max_tokens: Number(env.LLM_MAX_TOKENS ?? 500),
      messages: [
        { role: "system", content: env.SYSTEM_PROMPT ?? "You are a helpful WhatsApp assistant. Keep replies short and conversational." },
        ...history,
        { role: "user", content: userText },
      ],
    }),
  });

  if (!res.ok) {
    throw new Error(`LLM ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  const data = await res.json();
  const content = data.choices?.[0]?.message?.content?.trim();
  if (!content) throw new Error("Empty LLM response");
  return content;
}

/* ------------------------------------------------------------------ */
/* WhatsApp Cloud API send                                             */
/* ------------------------------------------------------------------ */

async function sendText(env, to, body) {
  const version = env.GRAPH_VERSION ?? "v23.0";
  const url = `${GRAPH}/${version}/${env.PHONE_NUMBER_ID}/messages`;
  const chunks = splitForWhatsApp(body);

  for (const chunk of chunks) {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${env.WHATSAPP_TOKEN}`,
      },
      body: JSON.stringify({
        messaging_product: "whatsapp",
        recipient_type: "individual",
        to,
        type: "text",
        text: { preview_url: false, body: chunk },
      }),
    });

    if (!res.ok) {
      const detail = (await res.text()).slice(0, 500);
      console.error("send failed", res.status, detail);
      // 131047 / 131026 = outside 24h window / undeliverable. Nothing useful to retry.
      throw new Error(`WhatsApp send ${res.status}: ${detail}`);
    }
  }
}

/** WhatsApp text limit is 4096 chars; split on paragraph/space boundaries. */
function splitForWhatsApp(text, limit = 3500) {
  const clean = (text ?? "").trim();
  if (clean.length <= limit) return [clean];

  const parts = [];
  let rest = clean;
  while (rest.length > limit) {
    let cut = rest.lastIndexOf("\n\n", limit);
    if (cut < limit * 0.5) cut = rest.lastIndexOf("\n", limit);
    if (cut < limit * 0.5) cut = rest.lastIndexOf(" ", limit);
    if (cut < limit * 0.5) cut = limit;
    parts.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) parts.push(rest);
  return parts;
}

/* ------------------------------------------------------------------ */
/* Webhook signature                                                   */
/* ------------------------------------------------------------------ */

async function validSignature(request, rawBody, env) {
  // Fail closed: unsigned traffic is rejected unless ALLOW_UNSIGNED was
  // explicitly enabled (for local experiments only).
  if (!env.APP_SECRET) return env.ALLOW_UNSIGNED === "true";
  const header = request.headers.get("x-hub-signature-256") ?? "";
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(env.APP_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const digest = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(rawBody));
  const expected = "sha256=" + [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return timingSafeEqual(header, expected);
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
