// ---- sharing (server/shares.py) -------------------------------------------
// The Share dialog, laid out as Google Docs' is (Forrest, 2026-09-28: "it
// should be like gdocs where you can share by email, or you can make it
// publically viewable"): add people by email, the people with access, general
// access (Restricted or Anyone with the link), Copy link and Done. It opens for
// whatever the header's Share points at: the page open, else the folder drilled
// into, else the whole wiki. The dialog offers the wider ones from there.
// Loaded for members only.
(function () {
  const API = window.DEXIO_API, W = window.DEXIO_WORKSPACE;
  if (!API || !W) return;
  let dlg = null, cur = null, back = null, busy = false;

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

  const LOCK = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.4"></rect>' +
    '<path d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7"></path></svg>';
  const GLOBE = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.8"></circle>' +
    '<path d="M2.4 8h11.2M8 2.2c1.7 1.6 2.5 3.6 2.5 5.8S9.7 12.2 8 13.8M8 2.2C6.3 3.8 5.5 5.8 5.5 8s.8 4.2 2.5 5.8"></path></svg>';
  const X = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"></path></svg>';
  const TEAM = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="5.6" cy="5.6" r="2.2"></circle>' +
    '<circle cx="11" cy="6.2" r="1.8"></circle><path d="M1.8 13c.4-2.2 1.9-3.4 3.8-3.4s3.4 1.2 3.8 3.4' +
    'M10.2 9.8c1.9-.3 3.5.8 4 3.2"></path></svg>';
  const EYE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.6 8S4 3.6 8 3.6 14.4 8 14.4 8 12 12.4 8 12.4 1.6 8 1.6 8z">' +
    '</path><circle cx="8" cy="8" r="2"></circle></svg>';
  const PENCIL = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10.6 2.9a1.5 1.5 0 0 1 2.1 0l.4.4a1.5 1.5 0 0 1 0 2.1' +
    'L6 12.5l-3.2.8.8-3.2zM9.5 4l2.5 2.5"></path></svg>';
  const CHEV = '<svg class="sw-chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 6.2 8 9.7l3.5-3.5"></path></svg>';
  const CHECK = '<svg class="sw-check" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.4 6.6 11.4 12.5 4.8"></path></svg>';

  // What the two pickers offer. Editor's line changes when the plan has no room.
  const ROLES = {
    viewer: { name: "Viewer", icon: EYE, desc: () => "Can read it, but not change it" },
    editor: {
      name: "Editor", icon: PENCIL,
      desc: () => (cur && !cur.editors.room
        ? (cur.editors.plan === "free" ? "Free is for one person" : "Your plan has no room left") +
          ". To add editors, choose a plan under Settings, Plan."
        : "Joins the workspace and can change the wiki"),
    },
  };
  const ACCESS = {
    off: { name: "Restricted", icon: LOCK, desc: () => "Only people with access can open it" },
    on: { name: "Public on the web", icon: GLOBE,
          desc: () => "Anyone can view it without signing in" },
  };
  let role = "viewer", opening = "wiki";   // opening: the kind being loaded
  let listingOpen = false;                 // the listing fields, ticked open, not yet listed

  function initials(name) {
    const parts = String(name || "?").replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
    return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
  }
  function hue(s) {
    let h = 0;
    for (const c of String(s || "")) h = (h * 31 + c.charCodeAt(0)) % 360;
    return h;
  }
  // name: how the person is shown ("Forrest (you)"); initials come from `plain`,
  // their plain name or address.
  function person(name, email, role, extra, plain) {
    const li = el("li", "sd-person");
    const av = el("span", "sd-avatar", initials(plain || name || email));
    av.style.setProperty("--h", hue(email));
    av.setAttribute("aria-hidden", "true");
    const who = el("span", "sd-who");
    who.append(el("span", "sd-name", name || email));
    if (name && email && name !== email) who.append(el("span", "sd-email", email));
    if (extra) who.append(el("span", "sd-note", extra));
    li.append(av, who, el("span", "sd-role", role));
    return li;
  }

  function what(t) {
    if (t.kind === "wiki") return "The whole wiki: every page and file in it, now and later.";
    if (t.kind === "folder") return `The folder ${t.path}: every page in it and in the folders under it, now and later.`;
    return `The page ${t.path}.`;
  }

  // ---- pickers ----
  // Each choice is a button that opens a menu drawn in the page's own colours,
  // like Layout and Show over the graph: arrow keys move, Enter picks, Escape or
  // a click elsewhere closes. They were native selects, whose closed box and
  // open list the browser drew (Forrest, 2026-09-28: "please improve the design
  // of the viewer and restricted dropdowns. as a standing rule we should not use
  // native browser dropdowns"). The menu hangs off the backdrop, not the box,
  // so the box's scrolling never clips it, and opens upward when there is no
  // room below.
  const menus = [];
  function picker(button, align, options, current, pick) {
    const menu = el("div", "sd-menu");
    menu.id = button.id + "-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", button.dataset.label);
    menu.hidden = true;
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", menu.id);
    dlg.append(menu);
    const live = () => [...menu.querySelectorAll(".sw-item:not(:disabled)")];
    function place() {
      const r = button.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
      const vw = document.documentElement.clientWidth, vh = window.innerHeight;
      const left = Math.max(12, Math.min(align === "end" ? r.right - w : r.left, vw - w - 12));
      const below = r.bottom + 6, above = r.top - 6 - h;
      menu.style.left = `${left}px`;
      menu.style.top = `${below + h > vh - 12 && above >= 12 ? above : below}px`;
    }
    function open(last) {
      for (const m of menus) m.close(false);
      const now = current();
      menu.innerHTML = Object.entries(options()).map(([value, o]) =>
        `<button type="button" class="sw-item" role="menuitemradio" tabindex="-1" data-value="${value}" ` +
        `aria-checked="${value === now}"${o.off ? " disabled" : ""}><span class="sd-micon">${o.icon}</span>` +
        `<span class="lo-text"><span class="sw-name">${o.name}</span><span class="lo-desc">${o.desc()}</span>` +
        `</span>${CHECK}</button>`).join("");
      menu.hidden = false;
      place();
      button.setAttribute("aria-expanded", "true");
      const list = live();
      const t = last ? list[list.length - 1] : list.find((i) => i.getAttribute("aria-checked") === "true") || list[0];
      if (t) t.focus();
    }
    function close(refocus) {
      if (menu.hidden) return;
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (refocus) button.focus();
    }
    button.addEventListener("click", () => (menu.hidden ? open(false) : close(true)));
    button.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      open(e.key === "ArrowUp");
    });
    menu.addEventListener("click", (e) => {
      const item = e.target.closest(".sw-item");
      if (!item || item.disabled) return;
      close(true);
      if (item.dataset.value !== current()) pick(item.dataset.value);
    });
    menu.addEventListener("keydown", (e) => {
      const list = live(), i = list.indexOf(document.activeElement);
      const go = (j) => { e.preventDefault(); if (list.length) list[(j + list.length) % list.length].focus(); };
      if (e.key === "ArrowDown") go(i + 1);
      else if (e.key === "ArrowUp") go(i - 1);
      else if (e.key === "Home") go(0);
      else if (e.key === "End") go(list.length - 1);
      // stopped here so the dialog's own Escape does not close the dialog too
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
      // back to the button, and the Tab goes on from there
      else if (e.key === "Tab") close(true);
    });
    const m = { close, menu, button };
    menus.push(m);
    return m;
  }

  // The button shows the current choice; disabled, it is a plain label.
  function showPick(button, o) {
    button.querySelector(".sd-pick-now").textContent = o.name;
    button.setAttribute("aria-label", `${button.dataset.label}: ${o.name}`);
  }

  function build() {
    dlg = el("div", "sd-backdrop");
    dlg.id = "share-dlg";
    dlg.hidden = true;
    dlg.innerHTML =
      `<div class="sd-box" role="dialog" aria-modal="true" aria-labelledby="sd-title">` +
      `<h2 id="sd-title"></h2><p class="sd-what"></p>` +
      `<p class="sd-scope" hidden></p>` +
      `<form class="sd-add" novalidate>` +
      `<label class="sd-label" for="sd-email">Add people</label>` +
      `<div class="sd-row"><input id="sd-email" type="email" autocomplete="email" ` +
      `placeholder="Email address" aria-describedby="sd-msg">` +
      `<button type="button" id="sd-role" class="sd-pick sd-role-pick" data-label="Access">` +
      `<span class="sd-pick-now">Viewer</span>${CHEV}</button>` +
      `<span id="sd-role-fixed" class="sd-role-fixed" hidden>${EYE}<span>Viewer</span></span>` +
      `<button class="sd-primary" type="submit">Share</button></div>` +
      `<p class="sd-hint" id="sd-role-hint" hidden></p>` +
      `<p class="sd-msg" id="sd-msg" role="status" aria-live="polite"></p></form>` +
      `<h3 class="sd-h">People with access</h3><ul class="sd-people"></ul>` +
      `<h3 class="sd-h">General access</h3>` +
      `<div class="sd-general"><span class="sd-gicon"></span><div class="sd-gtext">` +
      `<button type="button" id="sd-public" class="sd-pick sd-access-pick" data-label="General access">` +
      `<span class="sd-pick-now">Restricted</span>${CHEV}</button>` +
      `<p class="sd-gnote"></p></div></div>` +
      // The directory on dexio.wiki (Forrest, 2026-10-01: "users opt in to publish
      // their wiki to the dexio website when they share publically"). Only for
      // something public on its own; off until its owner ticks it.
      `<label class="sd-list" hidden><input type="checkbox" id="sd-listed">` +
      `<span class="sd-list-text"><span class="sd-list-name">List on dexio.wiki</span>` +
      `<span class="sd-list-desc">Anyone can find it in the public wikis on dexio.wiki and ` +
      `make a copy of it in their own workspace.</span></span></label>` +
      // What the listing says (Forrest, 2026-10-01: "when they publish a wiki, we should
      // allow them to give it a name and description", and "show the author"). Opens
      // when the box is ticked; nothing is listed until List it is clicked.
      `<div class="sd-listing" hidden>` +
      `<label class="sd-lf"><span>Name</span><input id="sd-l-title" type="text" autocomplete="off"></label>` +
      `<label class="sd-lf"><span>Description</span><textarea id="sd-l-desc" rows="2"></textarea></label>` +
      `<label class="sd-lf"><span>Author</span><input id="sd-l-author" type="text" autocomplete="off"></label>` +
      `<p class="sd-hint">These show with it on dexio.wiki, with a picture of its graph.</p>` +
      `<div class="sd-lactions"><button class="sd-primary" type="button" id="sd-l-save">List it</button></div>` +
      `</div>` +
      `<div class="sd-foot"><button class="sd-quiet sd-copy" type="button">Copy link</button>` +
      `<button class="sd-primary sd-done" type="button">Done</button></div></div>`;
    document.body.append(dlg);
    const box = dlg.querySelector(".sd-box");
    dlg.addEventListener("pointerdown", (e) => {
      for (const m of menus) if (!m.menu.contains(e.target) && !m.button.contains(e.target)) m.close(false);
      if (e.target === dlg) close();
    });
    // a menu stays where it opened; it closes rather than drift from its button
    box.addEventListener("scroll", () => { for (const m of menus) m.close(false); });
    window.addEventListener("resize", () => { for (const m of menus) m.close(false); });
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
      if (e.key !== "Tab") return;
      const f = [...box.querySelectorAll("input, textarea, button")].filter((x) => !x.disabled && x.offsetParent);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    });
    dlg.querySelector(".sd-done").onclick = close;
    dlg.querySelector(".sd-copy").onclick = copy;
    dlg.querySelector(".sd-add").onsubmit = add;
    dlg.querySelector("#sd-listed").onchange = (e) => {
      if (!cur) return;
      if (e.target.checked) {                 // open the fields; List it lists it
        listingOpen = true;
        render(cur);
        dlg.querySelector("#sd-l-title").focus();
      } else if (cur.public.listed) {
        setListed(false);
      } else {
        listingOpen = false;
        render(cur);
      }
    };
    dlg.querySelector("#sd-l-save").onclick = () => setListed(true, {
      title: dlg.querySelector("#sd-l-title").value,
      description: dlg.querySelector("#sd-l-desc").value,
      author: dlg.querySelector("#sd-l-author").value,
    });
    picker(dlg.querySelector("#sd-role"), "end",
           () => ({ viewer: ROLES.viewer, editor: { ...ROLES.editor, off: !!cur && !cur.editors.room } }),
           () => role, (v) => { role = v; showRole(); });
    picker(dlg.querySelector("#sd-public"), "start", () => ACCESS,
           () => (cur && cur.public.on ? "on" : "off"), (v) => setPublic(v === "on"));
  }

  function msg(text, bad) {
    const m = dlg.querySelector(".sd-msg");
    m.textContent = text || "";
    m.classList.toggle("bad", !!bad);
  }

  // The role, and what it means. Where Editor is not on offer (a folder or a
  // page, or the whole wiki shared by someone who is not an owner), the row
  // shows a fixed Viewer where the picker was, and the line under it says what
  // a viewer can do and where editors come from (Forrest, 2026-09-28: "when
  // sharing a folder, it should be more apparent that the recipient of the
  // share only has viewer permissions"). The picker used to just disappear,
  // leaving the role unsaid until someone was already in the list.
  function showRole() {
    const fixed = cur ? !cur.editors.allowed : opening !== "wiki";
    const pick = dlg.querySelector("#sd-role");
    pick.hidden = fixed;
    dlg.querySelector("#sd-role-fixed").hidden = !fixed;
    showPick(pick, ROLES[role]);
    const hint = dlg.querySelector("#sd-role-hint");
    let text = "";
    if (fixed) text = cur ? viewOnly(cur) : "";
    else if (role === "editor") {
      text = "Editors are members of the workspace: they, and the agents they connect, can " +
        "change the wiki. They get an invite by email." +
        (cur && cur.workspace.plan !== "free" ? " Each is a seat on your plan." : "");
    }
    hint.hidden = !text;
    hint.textContent = text;
    hint.classList.toggle("sd-viewonly", fixed);
  }

  function viewOnly(d) {
    const t = d.target;
    const owner = d.members.some((m) => m.you && m.role === "owner");
    const can = t.kind === "folder" ? "can open this folder and everything in it"
      : t.kind === "page" ? "can open this page" : "can open the whole wiki";
    const editors = !owner ? "Only an owner can add editors."
      : "Editors can only be added to the whole wiki.";
    return `People you add here are viewers: they ${can}, but can't change anything. ${editors}`;
  }

  function render(d) {
    cur = d;
    const t = d.target;
    dlg.querySelector("#sd-title").textContent = `Share “${t.title}”`;
    dlg.querySelector(".sd-what").textContent = what(t);
    scope(t);
    // Editors are members: the whole wiki only, owners only, room on the plan.
    // With no room, Editor is greyed out in the menu and says why there.
    if (!d.editors.allowed || !d.editors.room) role = "viewer";
    showRole();

    const list = dlg.querySelector(".sd-people");
    list.replaceChildren();
    if (t.kind === "wiki") {
      for (const m of d.members) {
        list.append(person((m.name || m.email) + (m.you ? " (you)" : ""), m.email,
                           m.role === "owner" ? "Owner" : "Editor", "", m.name || m.email));
      }
      for (const i of d.invites) list.append(person(null, i.email, "Editor", "Invite sent"));
    } else {
      const n = d.members.length;
      const li = person(`Everyone in ${d.workspace.name}`, "", n === 1 ? "Editor" : "Editors",
                        `${n} ${n === 1 ? "member" : "members"}`);
      li.querySelector(".sd-avatar").innerHTML = TEAM;
      li.querySelector(".sd-avatar").classList.add("sd-team");
      list.append(li);
    }
    for (const p of d.people) {
      const bits = [];
      if (p.pending) bits.push("Link sent, not opened yet");
      if (p.via) bits.push(p.via.kind === "wiki" ? "From the whole wiki" : `From folder ${p.via.path}`);
      const li = person(p.name, p.email, "Viewer", bits.join(" · "));
      const rm = el("button", "sd-remove");
      rm.type = "button";
      rm.innerHTML = X;
      rm.title = p.via ? "Stop sharing " + (p.via.kind === "wiki" ? "the whole wiki" : "folder " + p.via.path) +
        " with " + p.email : "Stop sharing with " + p.email;
      rm.setAttribute("aria-label", rm.title);
      rm.onclick = () => remove(p);
      li.append(rm);
      list.append(li);
    }

    const pub = dlg.querySelector("#sd-public"), note = dlg.querySelector(".sd-gnote");
    const on = d.public.on;
    showPick(pub, ACCESS[on ? "on" : "off"]);
    pub.disabled = !!d.public.via;
    dlg.querySelector(".sd-gicon").innerHTML = on ? GLOBE : LOCK;
    dlg.querySelector(".sd-general").classList.toggle("open", on);
    note.textContent = d.public.via
      ? `Public on the web, because ${d.public.via.kind === "wiki"
          ? "the whole wiki" : "the folder " + d.public.via.path} is shared that way. Change it there.`
      : on ? "Anyone can view it without signing in."
           : "Only people with access can open it with the link.";
    const own = on && d.public.own, listed = !!d.public.listed;
    dlg.querySelector(".sd-list").hidden = !own;
    dlg.querySelector("#sd-listed").checked = listed || listingOpen;
    const form = dlg.querySelector(".sd-listing"), show = own && (listed || listingOpen);
    // Filled when the fields come into view, so a re-render never undoes typing.
    if (show && form.hidden && d.public.listing) {
      const l = d.public.listing;
      for (const [id, key] of [["#sd-l-title", "title"], ["#sd-l-desc", "description"],
                               ["#sd-l-author", "author"]]) {
        const field = dlg.querySelector(id);
        field.value = l[key] || "";
        field.maxLength = (l.limits || {})[key] || 300;
      }
    }
    form.hidden = !show;
    dlg.querySelector("#sd-l-save").textContent = listed ? "Save" : "List it";
  }

  // Opened for a page or a folder, the wider scopes are a click away: the
  // folder drilled into (for a page inside it), then the whole wiki.
  function scope(t) {
    const p = dlg.querySelector(".sd-scope");
    p.replaceChildren();
    p.hidden = t.kind === "wiki";
    if (p.hidden) return;
    const f = window.dexio && window.dexio.focused && window.dexio.focused();
    const link = (text, kind, path) => {
      const b = el("button", "sd-link", text);
      b.type = "button";
      b.onclick = () => open(kind, path);
      return b;
    };
    if (t.kind === "page" && f && t.path.startsWith(f + "/")) {
      p.append("Share ", link(`the folder ${f}`, "folder", f), " or ",
               link("the whole wiki", "wiki", ""), " instead");
    } else {
      p.append(link("Share the whole wiki instead", "wiki", ""));
    }
  }

  async function open(kind, path) {
    if (!dlg) build();
    if (dlg.hidden) back = document.activeElement;   // not the dialog's own link
    for (const m of menus) m.close(false);
    cur = null;
    listingOpen = false;
    dlg.querySelector(".sd-listing").hidden = true;
    opening = kind;
    role = "viewer";
    msg("");
    showRole();
    dlg.querySelector("#sd-email").value = "";
    dlg.querySelector("#sd-title").textContent = "Share";
    dlg.querySelector(".sd-what").textContent = "";
    dlg.querySelector(".sd-people").replaceChildren(el("li", "sd-loading", "Loading…"));
    dlg.hidden = false;
    document.body.classList.add("sd-open");
    dlg.querySelector("#sd-email").focus();
    try {
      render(await call("GET", "share", { kind, path }));
    } catch (e) {
      dlg.querySelector(".sd-people").replaceChildren();
      msg(e.message, true);
    }
  }

  function close() {
    if (!dlg || dlg.hidden) return;
    for (const m of menus) m.close(false);
    dlg.hidden = true;
    document.body.classList.remove("sd-open");
    if (back && back.focus) back.focus();
  }

  async function add(e) {
    e.preventDefault();
    if (!cur || busy) return;
    const input = dlg.querySelector("#sd-email"), email = input.value.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      msg("Enter an email address.", true);
      input.focus();
      return;
    }
    busy = true;
    msg("Sending…");
    try {
      render(await call("POST", "share", null,
                        { kind: cur.target.kind, path: cur.target.path, email, role }));
      input.value = "";
      msg(role === "editor" ? `Invited ${email} to edit. They get an email to accept.`
                            : `Shared with ${email} as a viewer. They get an email with a link to open it.`);
    } catch (err) {
      msg(err.message, true);
    } finally {
      busy = false;
    }
  }

  async function remove(p) {
    if (!cur || busy) return;
    busy = true;
    try {
      render(await call("DELETE", `share/${p.id}`, { kind: cur.target.kind, path: cur.target.path }));
      msg(`Stopped sharing with ${p.email}.`);
    } catch (err) {
      msg(err.message, true);
    } finally {
      busy = false;
    }
  }

  async function setPublic(on) {
    if (!cur || busy) return;
    const wasListed = !!cur.public.listed;
    busy = true;
    try {
      render(await call("POST", "share/public", null,
                        { kind: cur.target.kind, path: cur.target.path, on }));
      msg(on ? "It is public now: anyone can view it without signing in."
             : "Only people with access can open it now." +
               (wasListed ? " It is no longer listed on dexio.wiki." : ""));
    } catch (err) {
      msg(err.message, true);
      render(cur);
    } finally {
      busy = false;
    }
  }

  async function setListed(on, fields) {
    if (!cur || busy) return;
    const was = !!cur.public.listed;
    busy = true;
    try {
      const d = await call("POST", "share/listed", null,
                           { kind: cur.target.kind, path: cur.target.path, on, ...(fields || {}) });
      listingOpen = false;
      if (!on) dlg.querySelector(".sd-listing").hidden = true;   // filled afresh next time
      render(d);
      msg(!on ? "Taken off dexio.wiki. It is still public to anyone with the link."
          : was ? "Saved. dexio.wiki shows the new details within a few minutes."
          : "Listed on dexio.wiki: anyone can find it there and make a copy.");
    } catch (err) {
      msg(err.message, true);
      render(cur);
    } finally {
      busy = false;
    }
  }

  async function copy() {
    if (!cur) return;
    const b = dlg.querySelector(".sd-copy");
    try {
      await navigator.clipboard.writeText(cur.link);
      b.textContent = "Link copied";
    } catch (e) {
      window.prompt("Copy this link:", cur.link);
    }
    setTimeout(() => { b.textContent = "Copy link"; }, 2000);
  }

  window.dexioShare = open;
  // The header's Share shares what is showing (graph.js, shareTarget): the page
  // open, else the folder drilled into (Forrest, 2026-09-28: "the Share should
  // be aware of which folder is currently active"), else the whole wiki. The
  // page head's own Share is gone ("it's weird seeing the share button above
  // another share button like this").
  const head = document.getElementById("share-wiki");
  if (head) head.addEventListener("click", () => {
    const t = window.dexio && window.dexio.shareTarget ? window.dexio.shareTarget()
                                                       : { kind: "wiki", path: "" };
    open(t.kind, t.path);
  });
})();
