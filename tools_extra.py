"""NM AI extra tools: coding, audio/video, business automation. All file work stays inside NM_WORKSPACE."""
import os, shlex, smtplib, subprocess, sys, time, json
from email.message import EmailMessage

WS = os.path.abspath(os.getenv("NM_WORKSPACE", "workspace"))
os.makedirs(WS, exist_ok=True)
ALLOWED = {"git", "node", "npm", "pip", "python", "pytest", "ffmpeg", "ffprobe"}

def _p(name):
    p = os.path.abspath(os.path.join(WS, name))
    if not p.startswith(WS + os.sep): raise ValueError("path outside workspace")
    return p

def _url(name): return f"/api/files/{name}"

# ---------- coding ----------
def list_files():
    return "\n".join(sorted(os.path.relpath(os.path.join(d, f), WS) for d, _, fs in os.walk(WS) for f in fs)) or "(empty)"

def read_file(path): return open(_p(path), encoding="utf-8", errors="ignore").read()[:8000]

def write_file(path, content):
    p = _p(path); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(content); return f"wrote {path} -> {_url(path)}"

def _run(args, timeout):
    r = subprocess.run(args, cwd=WS, capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr)[-4000:] or f"(exit {r.returncode}, no output)"

def run_python(code):
    f = _p("_run.py"); open(f, "w").write(code); return _run([sys.executable, f], 30)

def run_command(command):
    a = shlex.split(command, posix=(os.name != "nt"))
    if not a or a[0] not in ALLOWED: return f"blocked: only {sorted(ALLOWED)} are allowed"
    return _run(a, 120)

# ---------- audio / video ----------
_mg = None
def generate_music(prompt, seconds="10"):
    global _mg
    import numpy as np, scipy.io.wavfile as w
    from transformers import pipeline
    _mg = _mg or pipeline("text-to-audio", "facebook/musicgen-small")
    o = _mg(prompt, forward_params={"do_sample": True, "max_new_tokens": int(float(seconds) * 50)})
    name = f"music_{int(time.time())}.wav"
    w.write(_p(name), rate=o["sampling_rate"], data=np.squeeze(o["audio"]))
    return f"made {_url(name)}"

def text_to_speech(text):
    import pyttsx3
    name = f"speech_{int(time.time())}.wav"; e = pyttsx3.init()
    e.save_to_file(text, _p(name)); e.runAndWait(); return f"made {_url(name)}"

def make_video(image, audio):
    name = f"video_{int(time.time())}.mp4"
    out = _run(["ffmpeg", "-y", "-loop", "1", "-i", _p(image), "-i", _p(audio), "-shortest", "-c:v", "libx264",
                "-tune", "stillimage", "-pix_fmt", "yuv420p", "-c:a", "aac", _p(name)], 300)
    return f"made {_url(name)}" if os.path.exists(_p(name)) else out

# ---------- business automation ----------
def mail(to, subject, body):
    m = EmailMessage(); m["From"] = os.environ["SMTP_USER"]; m["To"] = to; m["Subject"] = subject; m.set_content(body)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))) as s:
        s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(m)
    return f"sent to {to}"

def webhook(url, json_body):
    import httpx
    r = httpx.post(url, json=json.loads(json_body), timeout=20); return f"{r.status_code} {r.text[:500]}"

def build(db):
    db.execute("CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, prompt TEXT, every INTEGER, last REAL, email TEXT)")
    db.commit()
    def schedule_task(prompt, every_minutes, email_to=""):
        c = db.execute("INSERT INTO jobs(prompt,every,last,email) VALUES(?,?,?,?)",
                       (prompt, max(1, int(float(every_minutes))), 0, email_to)); db.commit()
        return f"scheduled task {c.lastrowid}; results are saved to the knowledge base" + (" and emailed" if email_to else "")
    def list_tasks(): return json.dumps(db.execute("SELECT id,prompt,every,email FROM jobs").fetchall())
    def cancel_task(id): db.execute("DELETE FROM jobs WHERE id=?", (int(id),)); db.commit(); return "cancelled"
    def S(name, desc, props):
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
            "required": [k for k in props if k not in ("seconds", "email_to")]}}}
    T = [
        (list_files, S("list_files", "List files in the workspace", {})),
        (read_file, S("read_file", "Read a workspace file", {"path": "relative path"})),
        (write_file, S("write_file", "Create or overwrite a workspace file (code, docs, CSV)", {"path": "relative path", "content": "file text"})),
        (run_python, S("run_python", "Run Python code in the workspace (30s limit)", {"code": "python source"})),
        (run_command, S("run_command", "Run git/node/npm/pip/python/pytest/ffmpeg in the workspace", {"command": "command line"})),
        (generate_music, S("generate_music", "Generate music from a text prompt (local MusicGen)", {"prompt": "style and mood", "seconds": "length, default 10"})),
        (text_to_speech, S("text_to_speech", "Turn text into a speech audio file", {"text": "what to say"})),
        (make_video, S("make_video", "Make an mp4 from a workspace image plus audio file", {"image": "image filename", "audio": "audio filename"})),
        (mail, S("send_email", "Send an email via configured SMTP", {"to": "address", "subject": "subject", "body": "text"})),
        (webhook, S("webhook", "POST JSON to a URL (Zapier, Make, n8n, any API)", {"url": "endpoint", "json_body": "JSON string"})),
        (schedule_task, S("schedule_task", "Run a prompt on a repeating schedule", {"prompt": "what to do", "every_minutes": "interval", "email_to": "optional address for results"})),
        (list_tasks, S("list_tasks", "List scheduled tasks", {})),
        (cancel_task, S("cancel_task", "Cancel a scheduled task", {"id": "task id"})),
    ]
    reg = {s["function"]["name"]: (f, s) for f, s in T}
    reg["send_email"] = (mail, reg["send_email"][1])
    return reg
