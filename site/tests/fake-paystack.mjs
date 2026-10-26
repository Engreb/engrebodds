// A LOCAL stand-in for Paystack, for trying checkout without a Paystack account. Never deployed.
// The Edge Functions use it when supabase/functions/.env.local sets PAYSTACK_BASE=http://host.docker.internal:4399.
//
//   node site/tests/fake-paystack.mjs
//
// It answers transaction/initialize and transaction/verify like Paystack, shows a checkout page with
// "Pay" / "Pay the wrong amount" / "Cancel", and sends a correctly signed charge.success webhook.
import { createHmac } from "node:crypto";
import { createServer } from "node:http";

const PORT = 4399;
const SECRET = process.env.FAKE_PAYSTACK_SECRET ?? "sk_test_local_fake";
const WEBHOOK = process.env.FAKE_PAYSTACK_WEBHOOK ?? "http://127.0.0.1:54321/functions/v1/paystack-webhook";
const txs = new Map();
let nextId = 1000;

const json = (res, status, body) => { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(body)); };
const readBody = (req) => new Promise((ok) => { let b = ""; req.on("data", (c) => (b += c)); req.on("end", () => ok(b)); });
const naira = (kobo) => `₦${(kobo / 100).toLocaleString("en-NG")}`;

createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  if (req.method === "POST" && url.pathname === "/transaction/initialize") {
    if (req.headers.authorization !== `Bearer ${SECRET}`) return json(res, 401, { status: false, message: "Invalid key" });
    const p = JSON.parse(await readBody(req));
    txs.set(p.reference, { ...p, id: nextId++, status: "abandoned", paid_at: null, channel: null });
    return json(res, 200, { status: true, data: { authorization_url: `http://localhost:${PORT}/checkout/${encodeURIComponent(p.reference)}`,
                                                  reference: p.reference } });
  }
  const verify = url.pathname.match(/^\/transaction\/verify\/(.+)$/);
  if (req.method === "GET" && verify) {
    const tx = txs.get(decodeURIComponent(verify[1]));
    if (!tx) return json(res, 400, { status: false, message: "Transaction reference not found" });
    const { id, reference, status, amount, currency, channel, paid_at } = tx;
    return json(res, 200, { status: true, data: { id, reference, status, amount, currency, channel, paid_at, fees: 0,
                                                  gateway_response: status === "success" ? "Approved" : "Abandoned",
                                                  customer: { email: tx.email }, authorization: { last4: "4081" } } });
  }
  const page = url.pathname.match(/^\/checkout\/([^/]+)(\/(pay|short|cancel))?$/);
  if (page) {
    const ref = decodeURIComponent(page[1]);
    const tx = txs.get(ref);
    if (!tx) { res.writeHead(404); return res.end("Unknown reference"); }
    if (req.method === "POST" && (page[3] === "pay" || page[3] === "short")) {
      Object.assign(tx, { status: "success", channel: "card", paid_at: new Date().toISOString(),
                          amount: page[3] === "short" ? Math.round(tx.amount / 2) : tx.amount });
      const body = JSON.stringify({ event: "charge.success", data: { reference: ref, amount: tx.amount, currency: tx.currency } });
      const sig = createHmac("sha512", SECRET).update(body).digest("hex");
      const hook = await fetch(WEBHOOK, { method: "POST", headers: { "Content-Type": "application/json", "x-paystack-signature": sig }, body })
        .then((r) => `${r.status} ${r.statusText}`, (e) => `failed: ${e.message}`);
      console.log(`paid ${ref} (${naira(tx.amount)}); webhook ${hook}`);
    }
    if (req.method === "POST") {
      res.writeHead(303, { Location: `${tx.callback_url}?trxref=${encodeURIComponent(ref)}&reference=${encodeURIComponent(ref)}` });
      return res.end();
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(`<!doctype html><meta name="viewport" content="width=device-width"><title>Fake Paystack</title>
<body style="font:16px system-ui;max-width:420px;margin:40px auto;padding:0 16px">
<p style="background:#fde7dc;padding:8px 12px;border-radius:8px"><b>Local fake Paystack.</b> No real payment system is involved.</p>
<h2>Pay ${naira(tx.amount)}</h2><p>${tx.email} · ${tx.metadata?.plan ?? ""} · ref ${ref}</p>
<form method="post" action="/checkout/${encodeURIComponent(ref)}/pay"><button style="font-size:18px;padding:10px 18px">Pay (test card)</button></form><br>
<form method="post" action="/checkout/${encodeURIComponent(ref)}/short"><button>Pay the wrong amount (half)</button></form><br>
<form method="post" action="/checkout/${encodeURIComponent(ref)}/cancel"><button>Cancel</button></form></body>`);
  }
  res.writeHead(404);
  res.end();
}).listen(PORT, () => console.log(`Fake Paystack on http://localhost:${PORT} (webhook -> ${WEBHOOK})`));
