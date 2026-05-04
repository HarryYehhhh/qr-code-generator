import type {
  CreateQRCodeResponse,
  GetQRCodeResponse,
  QRCodeListItem,
} from "../types/qrCode";

const BASE_URL = import.meta.env.VITE_API_URL || "";

export async function listQRCodes(): Promise<QRCodeListItem[]> {
  const resp = await fetch(`${BASE_URL}/v1/qr_codes`);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function createQRCode(url: string): Promise<CreateQRCodeResponse> {
  const resp = await fetch(`${BASE_URL}/v1/qr_code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function getQRCode(
  token: string
): Promise<{ data?: GetQRCodeResponse; status: number }> {
  const resp = await fetch(`${BASE_URL}/v1/qr_code/${token}`);
  if (resp.status === 410) {
    return { status: 410 };
  }
  if (resp.status === 404) {
    return { status: 404 };
  }
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  const data: GetQRCodeResponse = await resp.json();
  return { data, status: 200 };
}

export function getQRCodeImageUrl(
  token: string,
  options?: { dimension?: number; color?: string; border?: number }
): string {
  const params = new URLSearchParams();
  if (options?.dimension) params.set("dimension", String(options.dimension));
  if (options?.color) params.set("color", options.color);
  if (options?.border !== undefined) params.set("border", String(options.border));

  const qs = params.toString() ? `?${params.toString()}` : "";
  return `${BASE_URL}/v1/qr_code_image/${token}${qs}`;
}

export interface RedirectTestResult {
  status: number;
  location: string | null;
  ok: boolean;
}

export async function testRedirect(token: string): Promise<RedirectTestResult> {
  const resp = await fetch(`${BASE_URL}/r/${token}`, {
    redirect: "manual",
  });
  return {
    status: resp.status,
    location: resp.headers.get("location"),
    // opaqueredirect yields status 0 in fetch — treat as success
    ok: resp.status === 302 || resp.type === "opaqueredirect",
  };
}

export async function updateQRCode(token: string, url: string): Promise<void> {
  const resp = await fetch(`${BASE_URL}/v1/qr_code/${token}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!resp.ok && resp.status !== 204) {
    const err = await resp.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
}

export async function deleteQRCode(token: string): Promise<void> {
  const resp = await fetch(`${BASE_URL}/v1/qr_code/${token}`, {
    method: "DELETE",
  });
  if (!resp.ok && resp.status !== 204) {
    const err = await resp.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
}
