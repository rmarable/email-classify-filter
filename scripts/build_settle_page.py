"""Build the settle phone page (a claude.ai artifact) from `ecf eval settle --export` output
(eval plan, Judges; OD-547).

Usage: `uv run python scripts/build_settle_page.py <export dir or its settle-page.json> <out.html>
[set label]`

The page embeds settle-page.json (no case ids, no judge names; the judges' answers are shuffled
per email) and works on a phone and a computer. It is published with the `db` capability; each
answer is saved as settle/<token> = {"answers": {field: value}, "on": "YYYY-MM-DD"} (UTC date).
Claude reads the collection back (ArtifactData), keeps only this export's tokens, and writes
{token: answers} to a file for `ecf eval settle --from <file> --tokens settle-tokens.json`.
Before saving, the page re-reads the email's document, so an answer saved from another device is
named and replaced only on a second, deliberate Save.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

PAGE = r"""<title>Settle Disagreements</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: a header with progress; on wide screens the email sits in a fixed left frame and the
   questions scroll in their own column; on a phone they stack, the email in its own scroll box. */
:root {
  --bg: #f3f5f6; --panel: #ffffff; --ink: #1c2429; --muted: #5b6870; --line: #d6dde1;
  --accent: #0f6b6b; --accent-ink: #ffffff; --chosen: #0f6b6b; --soft: #e4eeee;
  --note: #8a5a00; --note-bg: #fff4dc; --ok: #2f6b2f;
  --font-body: "Atkinson Hyperlegible", "Segoe UI", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #12171a; --panel: #1a2125; --ink: #e5ecef; --muted: #9aa8af; --line: #2e393f;
  --accent: #4fb3b0; --accent-ink: #0d1517; --chosen: #4fb3b0; --soft: #1f3335;
  --note: #f0c36a; --note-bg: #3a2e13; --ok: #8fd18f; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #12171a; --panel: #1a2125; --ink: #e5ecef; --muted: #9aa8af; --line: #2e393f;
  --accent: #4fb3b0; --accent-ink: #0d1517; --chosen: #4fb3b0; --soft: #1f3335;
  --note: #f0c36a; --note-bg: #3a2e13; --ok: #8fd18f; color-scheme: dark }
*, *::before, *::after { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { background: var(--bg); color: var(--ink); font-family: var(--font-body); font-size: 16px;
  line-height: 1.5; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px);
  z-index: 10; background: var(--bg); }
.app { display: flex; flex-direction: column; height: 100%; height: 100dvh; max-width: 1400px; margin: 0 auto; gap: 12px;
  padding-block: 12px; }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; }
h1 { font-size: 1.15rem; margin: 0; text-wrap: balance; }
.set { color: var(--muted); font-size: .85rem; }
.progress { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--muted); font-size: .9rem; }
.nav { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; width: 100%; }
.nav select { font: inherit; font-size: .9rem; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--line);
  background: var(--panel); color: var(--ink); max-width: 100%; }
.work { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: 16px; }
.email, .questions { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: auto; min-width: 0; }
.email { padding: 16px 18px; }
.email pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--font-mono);
  font-size: .86rem; line-height: 1.55; }
.questions { padding: 16px 18px; display: flex; flex-direction: column; gap: 18px; }
.count { font-size: .8rem; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.note { background: var(--note-bg); color: var(--note); border-radius: 6px; padding: 8px 12px; font-weight: 700; }
.field { display: flex; flex-direction: column; gap: 8px; }
.field h2 { font-size: 1rem; margin: 0; font-family: var(--font-mono); font-weight: 500; }
.help { color: var(--muted); font-size: .9rem; margin: 0; }
.choices { display: flex; flex-wrap: wrap; gap: 6px; }
.choice { border: 1px solid var(--line); background: var(--bg); color: var(--ink); border-radius: 999px;
  padding: 6px 14px; font: inherit; font-size: .92rem; cursor: pointer; }
.choice[aria-pressed="true"] { background: var(--chosen); color: var(--accent-ink); border-color: var(--chosen); }
.choice:focus-visible, .nav select:focus-visible, .btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.judges { font-size: .88rem; color: var(--ok); }
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.btn { border: 1px solid var(--accent); background: var(--accent); color: var(--accent-ink); border-radius: 6px;
  padding: 8px 16px; font: inherit; font-weight: 700; cursor: pointer; }
.btn.ghost { background: transparent; color: var(--accent); }
.btn:disabled { opacity: .45; cursor: default; }
.status { font-size: .88rem; color: var(--muted); }
.status.note, .progress.note { color: var(--note); }
.work[hidden] { display: none; }
details { border-top: 1px solid var(--line); padding-top: 12px; font-size: .9rem; }
summary { cursor: pointer; font-weight: 700; }
details p { margin: 8px 0 0; color: var(--muted); }
@media (max-width: 860px) {
  .app { height: auto; }
  .work { grid-template-columns: minmax(0, 1fr); }
  .email { max-height: 55vh; }
  .questions { overflow: visible; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
</style>
<div class="app">
  <header>
    <h1>Settle the judges' disagreements</h1>
    <span class="set" id="setlabel"></span>
    <span class="progress" id="progress">loading</span>
    <nav class="nav" id="nav" aria-label="Emails"></nav>
  </header>
  <div class="work">
    <section class="email" aria-label="The email as a mail client shows it"><pre id="view"></pre></section>
    <section class="questions" id="questions" aria-label="Your answers"></section>
  </div>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const SET = __SET__;
const items = DATA.items;
const saved = {};   // token -> answers saved in the db
const draft = {};   // token -> answers being chosen
const failed = {};  // token -> true when its last save failed
const other = {};   // token -> answers another device saved, shown until this page saves or matches them
let at = 0, db = null, busy = false, loading = true, loadFailed = false, helpOpen = false;
let holdUntil = 0;  // after a conflict, Save waits a moment so a double tap can't replace the other answer

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}
function show(v) { return v === true ? "yes" : v === false ? "no" : v === undefined || v === null ? "(none)" : String(v); }
function answered(t) { return !!saved[t]; }
function mineFor(it) { return draft[it.t] || (draft[it.t] = Object.assign({}, saved[it.t] || {})); }
function same(a, b) { return !!a && !!b && Object.keys(a).length === Object.keys(b).length
  && Object.keys(a).every(k => a[k] === b[k]); }
function nextUnanswered(from) {
  for (let k = 1; k <= items.length; k++) {
    // a conflict with another device still needs a look, so it counts as unanswered here
    const i = (from + k) % items.length; if (!answered(items[i].t) || other[items[i].t]) return i;
  }
  return -1;
}
function go(i) {
  at = i; render(true);
  // when focus was lost (a nav button that just became disabled, or Safari, which doesn't focus
  // buttons on click), put it on the first choice
  const active = document.activeElement;
  if (!active || active === document.body || active.disabled) { const c = document.querySelector(".choice"); if (c) c.focus({ preventScroll: true }); }
}

function buildNav() {
  // built once; renderNav updates it in place so keyboard focus stays where it is
  const sel = el("select", { id: "jump", "aria-label": "Go to email", onchange: e => go(Number(e.target.value)) });
  items.forEach((it, i) => sel.append(el("option", { value: String(i) }, "Email " + (i + 1))));
  document.getElementById("nav").replaceChildren(
    el("button", { class: "btn ghost", type: "button", id: "prev", onclick: () => go((at - 1 + items.length) % items.length) }, "Previous"),
    el("button", { class: "btn ghost", type: "button", id: "next", onclick: () => go((at + 1) % items.length) }, "Next"),
    el("button", { class: "btn ghost", type: "button", id: "nextun", onclick: () => go(nextUnanswered(at)) }, "Next to do"),
    sel);
}

function renderNav() {
  // an email in conflict with another device is "to check", not "answered"
  const c = items.filter(it => other[it.t]).length, n = items.filter(it => answered(it.t)).length - c;
  document.getElementById("progress").classList.toggle("note", loadFailed);
  document.getElementById("progress").textContent = loadFailed
    ? "Your saved answers didn't load: reload the page before answering."
    : n + " of " + items.length + " answered" + (c ? ", " + c + " to check" : "");
  const sel = document.getElementById("jump");
  items.forEach((it, i) => { sel.options[i].textContent = "Email " + (i + 1) + (other[it.t] ? "  (check)" : answered(it.t) ? "  (answered)" : ""); });
  sel.value = String(at);
  const nu = nextUnanswered(at);
  document.getElementById("nextun").disabled = nu < 0 || nu === at;
}

function updateState(it) {
  // the Save button and the status line, without rebuilding the panel
  const mine = mineFor(it), done = answered(it.t);
  const complete = it.fields.every(f => f.id in mine);
  const save = document.getElementById("save"), status = document.getElementById("status");
  const unchanged = done && same(mine, saved[it.t]);
  const hadOther = !!other[it.t];
  if (unchanged) { delete failed[it.t]; delete other[it.t]; }  // matches the db: nothing pending
  if (hadOther && !other[it.t]) renderNav();  // drop the email's "(check)" mark now, not at the next move
  const held = Date.now() < holdUntil;
  save.disabled = !(complete && db && !busy && !loading && !loadFailed && !unchanged && !held);
  save.textContent = other[it.t] && !unchanged ? "Replace the other device's answer" : done ? "Save changes" : "Save answer";
  status.classList.toggle("note", loadFailed || !!other[it.t]);
  status.textContent = loading ? "Loading your saved answers..."
    : busy ? "Saving..."
    : !db ? "Saving needs the page open in Claude (signed in): sign in, or reload the page."
    : loadFailed ? "Couldn't load your saved answers: reload the page before answering, or an earlier answer could be overwritten."
    : other[it.t] ? "Not saved: another device answered this email (" + it.fields.map(f => f.id + " " + show(other[it.t][f.id])).join(", ")
        + "). Save to replace that with your choice here, or pick the same answers to keep it."
    : failed[it.t] && !unchanged ? "Last save failed: check you're signed in, then try again."
    : !complete ? "Answer every field to save."
    : done ? (unchanged ? "Saved." : "Changed, not saved.") : "";
}

function render(reset) {
  const it = items[at];
  document.getElementById("view").textContent = it.view;
  const q = document.getElementById("questions");
  if (reset) document.querySelector(".email").scrollTop = 0;
  q.replaceChildren();
  if (reset) q.scrollTop = 0;
  const mine = mineFor(it), done = answered(it.t);
  q.append(el("div", { class: "count" }, "Email " + (at + 1) + " of " + items.length));
  if (it.note) q.append(el("div", { class: "note" }, it.note.charAt(0).toUpperCase() + it.note.slice(1) + "."));
  for (const f of it.fields) {
    const choices = f.kind === "boolean" ? [true, false] : f.values;
    const row = el("div", { class: "choices", role: "group", "aria-label": f.id });
    for (const c of choices) {
      const b = el("button", { class: "choice", type: "button", "aria-pressed": String(mine[f.id] === c) }, show(c));
      b.addEventListener("click", () => {
        mine[f.id] = c;
        for (const o of row.children) o.setAttribute("aria-pressed", String(o === b));
        updateState(it);
      });
      row.append(b);
    }
    const box = el("div", { class: "field" }, el("h2", {}, f.id), el("p", { class: "help" }, f.help), row);
    if (done) box.append(el("div", { class: "judges" }, "Judges said: " + (it.judges[f.id] || []).map(show).join(", ")));
    q.append(box);
  }
  q.append(el("div", { class: "actions" },
    el("button", { class: "btn", type: "button", id: "save", onclick: () => save(it) }, "Save answer"),
    el("span", { class: "status", id: "status", role: "status", tabindex: "-1" }, "")));
  const help = el("details", { open: helpOpen ? "" : null }, el("summary", {}, "Phishing, and signs to look for"),
    el("p", {}, DATA.phishing), el("p", {}, "Signs (general, not about this email): " + DATA.signs + "."));
  help.addEventListener("toggle", () => { helpOpen = help.open; });
  q.append(help);
  updateState(it);
  renderNav();
}

async function save(it) {
  const mine = mineFor(it);
  if (!db || busy || loading || loadFailed || Date.now() < holdUntil || !it.fields.every(f => f.id in mine)) return;
  busy = true; updateState(it);
  const answers = {}; for (const f of it.fields) answers[f.id] = mine[f.id];
  let error = null, elsewhere = null;
  try {
    const ref = db.doc("settle/" + it.t);
    // another device may have saved this email since the page loaded: don't replace that unseen
    const read = async () => {
      const c = await within(30000, ref.get());
      if (c.metadata && c.metadata.fromCache) throw { code: "unavailable" };
      return c;
    };
    let cur;
    try { cur = await read(); }
    catch (e) {
      if (!e || e.code !== "unavailable") throw e;
      await new Promise(ok => setTimeout(ok, 500 + Math.random() * 1000));
      cur = await read();
    }
    const v = cur.exists ? cur.data() : null;
    if (v && v.answers && !same(v.answers, saved[it.t]) && !same(v.answers, answers)) elsewhere = v.answers;
    else if (v && v.answers && same(v.answers, answers)) { /* already in the db (same answer, or a write that timed out but landed) */ }
    else await within(30000, ref.set({ answers, on: new Date().toISOString().slice(0, 10) }));
  } catch (e) { error = e || {}; }
  busy = false;
  if (elsewhere) {
    saved[it.t] = Object.assign({}, elsewhere); other[it.t] = saved[it.t]; delete failed[it.t];
    holdUntil = Date.now() + 1500;
    setTimeout(() => { if (!busy) updateState(items[at]); }, 1600);
    if (items[at] !== it) { renderNav(); updateState(items[at]); return; }  // the status says it on return
    render(false);
    const st = document.getElementById("status");
    st.focus({ preventScroll: true });
    st.scrollIntoView({ block: "center" });  // the message must be on screen beside Save
    return;
  }
  if (error) {
    failed[it.t] = true; updateState(items[at]);
    if (items[at] === it && !same(mineFor(it), saved[it.t])) document.getElementById("status").textContent =
      (error.code === "timeout" ? "Not saved (timed out): try again."
        : "Not saved (" + (error.code || "error") + "): check you're signed in, then try again.");
    return;
  }
  // the draft is left as it is: a choice made while saving stays, and shows as "Changed, not saved."
  saved[it.t] = answers; delete failed[it.t]; delete other[it.t];
  if (items[at] !== it) { renderNav(); updateState(items[at]); return; }  // moved on meanwhile
  render(false);  // shows the judges' answers for this email; Next to do moves on
  const nextun = document.getElementById("nextun");
  (nextun.disabled ? document.getElementById("status") : nextun).focus({ preventScroll: true });
  const shown = document.querySelector(".judges");
  if (shown) shown.scrollIntoView({ block: "center" });
}
function within(ms, p) {
  return Promise.race([p, new Promise((_, no) => setTimeout(() => no({ code: "timeout" }), ms))]);
}

document.getElementById("setlabel").textContent = SET;
if (!items.length) {
  document.getElementById("progress").textContent = "Nothing to settle: the judges agree on every open field.";
  document.querySelector(".work").hidden = true;
} else {
buildNav();
render(true);
(async () => {
  try { db = window.claude ? await within(15000, window.claude.use("db")) : null; } catch (e) { db = null; }
  if (!db) { loading = false; render(false); return; }
  try {
    // the first call can carry a consent prompt, so the bound is generous; "unavailable" is retried once
    const read = () => within(90000, db.collection("settle").limit(1000).get());
    let snap;
    try { snap = await read(); }
    catch (e) {
      if (!e || e.code !== "unavailable") throw e;
      await new Promise(ok => setTimeout(ok, 500 + Math.random() * 1000));
      snap = await read();
    }
    if (snap.metadata && snap.metadata.fromCache) throw new Error("not definitive");
    if (snap.size >= 1000) throw new Error("too many to be sure");  // a page could be missing
    const mine = new Set(items.map(it => it.t));
    for (const d of snap.docs) { const v = d.data(); if (v && v.answers && mine.has(d.id)) saved[d.id] = v.answers; }
  } catch (e) { loadFailed = true; }  // saving stays off: it could overwrite answers it can't see
  for (const t in saved) draft[t] = Object.assign({}, saved[t], draft[t] || {});
  const first = answered(items[at].t) ? nextUnanswered(at) : at;
  at = first < 0 ? 0 : first;
  loading = false;
  render(true);
})();
}
</script>
"""


def build(data: dict[str, Any], label: str = "") -> str:
    """The page for one export (`data` is left as it is). `<` is escaped in both embedded values
    so neither can close the script; the data goes in last, so nothing inside it is ever
    substituted. The output is ASCII, so a local copy reads right without a charset."""
    rng = random.SystemRandom()
    data = json.loads(json.dumps(data))  # a copy: the shuffle below must not reach the caller
    for it in data["items"]:  # the judges' order differs per email, so it names no judge
        for vals in it.get("judges", {}).values():
            rng.shuffle(vals)
    blob = json.dumps(data).replace("<", "\\u003c")
    return (
        PAGE.replace("__DATA__", "\0DATA\0")
        .replace("__SET__", json.dumps(label).replace("<", "\\u003c"))
        .replace("\0DATA\0", blob)
    )


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    src, out = Path(argv[0]), Path(argv[1])
    if src.name == "settle-page.json" and src.is_file():
        src = src.parent  # the file itself was given: use its folder
    if not (src / "settle-page.json").is_file():
        print(f"no settle-page.json in {src}: give the folder `ecf eval settle --export` wrote",
              file=sys.stderr)  # fmt: skip
        return 2
    data = json.loads((src / "settle-page.json").read_text(encoding="utf-8"))
    html = build(data, argv[2] if len(argv) == 3 else "")
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}: {len(data['items'])} emails, {len(html) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
