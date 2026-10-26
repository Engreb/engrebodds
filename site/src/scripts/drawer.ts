// Match drawer: a match tapped in a list opens in a side panel instead of a new page (as in the prototype).
// It loads the match page itself, so the panel shows exactly what that page shows the visitor, nothing more.
// Without JavaScript, or on any failure, the link simply opens the full page.
import { localTimes } from "./localtime";

let lastFocus: HTMLElement | null = null;
let openUrl: string | null = null;

function close(fromHistory = false) {
  document.getElementById("drawerRoot")?.remove();
  document.body.style.overflow = "";
  if (!fromHistory && openUrl) history.back();
  openUrl = null;
  lastFocus?.focus();
}

async function open(href: string, push = true) {
  const res = await fetch(href, { headers: { accept: "text/html" } });
  if (!res.ok) throw new Error(String(res.status));
  const page = new DOMParser().parseFromString(await res.text(), "text/html");
  const body = page.querySelector("main");
  if (!body) throw new Error("no content");

  document.getElementById("drawerRoot")?.remove();
  const root = document.createElement("div");
  root.id = "drawerRoot";
  root.innerHTML = `<div class="scrim"><aside class="drawer" role="dialog" aria-modal="true" aria-label="Match details">
    <div class="row" style="justify-content:space-between"><a class="btn sec" href="${href}">Open full page</a>
    <button class="x" type="button">Close</button></div></aside></div>`;
  const drawer = root.querySelector("aside")!;
  drawer.append(...body.childNodes);
  document.body.append(root);
  document.body.style.overflow = "hidden";
  localTimes(drawer);

  root.querySelector(".x")!.addEventListener("click", () => close());
  root.querySelector(".scrim")!.addEventListener("click", (e) => { if (e.target === e.currentTarget) close(); });
  if (push) history.pushState({ drawer: href }, "", href);
  openUrl = href;
  root.querySelector<HTMLButtonElement>(".x")!.focus();
}

document.addEventListener("click", (e) => {
  const a = (e.target as Element).closest<HTMLAnchorElement>("a.fx");
  if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  lastFocus = a;
  open(a.getAttribute("href")!).catch(() => { location.href = a.href; });
});

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openUrl) close(); });
window.addEventListener("popstate", () => { if (openUrl) close(true); });
