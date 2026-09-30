// Google Analytics behind a consent check (Forrest, 2026-09-28). Shared by dexio.wiki and
// app.dexio.wiki: this is the engine's copy of dexio-www's src/scripts/consent.js, so change both.
//
// The answer lives in the dexio_consent cookie on dexio.wiki, so one answer covers the site
// and the app, for 180 days. A device whose time zone is in the EEA, the UK or Switzerland
// is asked first, and nothing loads until it says yes. Everyone else gets Analytics as the
// page loads. Anyone can change their answer: any element with data-consent-open shows the
// banner (#consent) again, and buttons inside it carry data-consent="granted|denied".
//
// Google only ever sees the page's origin and path, plus campaign tags (utm_*, gclid). Other
// query strings can carry tokens or wiki addresses (the app's ?next=), and a same-site
// referrer can name a wiki page, so both are cut before the tag reads them.
(function () {
  var ID = "G-N3CK5LV6BK", NAME = "dexio_consent", AGE = 180 * 86400;
  var ASK = /^(Europe\/|Arctic\/Longyearbyen$|Atlantic\/(Azores|Canary|Faroe|Madeira|Reykjavik)$|Africa\/Ceuta$|Asia\/(Nicosia|Famagusta)$|America\/(Cayenne|Guadeloupe|Marigot|Martinique|St_Barthelemy)$|Indian\/(Mayotte|Reunion)$|(CET|EET|MET|WET|GB|GB-Eire|Eire|Iceland|Poland|Portugal)$)/;
  var KEEP = /^(utm_[a-z]+|gclid|gbraid|wbraid)$/;

  function saved() {
    var m = /(?:^|;\s*)dexio_consent=(granted|denied)/.exec(document.cookie);
    return m ? m[1] : null;
  }
  function mustAsk() {
    try { return ASK.test(Intl.DateTimeFormat().resolvedOptions().timeZone || ""); }
    catch (e) { return true; }
  }
  var ours = /(^|\.)dexio\.wiki$/.test(location.hostname);
  // Only Dexio's own hosts load Analytics. A self-hosted copy of the engine loads nothing
  // from Google and never shows the banner. (The site's copy has no need of this line.)
  if (!ours) { window.dexioConsent = { active: function () { return false; } }; return; }
  function remember(v) {
    document.cookie = NAME + "=" + v + (ours ? "; Domain=dexio.wiki" : "") +
      "; Path=/; Max-Age=" + AGE + "; SameSite=Lax; Secure";
  }
  function location_() {
    var q = new URLSearchParams(location.search), keep = new URLSearchParams();
    q.forEach(function (v, k) { if (KEEP.test(k)) keep.append(k, v); });
    var s = keep.toString();
    return location.origin + location.pathname + (s ? "?" + s : "");
  }
  function referrer() {
    try {
      if (!document.referrer) return "";
      var r = new URL(document.referrer);
      return /(^|\.)dexio\.wiki$/.test(r.hostname) ? r.origin + "/" : r.origin + r.pathname;
    } catch (e) { return ""; }
  }

  var loaded = false, failed = false;
  function load() {
    window["ga-disable-" + ID] = false;
    if (loaded) return;
    loaded = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", ID, { page_location: location_(), page_referrer: referrer() });
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + ID;
    // A blocker stopped it: say so, so a page waiting on an event moves on at once.
    s.onerror = function () {
      failed = true;
      try { window.dispatchEvent(new Event("dexio-analytics-failed")); } catch (e) {}
    };
    document.head.appendChild(s);
  }
  function stop() {
    window["ga-disable-" + ID] = true;
    ["_ga", "_ga_" + ID.slice(2)].forEach(function (n) {
      document.cookie = n + "=; Path=/; Max-Age=0";
      if (ours) document.cookie = n + "=; Path=/; Max-Age=0; Domain=dexio.wiki";
    });
  }

  var choice = saved();
  if (choice ? choice === "granted" : !mustAsk()) load();

  function banner(show) {
    var b = document.getElementById("consent");
    if (b) b.hidden = !show;
  }
  // For pages that send an event (the app's sign-up): true once the tag is on. Such a
  // page also listens for "dexio-analytics-failed" on window.
  window.dexioConsent = {
    active: function () { return loaded && !failed && window["ga-disable-" + ID] !== true; },
  };
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-consent],[data-consent-open]");
    if (!t) return;
    if (t.hasAttribute("data-consent-open")) { e.preventDefault(); banner(true); return; }
    var v = t.getAttribute("data-consent") === "granted" ? "granted" : "denied";
    remember(v);
    banner(false);
    if (v === "granted") load(); else stop();
  });
  function ready() { if (!choice && mustAsk()) banner(true); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
