import type { APIRoute } from "astro";

const API_URL = import.meta.env.API_URL || "http://127.0.0.1:8000";

async function proxy(request: Request, path: string): Promise<Response> {
  const upstream = `${API_URL}/${path}`;

  const headers = new Headers(request.headers);
  headers.delete("host");

  const init: RequestInit = {
    method: request.method,
    headers,
  };

  if (!["GET", "HEAD"].includes(request.method)) {
    init.body = request.body;
    // @ts-ignore
    init.duplex = "half";
  }

  try {
    const res = await fetch(upstream, init);
    const responseHeaders = new Headers(res.headers);
    // Allow SSE through
    if (res.headers.get("content-type")?.includes("text/event-stream")) {
      responseHeaders.set("Cache-Control", "no-cache");
      responseHeaders.set("X-Accel-Buffering", "no");
    }
    return new Response(res.body, {
      status: res.status,
      headers: responseHeaders,
    });
  } catch (err) {
    return new Response(JSON.stringify({ detail: `Upstream error: ${err}` }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}

export const GET: APIRoute = async ({ params, request }) => {
  return proxy(request, params.path ?? "");
};

export const POST: APIRoute = async ({ params, request }) => {
  return proxy(request, params.path ?? "");
};

export const PUT: APIRoute = async ({ params, request }) => {
  return proxy(request, params.path ?? "");
};

export const DELETE: APIRoute = async ({ params, request }) => {
  return proxy(request, params.path ?? "");
};
