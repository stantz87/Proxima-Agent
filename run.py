"""
PROXIMA CAPITAL — CHARITY DEAL PORTAL
Standalone app. Nothing to do with the Rightmove scanner.

Run:
    pip install flask
    python run.py

Then open:
    http://localhost:5050/portal        (agent-facing — share this link)
    http://localhost:5050/admin         (your admin panel)

Admin password: proxima2024
Change it by editing ADMIN_PASSWORD below.
"""

import os
import sqlite3
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask, request, session, redirect,
    render_template_string, send_from_directory, jsonify
)
from werkzeug.utils import secure_filename

# ── CONFIG ────────────────────────────────────────────────────────────────────
ADMIN_PASSWORD = "proxima2024"      # change this
SECRET_KEY     = "proxima-portal-secret"
DB_PATH        = "portal.db"
UPLOAD_FOLDER  = "uploads"
ALLOWED_EXT    = {"jpg", "jpeg", "png", "webp"}
MAX_PHOTOS     = 5
PORT           = 5050

app = Flask(__name__)
app.secret_key = SECRET_KEY
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── DATABASE ──────────────────────────────────────────────────────────────────


def send_notification(deal_address, name, email, phone, notes):
    try:
        payload = json.dumps({
            "from": "Proxima Portal <onboarding@resend.dev>",
            "to": [NOTIFY_EMAIL],
            "subject": f"New Reservation: {deal_address}",
            "html": f"""
            <h2>New Reservation — Proxima Capital</h2>
            <p><strong>Property:</strong> {deal_address}</p>
            <p><strong>Investor:</strong> {name}</p>
            <p><strong>Email:</strong> {email}</p>
            <p><strong>Phone:</strong> {phone or 'Not provided'}</p>
            <p><strong>Notes:</strong> {notes or 'None'}</p>
            <p><a href="https://proxima-portal.onrender.com/admin">View Admin Panel</a></p>
            """
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            }
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Email notification failed: {e}")

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS deals (
            id           TEXT PRIMARY KEY,
            address      TEXT NOT NULL,
            description  TEXT,
            price        TEXT,
            annual_rent  TEXT,
            monthly      TEXT,
            net_yield    TEXT,
            lease_term   TEXT,
            break_clause TEXT DEFAULT 'None',
            term_income  TEXT,
            status       TEXT DEFAULT 'available',
            notes        TEXT,
            created_at   TEXT,
            updated_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS photos (
            id         TEXT PRIMARY KEY,
            deal_id    TEXT NOT NULL,
            filename   TEXT NOT NULL,
            caption    TEXT,
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS updates (
            id         TEXT PRIMARY KEY,
            deal_id    TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reservations (
            id         TEXT PRIMARY KEY,
            deal_id    TEXT NOT NULL,
            name       TEXT,
            email      TEXT,
            phone      TEXT,
            notes      TEXT,
            created_at TEXT
        );
    """)
    # Seed the two live deals if empty
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM deals")
    if cur.fetchone()[0] == 0:
        now = datetime.now().isoformat()
        for addr, notes in [
            ("Winterbottom Avenue, Hartlepool",
             "3 bed semi-detached. Gravel driveway. Quiet residential street with off-road parking."),
            ("Annandale Crescent, Hartlepool",
             "3 bed semi-detached. Pebble dash exterior. Quiet residential crescent. Off-road parking."),
        ]:
            con.execute("""
                INSERT INTO deals
                (id,address,description,price,annual_rent,monthly,net_yield,
                 lease_term,break_clause,term_income,status,notes,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(uuid.uuid4()), addr,
                "3 Bed Semi-Detached · Freehold · No Chain · Immediate Completion",
                "£114,000", "£11,400 p.a.", "£950", "10.0%",
                "10yr FRI", "None", "£130,667", "available", notes, now, now
            ))
    con.commit()
    con.close()

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def all_deals():
    con = db()
    deals = con.execute("SELECT * FROM deals ORDER BY created_at DESC").fetchall()
    out = []
    for d in deals:
        photos  = con.execute("SELECT * FROM photos WHERE deal_id=? ORDER BY sort_order", (d["id"],)).fetchall()
        updates = con.execute("SELECT * FROM updates WHERE deal_id=? ORDER BY created_at DESC", (d["id"],)).fetchall()
        reservs = con.execute("SELECT * FROM reservations WHERE deal_id=? ORDER BY created_at DESC", (d["id"],)).fetchall()
        out.append({"d": dict(d), "photos": [dict(p) for p in photos],
                    "updates": [dict(u) for u in updates], "reservations": [dict(r) for r in reservs]})
    con.close()
    return out

def allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def admin_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("admin"):
            return redirect("/admin/login")
        return f(*a, **kw)
    return wrap

# ── SERVE UPLOADS ─────────────────────────────────────────────────────────────

init_db()

@app.route("/uploads/<path:fn>")
def serve_upload(fn):
    return send_from_directory(UPLOAD_FOLDER, fn)

# ── AGENT PORTAL ──────────────────────────────────────────────────────────────

@app.route("/portal")
def portal():
    return render_template_string(PORTAL_HTML, deals=all_deals())

@app.route("/portal/reserve", methods=["POST"])
def reserve():
    data    = request.json or {}
    deal_id = data.get("deal_id")
    name    = data.get("name","").strip()
    email   = data.get("email","").strip()
    if not deal_id or not name or not email:
        return jsonify({"ok": False, "error": "Name and email required"})
    con = db()
    deal = con.execute("SELECT status FROM deals WHERE id=?", (deal_id,)).fetchone()
    if not deal or deal["status"] != "available":
        con.close()
        return jsonify({"ok": False, "error": "Unit not available"})
    now = datetime.now().isoformat()
    con.execute("UPDATE deals SET status='reserved', updated_at=? WHERE id=?", (now, deal_id))
    con.execute("INSERT INTO reservations (id,deal_id,name,email,phone,notes,created_at) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), deal_id, name, email, data.get("phone",""), data.get("notes",""), now))
    msg = f"Reserved by {name} ({email})"
    if data.get("phone"): msg += f" · {data['phone']}"
    if data.get("notes"): msg += f" · {data['notes']}"
    con.execute("INSERT INTO updates (id,deal_id,message,created_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), deal_id, msg, now))
    con.commit()
    con.close()
    return jsonify({"ok": True})

# ── ADMIN LOGIN ───────────────────────────────────────────────────────────────


@app.route("/portal/enquire", methods=["POST"])
def enquire():
    data = request.json or {}
    deal_id = data.get("deal_id")
    name = data.get("name","").strip()
    email = data.get("email","").strip()
    if not name or not email:
        return jsonify({"ok": False, "error": "Name and email required"})
    # Get deal address
    con = db()
    deal = con.execute("SELECT address FROM deals WHERE id=?", (deal_id,)).fetchone()
    con.close()
    addr = deal["address"] if deal else "Unknown property"
    send_notification(addr, name, email, data.get("phone",""), data.get("notes",""))
    return jsonify({"ok": True})

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        error = "Incorrect password."
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

# ── ADMIN PANEL ───────────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin():
    return render_template_string(ADMIN_HTML, deals=all_deals(),
        msg=request.args.get("msg"), err=request.args.get("err"), max_photos=MAX_PHOTOS)

@app.route("/admin/add-deal", methods=["POST"])
@admin_required
def add_deal():
    f = request.form
    if not f.get("address"):
        return redirect("/admin?err=Address+required")
    now = datetime.now().isoformat()
    con = db()
    con.execute("""INSERT INTO deals
        (id,address,description,price,annual_rent,monthly,net_yield,
         lease_term,break_clause,term_income,status,notes,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), f["address"], f.get("description",""),
         f.get("price",""), f.get("annual_rent",""), f.get("monthly",""),
         f.get("net_yield",""), f.get("lease_term",""), f.get("break_clause","None"),
         f.get("term_income",""), f.get("status","available"), f.get("notes",""), now, now))
    con.commit(); con.close()
    return redirect("/admin?msg=Deal+added")

@app.route("/admin/delete-deal", methods=["POST"])
@admin_required
def delete_deal():
    did = request.form.get("deal_id")
    con = db()
    for p in con.execute("SELECT filename FROM photos WHERE deal_id=?", (did,)).fetchall():
        try: os.remove(os.path.join(UPLOAD_FOLDER, p["filename"]))
        except: pass
    con.executescript(f"DELETE FROM photos WHERE deal_id='{did}'; DELETE FROM updates WHERE deal_id='{did}'; DELETE FROM reservations WHERE deal_id='{did}'; DELETE FROM deals WHERE id='{did}';")
    con.commit(); con.close()
    return redirect("/admin?msg=Deal+deleted")

@app.route("/admin/update-status", methods=["POST"])
@admin_required
def update_status():
    did    = request.form.get("deal_id")
    status = request.form.get("status")
    now    = datetime.now().isoformat()
    con    = db()
    con.execute("UPDATE deals SET status=?, updated_at=? WHERE id=?", (status, now, did))
    con.execute("INSERT INTO updates (id,deal_id,message,created_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), did, f"Status updated: {status.replace('-',' ').title()}", now))
    con.commit(); con.close()
    return redirect("/admin?msg=Status+updated")

@app.route("/admin/add-update", methods=["POST"])
@admin_required
def add_update():
    did = request.form.get("deal_id")
    msg = request.form.get("message","").strip()
    if not msg:
        return redirect("/admin?err=Message+required")
    now = datetime.now().isoformat()
    con = db()
    con.execute("INSERT INTO updates (id,deal_id,message,created_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), did, msg, now))
    con.commit(); con.close()
    return redirect("/admin?msg=Update+posted")

@app.route("/admin/upload-photo", methods=["POST"])
@admin_required
def upload_photo():
    did     = request.form.get("deal_id")
    caption = request.form.get("caption","")
    f       = request.files.get("photo")
    if not f or not f.filename or not allowed(f.filename):
        return redirect("/admin?err=Invalid+file")
    con   = db()
    count = con.execute("SELECT COUNT(*) FROM photos WHERE deal_id=?", (did,)).fetchone()[0]
    if count >= MAX_PHOTOS:
        con.close()
        return redirect(f"/admin?err=Max+{MAX_PHOTOS}+photos+per+deal")
    ext      = f.filename.rsplit(".", 1)[1].lower()
    filename = f"{did[:8]}_{uuid.uuid4().hex[:8]}.{ext}"
    f.save(os.path.join(UPLOAD_FOLDER, filename))
    con.execute("INSERT INTO photos (id,deal_id,filename,caption,sort_order) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), did, filename, caption, count))
    con.commit(); con.close()
    return redirect("/admin?msg=Photo+uploaded")

@app.route("/admin/delete-photo", methods=["POST"])
@admin_required
def delete_photo():
    pid = request.form.get("photo_id")
    con = db()
    row = con.execute("SELECT filename FROM photos WHERE id=?", (pid,)).fetchone()
    if row:
        try: os.remove(os.path.join(UPLOAD_FOLDER, row["filename"]))
        except: pass
        con.execute("DELETE FROM photos WHERE id=?", (pid,))
        con.commit()
    con.close()
    return redirect("/admin?msg=Photo+removed")

# ── HTML TEMPLATES ────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Proxima Admin</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{background:#085454;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:Inter,sans-serif}.box{background:#fff;border-radius:14px;padding:40px;width:360px;border-top:4px solid #01C977}.logo{font-size:22px;font-weight:800;color:#085454}.logo span{color:#01C977}.sub{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:#9aabab;margin:4px 0 28px}label{font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#535353;display:block;margin-bottom:5px}input{width:100%;padding:10px 13px;border:1px solid #cdd;border-radius:8px;font-size:14px;margin-bottom:16px;outline:none}input:focus{border-color:#085454}button{width:100%;background:#085454;color:#fff;font-size:12px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:12px;border-radius:8px;border:none;cursor:pointer}.err{background:#fce8e8;color:#b93333;font-size:12px;padding:10px;border-radius:7px;margin-bottom:14px}</style></head>
<body><div class="box"><div class="logo">Proxima <span>Capital</span></div><div class="sub">Admin Portal</div>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<form method="POST"><label>Password</label><input type="password" name="password" autofocus><button type="submit">Sign In</button></form>
</div></body></html>"""

ADMIN_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Proxima Admin</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f3f4f4;font-family:Inter,sans-serif;color:#222}
.hdr{background:#085454;border-bottom:3px solid #01C977;padding:14px 28px;display:flex;align-items:center;justify-content:space-between}
.logo{font-size:18px;font-weight:800;color:#fff}.logo span{color:#01C977}
.hdr-sub{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.4);margin-top:2px}
.nav a{color:rgba(255,255,255,0.65);font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;text-decoration:none;margin-left:18px}
.nav a:hover{color:#01C977}
.body{padding:24px 28px}
.msg{padding:10px 14px;border-radius:7px;margin-bottom:16px;font-size:13px}
.msg.ok{background:#e6fbf2;color:#059255;border:1px solid #b3f0d9}
.msg.err{background:#fce8e8;color:#b93333;border:1px solid #f5b8b8}
.sec{font-size:11px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;color:#085454;margin:24px 0 12px}
.card{background:#fff;border-radius:11px;border:1px solid #dde2e2;margin-bottom:16px;overflow:hidden}
.card-head{background:#085454;color:#fff;padding:13px 18px;display:flex;align-items:center;justify-content:space-between}
.card-addr{font-size:14px;font-weight:800}
.card-body{padding:18px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:16px}
.item label{font-size:9px;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:3px;display:block}
.val{font-size:13px;font-weight:700;font-family:monospace;color:#222}
.val.g{color:#01a060}
.photo-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;align-items:flex-end}
.thumb{width:110px;height:80px;object-fit:cover;border-radius:7px;border:1px solid #dde;display:block}
.add-photo-box{width:110px;height:80px;background:#f0f4f4;border-radius:7px;border:2px dashed #cdd;display:flex;align-items:center;justify-content:center;font-size:11px;color:#9aabab;cursor:pointer;text-align:center;flex-direction:column;gap:4px}
.upd-item{background:#f7f9f9;border-radius:7px;padding:9px 12px;margin-bottom:7px;font-size:12px;color:#444;display:flex;justify-content:space-between;gap:12px}
.upd-time{font-size:10px;color:#9aabab;white-space:nowrap}
.res-item{background:#fff3e0;border:1px solid #ffd888;border-radius:7px;padding:9px 12px;margin-bottom:7px;font-size:12px;color:#444}
.inline{display:flex;gap:8px;align-items:center;margin-top:8px}
.inp{padding:8px 10px;border:1px solid #cdd;border-radius:6px;font-size:12px;outline:none;flex:1}
.inp:focus{border-color:#085454}
.sel{padding:7px 10px;border:1px solid #cdd;border-radius:6px;font-size:12px;background:#fff;outline:none}
.btn{background:#085454;color:#fff;font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:8px 14px;border-radius:6px;border:none;cursor:pointer}
.btn:hover{opacity:0.88}
.btn-red{background:#fce8e8;color:#b93333;font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:6px 12px;border-radius:6px;border:1px solid #f5b8b8;cursor:pointer}
.btn-green{background:#01C977;color:#024d30;font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:8px 16px;border-radius:6px;border:none;cursor:pointer}
.s-avail{color:#059255;background:#e6fbf2;border:1px solid #b3f0d9;padding:3px 10px;border-radius:20px;font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase}
.s-res{color:#c07a00;background:#fff3e0;border:1px solid #ffd888;padding:3px 10px;border-radius:20px;font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase}
.s-other{color:#3a3d8f;background:#eaecf4;border:1px solid #c0c4e8;padding:3px 10px;border-radius:20px;font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase}
.form-box{background:#fff;border-radius:11px;border:1px solid #dde2e2;padding:22px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.form-grid label{font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#535353;display:block;margin-bottom:4px}
.form-grid input,.form-grid select,.form-grid textarea{width:100%;padding:8px 10px;border:1px solid #cdd8d8;border-radius:7px;font-size:13px;outline:none;background:#fff}
textarea{resize:vertical;min-height:60px}
</style></head><body>
<div class="hdr">
  <div><div class="logo">Proxima <span>Capital</span></div><div class="hdr-sub">Admin Panel</div></div>
  <div class="nav">
    <a href="/portal" target="_blank">&#8599; View Agent Portal</a>
    <a href="/admin/logout">Sign Out</a>
  </div>
</div>
<div class="body">
{% if msg %}<div class="msg ok">{{ msg }}</div>{% endif %}
{% if err %}<div class="msg err">{{ err }}</div>{% endif %}

<div class="sec">Live Deals</div>
{% for item in deals %}{% set d=item.d %}{% set photos=item.photos %}{% set updates=item.updates %}{% set reservs=item.reservations %}
<div class="card">
  <div class="card-head">
    <div class="card-addr">{{ d.address }}</div>
    <div style="display:flex;gap:10px;align-items:center">
      {% if d.status=='available' %}<span class="s-avail">Available</span>
      {% elif d.status=='reserved' %}<span class="s-res">Reserved</span>
      {% else %}<span class="s-other">{{ d.status.title() }}</span>{% endif %}
      <form method="POST" action="/admin/delete-deal" style="margin:0">
        <input type="hidden" name="deal_id" value="{{ d.id }}">
        <button class="btn-red" onclick="return confirm('Delete this deal?')">Delete</button>
      </form>
    </div>
  </div>
  <div class="card-body">
    <div class="grid3">
      <div class="item"><label>Price</label><div class="val">{{ d.price }}</div></div>
      <div class="item"><label>Annual Rent</label><div class="val">{{ d.annual_rent }}</div></div>
      <div class="item"><label>Net Yield</label><div class="val g">{{ d.net_yield }}</div></div>
      <div class="item"><label>Lease</label><div class="val">{{ d.lease_term }}</div></div>
      <div class="item"><label>10yr Income</label><div class="val">{{ d.term_income }}</div></div>
      <div class="item"><label>Break Clause</label><div class="val">{{ d.break_clause }}</div></div>
    </div>

    <div style="font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:8px">Photos ({{ photos|length }}/{{ max_photos }})</div>
    <div class="photo-row">
      {% for p in photos %}
      <div style="position:relative">
        <img src="/uploads/{{ p.filename }}" class="thumb">
        <form method="POST" action="/admin/delete-photo" style="position:absolute;top:3px;right:3px;margin:0">
          <input type="hidden" name="photo_id" value="{{ p.id }}">
          <button style="background:#b93333;color:#fff;border:none;border-radius:50%;width:20px;height:20px;font-size:13px;cursor:pointer;line-height:1" title="Remove">&times;</button>
        </form>
      </div>
      {% endfor %}
      {% if photos|length < max_photos %}
      <form method="POST" action="/admin/upload-photo" enctype="multipart/form-data">
        <input type="hidden" name="deal_id" value="{{ d.id }}">
        <label class="add-photo-box" for="file_{{ d.id }}">&#43;<br>Add Photo</label>
        <input type="file" id="file_{{ d.id }}" name="photo" accept="image/*" style="display:none" onchange="this.form.submit()">
      </form>
      {% endif %}
    </div>

    {% if reservs %}
    <div style="font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:8px">Reservations</div>
    {% for r in reservs %}
    <div class="res-item"><strong>{{ r.name }}</strong> &mdash; {{ r.email }}{% if r.phone %} &mdash; {{ r.phone }}{% endif %}{% if r.notes %}<br><span style="color:#888">{{ r.notes }}</span>{% endif %}<span style="float:right;font-size:10px;color:#9aabab">{{ r.created_at[:10] }}</span></div>
    {% endfor %}
    {% endif %}

    <div style="font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:8px">Progress Updates</div>
    {% for u in updates %}
    <div class="upd-item"><span>{{ u.message }}</span><span class="upd-time">{{ u.created_at[:16].replace('T',' ') }}</span></div>
    {% endfor %}
    {% if not updates %}<div style="font-size:12px;color:#9aabab;margin-bottom:8px">No updates yet.</div>{% endif %}
    <form method="POST" action="/admin/add-update">
      <input type="hidden" name="deal_id" value="{{ d.id }}">
      <div class="inline">
        <input class="inp" type="text" name="message" placeholder="e.g. Solicitors instructed. Exchange week commencing 19 May." required>
        <button class="btn" type="submit">Post</button>
      </div>
    </form>

    <form method="POST" action="/admin/update-status" style="margin-top:14px;display:flex;gap:8px;align-items:center">
      <input type="hidden" name="deal_id" value="{{ d.id }}">
      <span style="font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#535353">Status:</span>
      <select name="status" class="sel">
        <option value="available" {% if d.status=='available' %}selected{% endif %}>Available</option>
        <option value="reserved" {% if d.status=='reserved' %}selected{% endif %}>Reserved</option>
        <option value="under-offer" {% if d.status=='under-offer' %}selected{% endif %}>Under Offer</option>
        <option value="exchanged" {% if d.status=='exchanged' %}selected{% endif %}>Exchanged</option>
        <option value="completed" {% if d.status=='completed' %}selected{% endif %}>Completed</option>
      </select>
      <button class="btn" type="submit">Update</button>
    </form>
  </div>
</div>
{% endfor %}

<div class="sec">Add New Deal</div>
<div class="form-box">
  <form method="POST" action="/admin/add-deal">
    <div class="form-grid">
      <div><label>Street Address *</label><input type="text" name="address" placeholder="e.g. 14 Example Street, Hartlepool" required></div>
      <div><label>Property Description</label><input type="text" name="description" placeholder="3 Bed Semi-Detached · Freehold · No Chain"></div>
      <div><label>Sale Price</label><input type="text" name="price" placeholder="£114,000"></div>
      <div><label>Annual Rent</label><input type="text" name="annual_rent" placeholder="£11,400 p.a."></div>
      <div><label>Monthly Income</label><input type="text" name="monthly" placeholder="£950"></div>
      <div><label>Net Yield</label><input type="text" name="net_yield" placeholder="10.0%"></div>
      <div><label>Lease Term</label><input type="text" name="lease_term" placeholder="10yr FRI"></div>
      <div><label>Break Clause</label><select name="break_clause"><option>None</option><option>6-Month Break</option><option>12-Month Break</option></select></div>
      <div><label>Projected Term Income</label><input type="text" name="term_income" placeholder="£130,667"></div>
      <div><label>Status</label><select name="status"><option value="available">Available</option><option value="reserved">Reserved</option></select></div>
    </div>
    <div style="margin-bottom:14px"><label style="font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#535353;display:block;margin-bottom:4px">Property Notes</label>
      <textarea name="notes" placeholder="e.g. Pebble dash exterior. Quiet residential crescent. Off-road parking."></textarea>
    </div>
    <button class="btn" type="submit">Add Deal</button>
  </form>
</div>
</div></body></html>"""

PORTAL_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proxima Capital — Agent Portal</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f3f4f4;font-family:Inter,sans-serif;color:#222}
.hdr{background:#085454;border-bottom:3px solid #01C977;padding:16px 28px;display:flex;align-items:center;justify-content:space-between}
.logo{font-size:19px;font-weight:800;color:#fff}.logo span{color:#01C977}
.hdr-sub{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.4);margin-top:2px}
.pill-hdr{background:rgba(1,201,119,0.12);border:1px solid rgba(1,201,119,0.3);color:#01C977;font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;padding:5px 13px;border-radius:20px}
.stats{background:#085454;padding:0 28px 16px;display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.stat{background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.09);border-radius:9px;padding:13px 15px}
.sl{font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.45);margin-bottom:5px}
.sv{font-size:21px;font-weight:800;color:#fff;font-family:monospace}.sv.g{color:#01C977}
.ss{font-size:10px;color:rgba(255,255,255,0.35);margin-top:2px}
.body{padding:22px 28px}
.sec{font-size:11px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;color:#085454;margin-bottom:14px}
.card{background:#fff;border-radius:12px;border:1px solid #dde2e2;overflow:hidden;margin-bottom:16px}
.photos{display:grid;gap:2px}
.photos img{width:100%;height:180px;object-fit:cover;display:block}
.no-photo{height:140px;background:#e8f0f0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#9aabab}
.card-body{padding:18px 20px}
.addr{font-size:14px;font-weight:800;color:#085454;margin-bottom:2px}
.desc{font-size:11px;color:#535353;margin-bottom:11px}
.pills{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px}
.p{font-size:9px;font-weight:800;letter-spacing:0.8px;text-transform:uppercase;padding:3px 8px;border-radius:4px}
.ps{background:#e5efef;color:#085454}.pg{background:#e6fbf2;color:#059255}
.pr{background:#fce8e8;color:#b93333}.pa{background:#fff3e0;color:#c07a00}
.notes{font-size:12px;color:#535353;margin-bottom:12px;line-height:1.6}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
.ml{font-size:9px;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:2px}
.mv{font-size:13px;font-weight:800;font-family:monospace;color:#222}.mv.g{color:#01a060}
.bottom-row{display:flex;align-items:center;justify-content:space-between;padding-top:14px;border-top:1px solid #eff0f0;flex-wrap:wrap;gap:10px}
.yld{background:#085454;color:#01C977;font-size:20px;font-weight:800;padding:10px 16px;border-radius:9px;font-family:monospace;text-align:center}
.ys{font-size:9px;color:rgba(255,255,255,0.4);letter-spacing:1px;text-transform:uppercase;margin-top:2px}
.sc{font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:4px 11px;border-radius:20px}
.sa{background:#e6fbf2;color:#059255;border:1px solid #b3f0d9}
.sr{background:#fff3e0;color:#c07a00;border:1px solid #ffd888}
.so{background:#fce8e8;color:#b93333;border:1px solid #f5b8b8}
.sd{background:#eaecf4;color:#3a3d8f;border:1px solid #c0c4e8}
.btn-res{background:#01C977;color:#024d30;font-size:11px;font-weight:800;letter-spacing:0.8px;text-transform:uppercase;padding:9px 18px;border-radius:7px;border:none;cursor:pointer}
.btn-res:hover{opacity:0.85}.btn-res:disabled{opacity:0.35;cursor:not-allowed}
.upds{margin-top:14px}
.ut{font-size:9px;font-weight:800;letter-spacing:1.2px;text-transform:uppercase;color:#9aabab;margin-bottom:8px}
.ui{background:#f7f9f9;border-radius:7px;padding:8px 12px;margin-bottom:6px;font-size:12px;color:#444;display:flex;justify-content:space-between;gap:12px}
.ud{font-size:10px;color:#9aabab;white-space:nowrap}
.foot{background:#f7f9f9;border-top:1px solid #eff0f0;padding:9px 20px;font-size:10px;color:#9aabab;display:flex;justify-content:space-between}
.modal-bg{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(8,84,84,0.5);z-index:100;align-items:center;justify-content:center;padding:20px}
.modal-bg.open{display:flex}
.modal{background:#fff;border-radius:13px;padding:26px;max-width:420px;width:100%;border-top:4px solid #01C977}
.mt{font-size:15px;font-weight:800;color:#085454;margin-bottom:3px}
.ms{font-size:11px;color:#535353;margin-bottom:16px}
.ml2{font-size:9px;font-weight:800;letter-spacing:1.2px;text-transform:uppercase;color:#535353;margin-bottom:4px;display:block;margin-top:10px}
.mi{width:100%;padding:9px 11px;border:1px solid #cdd;border-radius:7px;font-size:13px;color:#222;outline:none}
.mi:focus{border-color:#085454}
.macts{display:flex;gap:9px;margin-top:18px}
.msub{flex:1;background:#085454;color:#fff;font-size:11px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:10px;border-radius:7px;border:none;cursor:pointer}
.mcan{background:#fff;color:#535353;font-size:11px;padding:10px 16px;border-radius:7px;border:1px solid #cdd;cursor:pointer}
.toast{position:fixed;bottom:20px;right:20px;background:#085454;color:#fff;padding:11px 18px;border-radius:9px;font-size:12px;font-weight:700;border-left:4px solid #01C977;opacity:0;transform:translateY(8px);transition:all 0.3s;pointer-events:none;z-index:999}
.toast.show{opacity:1;transform:translateY(0)}
</style></head><body>
<div class="hdr">
  <div><div class="logo">Proxima <span>Capital</span></div><div class="hdr-sub">Deal Portal</div></div>
  <div class="pill-hdr">&#10003; Authorised Agent</div>
</div>

<div class="body">
{% if msg %}<div class="msg ok">{{ msg }}</div>{% endif %}
{% if err %}<div class="msg err">{{ err }}</div>{% endif %}

<div class="sec">Live Deals</div>
{% for item in deals %}{% set d=item.d %}{% set photos=item.photos %}{% set updates=item.updates %}{% set reservs=item.reservations %}
<div class="card">
  <div class="card-head">
    <div class="card-addr">{{ d.address }}</div>
    <div style="display:flex;gap:10px;align-items:center">
      {% if d.status=='available' %}<span class="s-avail">Available</span>
      {% elif d.status=='reserved' %}<span class="s-res">Reserved</span>
      {% else %}<span class="s-other">{{ d.status.title() }}</span>{% endif %}
      <form method="POST" action="/admin/delete-deal" style="margin:0">
        <input type="hidden" name="deal_id" value="{{ d.id }}">
        <button class="btn-red" onclick="return confirm('Delete this deal?')">Delete</button>
      </form>
    </div>
  </div>
  <div class="card-body">
    <div class="grid3">
      <div class="item"><label>Price</label><div class="val">{{ d.price }}</div></div>
      <div class="item"><label>Annual Rent</label><div class="val">{{ d.annual_rent }}</div></div>
      <div class="item"><label>Net Yield</label><div class="val g">{{ d.net_yield }}</div></div>
      <div class="item"><label>Lease</label><div class="val">{{ d.lease_term }}</div></div>
      <div class="item"><label>10yr Income</label><div class="val">{{ d.term_income }}</div></div>
      <div class="item"><label>Break Clause</label><div class="val">{{ d.break_clause }}</div></div>
    </div>

    <div style="font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:8px">Photos ({{ photos|length }}/{{ max_photos }})</div>
    <div class="photo-row">
      {% for p in photos %}
      <div style="position:relative">
        <img src="/uploads/{{ p.filename }}" class="thumb">
        <form method="POST" action="/admin/delete-photo" style="position:absolute;top:3px;right:3px;margin:0">
          <input type="hidden" name="photo_id" value="{{ p.id }}">
          <button style="background:#b93333;color:#fff;border:none;border-radius:50%;width:20px;height:20px;font-size:13px;cursor:pointer;line-height:1" title="Remove">&times;</button>
        </form>
      </div>
      {% endfor %}
      {% if photos|length < max_photos %}
      <form method="POST" action="/admin/upload-photo" enctype="multipart/form-data">
        <input type="hidden" name="deal_id" value="{{ d.id }}">
        <label class="add-photo-box" for="file_{{ d.id }}">&#43;<br>Add Photo</label>
        <input type="file" id="file_{{ d.id }}" name="photo" accept="image/*" style="display:none" onchange="this.form.submit()">
      </form>
      {% endif %}
    </div>

    {% if reservs %}
    <div style="font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:8px">Reservations</div>
    {% for r in reservs %}
    <div class="res-item"><strong>{{ r.name }}</strong> &mdash; {{ r.email }}{% if r.phone %} &mdash; {{ r.phone }}{% endif %}{% if r.notes %}<br><span style="color:#888">{{ r.notes }}</span>{% endif %}<span style="float:right;font-size:10px;color:#9aabab">{{ r.created_at[:10] }}</span></div>
    {% endfor %}
    {% endif %}

    <div style="font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:8px">Progress Updates</div>
    {% for u in updates %}
    <div class="upd-item"><span>{{ u.message }}</span><span class="upd-time">{{ u.created_at[:16].replace('T',' ') }}</span></div>
    {% endfor %}
    {% if not updates %}<div style="font-size:12px;color:#9aabab;margin-bottom:8px">No updates yet.</div>{% endif %}
    <form method="POST" action="/admin/add-update">
      <input type="hidden" name="deal_id" value="{{ d.id }}">
      <div class="inline">
        <input class="inp" type="text" name="message" placeholder="e.g. Solicitors instructed. Exchange week commencing 19 May." required>
        <button class="btn" type="submit">Post</button>
      </div>
    </form>

    <form method="POST" action="/admin/update-status" style="margin-top:14px;display:flex;gap:8px;align-items:center">
      <input type="hidden" name="deal_id" value="{{ d.id }}">
      <span style="font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#535353">Status:</span>
      <select name="status" class="sel">
        <option value="available" {% if d.status=='available' %}selected{% endif %}>Available</option>
        <option value="reserved" {% if d.status=='reserved' %}selected{% endif %}>Reserved</option>
        <option value="under-offer" {% if d.status=='under-offer' %}selected{% endif %}>Under Offer</option>
        <option value="exchanged" {% if d.status=='exchanged' %}selected{% endif %}>Exchanged</option>
        <option value="completed" {% if d.status=='completed' %}selected{% endif %}>Completed</option>
      </select>
      <button class="btn" type="submit">Update</button>
    </form>
  </div>
</div>
{% endfor %}

<div class="sec">Add New Deal</div>
<div class="form-box">
  <form method="POST" action="/admin/add-deal">
    <div class="form-grid">
      <div><label>Street Address *</label><input type="text" name="address" placeholder="e.g. 14 Example Street, Hartlepool" required></div>
      <div><label>Property Description</label><input type="text" name="description" placeholder="3 Bed Semi-Detached · Freehold · No Chain"></div>
      <div><label>Sale Price</label><input type="text" name="price" placeholder="£114,000"></div>
      <div><label>Annual Rent</label><input type="text" name="annual_rent" placeholder="£11,400 p.a."></div>
      <div><label>Monthly Income</label><input type="text" name="monthly" placeholder="£950"></div>
      <div><label>Net Yield</label><input type="text" name="net_yield" placeholder="10.0%"></div>
      <div><label>Lease Term</label><input type="text" name="lease_term" placeholder="10yr FRI"></div>
      <div><label>Break Clause</label><select name="break_clause"><option>None</option><option>6-Month Break</option><option>12-Month Break</option></select></div>
      <div><label>Projected Term Income</label><input type="text" name="term_income" placeholder="£130,667"></div>
      <div><label>Status</label><select name="status"><option value="available">Available</option><option value="reserved">Reserved</option></select></div>
    </div>
    <div style="margin-bottom:14px"><label style="font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#535353;display:block;margin-bottom:4px">Property Notes</label>
      <textarea name="notes" placeholder="e.g. Pebble dash exterior. Quiet residential crescent. Off-road parking."></textarea>
    </div>
    <button class="btn" type="submit">Add Deal</button>
  </form>
</div>
</div></body></html>"""

PORTAL_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proxima Capital — Agent Portal</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f3f4f4;font-family:Inter,sans-serif;color:#222}
.hdr{background:#085454;border-bottom:3px solid #01C977;padding:16px 28px;display:flex;align-items:center;justify-content:space-between}
.logo{font-size:19px;font-weight:800;color:#fff}.logo span{color:#01C977}
.hdr-sub{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.4);margin-top:2px}
.pill-hdr{background:rgba(1,201,119,0.12);border:1px solid rgba(1,201,119,0.3);color:#01C977;font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;padding:5px 13px;border-radius:20px}
.stats{background:#085454;padding:0 28px 16px;display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.stat{background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.09);border-radius:9px;padding:13px 15px}
.sl{font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.45);margin-bottom:5px}
.sv{font-size:21px;font-weight:800;color:#fff;font-family:monospace}.sv.g{color:#01C977}
.ss{font-size:10px;color:rgba(255,255,255,0.35);margin-top:2px}
.body{padding:22px 28px}
.sec{font-size:11px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;color:#085454;margin-bottom:14px}
.card{background:#fff;border-radius:12px;border:1px solid #dde2e2;overflow:hidden;margin-bottom:16px}
.photos{display:grid;gap:2px}
.photos img{width:100%;height:180px;object-fit:cover;display:block}
.no-photo{height:140px;background:#e8f0f0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#9aabab}
.card-body{padding:18px 20px}
.addr{font-size:14px;font-weight:800;color:#085454;margin-bottom:2px}
.desc{font-size:11px;color:#535353;margin-bottom:11px}
.pills{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px}
.p{font-size:9px;font-weight:800;letter-spacing:0.8px;text-transform:uppercase;padding:3px 8px;border-radius:4px}
.ps{background:#e5efef;color:#085454}.pg{background:#e6fbf2;color:#059255}
.pr{background:#fce8e8;color:#b93333}.pa{background:#fff3e0;color:#c07a00}
.notes{font-size:12px;color:#535353;margin-bottom:12px;line-height:1.6}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
.ml{font-size:9px;letter-spacing:1px;text-transform:uppercase;color:#9aabab;margin-bottom:2px}
.mv{font-size:13px;font-weight:800;font-family:monospace;color:#222}.mv.g{color:#01a060}
.bottom-row{display:flex;align-items:center;justify-content:space-between;padding-top:14px;border-top:1px solid #eff0f0;flex-wrap:wrap;gap:10px}
.yld{background:#085454;color:#01C977;font-size:20px;font-weight:800;padding:10px 16px;border-radius:9px;font-family:monospace;text-align:center}
.ys{font-size:9px;color:rgba(255,255,255,0.4);letter-spacing:1px;text-transform:uppercase;margin-top:2px}
.sc{font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:4px 11px;border-radius:20px}
.sa{background:#e6fbf2;color:#059255;border:1px solid #b3f0d9}
.sr{background:#fff3e0;color:#c07a00;border:1px solid #ffd888}
.so{background:#fce8e8;color:#b93333;border:1px solid #f5b8b8}
.sd{background:#eaecf4;color:#3a3d8f;border:1px solid #c0c4e8}
.btn-res{background:#01C977;color:#024d30;font-size:11px;font-weight:800;letter-spacing:0.8px;text-transform:uppercase;padding:9px 18px;border-radius:7px;border:none;cursor:pointer}
.btn-res:hover{opacity:0.85}.btn-res:disabled{opacity:0.35;cursor:not-allowed}
.upds{margin-top:14px}
.ut{font-size:9px;font-weight:800;letter-spacing:1.2px;text-transform:uppercase;color:#9aabab;margin-bottom:8px}
.ui{background:#f7f9f9;border-radius:7px;padding:8px 12px;margin-bottom:6px;font-size:12px;color:#444;display:flex;justify-content:space-between;gap:12px}
.ud{font-size:10px;color:#9aabab;white-space:nowrap}
.foot{background:#f7f9f9;border-top:1px solid #eff0f0;padding:9px 20px;font-size:10px;color:#9aabab;display:flex;justify-content:space-between}
.modal-bg{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(8,84,84,0.5);z-index:100;align-items:center;justify-content:center;padding:20px}
.modal-bg.open{display:flex}
.modal{background:#fff;border-radius:13px;padding:26px;max-width:420px;width:100%;border-top:4px solid #01C977}
.mt{font-size:15px;font-weight:800;color:#085454;margin-bottom:3px}
.ms{font-size:11px;color:#535353;margin-bottom:16px}
.ml2{font-size:9px;font-weight:800;letter-spacing:1.2px;text-transform:uppercase;color:#535353;margin-bottom:4px;display:block;margin-top:10px}
.mi{width:100%;padding:9px 11px;border:1px solid #cdd;border-radius:7px;font-size:13px;color:#222;outline:none}
.mi:focus{border-color:#085454}
.macts{display:flex;gap:9px;margin-top:18px}
.msub{flex:1;background:#085454;color:#fff;font-size:11px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:10px;border-radius:7px;border:none;cursor:pointer}
.mcan{background:#fff;color:#535353;font-size:11px;padding:10px 16px;border-radius:7px;border:1px solid #cdd;cursor:pointer}
.toast{position:fixed;bottom:20px;right:20px;background:#085454;color:#fff;padding:11px 18px;border-radius:9px;font-size:12px;font-weight:700;border-left:4px solid #01C977;opacity:0;transform:translateY(8px);transition:all 0.3s;pointer-events:none;z-index:999}
.toast.show{opacity:1;transform:translateY(0)}
</style></head><body>
<div class="hdr">
  <div><div class="logo">Proxima <span>Capital</span></div><div class="hdr-sub">Deal Portal</div></div>
  <div class="pill-hdr">&#10003; Authorised Agent</div>
</div>

<div class="body">
<div class="sec">Live Deals</div>
{% for item in deals %}{% set d=item.d %}{% set photos=item.photos %}{% set updates=item.updates %}
<div class="card">
  {% if photos %}
  <div class="photos" style="grid-template-columns:repeat({{ [photos|length,3]|min }},1fr)">
    {% for p in photos[:3] %}<img src="/uploads/{{ p.filename }}" alt="{{ p.caption or d.address }}">{% endfor %}
  </div>
  {% else %}<div class="no-photo">Photos coming soon</div>{% endif %}
  <div class="card-body">
    <div class="addr">{{ d.address }}</div>
    <div class="desc">{{ d.description or '3 Bed Semi-Detached · Freehold · No Chain · Immediate Completion' }}</div>
    <div class="pills">
      <span class="p ps">FRI Lease</span><span class="p pg">Exempt Accommodation</span>
      <span class="p ps">{{ d.lease_term or '10yr FRI' }}</span><span class="p pg">Day 1 Income</span>
      <span class="p pr">No Break Clause</span><span class="p pg">HB Backed</span>
    </div>
    {% if d.notes %}<div class="notes">{{ d.notes }}</div>{% endif %}
    <div class="metrics">
      <div><div class="ml">Sale Price</div><div class="mv">{{ d.price }}</div></div>
      <div><div class="ml">Annual Rent</div><div class="mv g">{{ d.annual_rent }}</div></div>
      <div><div class="ml">Monthly Income</div><div class="mv">{{ d.monthly }}</div></div>
      <div><div class="ml">Lease Term</div><div class="mv">{{ d.lease_term }}</div></div>
      <div><div class="ml">10yr Income</div><div class="mv">{{ d.term_income }}</div></div>
      <div><div class="ml">Break Clause</div><div class="mv">{{ d.break_clause }}</div></div>
    </div>
    <div class="bottom-row">
      <div class="yld">{{ d.net_yield }}<div class="ys">Net Yield</div></div>
      {% if d.status=='available' %}<span class="sc sa">Available</span>
      {% elif d.status=='reserved' %}<span class="sc sr">Reserved</span>
      {% elif d.status=='under-offer' %}<span class="sc so">Under Offer</span>
      {% else %}<span class="sc sd">{{ d.status.title() }}</span>{% endif %}
      <button class="btn-res" {% if d.status!='available' %}disabled{% endif %}
        onclick="openModal('{{ d.id }}','{{ d.address }}','{{ d.price }} · {{ d.net_yield }} · {{ d.annual_rent }}')">
        &#128274; Reserve This Unit
      </button>
      {% if d.pdf_filename %}
      <a href="/uploads/{{ d.pdf_filename }}" target="_blank" class="btn-pdf">&#128196; Download Fact Sheet</a>
      {% endif %}
    </div>
    {% if updates %}
    <div class="upds"><div class="ut">Progress Updates</div>
    {% for u in updates %}<div class="ui"><span>{{ u.message }}</span><span class="ud">{{ u.created_at[:10] }}</span></div>{% endfor %}
    </div>{% endif %}
  </div>
  <div class="foot">
    <span>Hartlepool, County Durham &middot; Tees Valley</span>
    <span>RPI-linked reviews &middot; HB backed &middot; Exempt Accommodation</span>
  </div>
</div>
{% endfor %}
<p style="font-size:11px;color:#9aabab;margin-top:20px;line-height:1.7">For sophisticated and high-net-worth investors only. Capital is at risk. This portal does not constitute financial advice.<br>invest@proxcap.co &nbsp;|&nbsp; +44 20 8368 9966 &nbsp;|&nbsp; proxcap.co</p>
</div>
<div class="modal-bg" id="modal">
  <div class="modal">
    <div class="mt" id="m-title">Reserve Unit</div><div class="ms" id="m-sub"></div>
    <span class="ml2">Investor Name</span><input class="mi" id="m-name" placeholder="Full name">
    <span class="ml2">Email</span><input class="mi" id="m-email" placeholder="investor@email.com">
    <span class="ml2">Phone</span><input class="mi" id="m-phone" placeholder="07...">
    <span class="ml2">Notes</span><input class="mi" id="m-notes" placeholder="Cash buyer, SIPP, timeline...">
    <input type="hidden" id="m-id">
    <div class="macts"><button class="mcan" onclick="closeModal()">Cancel</button><button class="msub" onclick="submitRes()">Confirm Reservation</button></div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
function openModal(id,addr,sub){
  document.getElementById('m-id').value=id;
  document.getElementById('m-title').textContent='Enquire: '+addr;
  document.getElementById('m-sub').textContent=sub;
  ['m-name','m-email','m-phone','m-notes'].forEach(function(i){document.getElementById(i).value=''});
  document.getElementById('modal').classList.add('open');
}
function closeModal(){document.getElementById('modal').classList.remove('open')}
function submitRes(){
  var name=document.getElementById('m-name').value.trim();
  var email=document.getElementById('m-email').value.trim();
  if(!name||!email){showToast('Name and email required.');return;}
  fetch('/portal/enquire',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({deal_id:document.getElementById('m-id').value,name:name,email:email,
      phone:document.getElementById('m-phone').value,notes:document.getElementById('m-notes').value})
  }).then(function(r){return r.json()}).then(function(d){
    if(d.ok){closeModal();showToast('Enquiry sent. Proxima Capital will be in touch shortly.');setTimeout(function(){location.reload()},2200);}
    else{showToast('Error: '+d.error);}
  }).catch(function(){showToast('Network error. Please call us directly.');});
}
function showToast(msg){var t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(function(){t.classList.remove('show')},4000);}
document.getElementById('modal').addEventListener('click',function(e){if(e.target===this)closeModal()});
</script>
</body></html>"""

# ── RUN ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("\n" + "="*55)
    print("  PROXIMA CAPITAL — CHARITY DEAL PORTAL")
    print("="*55)
    print(f"  Agent Portal  →  http://localhost:{PORT}/portal")
    print(f"  Admin Panel   →  http://localhost:{PORT}/admin")
    print(f"  Admin Login   →  http://localhost:{PORT}/admin/login")
    print("="*55 + "\n")
    app.run(debug=False, port=PORT)
