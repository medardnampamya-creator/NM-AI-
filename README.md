# NM AI (standalone)
1. Install Ollama (ollama.com), then: `ollama pull llama3.1`
2. `pip install -r requirements.txt`
3. Optional: run SearXNG (JSON format enabled) on :8080 for live web search
4. `set NM_API_KEY=yourkey` (Windows) then `uvicorn server:app --host 0.0.0.0 --port 8000`
5. Open http://localhost:8000

Env: NM_MODEL, OLLAMA_URL, SEARXNG_URL, NM_WHISPER (tiny/base/small), NM_DB
Tools: web_search, read_url, search_knowledge, save_note, calculator, current_time

## Extra tools
- Coding: list_files, read_file, write_file, run_python, run_command (git/node/npm/pip/python/pytest/ffmpeg). All inside ./workspace
- Audio/video: generate_music (MusicGen), text_to_speech, make_video (image + audio -> mp4). Needs `pip install -r requirements-extra.txt` and ffmpeg
- Business: send_email (set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS), webhook (Zapier/Make/n8n/any API), schedule_task / list_tasks / cancel_task (results saved to knowledge base, optionally emailed)
- Generated files are served at /api/files/<name> and linked in chat
Note: run_python executes real code on your machine; keep the server on localhost or behind NM_API_KEY.

## App tabs (mobile-friendly)
- Chat: tools, voice, file links
- Code: pick or create a workspace file, edit, save, run .py, or send it to the AI for review
- Tasks: schedule prompts, run now, delete, see recent results
- Knowledge: notes, URLs, uploaded files
Tip: on your phone, open http://<laptop-ip>:8000 on the same Wi-Fi (start uvicorn with --host 0.0.0.0, and set NM_API_KEY).

## Install as a phone app (PWA)
Phones only allow install, service workers and the microphone over HTTPS (localhost is exempt), so plain http://<laptop-ip>:8000 will NOT offer install. Easiest fix, free:
1. Install Tailscale on the laptop and the phone, sign in to both (same account).
2. On the laptop: `tailscale serve --bg 8000` (prints an https://<name>.<tailnet>.ts.net address, reachable only from your own devices).
3. Open that address on the phone.
   - Android (Chrome): tap Install in the app header, or menu > Install app.
   - iPhone (Safari): Share > Add to Home Screen.
4. Tap Key once and enter your NM_API_KEY. Keep the server running on the laptop; the app is a front end for it.
Alternative: a Cloudflare Tunnel also gives an https address (then the API key matters even more).
The app shell works offline, but chat needs the laptop reachable.
