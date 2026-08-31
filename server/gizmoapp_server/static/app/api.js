function runtimeTimeout() {
  return window.GizmoAppRuntime?.readConfig().requestTimeoutMs || 15000;
}


export async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs || runtimeTimeout();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const contentType = response.headers.get("content-type") || "";
    const requestId = response.headers.get("x-request-id");
    if (!contentType.includes("application/json")) {
      throw new Error(`The server returned an unexpected response (${response.status}).${requestId ? ` Request ${requestId}.` : ""}`);
    }
    const payload = await response.json();
    if (!response.ok) {
      const message = payload.errors?.join("; ") || `Request failed with ${response.status}`;
      const responseRequestId = payload.requestId || requestId;
      throw new Error(responseRequestId ? `${message} (request ${responseRequestId})` : message);
    }
    return payload;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds.`);
    }
    if (error instanceof TypeError) {
      throw new Error("The redesign service could not be reached. Check that the preview is online, then try again.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}


export function fetchBootstrap(apiBase) {
  return requestJson(`${apiBase}/bootstrap`);
}


export function fetchCapabilities(apiBase) {
  return requestJson(`${apiBase}/capabilities`);
}


export function searchRecords(apiBase, query) {
  const params = new URLSearchParams({ q: query });
  return requestJson(`${apiBase}/search?${params.toString()}`);
}
