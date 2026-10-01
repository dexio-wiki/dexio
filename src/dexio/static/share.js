// ---- sharing (server/shares.py) -------------------------------------------
// The Share dialog, laid out as Google Docs' is (Forrest, 2026-09-28: "it
// should be like gdocs where you can share by email, or you can make it
// publically viewable"). Since 2026-10-01 it reads, top down: Visibility
// (Restricted, Anyone with the link, Published on dexio.wiki), the link with
// Copy, the Publish form when Published, and people by email when Restricted,
// with a close button at the top. Picking a level applies it at once, except
// Published, which waits for the form's own Publish (Forrest, 2026-10-01: "the
// dropdown should automatically apply the update. Except publication should
// require a second button press. This modal is also missing a close button").
// It opens for whatever the header's Share points at: the page open, else the
// folder drilled into, else the whole wiki. Loaded for members only.
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
  const COPY = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.6" y="5.6" width="8.4" height="8.4" rx="1.4"></rect>' +
    '<path d="M3.6 10.4h-.4A1.2 1.2 0 0 1 2 9.2V3.2C2 2.5 2.5 2 3.2 2h6c.7 0 1.2.5 1.2 1.2v.4"></path></svg>';
  const DONE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.4 6.6 11.4 12.5 4.8"></path></svg>';

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
  const LINK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.7 9.3a2.6 2.6 0 0 0 3.7 0' +
    'l2.1-2.1a2.6 2.6 0 0 0-3.7-3.7l-1 1M9.3 6.7a2.6 2.6 0 0 0-3.7 0L3.5 8.8a2.6 2.6 0 0 0 3.7 3.7l1-1">' +
    '</path></svg>';
  const UPLOAD = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 10.2V2.6M5.1 5.4 8 2.5' +
    'l2.9 2.9M2.8 9.6v2.6c0 .7.5 1.2 1.2 1.2h8c.7 0 1.2-.5 1.2-1.2V9.6"></path></svg>';
  // Visibility has three levels (Forrest, 2026-10-01: "the options should be
  // Restricted, Anyone with the link (stop google from indexing shared wikis),
  // Published on dexio.wiki"). Anyone with the link can read it, and search
  // engines are told not to list it; Published lists it on dexio.wiki, where
  // anyone can find it and make a copy, and only that is for search engines.
  // Publishing takes a wiki or a folder, with a name, a description and the
  // workspace's publisher name. It replaced a separate Publish button the same day.
  const ACCESS = {
    off: { name: "Restricted", icon: LOCK, desc: () => "Only people with access can open it" },
    link: { name: "Anyone with the link", icon: LINK,
            desc: () => "Anyone with the link can view it without signing in" },
    published: { name: "Published on dexio.wiki", icon: UPLOAD,
                 desc: () => (cur && !cur.public.publishable
                   ? "Publish a folder or the whole wiki"
                   : "Anyone can find it on dexio.wiki, read it and make a copy") },
  };
  // Published on dexio.wiki when picked but not yet published, and the Publish
  // form's data. Restricted and Anyone with the link apply as they are picked;
  // Published waits for Publish, and closing the dialog or picking another
  // level drops it. (From 9e4ed3a to this change everything waited for an
  // Update button at the foot, which is gone.)
  let staged = null, pstate = null;
  let role = "viewer", opening = "wiki";   // opening: the kind being loaded

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
      `<div class="sd-head"><h2 id="sd-title"></h2>` +
      `<button type="button" class="sd-close" aria-label="Close" title="Close (Esc)">${X}</button></div>` +
      `<p class="sd-what"></p>` +
      // Visibility comes first, under the title (Forrest, 2026-10-01: "the dropdown
      // should be at the top, yes? and perhaps change the name to something else from
      // 'general access'"); the people below it are only for Restricted.
      `<h3 class="sd-h sd-first">Visibility</h3>` +
      `<div class="sd-general"><span class="sd-gicon"></span><div class="sd-gtext">` +
      `<button type="button" id="sd-public" class="sd-pick sd-access-pick" data-label="Visibility">` +
      `<span class="sd-pick-now">Restricted</span>${CHEV}</button>` +
      `<p class="sd-gnote"></p></div></div>` +
      // The address itself, with Copy at its end, at every level (Forrest, 2026-10-01:
      // "B" of three designs for Copy link, then "we should show the link regardless
      // of which visibility is set, right?"). Under Restricted it opens for people
      // with access. One click on it selects all of it.
      `<div class="sd-link"><span class="sd-url" id="sd-url"></span>` +
      `<button class="sd-copy" type="button" aria-describedby="sd-url">${COPY}<span>Copy</span></button></div>` +
      // Published on dexio.wiki: what it shows there, under Visibility.
      `<div class="sd-publish" hidden>` +
      `<div class="pd-shot"><img alt="" width="640" height="360"><span class="pd-size"></span></div>` +
      `<div class="pd-fields">` +
      `<label class="sd-lf"><span>Name</span><input id="sd-p-title" type="text" autocomplete="off"></label>` +
      `<label class="sd-lf"><span>Description</span><textarea id="sd-p-desc" rows="2"></textarea></label>` +
      `<label class="sd-lf pd-pubname" hidden><span>Publisher name</span>` +
      `<input id="sd-p-publisher" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" ` +
      `maxlength="39" placeholder="wrenfield-roasters"></label></div>` +
      `<p class="pd-by"></p>` +
      // The second press Published takes; Save once it is, for its name and description.
      `<div class="sd-pfoot"><button class="sd-primary sd-pub" type="button">Publish</button></div>` +
      `</div>` +
      // Visibility's progress and errors, and Publish's.
      `<p class="sd-msg" id="sd-g-msg" role="status" aria-live="polite"></p>` +
      // Only under Restricted (Forrest, 2026-10-01: "this UI should only be visible if
      // Restricted is selected"): once anyone can read it, viewers add nothing.
      `<div class="sd-whosec">` +
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
      `<h3 class="sd-h">People with access</h3><ul class="sd-people"></ul></div>` +
      `</div>`;
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
    dlg.querySelector(".sd-close").onclick = close;
    dlg.querySelector(".sd-pub").onclick = publish;
    dlg.querySelector(".sd-publish").addEventListener("input", showPubButton);
    dlg.querySelector(".sd-copy").onclick = copy;
    dlg.querySelector(".sd-add").onsubmit = add;
    picker(dlg.querySelector("#sd-role"), "end",
           () => ({ viewer: ROLES.viewer, editor: { ...ROLES.editor, off: !!cur && !cur.editors.room } }),
           () => role, (v) => { role = v; showRole(); });
    picker(dlg.querySelector("#sd-public"), "start",
           () => ({ off: ACCESS.off, link: ACCESS.link,
                    published: { ...ACCESS.published, off: !!cur && !cur.public.publishable } }),
           () => (cur ? shown(cur) : "off"), choose);
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
    const url = dlg.querySelector("#sd-url");
    url.textContent = d.link.replace(/^https?:\/\//, "");
    url.title = d.link;
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
    const lv = shown(d);
    showPick(pub, ACCESS[lv]);
    pub.disabled = !!d.public.via;
    dlg.querySelector(".sd-gicon").innerHTML = ACCESS[lv].icon;
    dlg.querySelector(".sd-general").classList.toggle("open", lv !== "off");
    const via = d.public.via;
    note.textContent = via
      ? `${ACCESS[lv].name}, because ${via.kind === "wiki" ? "the whole wiki"
          : "the folder " + via.path} is shared that way. Change it there.`
      : lv === "published" ? (d.public.listed
          ? "Anyone can find it on dexio.wiki, read it and make a copy."
          : "Give it a name and a line on what it is, then click Publish.")
      : lv === "link" ? "Anyone with the link can view it without signing in."
      : "Only people with access can open it with the link.";
    showPublish(d, lv === "published" && !via);
    // People with access, only under Restricted, and no line in their place
    // (Forrest, 2026-10-01: "I don't think we need this line in the share modal",
    // the one on members being able to edit it).
    dlg.querySelector(".sd-whosec").hidden = lv !== "off";
  }

  // The level Visibility shows: what is staged, else what it is.
  function shown(d) {
    return (!d.public.via && staged) || level(d);
  }

  // A level picked in Visibility. Restricted and Anyone with the link apply at
  // once and the dialog stays open on the result; Published opens its form and
  // waits for Publish. Picking what it already is drops a waiting Published.
  async function choose(v) {
    if (!cur || busy) return;
    const t = cur.target, was = level(cur);
    gmsg("");
    if (v === "published" && was !== "published") {
      staged = v;
      render(cur);
      setTimeout(() => { const f = dlg.querySelector("#sd-p-title"); if (f && !f.closest("[hidden]")) f.focus(); }, 0);
      return;
    }
    staged = null;
    if (v === was) { render(cur); return; }
    busy = true;
    staged = v;                               // shows the pick while it saves
    render(cur);
    gmsg("Saving…");
    try {
      if (v === "link" && was === "published") {
        await call("POST", "publish", null, { kind: t.kind, path: t.path, on: false });
        pstate = null;
        staged = null;
        render(await call("GET", "share", { kind: t.kind, path: t.path }));
      } else {
        staged = null;
        render(await call("POST", "share/public", null, { kind: t.kind, path: t.path, on: v === "link" }));
      }
      gmsg(was === "published" ? (v === "link"
          ? "Taken off dexio.wiki. Anyone with the link can still view it."
          : "Taken off dexio.wiki. Only people with access can open it now.")
        : v === "link" ? "Saved. Anyone with the link can view it now."
        : "Saved. Only people with access can open it now.");
    } catch (err) {
      staged = null;
      render(cur);
      gmsg(err.message, true);
    } finally {
      busy = false;
    }
  }

  // Which level of Visibility a target is at: published (listed), anyone with
  // the link (public, not listed) or restricted; inherited from a wider share.
  function level(d) {
    if (d.public.via) return d.public.via.listed ? "published" : "link";
    if (d.public.listed) return "published";
    return d.public.on ? "link" : "off";
  }

  // The form under Published on dexio.wiki: the picture of its graph, its name and
  // description, and who it is by (the workspace's publisher name, which an owner
  // gives it here the first time). Read once per target from /publish.
  async function showPublish(d, show) {
    const sec = dlg.querySelector(".sd-publish");
    if (!show) { sec.hidden = true; return; }
    const key = d.target.kind + ":" + d.target.path;
    if (!pstate || pstate.key !== key) {
      sec.hidden = true;
      try {
        pstate = { key, ...(await call("GET", "publish", { kind: d.target.kind, path: d.target.path })) };
      } catch (err) {
        msg(err.message, true);
        return;
      }
      if (cur !== d) return;                  // another target opened meanwhile
      fillPublish(pstate);
    }
    showPubButton();
    sec.hidden = false;
  }

  function fillPublish(s) {
    const l = s.listing || {};
    for (const [id, k] of [["#sd-p-title", "title"], ["#sd-p-desc", "description"]]) {
      const f = dlg.querySelector(id);
      f.value = l[k] || "";
      f.maxLength = (l.limits || {})[k] || 300;
    }
    const img = dlg.querySelector(".sd-publish .pd-shot img");
    const src = url("publish/preview.svg", { kind: s.target.kind, path: s.target.path, v: s.version });
    if (img.getAttribute("src") !== src) img.src = src;
    dlg.querySelector(".sd-publish .pd-size").textContent =
      `${Number(s.pages || 0).toLocaleString()} page${s.pages === 1 ? "" : "s"}`;
    const naming = !l.publisher && l.can_name_publisher;
    dlg.querySelector(".sd-publish .pd-pubname").hidden = !naming;
    const by = dlg.querySelector(".sd-publish .pd-by");
    by.replaceChildren();
    if (l.publisher) {
      const a = el("a", "", l.publisher);
      a.href = `https://dexio.wiki/wikis/${l.publisher_slug}/`;
      a.target = "_blank"; a.rel = "noopener";
      by.append("By ", a, ", this workspace's publisher name.");
    } else if (naming) {
      by.append("Letters, numbers and hyphens, no spaces. Everything this workspace publishes " +
                "shows as by this name, which no other workspace can use.");
    } else {
      by.append("This workspace needs a publisher name before it can publish. Ask an owner to " +
                "set one in Settings, General.");
    }
  }

  function gmsg(text, bad) {
    const m = dlg.querySelector("#sd-g-msg");
    m.textContent = text || "";
    m.classList.toggle("bad", !!bad);
  }

  // Whether the Publish form says something other than what is published.
  function fieldsChanged() {
    if (!pstate) return false;
    const l = pstate.listing || {};
    const naming = !dlg.querySelector(".sd-publish .pd-pubname").hidden;
    return dlg.querySelector("#sd-p-title").value !== (l.title || "") ||
      dlg.querySelector("#sd-p-desc").value !== (l.description || "") ||
      (naming && dlg.querySelector("#sd-p-publisher").value.trim() !== "");
  }

  // Publish while Published waits. Once published it reads Published, greyed
  // out (Forrest, 2026-10-01: "If the wiki is published, the button should say
  // Published and be greyed out"), and turns into Save when the name or
  // description says something new.
  function showPubButton() {
    if (!cur) return;
    const b = dlg.querySelector(".sd-pub"), live = level(cur) === "published";
    const changed = live && fieldsChanged();
    b.textContent = !live ? "Publish" : changed ? "Save" : "Published";
    b.disabled = live && !changed;
  }

  // The dialog shares what Share was pressed on (the page open, else the folder in
  // view, else the whole wiki), and offers nothing wider: the line that did ("Share
  // the whole wiki instead") went on 2026-10-01 (Forrest: "can we get rid of this
  // button in the share modal?").

  async function open(kind, path) {
    if (!dlg) build();
    if (dlg.hidden) back = document.activeElement;   // not the dialog's own link
    for (const m of menus) m.close(false);
    cur = null;
    staged = null;
    pstate = null;
    opening = kind;
    role = "viewer";
    msg("");
    gmsg("");
    showRole();
    dlg.querySelector("#sd-email").value = "";
    dlg.querySelector("#sd-title").textContent = "Share";
    dlg.querySelector(".sd-what").textContent = "";
    dlg.querySelector("#sd-url").textContent = "";
    copied(false);
    dlg.querySelector(".sd-people").replaceChildren(el("li", "sd-loading", "Loading…"));
    dlg.hidden = false;
    document.body.classList.add("sd-open");
    focusFirst();
    try {
      render(await call("GET", "share", { kind, path }));
      const a = document.activeElement;
      if (!dlg.contains(a) || !a.offsetParent) focusFirst();
    } catch (e) {
      dlg.querySelector(".sd-people").replaceChildren();
      msg(e.message, true);
    }
  }

  // The email box where people can be added (Restricted), else Visibility, else
  // the link's Copy when Visibility is fixed by a wider share.
  function focusFirst() {
    const email = dlg.querySelector("#sd-email"), pub = dlg.querySelector("#sd-public");
    (!dlg.querySelector(".sd-whosec").hidden ? email
      : !pub.disabled ? pub : dlg.querySelector(".sd-copy")).focus();
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

  // Publish (the second press Published takes), or Save for a new name or
  // description once it is published. The dialog stays open on the result.
  async function publish() {
    if (!cur || busy || cur.public.via) return;
    const t = cur.target, was = level(cur);
    busy = true;
    gmsg(was === "published" ? "Saving…" : "Publishing…");
    try {
      const body = { kind: t.kind, path: t.path, on: true,
                     title: dlg.querySelector("#sd-p-title").value,
                     description: dlg.querySelector("#sd-p-desc").value };
      if (!dlg.querySelector(".sd-publish .pd-pubname").hidden) {
        body.publisher = dlg.querySelector("#sd-p-publisher").value;
      }
      const s = await call("POST", "publish", null, body);
      pstate = { key: t.kind + ":" + t.path, ...s };
      fillPublish(pstate);
      staged = null;
      render(await call("GET", "share", { kind: t.kind, path: t.path }));
      // after a first Publish the greyed Published button says it
      gmsg(was === "published" ? "Saved. dexio.wiki shows the new details within a few minutes." : "");
    } catch (err) {
      gmsg(err.message, true);
    } finally {
      busy = false;
    }
  }

  async function copy() {
    if (!cur) return;
    try {
      await navigator.clipboard.writeText(cur.link);
      copied(true);
    } catch (e) {
      window.prompt("Copy this link:", cur.link);
    }
    clearTimeout(copy.t);
    copy.t = setTimeout(() => copied(false), 2000);
  }

  // Copy, or for two seconds after, Copied with a check.
  function copied(on) {
    dlg.querySelector(".sd-copy").innerHTML = on ? `${DONE}<span>Copied</span>` : `${COPY}<span>Copy</span>`;
    dlg.querySelector(".sd-link").classList.toggle("done", on);
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
