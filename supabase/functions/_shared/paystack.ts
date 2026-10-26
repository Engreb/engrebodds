// Paystack: API calls, webhook signatures, and the accounting fields we keep (docs/phase-5-plan.md).
// No Deno-specific APIs, so the handlers can be tested with a fake fetch.

export type Mode = "test" | "live";
export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export interface Transaction {
  id: number;
  reference: string;
  status: string; // success | failed | abandoned | ongoing | pending | reversed ...
  amount: number; // kobo
  currency: string;
  channel?: string;
  paid_at?: string | null;
  fees?: number | null;
  gateway_response?: string | null;
}

/** test / live from the secret key's prefix; anything else is refused. */
export function keyMode(secret: string | undefined): Mode | null {
  if (secret?.startsWith("sk_test_")) return "test";
  if (secret?.startsWith("sk_live_")) return "live";
  return null;
}

/** Only what accounting needs: no card details, customer data or IP addresses. */
export function strip(tx: Partial<Transaction> & Record<string, unknown>): Record<string, unknown> {
  return {
    id: tx.id ?? null, reference: tx.reference ?? null, amount: tx.amount ?? null, currency: tx.currency ?? null,
    status: tx.status ?? null, channel: tx.channel ?? null, paid_at: tx.paid_at ?? null, fees: tx.fees ?? null,
    gateway_response: tx.gateway_response ?? null,
  };
}

const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

/** Paystack signs the raw body with HMAC-SHA512 using the secret key (x-paystack-signature, hex). */
export async function sign(rawBody: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-512" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(rawBody)));
}

export async function validSignature(rawBody: string, signature: string | null, secret: string): Promise<boolean> {
  if (!signature) return false;
  const expected = await sign(rawBody, secret);
  if (expected.length !== signature.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  return diff === 0;
}

export class Paystack {
  constructor(private secret: string, private fetchImpl: Fetch = fetch, private base = "https://api.paystack.co") {}

  private async call(path: string, init: RequestInit = {}): Promise<{ status: number; body: Record<string, unknown> }> {
    const res = await this.fetchImpl(this.base + path, {
      ...init, headers: { Authorization: `Bearer ${this.secret}`, "Content-Type": "application/json" },
    });
    return { status: res.status, body: await res.json().catch(() => ({})) };
  }

  async initialize(p: { email: string; amount: number; currency: string; reference: string; callback_url: string;
                        metadata: Record<string, unknown> }): Promise<string> {
    const { status, body } = await this.call("/transaction/initialize", {
      method: "POST", body: JSON.stringify({ ...p, channels: ["card", "bank_transfer", "ussd"] }),
    });
    const url = (body.data as { authorization_url?: string } | undefined)?.authorization_url;
    if (status !== 200 || !body.status || !url) throw new Error(`Paystack initialize failed (${status}): ${body.message ?? ""}`);
    return url;
  }

  /** The transaction as Paystack has it, or null if Paystack doesn't know the reference. */
  async verify(reference: string): Promise<Transaction | null> {
    const { status, body } = await this.call(`/transaction/verify/${encodeURIComponent(reference)}`);
    if (status === 404 || (status === 400 && /not found/i.test(String(body.message ?? "")))) return null;
    if (status !== 200 || !body.status) throw new Error(`Paystack verify failed (${status}): ${body.message ?? ""}`);
    return body.data as Transaction;
  }
}
