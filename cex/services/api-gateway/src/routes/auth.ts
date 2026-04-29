import { Request, Response, Router } from "express";

type AuthProxyOptions = {
  authServiceUrl: string;
};

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade"
]);

function stripTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

export function createAuthProxyRouter(options: AuthProxyOptions): Router {
  const router = Router();
  const authServiceBaseUrl = stripTrailingSlash(options.authServiceUrl);

  const proxyRequest = async (req: Request, res: Response) => {
    const targetUrl = `${authServiceBaseUrl}${req.originalUrl}`;

    const outboundHeaders = new Headers();
    for (const [name, rawValue] of Object.entries(req.headers)) {
      if (!rawValue || HOP_BY_HOP_HEADERS.has(name.toLowerCase()) || name.toLowerCase() === "host") {
        continue;
      }
      if (Array.isArray(rawValue)) {
        for (const value of rawValue) {
          outboundHeaders.append(name, value);
        }
      } else {
        outboundHeaders.set(name, rawValue);
      }
    }

    let body: string | undefined;
    if (req.method !== "GET" && req.method !== "HEAD") {
      body = JSON.stringify(req.body ?? {});
      if (!outboundHeaders.has("content-type")) {
        outboundHeaders.set("content-type", "application/json");
      }
    }

    try {
      const upstreamResponse = await fetch(targetUrl, {
        method: req.method,
        headers: outboundHeaders,
        body,
        redirect: "manual"
      });

      for (const [name, value] of upstreamResponse.headers.entries()) {
        if (HOP_BY_HOP_HEADERS.has(name.toLowerCase())) {
          continue;
        }
        if (name.toLowerCase() === "set-cookie") {
          continue;
        }
        res.setHeader(name, value);
      }

      const getSetCookie = (upstreamResponse.headers as Headers & { getSetCookie?: () => string[] }).getSetCookie;
      const setCookies = typeof getSetCookie === "function" ? getSetCookie.call(upstreamResponse.headers) : [];
      if (setCookies.length > 0) {
        res.setHeader("set-cookie", setCookies);
      } else {
        const singleSetCookie = upstreamResponse.headers.get("set-cookie");
        if (singleSetCookie) {
          res.setHeader("set-cookie", singleSetCookie);
        }
      }

      const responseBody = Buffer.from(await upstreamResponse.arrayBuffer());
      res.status(upstreamResponse.status).send(responseBody);
    } catch {
      res.status(502).json({ message: "Auth service unavailable" });
    }
  };

  router.all("/auth", proxyRequest);
  router.all("/auth/*", proxyRequest);

  return router;
}
