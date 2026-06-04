import { Body, Controller, Get, Headers, Post, Res } from "@nestjs/common";
import type { Response } from "express";
import { InferenceService } from "./inference.service";
import { Public } from "../common/auth.guard";
import { CurrentUser } from "../common/current-user.decorator";
import type { AuthUser } from "../common/auth.guard";

// OpenAI-compatible surface. Authenticated by Bearer API key (anm_live_…),
// NOT the session cookie — hence @Public (the service resolves the key).
@Public()
@Controller("v1")
export class InferenceController {
  constructor(private readonly inference: InferenceService) {}

  @Get("models")
  models() {
    return this.inference.listModels();
  }

  @Post("chat/completions")
  async chat(@Headers("authorization") auth: string, @Body() body: any, @Res() res: Response) {
    const completion = await this.inference.chatCompletion(auth, body);
    if (!body?.stream) {
      res.json(completion);
      return;
    }
    // OpenAI-compatible SSE. The result is computed in full then streamed as
    // chat.completion.chunk deltas (the worker job model is request/response,
    // so this chunks the final text rather than true token streaming).
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("Connection", "keep-alive");
    const base = { id: completion.id, object: "chat.completion.chunk", created: completion.created, model: completion.model };
    const content: string = completion.choices?.[0]?.message?.content ?? "";
    const write = (d: object) => res.write(`data: ${JSON.stringify(d)}\n\n`);
    write({ ...base, choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }] });
    for (let i = 0; i < content.length; i += 40) {
      write({ ...base, choices: [{ index: 0, delta: { content: content.slice(i, i + 40) }, finish_reason: null }] });
    }
    write({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] });
    res.write("data: [DONE]\n\n");
    res.end();
  }

  @Post("completions")
  completions(@Headers("authorization") auth: string, @Body() body: any) {
    return this.inference.completion(auth, body);
  }

  @Post("embeddings")
  embeddings(@Headers("authorization") auth: string, @Body() body: any) {
    return this.inference.embeddings(auth, body);
  }

  @Get("usage")
  usage(@Headers("authorization") auth: string) {
    return this.inference.usage(auth);
  }
}

// Session-authed (cookie) surface for the web UI — the in-browser playground
// runs real inference without the user pasting an API key. Charges the logged-
// in user's credits exactly like a keyed request.
@Controller("api/ai")
export class AiUiController {
  constructor(private readonly inference: InferenceService) {}

  @Post("chat")
  chat(@CurrentUser() user: AuthUser, @Body() body: any) {
    return this.inference.chatCompletionForUser(user.id, body);
  }
}
