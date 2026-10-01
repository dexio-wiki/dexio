// ---- Publish (server/shares.py publish_state and publish, preview.py) ----------
// Forrest, 2026-10-01: "should we have a second button for publish, rather than lump
// it into share?", then "yes". Publishing puts a wiki, or the folder in view, among
// the public wikis on dexio.wiki in one step: public on its own, and listed with the
// name, description and author the directory shows, beside a picture of its graph.
// Unpublish takes it off the directory and leaves it public; Share makes it private.
// Loaded for members only, after share.js, whose dialog styles it borrows.
(function () {
  const API = window.DEXIO_API, W = window.DEXIO_WORKSPACE;
  if (!API || !W) return;
  const SITE = "https://dexio.wiki/wikis/";
  let dlg = null, cur = null, back = null, busy = false, offer = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function url(route, params) {
    const q = new URLSearchParams({ ...(params || {}), w: W });
    return `${API}/${route}?${q}`;
  }
  async function call(method, route, params, body) {
    const opts = { method, headers: {} };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let r;
    try { r = await fetch(url(route, params), opts); }
    catch (e) { throw new Error("Could not reach Dexio. Check your connection and try again."); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((data && data.error) || "That did not work. Try again.");
    return data;
  }
  const plural = (n, w) => `${Number(n || 0).toLocaleString()} ${w}${n === 1 ? "" : "s"}`;

  function build() {
    dlg = el("div", "sd-backdrop");
    dlg.id = "publish-dlg";
    dlg.hidden = true;
    dlg.innerHTML =
      `<div class="sd-box pd-box" role="dialog" aria-modal="true" aria-labelledby="pd-title">` +
      `<h2 id="pd-title">Publish on dexio.wiki</h2>` +
      `<p class="sd-what">Anyone can read it without signing in, find it among the public wikis on ` +
      `dexio.wiki, and make a copy of it in their own workspace.</p>` +
      `<div class="pd-scope" role="radiogroup" aria-label="What to publish" hidden></div>` +
      `<p class="pd-status" hidden></p>` +
      `<div class="pd-shot"><img alt="" width="640" height="360"><span class="pd-size"></span></div>` +
      `<div class="pd-fields">` +
      `<label class="sd-lf"><span>Name</span><input id="pd-title-in" type="text" autocomplete="off"></label>` +
      `<label class="sd-lf"><span>Description</span><textarea id="pd-desc" rows="2"></textarea></label>` +
      `<label class="sd-lf"><span>Author</span><input id="pd-author" type="text" autocomplete="off"></label>` +
      `</div>` +
      `<p class="sd-msg" id="pd-msg" role="status" aria-live="polite"></p>` +
      `<div class="sd-foot"><button class="sd-quiet" type="button" id="pd-unpublish" hidden>Unpublish</button>` +
      `<span class="pd-gap"></span><button class="sd-quiet" type="button" id="pd-cancel">Cancel</button>` +
      `<button class="sd-primary" type="button" id="pd-go">Publish</button></div></div>`;
    document.body.append(dlg);
    const box = dlg.querySelector(".pd-box");
    dlg.addEventListener("pointerdown", (e) => { if (e.target === dlg) close(); });
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
      if (e.key !== "Tab") return;
      const f = [...box.querySelectorAll("input, textarea, button, a[href]")]
        .filter((x) => !x.disabled && x.offsetParent);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    });
    dlg.querySelector("#pd-cancel").onclick = close;
    dlg.querySelector("#pd-go").onclick = () => save(true);
    dlg.querySelector("#pd-unpublish").onclick = () => save(false);
  }

  function msg(text, bad) {
    const m = dlg.querySelector("#pd-msg");
    m.textContent = text || "";
    m.classList.toggle("bad", !!bad);
  }

  // A folder in view can be published on its own or with the whole wiki.
  function scope() {
    const box = dlg.querySelector(".pd-scope");
    box.replaceChildren();
    box.hidden = !offer;
    if (!offer) return;
    for (const [kind, path, label] of [["folder", offer, `The folder ${offer}`],
                                       ["wiki", "", "The whole wiki"]]) {
      const b = el("button", "pd-pill", label);
      b.type = "button";
      b.setAttribute("role", "radio");
      const on = cur && cur.target.kind === kind && cur.target.path === path;
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.onclick = () => { if (!on) load(kind, path); };
      box.append(b);
    }
  }

  function render(d, fill) {
    cur = d;
    scope();
    const status = dlg.querySelector(".pd-status");
    status.hidden = !d.published;
    status.innerHTML = "";
    if (d.published) {
      status.append("Published. ");
      const a = el("a", "", "See it on dexio.wiki");
      a.href = SITE; a.target = "_blank"; a.rel = "noopener";
      status.append(a);
    }
    const img = dlg.querySelector(".pd-shot img");
    const src = url("publish/preview.svg", { kind: d.target.kind, path: d.target.path, v: d.version });
    if (img.getAttribute("src") !== src) img.src = src;
    dlg.querySelector(".pd-size").textContent = plural(d.pages, "page");
    if (fill) {
      const l = d.listing || {};
      for (const [id, key] of [["#pd-title-in", "title"], ["#pd-desc", "description"],
                               ["#pd-author", "author"]]) {
        const f = dlg.querySelector(id);
        f.value = l[key] || "";
        f.maxLength = (l.limits || {})[key] || 300;
      }
    }
    dlg.querySelector("#pd-unpublish").hidden = !d.published;
    dlg.querySelector("#pd-go").textContent = d.published ? "Save" : "Publish";
  }

  async function load(kind, path) {
    msg("");
    try {
      render(await call("GET", "publish", { kind, path }), true);
    } catch (err) {
      msg(err.message, true);
    }
  }

  async function open(kind, path, folder) {
    if (!dlg) build();
    if (dlg.hidden) back = document.activeElement;
    offer = folder || (kind === "folder" ? path : null);
    cur = null;
    msg("");
    dlg.querySelector(".pd-scope").hidden = true;
    dlg.hidden = false;
    document.body.classList.add("sd-open");
    await load(kind, path);
    const t = dlg.querySelector("#pd-title-in");
    if (t && !dlg.hidden) t.focus();
  }

  function close() {
    if (!dlg || dlg.hidden) return;
    dlg.hidden = true;
    document.body.classList.remove("sd-open");
    if (back && back.focus) back.focus();
  }

  async function save(on) {
    if (!cur || busy) return;
    const was = cur.published;
    busy = true;
    msg(on ? (was ? "Saving…" : "Publishing…") : "");
    try {
      const body = { kind: cur.target.kind, path: cur.target.path, on };
      if (on) {
        body.title = dlg.querySelector("#pd-title-in").value;
        body.description = dlg.querySelector("#pd-desc").value;
        body.author = dlg.querySelector("#pd-author").value;
      }
      render(await call("POST", "publish", null, body), !on);
      msg(!on ? "Taken off dexio.wiki. It is still public to anyone with the link; Share can make it private."
          : was ? "Saved. dexio.wiki shows the new details within a few minutes."
          : "Published. It shows among the public wikis on dexio.wiki within a few minutes.");
    } catch (err) {
      msg(err.message, true);
    } finally {
      busy = false;
    }
  }

  window.dexioPublish = open;
  // The header's Publish publishes the folder in view, or else the whole wiki.
  const head = document.getElementById("publish-wiki");
  if (head) head.addEventListener("click", () => {
    const f = window.dexio && window.dexio.focused ? window.dexio.focused() : null;
    if (f) open("folder", f, f);
    else open("wiki", "");
  });
})();
