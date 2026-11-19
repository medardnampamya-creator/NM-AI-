"""NM AI - standalone backend. Local Ollama model + tools + knowledge base + local Whisper."""
import ast, json, operator, os, re, sqlite3, tempfile, time
from html import unescape
import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

OLLAMA = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("NM_MODEL", "llama3.1")
SEARX = os.getenv("SEARXNG_URL", "http://localhost:8080")
API_KEY = os.getenv("NM_API_KEY", "")  # set to require X-API-Key on every call
DB = os.getenv("NM_DB", "nm_ai.db")
SYSTEM = ("You are NM AI, a capable all-in-one assistant for coding, writing, research, music/audio/video "
          "and business automation. Use tools when they help and run code to verify it. When you create a file, "
          "give its link exactly as /api/files/<name>. Be direct and accurate; say when you are unsure.")

app = FastAPI(title="NM AI")
db = sqlite3.connect(DB, check_same_thread=False)
db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5(title, body)")
db.execute("CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY, ts REAL, role TEXT, content TEXT)")
db.commit()

def auth(x_api_key: str = Header(default=""), key: str = ""):
    if API_KEY and (x_api_key or key) != API_KEY:
        raise HTTPException(401, "Bad API key")

# ---------- knowledge base ----------
def kb_search(q: str, n: int = 4):
    words = re.findall(r"\w+", q)[:8]
    if not words:
        return []
    m = " OR ".join(f'"{w}"' for w in words)
    return db.execute("SELECT rowid,title,snippet(notes,1,'','','…',40) FROM notes WHERE notes MATCH ? "
                      "ORDER BY rank LIMIT ?", (m, n)).fetchall()

def kb_add(title: str, body: str):
    cur = db.execute("INSERT INTO notes(title,body) VALUES(?,?)", (title, body)); db.commit()
    return cur.lastrowid

# ---------- tools ----------
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg}
def _ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
    if isinstance(n, ast.BinOp): return OPS[type(n.op)](_ev(n.left), _ev(n.right))
    if isinstance(n, ast.UnaryOp): return OPS[type(n.op)](_ev(n.operand))
    raise ValueError("unsupported")

def t_calc(expression): return str(_ev(ast.parse(expression, mode="eval").body))
def t_time(): return time.strftime("%A %Y-%m-%d %H:%M:%S %Z")
def t_search_kb(query): return json.dumps([{"id": r[0], "title": r[1], "snippet": r[2]} for r in kb_search(query)])
def t_save_note(title, body): return f"saved note {kb_add(title, body)}"

def t_web(query):
    r = httpx.get(f"{SEARX}/search", params={"q": query, "format": "json"}, timeout=20)
    return json.dumps([{"title": x["title"], "url": x["url"], "content": x.get("content", "")[:300]}
                       for x in r.json().get("results", [])[:6]])

def t_read_url(url):
    r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "NM-AI"})
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", r.text)
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))[:6000]

def _schema(name, desc, props):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
        "required": list(props)}}}

TOOLS = {
    "web_search": (t_web, _schema("web_search", "Search the live web", {"query": "search terms"})),
    "read_url": (t_read_url, _schema("read_url", "Fetch and read a web page", {"url": "full URL"})),
    "search_knowledge": (t_search_kb, _schema("search_knowledge", "Search the user's saved notes", {"query": "keywords"})),
    "save_note": (t_save_note, _schema("save_note", "Save something to long-term memory", {"title": "short title", "body": "content"})),
    "calculator": (t_calc, _schema("calculator", "Evaluate arithmetic", {"expression": "e.g. 12*(3+4)"})),
    "current_time": (t_time, _schema("current_time", "Get current date and time", {})),
}

import tools_extra
TOOLS.update(tools_extra.build(db))

# ---------- chat ----------
def sse(kind, data): return f"data: {json.dumps({'type': kind, 'data': data})}\n\n"

def run_chat(history):
    ctx = kb_search(history[-1]["content"], 3)
    sys = SYSTEM + ("\n\nRelevant saved notes:\n" + "\n".join(f"- {r[1]}: {r[2]}" for r in ctx) if ctx else "")
    msgs = [{"role": "system", "content": sys}] + history
    db.execute("INSERT INTO chats(ts,role,content) VALUES(?,?,?)", (time.time(), "user", history[-1]["content"]))
    try:
        for _ in range(5):
            r = httpx.post(f"{OLLAMA}/api/chat", timeout=300, json={
                "model": MODEL, "messages": msgs, "stream": False,
                "tools": [v[1] for v in TOOLS.values()]}).json()
            m = r["message"]; calls = m.get("tool_calls") or []
            if not calls:
                db.execute("INSERT INTO chats(ts,role,content) VALUES(?,?,?)", (time.time(), "assistant", m["content"])); db.commit()
                yield sse("text", m["content"]); break
            msgs.append(m)
            for c in calls:
                name, args = c["function"]["name"], c["function"].get("arguments", {})
                yield sse("tool", f"{name}({', '.join(map(str, args.values()))})")
                try: out = TOOLS[name][0](**args)
                except Exception as e: out = f"error: {e}"
                msgs.append({"role": "tool", "content": str(out)[:6000]})
        else:
            yield sse("text", "Stopped after too many tool steps.")
    except Exception as e:
        yield sse("text", f"Model error: {e}. Is Ollama running with model '{MODEL}'?")
    yield sse("done", "")

@app.post("/api/chat", dependencies=[Depends(auth)])
def chat(body: dict):
    return StreamingResponse(run_chat(body["messages"]), media_type="text/event-stream")

@app.get("/api/notes", dependencies=[Depends(auth)])
def notes(): return [{"id": r[0], "title": r[1], "body": r[2][:200]} for r in db.execute("SELECT rowid,title,body FROM notes ORDER BY rowid DESC LIMIT 100")]

@app.post("/api/notes", dependencies=[Depends(auth)])
def add_note(b: dict): return {"id": kb_add(b["title"], b["body"])}

@app.post("/api/notes/url", dependencies=[Depends(auth)])
def add_url(b: dict): return {"id": kb_add(b["url"], t_read_url(b["url"]))}

@app.post("/api/notes/file", dependencies=[Depends(auth)])
async def add_file(f: UploadFile = File(...)):
    return {"id": kb_add(f.filename, (await f.read()).decode("utf-8", "ignore"))}

@app.delete("/api/notes/{nid}", dependencies=[Depends(auth)])
def del_note(nid: int):
    db.execute("DELETE FROM notes WHERE rowid=?", (nid,)); db.commit(); return {"ok": True}

_whisper = None
@app.post("/api/transcribe", dependencies=[Depends(auth)])
async def transcribe(audio: UploadFile = File(...)):
    global _whisper
    from faster_whisper import WhisperModel
    _whisper = _whisper or WhisperModel(os.getenv("NM_WHISPER", "base"), compute_type="int8")
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as t:
        t.write(await audio.read()); p = t.name
    segs, _ = _whisper.transcribe(p); os.unlink(p)
    return {"text": " ".join(s.text for s in segs).strip()}

@app.get("/api/health")
def health(): return {"model": MODEL, "tools": list(TOOLS), "auth": bool(API_KEY)}

@app.get("/")
def index(): return FileResponse("static/index.html")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/api/files/{name:path}", dependencies=[Depends(auth)])
def get_file(name: str): return FileResponse(tools_extra._p(name))

def answer(prompt):
    out = ""
    for ev in run_chat([{"role": "user", "content": prompt}]):
        d = json.loads(ev[6:])
        if d["type"] == "text": out = d["data"]
    return out

def scheduler():
    while True:
        time.sleep(30)
        try:
            for jid, prompt, every, last, email in db.execute("SELECT id,prompt,every,last,email FROM jobs").fetchall():
                if time.time() - last < every * 60: continue
                db.execute("UPDATE jobs SET last=? WHERE id=?", (time.time(), jid)); db.commit()
                res = answer(prompt)
                kb_add(f"Task {jid} {time.strftime('%Y-%m-%d %H:%M')}", res)
                if email: tools_extra.mail(email, f"NM AI task {jid}", res)
        except Exception as e:
            print("scheduler:", e)

@app.on_event("startup")
def _start():
    import threading; threading.Thread(target=scheduler, daemon=True).start()

# ---------- workspace editor + task dashboard API ----------
import sys, threading
D = [Depends(auth)]

@app.get("/api/ws/list", dependencies=D)
def ws_list(): return [f for f in tools_extra.list_files().split("\n") if f and f != "(empty)"]

@app.get("/api/ws/read", dependencies=D)
def ws_read(path: str):
    return {"content": open(tools_extra._p(path), encoding="utf-8", errors="ignore").read()}

@app.put("/api/ws/write", dependencies=D)
def ws_write(b: dict):
    tools_extra.write_file(b["path"], b["content"]); return {"ok": True}

@app.post("/api/ws/run", dependencies=D)
def ws_run(b: dict):
    if not b["path"].endswith(".py"): raise HTTPException(400, "Only .py files can be run here")
    try: return {"output": tools_extra._run([sys.executable, tools_extra._p(b["path"])], 30)}
    except Exception as e: return {"output": f"error: {e}"}

@app.get("/api/tasks", dependencies=D)
def tasks_get():
    jobs = [dict(zip(("id", "prompt", "every", "last", "email"), r)) for r in db.execute("SELECT id,prompt,every,last,email FROM jobs")]
    res = [{"id": r[0], "title": r[1], "body": r[2][:600]} for r in
           db.execute("SELECT rowid,title,body FROM notes WHERE title LIKE 'Task %' ORDER BY rowid DESC LIMIT 20")]
    return {"jobs": jobs, "results": res}

@app.post("/api/tasks", dependencies=D)
def tasks_add(b: dict): return {"msg": TOOLS["schedule_task"][0](b["prompt"], b["every"], b.get("email", ""))}

@app.delete("/api/tasks/{jid}", dependencies=D)
def tasks_del(jid: int):
    db.execute("DELETE FROM jobs WHERE id=?", (jid,)); db.commit(); return {"ok": True}

@app.post("/api/tasks/{jid}/run", dependencies=D)
def tasks_run(jid: int):
    row = db.execute("SELECT prompt FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row: raise HTTPException(404, "No such task")
    def go():
        kb_add(f"Task {jid} {time.strftime('%Y-%m-%d %H:%M')}", answer(row[0]))
    threading.Thread(target=go, daemon=True).start()
    return {"started": True}

@app.get("/sw.js")
def sw(): return FileResponse("static/sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})

@app.get("/manifest.webmanifest")
def manifest(): return FileResponse("static/manifest.webmanifest", media_type="application/manifest+json")
