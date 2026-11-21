#!/usr/bin/env python3
"""Loads the real page in Chromium against a seeded server: the notes list must paint,
with no script errors (a broken comment or typo shows up here as an empty, forever-loading list)."""
import sys, os, subprocess, time, tempfile, base64, json
try:
    from playwright.sync_api import sync_playwright
    import requests
except ImportError:
    print("  (skipped: playwright/requests not installed)"); sys.exit(0)
here = os.path.dirname(os.path.abspath(__file__)); port = 5394
tmp = tempfile.mkdtemp()
srv = subprocess.Popen([sys.executable, "app.py"], cwd=here, env=dict(os.environ, QS_DB=os.path.join(tmp, "l.db"), QS_PORT=str(port)),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
B = f"http://127.0.0.1:{port}"
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")
try:
    for _ in range(40):
        try: requests.get(B + "/api/version", timeout=1); break
        except Exception: time.sleep(0.25)
    PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    for i in range(25):
        r = requests.post(B + "/api/notes", json={"body": f"Note {i} about fedora #tag{i % 3} with enough words to be real"})
        if i % 8 == 0:
            requests.post(f"{B}/api/notes/{r.json()['id']}/attachments", files={"file": ("p.png", PNG, "image/png")}, data={"kind": "image", "meta": "{}"})
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 400, "height": 820}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); pg.wait_for_timeout(2500)
        check(not errs, f"no script errors on load: {errs[:2]}")
        check(pg.locator("#masonryBox > *").count() >= 20, "the notes list paints")
        check(pg.locator(".c-media img").count() >= 1 and "/thumb" in (pg.locator(".c-media img").first.get_attribute("src") or ""), "photo cards use thumbnails")
        pg.reload(); pg.wait_for_timeout(2000)
        check(pg.locator("#masonryBox > *").count() >= 20, "the list paints again after a reload")
        for fn in ("openFilters", "openDigest", "openDups", "openHealth"):
            check(pg.evaluate(f"typeof {fn}") == "function", f"{fn} is defined")

        # ── story from a photo
        import sqlite3
        nid = requests.get(B + "/api/notes?type=photo").json()["notes"][0]["id"]
        pg.evaluate(f"openDetail({json.dumps(nid)})"); pg.wait_for_timeout(1200)
        check(pg.locator(".story-cta").count() == 1, "photo note shows the story button")
        pg.locator(".story-cta").click(); pg.wait_for_timeout(500)
        check(pg.locator("#storySheet.on").count() == 1 and pg.locator("#stOutline").count() == 1, "story sheet opens with the form")
        pg.locator('[data-st=genre][data-v="mystery"]').click(); pg.locator("#stOutline").fill("a fox finds a lantern")
        pg.locator('[data-st=go]').click(); pg.wait_for_timeout(800)
        check("description" in pg.locator("#storyBody").inner_text().lower() or "still" in pg.locator("#storyBody").inner_text().lower(), "no description yet shows a clear message")
        check(pg.locator("#stOutline").input_value() == "a fox finds a lantern", "the outline survives the error")
        c = sqlite3.connect(os.path.join(tmp, "l.db")); c.execute("UPDATE attachments SET transcript='Description: a red fox', transcript_status='done'"); c.commit()
        pg.locator('[data-st=go]').click(); pg.wait_for_timeout(800)
        check("writing your story" in pg.locator("#storyBody").inner_text().lower(), "pending state shows")
        rid = c.execute("SELECT id FROM ask_requests WHERE mode='story'").fetchone()[0]
        requests.post(B + "/api/ask/answer", json={"request_id": rid, "status": "done", "answer": "# The Fox\n\nOnce upon a time."})
        pg.wait_for_function("document.querySelector('#storyBody .st-story')", timeout=12000)
        check("the fox" in pg.locator("#storyBody .st-story").inner_text().lower(), "the finished story renders")
        pg.locator('[data-st=tweak][data-v="Funnier"]').click(); pg.wait_for_timeout(800)
        check("revising" in pg.locator("#storyBody").inner_text().lower(), "revise shows the pending state")
        rid2 = c.execute("SELECT id FROM ask_requests WHERE mode='story' AND question='Funnier'").fetchone()[0]
        requests.post(B + "/api/ask/answer", json={"request_id": rid2, "status": "done", "answer": "# The Funny Fox\n\nOnce upon a time, slipping."})
        pg.wait_for_function("document.querySelector('#storyBody .st-story') && document.querySelector('#storyBody .st-story').innerText.includes('Funny')", timeout=12000)
        check(pg.locator("[data-st=undo]").count() == 1, "undo is offered after a revision")
        pg.locator("[data-st=undo]").click(); pg.wait_for_timeout(300)
        check("the fox" in pg.locator("#storyBody .st-story").inner_text().lower() and "funny" not in pg.locator("#storyBody .st-story").inner_text().lower(), "undo restores the previous version")
        pg.locator('[data-st=append]').click(); pg.wait_for_timeout(1200)
        check("Once upon a time" in requests.get(f"{B}/api/notes/{nid}").json()["body"], "Add to this note appends the story")
        check(not errs, f"no script errors in the story flow: {errs[:2]}")
        # ── export includes the photo; share passes files
        pg.evaluate(f"openDetail({json.dumps(nid)})"); pg.wait_for_timeout(1000)
        r = pg.evaluate("""async()=>{ const d = noteToDoc(S.cur); const im = await loadDocImages(d);
            window.__shared=null; navigator.share = async x=>{ window.__shared=x; }; navigator.canShare = x=>true;
            shareDoc(d); await shareClick("share");
            return {n: im.length, html: docHtmlWithImages(d, im).includes('src="data:image/jpeg'), files: (window.__shared?.files||[]).length, text: !!window.__shared?.text}; }""")
        check(r["n"] == 1 and r["html"], "PDF html embeds the photo as an image")
        check(r["files"] == 1 and r["text"], "share passes the photo and the text")
        r = pg.evaluate("""async()=>{ window.__n=0; navigator.share = async x=>{ window.__n++; if (x.files) throw new DOMException("x","NotAllowedError"); window.__t=x.text; };
            await shareClick("share"); return {n:window.__n, t:!!window.__t}; }""")
        check(r["n"] == 2 and r["t"], "share falls back to text when files are refused")
        r = pg.evaluate('()=>{\n const codes=[\'def i():\\n  return git_out("a")=="true"\\n h=d["k"]=="b" and (m)=="c"\\nif x == "true" or y=="no":\\n  z = (p == q)\',\'a ==b== c\',\'==== h ====\',\'if a === b && c == d {}\'];\n const out={codeMarks:0, codeParsed:0, codePrev:true};\n for (const c of codes){ out.codeMarks += /<mark/.test(mdLite(c)) ? 1:0; out.codeParsed += parseHighlights(c).ranges.length; out.codePrev = out.codePrev && plainPreview(c).includes("=="); }\n const body = buildBody("Buy milk and eggs today", [{start:4,end:8,color:"y"},{start:13,end:17,color:"g"}]);\n const pr = parseHighlights(body);\n const html = mdLite(body);\n out.rt = pr.plain==="Buy milk and eggs today" && pr.ranges.length===2 && pr.ranges[1].color==="g";\n out.html = (html.match(/<mark/g)||[]).length===2 && html.includes("hl-g") && !/[\u2060-\u2064]/.test(html);\n out.prev = plainPreview(body)==="Buy milk and eggs today";\n out.legacy = (mdLite("old =={g}eggs== here").match(/hl-g/g)||[]).length===1;\n out.rebuild = buildBody(pr.plain, pr.ranges)===body;\n return out; }')
        check(r["codeMarks"] == 0 and r["codeParsed"] == 0 and r["codePrev"], "code with == is never a highlight")
        check(r["rt"] and r["html"] and r["prev"] and r["rebuild"], "new invisible highlights round-trip, render and preview cleanly")
        check(r["legacy"], "old =={g}x== highlights still render")
        check(not errs, f"no script errors after use: {errs[:2]}")

        # ── task follow-through card on the dashboard
        nid2 = requests.get(B + "/api/notes").json()["notes"][0]["id"]
        for tid, tx in (("fa1", "Pay the water bill"), ("fa2", "Collect all the documents for the passport renewal appointment")):
            c.execute("INSERT INTO action_items (id, note_id, text) VALUES (?,?,?)", (tid, nid2, tx))
        c.execute("INSERT INTO followups (note_id, note_title, text, key, move, move_date, reason) VALUES (?,?,?,?,?,?,?)",
                  (nid2, "Chores", "Pay the water bill", nid2 + "|pay the water bill", "reschedule", "2026-12-04", "Open 9 days"))
        c.execute("INSERT INTO followups (note_id, note_title, text, key, move, move_date, step, reason) VALUES (?,?,?,?,?,?,?,?)",
                  (nid2, "Chores", "Collect all the documents for the passport renewal appointment", nid2 + "|collect all the documents for the passport renewal appointment",
                   "first_step", "2026-12-03", "Open the passport folder", "Open 12 days · mentions a deadline"))
        c.commit()
        pg.evaluate("loadStats()"); pg.wait_for_timeout(600)
        badge = "(()=>{const e=document.getElementById('bFu'); return e.style.display === 'none' ? '' : e.textContent})()"
        check(pg.evaluate(badge) == "2", "the drawer badge counts decisions waiting")
        pg.evaluate("openPulse()"); pg.wait_for_function("document.querySelectorAll('.fu .fu-i').length === 2", timeout=8000)
        check(pg.locator(".fu-h").inner_text().startswith("Needs a decision"), "the decision card shows on the dashboard")
        check(pg.locator(".fu-go").first.inner_text().startswith("Move to"), "the proposed move is the main button")
        check("Start with" in pg.locator(".fu-step").inner_text() and "passport folder" in pg.locator(".fu-step").inner_text(), "a drafted first step is shown")
        check(pg.locator(".fu-go").nth(1).inner_text().startswith("Plan it for"), "a first-step proposal offers to plan it")
        check(pg.locator(".fu-i").first.locator('[data-fu-act="drop"]').count() == 1 and pg.locator(".fu-i").first.locator('[data-fu-act="done"]').count() == 1, "done / drop / not now are one tap away")
        pg.locator(".fu-go").first.click(); pg.wait_for_function("document.querySelectorAll('.fu .fu-i').length === 1", timeout=8000)
        check("planned" in pg.locator("#toasts").inner_text().lower() and pg.locator(".toast-act").count() == 1, "accepting plans it and offers Undo")
        check(any(r["title"] == "Pay the water bill" for r in requests.get(B + "/api/reminders").json()["reminders"]), "the plan became a reminder")
        pg.locator(".toast-act").click(); pg.wait_for_function("document.querySelectorAll('.fu .fu-i').length === 2", timeout=8000)
        check(not any(r["title"] == "Pay the water bill" for r in requests.get(B + "/api/reminders").json()["reminders"]), "Undo removes the reminder and restores the card")
        pg.locator('.fu-i >> nth=0 >> [data-fu-act="dismiss"]').click(); pg.wait_for_function("document.querySelectorAll('.fu .fu-i').length === 1", timeout=8000)
        check(c.execute("SELECT status FROM followups WHERE text='Pay the water bill'").fetchone()[0] == "dismissed", "'Not now' is recorded")
        pg.locator('.fu-i >> nth=0 >> [data-fu-act="done"]').click(); pg.wait_for_timeout(1200)
        check(pg.locator(".fu .fu-i").count() == 0 and c.execute("SELECT done FROM action_items WHERE id='fa2'").fetchone()[0] == 1, "'Already done' completes the task and clears the card")
        check(pg.evaluate(badge) == "", "the badge clears when nothing is waiting")
        pg.evaluate("openSettings()"); pg.wait_for_timeout(700)
        check("Task follow-ups" in pg.locator("#setBody").inner_text() and pg.locator("#fuCheck").count() == 1, "Settings offers the switch and Check now")
        pg.locator("#fuCheck").click(); pg.wait_for_timeout(600)
        check(c.execute("SELECT value FROM settings WHERE key='followups_refresh_requested'").fetchone()[0] == "1", "Check now asks the agent to look")
        check(not errs, f"no script errors in the follow-up flow: {errs[:2]}")
        b.close()
finally:
    srv.terminate()
t = P + F
print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{t} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
