// "Copy selections" buttons: the text to copy is rendered on the server into data-copy.
document.addEventListener("click", async (e) => {
  const btn = (e.target as Element).closest<HTMLButtonElement>("button[data-copy]");
  if (!btn) return;
  const label = btn.textContent;
  try {
    await navigator.clipboard.writeText(btn.dataset.copy ?? "");
    btn.textContent = "Copied";
  } catch {
    btn.textContent = "Copy failed: select the list instead";
  }
  setTimeout(() => (btn.textContent = label), 2000);
});
