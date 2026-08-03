import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AnimicaAI,
  parseSseStream,
  DEFAULT_AI_BASE_URL,
  HttpError,
} from "../dist/esm/index.js";
import { makeFetchStub, jsonResponse, sseResponse } from "./helpers.mjs";

test("default base url + models.list()", async () => {
  const fetchStub = makeFetchStub(() =>
    jsonResponse({
      object: "list",
      data: [{ id: "kimi-k3", object: "model", owned_by: "animica" }],
    })
  );
  const ai = new AnimicaAI({ fetch: fetchStub });
  assert.equal(ai.baseUrl, "https://animica.dev/v1");
  assert.equal(DEFAULT_AI_BASE_URL, "https://animica.dev/v1");
  const models = await ai.models.list();
  assert.equal(fetchStub.calls[0].url, "https://animica.dev/v1/models");
  assert.equal(fetchStub.calls[0].method, "GET");
  // keyless by default: no authorization header
  assert.equal(fetchStub.calls[0].headers.authorization, undefined);
  assert.equal(models.data[0].id, "kimi-k3");
});

test("apiKey (optional) is sent as Bearer when provided", async () => {
  const fetchStub = makeFetchStub(() => jsonResponse({ object: "list", data: [] }));
  const ai = new AnimicaAI({ fetch: fetchStub, apiKey: "anm_test_key" });
  await ai.models.list();
  assert.equal(fetchStub.calls[0].headers.authorization, "Bearer anm_test_key");
});

test("chat.completions.create (non-stream) POSTs OpenAI-shaped body", async () => {
  const completion = {
    id: "chatcmpl-1",
    object: "chat.completion",
    model: "kimi-k3",
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: "hello" },
        finish_reason: "stop",
      },
    ],
  };
  const fetchStub = makeFetchStub(() => jsonResponse(completion));
  const ai = new AnimicaAI({ baseUrl: "http://ai.test/v1/", fetch: fetchStub });
  const out = await ai.chat.completions.create({
    model: "kimi-k3",
    messages: [{ role: "user", content: "hi" }],
    temperature: 0.2,
  });
  const call = fetchStub.calls[0];
  assert.equal(call.url, "http://ai.test/v1/chat/completions"); // trailing slash trimmed
  assert.equal(call.method, "POST");
  assert.equal(call.json.model, "kimi-k3");
  assert.equal(call.json.stream, false);
  assert.deepEqual(call.json.messages, [{ role: "user", content: "hi" }]);
  assert.equal(out.choices[0].message.content, "hello");
});

test("non-2xx surfaces HttpError with body", async () => {
  const fetchStub = makeFetchStub(() =>
    jsonResponse({ error: { message: "rate limited" } }, 429)
  );
  const ai = new AnimicaAI({ fetch: fetchStub });
  await assert.rejects(
    () => ai.chat.completions.create({ model: "kimi-k3", messages: [] }),
    (err) => {
      assert.ok(err instanceof HttpError);
      assert.equal(err.status, 429);
      assert.match(err.body ?? "", /rate limited/);
      return true;
    }
  );
});

test("streaming: SSE chunks parse across arbitrary chunk boundaries", async () => {
  const ev = (obj) => `data: ${JSON.stringify(obj)}\n\n`;
  const full =
    ": keepalive comment\n\n" +
    ev({ id: "c", object: "chat.completion.chunk", choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }] }) +
    ev({ id: "c", object: "chat.completion.chunk", choices: [{ index: 0, delta: { content: "Hel" }, finish_reason: null }] }) +
    ev({ id: "c", object: "chat.completion.chunk", choices: [{ index: 0, delta: { content: "lo!" }, finish_reason: null }] }) +
    ev({ id: "c", object: "chat.completion.chunk", choices: [{ index: 0, delta: {}, finish_reason: "stop" }] }) +
    "data: [DONE]\n\n";
  // Split at hostile boundaries: mid-"data:", mid-JSON, mid-"\n\n".
  const chunks = [];
  for (let i = 0; i < full.length; i += 7) chunks.push(full.slice(i, i + 7));

  const fetchStub = makeFetchStub(() => sseResponse(chunks));
  const ai = new AnimicaAI({ fetch: fetchStub });
  const stream = await ai.chat.completions.create({
    model: "kimi-k3",
    messages: [{ role: "user", content: "hi" }],
    stream: true,
  });

  assert.equal(fetchStub.calls[0].json.stream, true);
  assert.equal(fetchStub.calls[0].headers.accept, "text/event-stream");

  let text = "";
  let finish = null;
  let count = 0;
  for await (const chunk of stream) {
    count++;
    const d = chunk.choices[0];
    if (d.delta.content) text += d.delta.content;
    if (d.finish_reason) finish = d.finish_reason;
  }
  assert.equal(count, 4); // [DONE] is not yielded
  assert.equal(text, "Hello!");
  assert.equal(finish, "stop");
});

test("parseSseStream: CRLF line endings + multi-line data + missing trailing blank line", async () => {
  const raw =
    'data: {"a":\r\ndata: 1}\r\n\r\n' + // multi-line data joined with \n -> {"a":\n1}
    'data: {"b":2}'; // final event without trailing blank line
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(c) {
      c.enqueue(encoder.encode(raw));
      c.close();
    },
  });
  const got = [];
  for await (const obj of parseSseStream(body)) got.push(obj);
  assert.deepEqual(got, [{ a: 1 }, { b: 2 }]);
});

test("parseSseStream stops at [DONE] and ignores later events", async () => {
  const raw = 'data: {"x":1}\n\ndata: [DONE]\n\ndata: {"x":2}\n\n';
  const body = new Response(raw).body;
  const got = [];
  for await (const obj of parseSseStream(body)) got.push(obj);
  assert.deepEqual(got, [{ x: 1 }]);
});
