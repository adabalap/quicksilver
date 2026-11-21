#!/usr/bin/env python3
"""The 12 interface improvements: share target with files, shortcuts, restructured dashboard, swipe on
tasks and follow-ups, teaching empty states, icon set, sync chip, tour, motion/accessibility rules."""
import sys, os, re, json, subprocess, time, tempfile, sqlite3, base64
try:
    from playwright.sync_api import sync_playwright
    import requests
except ImportError:
    print("  (skipped: playwright/requests not installed)"); sys.exit(0)
here = os.path.dirname(os.path.abspath(__file__)); port = 5395
tmp = tempfile.mkdtemp(); dbp = os.path.join(tmp, "p.db")
srv = subprocess.Popen([sys.executable, "app.py"], cwd=here, env=dict(os.environ, QS_DB=dbp, QS_PORT=str(port)),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
B = f"http://127.0.0.1:{port}"
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
def lum(h):
    h = h.lstrip("#"); c = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
    c = [x/12.92 if x <= .03928 else ((x+.055)/1.055)**2.4 for x in c]
    return .2126*c[0] + .7152*c[1] + .0722*c[2]
def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True); return (la+.05)/(lb+.05)

# swipe a row with synthetic touch pointer events (what a finger sends)
SWIPE = """([sel, dx]) => { const el = document.querySelector(sel); const r = el.getBoundingClientRect();
  const x0 = r.left + r.width/2, y = r.top + r.height/2;
  const ev = (t, x) => el.dispatchEvent(new PointerEvent(t, {bubbles:true, pointerId:7, pointerType:'touch', isPrimary:true, clientX:x, clientY:y}));
  ev('pointerdown', x0); for (let i=1;i<=8;i++) ev('pointermove', x0 + dx*i/8); ev('pointerup', x0 + dx); }"""
try:
    for _ in range(40):
        try: requests.get(B + "/api/version", timeout=1); break
        except Exception: time.sleep(0.25)

    # ── static rules ──────────────────────────────────────────────────────────
    css = "".join(open(os.path.join(here, "static/css", f)).read() for f in os.listdir(os.path.join(here, "static/css")))
    small = [m for m in re.findall(r"font-size:(\d+(?:\.\d+)?)px", css) if float(m) < 11]
    check(not small, f"no text smaller than 11px in the stylesheets: {small[:3]}")
    check(ratio("#6f6b80", "#ffffff") >= 4.5, "muted text meets 4.5:1 on white")
    check(ratio("#9a95ae", "#131219") >= 4.5, "muted text meets 4.5:1 on the dark background")
    check(ratio("#ffffff", "#23805f") >= 4.5, "white on the success colour meets 4.5:1")
    check("prefers-reduced-motion: reduce" in css, "reduced-motion is honoured")
    check(":focus-visible" in css, "keyboard focus is visible")
    html = open(os.path.join(here, "static/index.html")).read()
    check("polish.css" in html and "91-ui.js" in html, "the new sheet and script are loaded")
    check(not re.search(r"<span>(📝|☑️|📅|🎙|📷|✏️)</span>", html), "the capture menu uses the icon set, not emoji")
    mf = json.load(open(os.path.join(here, "static/manifest.json")))
    check(len(mf.get("shortcuts", [])) == 4 and {s["url"] for s in mf["shortcuts"]} == {"/?new=text", "/?new=voice", "/?new=photo", "/?ask=1"}, "app shortcuts: note, voice, photo, ask")
    st = mf["share_target"]
    check(st["method"] == "POST" and st["enctype"] == "multipart/form-data" and st["params"]["files"][0]["name"] == "media", "the share target accepts files")
    sw = open(os.path.join(here, "static/sw.js")).read()
    check('method !== "GET"' in sw and "hg-v18" in sw and "handleShare" in sw, "the service worker never caches share-sheet posts")
    ui = open(os.path.join(ROOT, "static/js/91-ui.js")).read() if "ROOT" in globals() else open("static/js/91-ui.js").read()
    check("?note=<id> opens that note (links from Medha's brief and notifications)", 'q.get("note")' in ui and "openDetail(open_)" in ui)
    check("?bill=<id> opens that bill with its Mark as paid button", 'q.get("bill")' in ui and "openBill(bill_)" in ui)

    # ── share target with files ───────────────────────────────────────────────
    r = requests.post(B + "/share-target", data={"title": "Great article", "text": "Worth a read", "url": "https://example.com/a"}, allow_redirects=False)
    check(r.status_code == 302 and "/?shared=" in r.headers["Location"], "text share redirects to the new note")
    nid = r.headers["Location"].split("shared=")[1]
    check("Worth a read" in requests.get(f"{B}/api/notes/{nid}").json()["body"], "…with the shared text")
    r = requests.post(B + "/share-target", files=[("media", ("a.png", PNG, "image/png"))], allow_redirects=False)
    nid = r.headers["Location"].split("shared=")[1]
    n = requests.get(f"{B}/api/notes/{nid}").json()
    check(r.status_code == 302 and len(n.get("attachments", [])) == 1 and n["attachments"][0]["kind"] == "image", "a shared photo becomes a photo note")
    r = requests.post(B + "/share-target", data={"text": "with a photo"}, files=[("media", ("a.png", PNG, "image/png")), ("media", ("x.exe", b"MZ....", "application/octet-stream"))], allow_redirects=False)
    nid = r.headers["Location"].split("shared=")[1]
    check(len(requests.get(f"{B}/api/notes/{nid}").json()["attachments"]) == 1, "unsupported files are skipped; the rest still arrive")
    before = requests.get(B + "/api/stats").json()["total_notes"]
    r = requests.post(B + "/share-target", files=[("media", ("x.exe", b"MZ....", "application/octet-stream"))], allow_redirects=False)
    check(r.headers["Location"].rstrip("/").endswith(":%d" % port) or r.headers["Location"] in ("/", B + "/"), "nothing usable → back to the app")
    check(requests.get(B + "/api/stats").json()["total_notes"] == before, "…and no empty note is left behind")
    r = requests.post(B + "/share-target", files=[("media", ("bad.png", b"not really a png at all", "image/png"))], allow_redirects=False)
    check(requests.get(B + "/api/stats").json()["total_notes"] == before, "a file that isn't what it claims creates nothing")

    # ── pulse: today's counts ─────────────────────────────────────────────────
    p = requests.get(B + "/api/pulse?tz=330").json()
    check(set(p["today"]) == {"organised", "tasks", "reminders", "planned"}, "the dashboard API carries exact counts for today")
    c = sqlite3.connect(dbp)
    some = requests.get(B + "/api/notes").json()["notes"][0]["id"]
    c.execute("INSERT OR REPLACE INTO enrichments (note_id, title, summary) VALUES (?,?,?)", (some, "Organised today", "x"))
    c.execute("INSERT INTO action_items (id, note_id, text) VALUES ('tkA', ?, 'Call the bank')", (some,))
    c.execute("INSERT INTO action_items (id, note_id, text) VALUES ('tkB', ?, 'File the report')", (some,))
    c.commit()
    t = requests.get(B + "/api/pulse?tz=330").json()["today"]
    check(t["organised"] >= 1 and t["tasks"] == 2, f"…and they count what happened today: {t}")

    # ── in the browser ────────────────────────────────────────────────────────
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 820}, has_touch=True)
        pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # tour for a newcomer (fewer than 3 notes would be needed; here 3 exist → none)
        pg.goto(B + "/"); pg.wait_for_timeout(2600)
        check(not errs, f"no script errors on load: {errs[:2]}")
        check(pg.locator("#tour.on").count() == 0, "the tour doesn't greet someone who already has notes")
        pg.evaluate("openTour()"); pg.wait_for_timeout(300)
        check(pg.locator("#tour.on").count() == 1 and pg.locator("#tour[role=dialog]").count() == 1, "the tour opens as a dialog")
        check(pg.evaluate("document.activeElement.className") == "tour-next", "focus moves into the tour")
        for _ in range(3): pg.locator("[data-tour=next]").click()
        check(pg.locator(".tour-next").inner_text() == "Start writing", "the last step offers to start")
        pg.locator("[data-tour=next]").click(); pg.wait_for_timeout(200)
        check(pg.locator("#tour.on").count() == 0 and pg.evaluate("localStorage.getItem('hg_tour')") == "1", "finishing the tour is remembered")
        pg.evaluate("openTour()"); pg.keyboard.press("Escape")
        check(pg.locator("#tour.on").count() == 0, "Escape closes the tour")

        # sync chip
        check(pg.locator("#syncChip.ok, #syncChip.ag").count() == 1, "the sync chip shows synced (or that the agent is off)")
        ctx.set_offline(True); pg.evaluate("api('/stats').catch(()=>{})"); pg.wait_for_timeout(600)
        check(pg.locator("#syncChip.off").count() == 1 and "Offline" in pg.locator("#syncChip").inner_text(), "going offline turns the chip red")
        ctx.set_offline(False); pg.evaluate("api('/stats')"); pg.wait_for_timeout(600)
        check(pg.locator("#syncChip.ok, #syncChip.ag").count() == 1 and pg.locator("#syncChip.off").count() == 0, "coming back clears the offline state")

        # swipe tasks
        pg.evaluate("switchView('vActs')"); pg.wait_for_timeout(900)
        check(pg.locator('#aBody .aitem[tabindex="0"][role=checkbox]').count() >= 2, "task rows are keyboard-reachable checkboxes")
        pg.evaluate(SWIPE, ["#aBody .aitem[data-aid=tkA]", 220]); pg.wait_for_timeout(900)
        check(c.execute("SELECT done FROM action_items WHERE id='tkA'").fetchone()[0] == 1, "swipe right completes the task")
        pg.evaluate(SWIPE, ["#aBody .aitem[data-aid=tkB]", -220]); pg.wait_for_timeout(900)
        check(c.execute("SELECT dismissed FROM action_items WHERE id='tkB'").fetchone()[0] == 1, "swipe left drops the task")
        check("dropped" in pg.locator("#toasts").inner_text().lower() and pg.locator(".toast-act").count() == 1, "…with an Undo")
        pg.locator(".toast-act").click(); pg.wait_for_timeout(700)
        check(c.execute("SELECT dismissed FROM action_items WHERE id='tkB'").fetchone()[0] == 0, "Undo brings the task back")
        pg.evaluate(SWIPE, ["#aBody .aitem[data-aid=tkB]", 20]); pg.wait_for_timeout(500)
        check(c.execute("SELECT done, dismissed FROM action_items WHERE id='tkB'").fetchone() == (0, 0), "a short drag does nothing")
        pg.locator("#aBody .aitem[data-aid=tkB]").focus(); pg.keyboard.press("Space"); pg.wait_for_timeout(700)
        check(c.execute("SELECT done FROM action_items WHERE id='tkB'").fetchone()[0] == 1, "Space toggles a focused task (no gesture needed)")

        # dashboard
        old = "datetime('now','-9 days')"
        c.execute(f"UPDATE notes SET created_at={old}, updated_at={old} WHERE id=?", (some,))
        c.execute("INSERT INTO action_items (id, note_id, text) VALUES ('tkC', ?, 'Renew the licence')", (some,))
        c.execute("INSERT INTO followups (note_id, note_title, text, key, move, move_date, step, reason) VALUES (?,?,?,?,?,?,?,?)",
                  (some, "Chores", "Renew the licence", f"{some}|renew the licence", "reschedule", "2026-11-02", "", "Open 9 days"))
        c.execute("INSERT INTO followups (note_id, note_title, text, key, move, move_date, step, reason) VALUES (?,?,?,?,?,?,?,?)",
                  (some, "Chores", "Call the bank", f"{some}|call the bank", "reschedule", "2026-11-02", "", "Open 9 days"))
        c.execute("UPDATE action_items SET done=0 WHERE id IN ('tkA')"); c.commit()
        pg.evaluate("openPulse()"); pg.wait_for_timeout(1500)
        txt = pg.locator("#pulseBody").inner_text()
        low = txt.lower(); order = [low.find(s) for s in ("needs a decision", "today", "this week", "agent status")]
        check(all(i >= 0 for i in order) and order == sorted(order), f"dashboard order: decisions → today → week → status ({order})")
        check(txt.find("Good ") == 0 or txt.find("Late night") == 0, "it opens with a greeting")
        check(pg.locator(".pulse-hero").count() == 0, "the stats-first hero is gone")
        check("Today the agent" in txt, "today's work is summarised in a sentence")
        check(pg.locator(".dbar-n").count() == 7 and pg.locator(".dbar-d").count() == 7, "the week chart labels every bar with its value and day")
        check(pg.locator("[role=img][aria-label^='Notes captured']").count() == 1, "…and has a text alternative")
        check(pg.locator(".dtile").count() <= 3 and all(int(x or 0) > 0 for x in pg.locator(".dtile-n").all_inner_texts()), "tiles with zero are hidden")
        check(pg.locator("#pulseBody .ic").count() >= 8 and not re.search(r"[☀-➿\U0001F300-\U0001FAFF]", txt), "the dashboard uses line icons, not emoji")
        check(pg.evaluate("document.querySelector('#pulseBody').scrollWidth <= document.querySelector('#pulseBody').clientWidth + 1"), "no sideways scroll on a phone")
        check(pg.locator("#fuSlot .fu-i").count() == 2, "two follow-up cards are waiting")
        pg.evaluate(SWIPE, ["#fuSlot .fu-i[data-fu-id='1']", -220]); pg.wait_for_timeout(1000)
        check(c.execute("SELECT status FROM followups WHERE id=1").fetchone()[0] == "dismissed", "swipe left on a follow-up is 'Not now'")
        pg.evaluate(SWIPE, ["#fuSlot .fu-i[data-fu-id='2']", 220]); pg.wait_for_timeout(1000)
        check(c.execute("SELECT status FROM followups WHERE id=2").fetchone()[0] == "accepted", "swipe right accepts the plan")
        check(c.execute("SELECT COUNT(*) FROM reminders WHERE title='Call the bank' AND source='user'").fetchone()[0] == 1, "…and creates the reminder")
        pg.evaluate("document.querySelector('#pulseSheet').classList.remove('on')")

        # sheets are dialogs; icons everywhere
        check(pg.evaluate("[...document.querySelectorAll('.sheet')].every(s => s.getAttribute('role')==='dialog' && s.getAttribute('aria-label'))"), "every sheet is a labelled dialog")
        check(pg.locator("#toasts[aria-live=polite]").count() == 1, "toasts are announced to screen readers")
        check(pg.locator(".fab-opt .ic").count() == 7, "the capture menu uses the icon set")

        # drawer alignment
        pg.evaluate("document.querySelector('#burgerBtn').click()"); pg.wait_for_timeout(400)
        xs = pg.evaluate("[...document.querySelectorAll('#menuLayer .msec .mitem > svg')].map(s => Math.round(s.getBoundingClientRect().left))")
        check(len(set(xs)) == 1, f"drawer icons share one left edge: {sorted(set(xs))}")
        bx = pg.evaluate("(()=>{const b=document.querySelector('#bFu'); b.style.display='block'; b.textContent='2'; const r=b.getBoundingClientRect(), p=b.parentElement.getBoundingClientRect(); return p.right-r.right})()")
        check(0 <= bx <= 14, f"the drawer badge sits on the right edge ({bx}px in)")
        pg.evaluate("document.querySelector('#menuBk').click()")

        # shortcuts and share landing
        pg.goto(B + "/?new=text"); pg.wait_for_timeout(1800)
        check(pg.locator("#editor.on, .editor.on, #edText").first.is_visible() and "new=" not in pg.url, "?new=text opens the editor and tidies the URL")
        pg.goto(B + "/?ask=1"); pg.wait_for_timeout(1800)
        check(pg.evaluate("S.askMode") is True, "?ask=1 opens Ask")
        shared_id = requests.post(B + "/share-target", data={"text": "From another app"}, allow_redirects=False).headers["Location"].split("shared=")[1]
        pg.goto(B + "/?shared=" + shared_id); pg.wait_for_timeout(2200)
        check(pg.evaluate("S.cur && S.cur.id") == shared_id and "shared=" not in pg.url, "?shared= lands on the new note")
        # ── refreshes don't flash or lose your place
        pg.goto(B + "/"); pg.wait_for_timeout(2400)
        for i in range(30):
            requests.post(B + "/api/notes", json={"body": f"Filler note number {i} so that the list scrolls on a phone screen"})
        pg.evaluate("refreshAll()"); pg.wait_for_timeout(1500)
        pg.evaluate("document.querySelector('.main').scrollTop = 600"); pg.wait_for_timeout(200)
        pg.evaluate("document.querySelector('#feed .card').__keep = 'same node'")
        y0 = pg.evaluate("document.querySelector('.main').scrollTop")
        pg.evaluate("refreshAll()"); pg.wait_for_timeout(1200)
        check(pg.evaluate("document.querySelector('#feed .card').__keep") == "same node", "an unchanged refresh leaves the cards untouched (no flash)")
        check(abs(pg.evaluate("document.querySelector('.main').scrollTop") - y0) < 2 and y0 > 100, f"…and the scroll position holds ({y0})")
        requests.post(B + "/api/notes", json={"body": "A brand new note that arrives during a refresh"})
        pg.evaluate("invalidateViewCache(); refreshAll()"); pg.wait_for_timeout(1500)
        check("brand new note" in pg.locator("#feed").inner_text() and abs(pg.evaluate("document.querySelector('.main').scrollTop") - y0) < 80, "a new note appears without throwing you back to the top")
        pg.evaluate("openPulse()"); pg.wait_for_timeout(1500)
        pg.evaluate("document.querySelector('#pulseBody details') && (document.querySelector('#pulseBody details').open = true)")
        pg.evaluate("document.querySelector('#pulseBody .dcard').__keep = 'dash node'")
        pg.evaluate("document.querySelector('#pulseBody').scrollTop = 200"); pg.wait_for_timeout(200)
        yd = pg.evaluate("document.querySelector('#pulseBody').scrollTop")
        pg.evaluate("openPulse(true)"); pg.wait_for_timeout(1200); pg.evaluate("openPulse(true)"); pg.wait_for_timeout(1200)
        check(pg.evaluate("document.querySelector('#pulseBody .dcard').__keep") == "dash node", "a quiet dashboard refresh leaves unchanged sections alone")
        check(abs(pg.evaluate("document.querySelector('#pulseBody').scrollTop") - yd) < 2, "…and keeps your scroll position")
        check(pg.evaluate("!document.querySelector('#pulseBody details') || document.querySelector('#pulseBody details').open"), "…and what you opened stays open")
        pg.evaluate("document.querySelector('#pulseSheet').classList.remove('on')")

        # ── Your week and System health
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('weekly_digest', ?)", (json.dumps({
            "week": "2026-W44", "ts": "2026-10-25T18:00:00Z", "text": "A busy week of planning and a lot of photos.",
            "counts": {"notes": 20, "photos": 5, "voice": 0, "sketches": 1, "tasks_done": 8, "tasks_open": 1},
            "top_tags": [{"tag": "work", "count": 5}, {"tag": "health", "count": 3}],
            "open_tasks": [{"note_id": some, "text": "Visit the bank"}], "revisit": [{"id": some, "title": "Passport"}], "notes": [{"id": some, "title": "A note"}]}),)); c.commit()
        pg.evaluate("openDigest()"); pg.wait_for_timeout(1000)
        wk = pg.locator("#digestBody").inner_text()
        check(pg.locator(".wk-hero").count() == 1 and pg.locator(".wk-tile").count() == 3 and pg.locator(".wk-tag").count() == 2, "Your week has a headline, three tiles and topic bars")
        check("1 sketch" in wk and "1 sketches" not in wk and "5 photos" in wk, "…and gets plurals right")
        check(not re.search(r"[\u2600-\u27BF\U0001F300-\U0001FAFF]", wk) and pg.locator("#digestBody .ic").count() >= 6, "…with icons, not emoji or ☐ glyphs")
        pg.locator("[data-dg=tag]").first.click(); pg.wait_for_timeout(800)
        check(pg.locator("#digestSheet.on").count() == 0, "a topic bar filters to that tag")
        pg.evaluate("openHealth()"); pg.wait_for_timeout(1200)
        hh = pg.locator("#healthBody").inner_text().lower()
        check(pg.locator(".hh-hero").count() == 1 and ("isn't running" in hh or "all systems normal" in hh or "all systems normal" in hh or "needs a look" in hh), "System health opens with a one-line verdict")
        check(pg.locator(".hh-meter").count() == 1 and pg.locator(".hh-tile").count() == 4, "…a storage meter and four queue tiles")
        check(pg.locator("#healthBody .hh-row").count() >= 8, "…and grouped rows")
        check(pg.evaluate("document.querySelector('#healthBody').scrollWidth <= document.querySelector('#healthBody').clientWidth + 1"), "…without sideways scroll")
        pg.evaluate("document.querySelector('#healthSheet').classList.remove('on')")

        # ── the share fallback in the service worker (old server, or none)
        swsrc = open(os.path.join(here, "static/sw.js")).read()
        import re as _re
        fn = _re.search(r"async function handleShare[\s\S]*?\n}\n", swsrc).group(0)
        probe = """async (src) => {
          const handleShare = new Function(src + "; return handleShare;")();
          const real = window.fetch, out = {};
          const mk = () => { const f = new FormData(); f.set("text", "hello there"); f.set("url", "https://x.test"); return new Request("/share-target", {method:"POST", body:f}); };
          window.fetch = async (u) => (String(u.url||u).includes("/api/version") ? {ok:true} : {status:405, type:"basic"});
          let r = await handleShare(mk()); out.old = [r.status, r.headers.get("location")];
          window.fetch = async (u) => { throw new TypeError("offline"); };
          r = await handleShare(mk()); out.off = [r.status, r.headers.get("location")];
          const ok = {status:200, type:"basic", marker:1};
          window.fetch = async () => ok; r = await handleShare(mk()); out.ok = r === ok;
          window.fetch = real; return out; }"""
        o = pg.evaluate(probe, fn)
        check(o["old"][0] == 303 and "/share-target?" in o["old"][1] and "hello+there" in o["old"][1], f"an older server (405) gets the shared text through the GET route: {o['old']}")
        check(o["off"][0] == 303 and "share_text=hello" in o["off"][1], f"an unreachable server hands the text to the page to save offline: {o['off']}")
        check(o["ok"] is True, "a working server's response passes straight through")
        pg.goto(B + "/?share_text=Saved%20while%20offline"); pg.wait_for_timeout(2200)
        check(any("Saved while offline" == n["body"] for n in requests.get(B + "/api/notes?limit=200").json()["notes"]) and "share_text" not in pg.url, "held shared text becomes a note")
        check(requests.get(B + "/api/version").json()["api"] >= 5, "the app level is bumped so a stale server is flagged")

        # ── About page
        about = open(os.path.join(here, "static/about.html")).read()
        check("v1.6" in about and "Version 1.6" in about and "Version 1.5" in about and "Version 1.4" in about and 'id="dash"' in about and 'id="gestures"' in about and "Version 1.3" in about, "the About page covers v1.3, the dashboard and gestures")
        check(all(x in about for x in ('href="#dash"', 'href="#gestures"')), "…and its contents list links to them")
        pg.goto(B + "/about.html"); pg.wait_for_timeout(800)
        check(pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "the About page has no sideways scroll on a phone")

        # ── Cartoon ink: a contrast floor, not a palette that happens to work.
        #
        # The cartoon theme derives each card's outline and body colour from
        # the card's own paper. An earlier version fixed the lightness of both
        # by hand, which measured 7.5:1 on one swatch and 4.48:1 on another --
        # below AA, on the same setting. _inkFor now searches for a lightness
        # that meets a target, so the floor holds for any palette. Run the real
        # shipped functions against every theme rather than trusting the eight
        # swatches it was developed against.
        # Measure what cardBg actually emits, not _inkFor called with the
        # targets restated here -- restating them means a weakened target in
        # cardBg sails past a green test.
        pg.goto(B + "/"); pg.wait_for_timeout(900)
        worst = pg.evaluate("""() => {
          const pick = s => { const m = {}; for (const kv of s.split(";")) {
              const i = kv.indexOf(":"); if (i > 0) m[kv.slice(0, i).trim()] = kv.slice(i + 1).trim(); }
            return m; };
          setNoteTheme("cartoon");
          let wt = 99, we = 99, n = 0, bad = [];
          for (const dk of [false, true]) {
            document.documentElement.dataset.theme = dk ? "dark" : "light";
            for (let i = 0; i < 8; i++) {
              const m = pick(cardBg({ id: "x" + i, color_idx: i }));
              const paper = m["background"], e = m["--nt-ink"], t = m["--nt-text"];
              if (!paper || !e || !t) { bad.push("missing ink at " + i); continue; }
              const re = _ratio(e, paper), rt = _ratio(t, paper); n++;
              we = Math.min(we, re); wt = Math.min(wt, rt);
              if (rt < 4.5) bad.push(paper + " text " + rt.toFixed(2));
              if (re < 4.5) bad.push(paper + " edge " + re.toFixed(2));
            }
          }
          // Palette-independence: the floor must survive a palette nobody has
          // added yet, including a pale "dark" one with no room to darken.
          // Two separate properties, and they fail in different ways: the
          // target can be met by a flat white or black, which clears contrast
          // while throwing away the hue that made the ink the card's own.
          let gen = 0, genBad = 0, flat = 0;
          for (const th of Object.values(_themeRegistry()))
            for (const [pal, d] of [[th.light, false], [th.dark, true]])
              for (const p of pal) { gen++;
                const ink = _inkFor(p, 7.0, d?0.16:0.38, d?0.42:0.62, d);
                if (_ratio(ink, p) < 6.99) genBad++;
                if (ink === "#ffffff" || ink === "#000000") flat++; }
          document.documentElement.removeAttribute("data-theme");
          return { we, wt, n, bad, gen, genBad, flat };
        }""")
        check(worst["n"] == 16, f"cardBg was measured on all 8 cartoon swatches, both modes ({worst['n']})")
        check(not worst["bad"], f"every card ink cardBg emits clears AA: {worst['bad'][:3]}")
        check(worst["wt"] >= 4.5, f"the faintest body ink still clears AA ({worst['wt']:.2f}:1)")
        check(worst["gen"] >= 100 and worst["genBad"] == 0,
              f"the ink search holds its target on every palette ({worst['gen']} swatches, {worst['genBad']} short)")
        check(worst["flat"] == 0,
              f"…and keeps each card's hue rather than falling back to flat black or white ({worst['flat']} flat)")

        # The stamp sits in the footer's bottom-right corner and the footer
        # reserves a strip for it. Three numbers in two rules that have to
        # agree: when they did not, a task badge and the stamp overlapped.
        css = open(os.path.join(here, "static", "css", "themes.css")).read()
        import re as _re
        _st = _re.search(r'cartoon"\] \.card::after\{[^}]*right:(\d+)px[^}]*width:(\d+)px', css, _re.S)
        _ft = _re.search(r'cartoon"\] \.card \.c-foot\{padding-right:(\d+)px', css)
        check(bool(_st and _ft), "the cartoon stamp and footer rules are both present")
        if _st and _ft:
            need = int(_st.group(1)) + int(_st.group(2))
            check(int(_ft.group(1)) > need,
                  f"the footer reserves more than the stamp occupies ({_ft.group(1)}px > {need}px)")

        # The die-cut edge is three numbers in two rules that have to agree:
        # the inset ring's width, the halftone's inset, and the card's padding.
        # Ring wider than the padding puts body text on the paper margin; the
        # halftone inset not matching the ring stipples the margin itself.
        _ring = _re.search(r'cartoon"\] \.card\{.*?box-shadow:inset 0 0 0 (\d+)px', css, _re.S)
        _pad = _re.search(r'cartoon"\] \.card\{.*?padding:(\d+)px', css, _re.S)
        _half = _re.search(r'cartoon"\] \.card::before\{[^}]*inset:(\d+)px', css, _re.S)
        check(bool(_ring and _pad and _half), "the die-cut ring, padding and halftone rules are all present")
        if _ring and _pad and _half:
            check(int(_half.group(1)) == int(_ring.group(1)),
                  f"the halftone starts where the paper margin ends ({_half.group(1)}px == {_ring.group(1)}px)")
            check(int(_pad.group(1)) > int(_ring.group(1)),
                  f"the card's padding clears the paper margin ({_pad.group(1)}px > {_ring.group(1)}px)")
        # Dark mode must not restate the card's box-shadow: an equal-specificity
        # override after the :active rule is how a pressed card stopped
        # collapsing its shadow in dark.
        # Text on a violet fill. Dark mode lightens the violet so it shows on
        # a dark page, and white on that light violet is 2.77:1 -- every
        # primary button in the app hard-coded #fff and failed. --on-violet
        # flips to dark there. A new violet button that hard-codes #fff again
        # brings the failure back on one screen at a time, which is how it
        # went unnoticed across seventeen.
        _cssdir = os.path.join(here, "static", "css")
        _bad_violet = []
        for _fn in sorted(os.listdir(_cssdir)):
            if not _fn.endswith(".css"): continue
            for _i, _ln in enumerate(open(os.path.join(_cssdir, _fn), encoding="utf-8"), 1):
                if "background:var(--violet)" in _ln and _re.search(r"color:#fff(?![0-9a-fA-F])", _ln):
                    _bad_violet.append(f"{_fn}:{_i}")
        check(not _bad_violet, f"no violet fill hard-codes white text over it: {_bad_violet[:4]}")
        _base = open(os.path.join(_cssdir, "base.css"), encoding="utf-8").read()
        check(_base.count("--on-violet:") == 3 and _base.count("--prism-soft:") == 3,
              "both tokens are declared for light, explicit dark and system dark")
        _darkshadow = _re.search(r'data-theme="dark"\]\[data-nt="cartoon"\] \.card\{[^}]*box-shadow', css)
        check(not _darkshadow, "dark mode does not restate the cartoon card's shadow")

        # A failed action has to say something the person can act on. The case
        # that matters is a route the running server does not have yet, which
        # is what every new route looks like until hg_ui is restarted.
        why = pg.evaluate("""async () => {
          let missing = null;
          try { await api("/no-such-route-here", {method:"DELETE"}); }
          catch(e){ missing = apiWhy(e, "Couldn't dismiss"); }
          return { missing,
                   offline: apiWhy({offline:true}, "Couldn't dismiss"),
                   server:  apiWhy({status:500}, "Couldn't dismiss"),
                   plain:   apiWhy({}, "Couldn't dismiss"),
                   gone:    apiWhy({status:404, data:{error:"gone", message:"That save is no longer here."}}, "Couldn't save"),
                   goneBare: apiWhy({status:404, data:{error:"gone"}}, "Couldn't save"),
                   decided: apiWhy({status:409, data:{error:"decided", message:"That one has already been handled."}}, "x") };
        }""")
        check(why["missing"] and "estart" in why["missing"],
              f"a route the server lacks says to restart it: {why['missing']!r}")
        check("offline" in (why["offline"] or ""), "an offline failure says so instead")
        check(why["server"] == "Couldn't dismiss (500)",
              f"any other failure carries its status: {why['server']!r}")
        check(why["plain"] == "Couldn't dismiss", "…and a statusless failure still reads cleanly")
        # A route may answer 404 ITSELF. Reading every 404 as a missing route
        # told someone tapping a stale card to restart a healthy server.
        check(why["gone"] == "That save is no longer here.",
              f"a route's own 404 shows its own sentence, not 'restart': {why['gone']!r}")
        check("estart" not in (why["goneBare"] or ""),
              f"…and is never mistaken for a missing route even without one: {why['goneBare']!r}")
        check(why["decided"] == "That one has already been handled.",
              f"a route's own message is preferred over the generic wording: {why['decided']!r}")

        # ── reminders: soonest first, one line per bill ──────────────────────
        from datetime import date, timedelta
        dd = lambda n: (date.today() + timedelta(days=n)).isoformat()
        c.execute("INSERT INTO reminders (id, note_id, title, datetime_resolved, source, description) VALUES "
                  "('rbA', ?, 'Electricity bill · due soon', ?, 'user', 'Dear customer, your BESCOM bill…'),"
                  "('rbB', ?, 'Electricity bill · due soon', ?, 'user', 'Dear customer, your BESCOM bill…'),"
                  "('rcX', ?, 'Renew the passport', ?, 'user', '')",
                  (some, dd(2), some, dd(5), some, dd(12)))
        c.execute("INSERT INTO sms_bills (key, received_at, merchant, due_date, status, reminder_ids) "
                  "VALUES ('kb', ?, 'Bescom', ?, 'open', '[\"rbA\",\"rbB\"]')", (dd(-1) + "T09:00:00", dd(5)))
        c.commit()
        requests.post(B + "/api/settings", json={"sms": "on"})
        pg.evaluate("try{localStorage.removeItem('hg_rem_sort')}catch(e){}")
        pg.evaluate("switchView('vReminders')"); pg.wait_for_timeout(900)
        rows = pg.locator("#rBody .aitem")
        texts = [rows.nth(i).inner_text() for i in range(rows.count())]
        bill_rows = [x for x in texts if "Electricity bill" in x]
        check(len(bill_rows) == 1, f"a bill's heads-up and due-day reminders are one line: {texts}")
        check(bill_rows and "Heads-up" in bill_rows[0] and "due soon" not in bill_rows[0],
              f"…which says when it is due and mentions the heads-up, without repeating 'due' in the title: {bill_rows}")
        check(pg.locator("#rBody .aitem .rmsg").count() == 0 or "BESCOM" not in pg.locator("#rBody").inner_text(),
              "…and does not paste the bill's message under it")
        ri = [x for x in texts if "Electricity bill" in x or "passport" in x]
        check(len(ri) == 2 and "Electricity" in ri[0], f"soonest first by default: {ri}")
        pg.locator("#rBody .aitem", has_text="Electricity bill").locator("[data-hs=bill-open]").click()
        pg.wait_for_timeout(900)
        check("Bescom" in pg.locator("body").inner_text() and "paid" in pg.locator("body").inner_text().lower(),
              "the bill's line opens the bill, where its message and Mark as paid live")
        pg.evaluate("closeOverlays()"); pg.wait_for_timeout(300)
        pg.locator("#rBody .aitem", has_text="Electricity bill").locator("[data-dismiss-rid]").click()
        pg.wait_for_timeout(900)
        gone = [r[0] for r in c.execute("SELECT dismissed FROM reminders WHERE id IN ('rbA','rbB')")]
        check(gone == [1, 1], f"dismissing the bill's line dismisses both of its reminders: {gone}")

        check(not errs, f"no script errors anywhere: {errs[:2]}")
        b.close()

        # empty states on a brand-new database
        for t_ in ("notes", "action_items", "reminders"):
            pass
    srv.terminate(); srv.wait(5)
    tmp2 = tempfile.mkdtemp()
    srv = subprocess.Popen([sys.executable, "app.py"], cwd=here, env=dict(os.environ, QS_DB=os.path.join(tmp2, "e.db"), QS_PORT=str(port + 1)),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    B2 = f"http://127.0.0.1:{port + 1}"
    for _ in range(40):
        try: requests.get(B2 + "/api/version", timeout=1); break
        except Exception: time.sleep(0.25)
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 390, "height": 820}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B2 + "/"); pg.wait_for_timeout(3200)
        check(pg.locator("#tour.on").count() == 1, "a first-time visitor is greeted with the tour")
        pg.locator("[data-tour=skip]").click(); pg.wait_for_timeout(200)
        check(pg.locator("#tour.on").count() == 0, "…which they can skip")
        check("first note starts here" in pg.locator("#feed").inner_text() and pg.locator("#feed .empty-go").count() == 3, "the empty feed teaches and offers three ways to start")
        pg.locator('#feed [data-empty=text]').click(); pg.wait_for_timeout(500)
        check(pg.evaluate("document.querySelector('#edText') && document.querySelector('#edText').offsetParent !== null"), "…and the button opens the editor")
        pg.evaluate("closeEditor && closeEditor()"); pg.wait_for_timeout(300)
        pg.evaluate("switchView('vActs')"); pg.wait_for_timeout(700)
        check("agent lifts it here" in pg.locator("#aBody").inner_text(), "empty tasks explain what the agent does")
        pg.evaluate("switchView('vReminders')"); pg.wait_for_timeout(700)
        check("Google Calendar" in pg.locator("#rBody").inner_text() and pg.locator("#rBody [data-empty=event]").count() == 1, "empty reminders explain and offer an event")
        pg.evaluate("openPulse()"); pg.wait_for_timeout(1500)
        t2 = pg.locator("#pulseBody").inner_text()
        check("all caught up" in t2.lower() and pg.locator(".dtile").count() == 0, "an empty dashboard is calm: caught up, no zero tiles")
        check(not errs, f"no script errors on a fresh install: {errs[:2]}")
        b.close()
finally:
    srv.terminate()
t = P + F
print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{t} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
