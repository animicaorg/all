// Tiny offline fetch stub — records requests, returns canned Responses.
// No network access ever happens in tests.

export function makeFetchStub(handler) {
  const calls = [];
  const stub = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.toString();
    const record = {
      url,
      method: init.method ?? "GET",
      headers: init.headers ?? {},
      body: init.body,
      json: undefined,
    };
    if (typeof init.body === "string") {
      try {
        record.json = JSON.parse(init.body);
      } catch {
        /* not JSON */
      }
    }
    calls.push(record);
    return handler(record);
  };
  stub.calls = calls;
  return stub;
}

export function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Build an SSE Response whose body arrives in the given raw string chunks. */
export function sseResponse(chunks, status = 200) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(body, {
    status,
    headers: { "content-type": "text/event-stream" },
  });
}
