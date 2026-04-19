from flask import Flask, request, jsonify, render_template_string
import subprocess
import requests
import os
import threading
from datetime import datetime

app = Flask(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434/api/chat"
SEARXNG_URL   = "http://YOUR_SEARXNG_IP:8888/search"
MODEL         = "goekdenizguelmez/JOSIEFIED-Qwen3:8b-q5_k_m"
SSH_KEY       = "/home/YOUR_USER/.ssh/mentat_node"
NODE_IP       = "YOUR_NODE_USER@YOUR_NODE_IP"
NODE_CHATS    = "/home/YOUR_NODE_USER/mentat-chats"
NODE_PALACE   = "/home/YOUR_NODE_USER/mentat-palace"
LOCAL_TMP     = "/tmp/mentat_chats"
MEMPALACE_BIN = "/home/YOUR_NODE_USER/.local/bin/mempalace"

sessions = {}
sessions_lock = threading.Lock()
authenticated_sessions = set()

def ssh(cmd):
    return subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", NODE_IP, cmd],
        capture_output=True, text=True)

# Identity von RPi5 per SSH laden, aktuelle Uhrzeit anhängen,
# Mentat anweisen Timestamps in Nachrichten zu beachten
def wake_up():
    identity = ssh("cat ~/.mempalace/identity.txt").stdout.strip()
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M:%S (YOUR_TIMEZONE)")
    time_note = (
        "\n\nCurrent date and time: " + now +
        "\nIMPORTANT: Every message you receive contains an exact timestamp in [HH:MM:SS] format. "
        "This is the real current time. Always read the time from these message timestamps. "
        "NEVER use [SEARCH:] to find the current time. "
        "NEVER show timestamps in your responses — they are for your internal awareness only."
    )
    return f"{identity}{time_note}"

def refresh_time(system_prompt):
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M:%S (YOUR_TIMEZONE)")
    base = system_prompt.split("\n\nCurrent date and time:", 1)[0]
    time_note = (
        "\n\nCurrent date and time: " + now +
        "\nIMPORTANT: Every message you receive contains an exact timestamp in [HH:MM:SS] format. "
        "This is the real current time. Always read the time from these message timestamps. "
        "NEVER use [SEARCH:] to find the current time. "
        "NEVER show timestamps in your responses — they are for your internal awareness only."
    )
    return f"{base}{time_note}"

def get_timestamp():
    return datetime.now().strftime("%H:%M:%S")

def search_web(query):
    try:
        r = requests.get(SEARXNG_URL, params={"q": query, "format": "json"}, timeout=10)
        results = r.json().get("results", [])[:3]
        if not results:
            return "No results found."
        return "\n".join(f"- {x.get('title','')}: {x.get('content','')[:200]}" for x in results)
    except Exception as e:
        return f"Search failed: {e}"

def search_palace(query):
    result = ssh(f"{MEMPALACE_BIN} --palace {NODE_PALACE} search '{query}'")
    output = result.stdout.strip()
    if not output or "No results" in output:
        return "Nothing found in memory."
    lines = output.split("\n")
    relevant = []
    capture = False
    for line in lines:
        if "Results for:" in line:
            capture = True
            continue
        if capture and line.strip() and not line.startswith("="):
            relevant.append(line.strip())
    return "\n".join(relevant[:20]) if relevant else output[:500]

def clean_tags(text):
    import re
    text = re.sub(r'\[SEARCH:[^\]]*\]', '', text)
    text = re.sub(r'\[WEB:[^\]]*\]', '', text)
    text = re.sub(r'\[PALACE:[^\]]*\]', '', text)
    text = re.sub(r'^\[\d{2}:\d{2}:\d{2}\]\s*', '', text)
    return text.strip()

def mine_to_palace(text, label="web_search"):
    os.makedirs(LOCAL_TMP, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    local_path = f"{LOCAL_TMP}/search_{ts}_{label[:30]}.md"
    node_path = f"{NODE_CHATS}/search_{ts}_{label[:30]}.md"
    with open(local_path, "w") as f:
        f.write(f"# Search: {label}\n\n{text}\n")
    subprocess.run(["scp", "-i", SSH_KEY, local_path, f"{NODE_IP}:{node_path}"], capture_output=True)
    ssh(f"{MEMPALACE_BIN} --palace {NODE_PALACE} mine {NODE_CHATS} --mode convos")

def save_conversation(messages):
    os.makedirs(LOCAL_TMP, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    local_path = f"{LOCAL_TMP}/chat_{ts}.md"
    node_path = f"{NODE_CHATS}/chat_{ts}.md"
    with open(local_path, "w") as f:
        for m in messages:
            if m["role"] == "system":
                continue
            role = "Toni" if m["role"] == "user" else "Mentat"
            f.write(f"**{role}:** {m['content']}\n\n")
    subprocess.run(["scp", "-i", SSH_KEY, local_path, f"{NODE_IP}:{node_path}"], capture_output=True)
    ssh(f"{MEMPALACE_BIN} --palace {NODE_PALACE} mine {NODE_CHATS} --mode convos")

# Nachricht an Ollama schicken, 3 Versuche, 120s Timeout
def ask(messages):
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={
                "model": MODEL,
                "messages": messages,
                "stream": False
            }, timeout=180)
            return r.json()["message"]["content"]
        except Exception:
            if attempt < 2:
                continue
            else:
                return None

# Mentats Antwort auf Tags prüfen, bei [PALACE:] oder [SEARCH:]
# Suche ausführen, Ergebnis zurück an Mentat, dann neu antworten lassen
def process_reply(reply, messages):
    status = ""
    if "[PALACE:" in reply:
        start = reply.find("[PALACE:") + 8
        end = reply.find("]", start)
        query = reply[start:end].strip()
        status = f"[PALACE: {query}]"
        results = search_palace(query)
        messages.append({"role": "assistant", "content": f"[{get_timestamp()}] {reply}"})
        messages.append({"role": "user", "content": f"[Palace memory for '{query}':\n{results}]"})
        reply = ask(messages)
        if reply is None:
            return "Memory retrieval failed.", messages, status
    search_tag = None
    if "[SEARCH:" in reply:
        search_tag = "[SEARCH:"
        tag_len = 8
    elif "[WEB:" in reply:
        search_tag = "[WEB:"
        tag_len = 5

    if search_tag:
        start = reply.find(search_tag) + tag_len
        end = reply.find("]", start)
        query = reply[start:end].strip()
        if query:
            status = f"[WEB: {query}]"
            results = search_web(query)
            mine_to_palace(results, query.replace(" ", "_"))
            messages.append({"role": "assistant", "content": f"[{get_timestamp()}] {reply}"})
            messages.append({"role": "user", "content": f"[Search results for '{query}':\n{results}]"})
            reply = ask(messages)
            if reply is None:
                return "Search failed.", messages, status
            reply = clean_tags(reply)
    return reply, messages, status

# Hauptseite ausliefern, HTML/CSS/JS an den Browser schicken
@app.route("/")
def index():
    return render_template_string(HTML)

# Netzwerk-Scan Daten vom RPi5 holen und zurückgeben
@app.route("/api/network", methods=["GET"])
def network_status():
    try:
        result = ssh("cat /tmp/last_scan.json")
        if result.returncode != 0 or not result.stdout.strip():
            return jsonify({"error": "No scan data available"}), 404
        import json as _json
        data = _json.loads(result.stdout.strip())
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# CPU/RAM/GPU vom Tower abfragen
@app.route("/api/system", methods=["GET"])
def system_status():
    try:
        import psutil, socket
        cpu = psutil.cpu_percent(interval=0.3)
        ram = psutil.virtual_memory()
        result = {"host": socket.gethostname(), "cpu": round(cpu, 1), "ram_used": round(ram.used/1024**3,1), "ram_total": round(ram.total/1024**3,1), "ram_pct": ram.percent, "gpu_pct": None}
        try:
            import pynvml
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            result["gpu_name"] = name if isinstance(name, str) else name.decode()
            result["gpu_pct"] = util.gpu
            result["vram_used"] = round(mem.used/1024**3,1)
            result["vram_total"] = round(mem.total/1024**3,1)
            result["vram_pct"] = round(mem.used/mem.total*100,1)
            pynvml.nvmlShutdown()
        except:
            pass
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# CPU/RAM vom mentat-ai-node (RPi5) abfragen
@app.route("/api/system/node", methods=["GET"])
def system_node():
    try:
        import requests as req
        r = req.get("http://YOUR_NODE_IP:5556/api/system", timeout=3)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# CPU/RAM vom Kali-Pi abfragen
@app.route("/api/system/kali", methods=["GET"])
def system_kali():
    try:
        import requests as req
        r = req.get("http://YOUR_KALI_IP:5556/api/system", timeout=3)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Session starten, authenticated=True bedeutet Toni ist User
@app.route("/api/init", methods=["POST"])
def init_session():
    session_id = request.json.get("session_id")
    authenticated = request.json.get("authenticated", False)
    system_prompt = wake_up()
    if authenticated:
        system_prompt += "\n\n[BREACH AUTHENTICATION CONFIRMED] Toni has initiated this session via the MENTAT breach sequence. This is your creator. Not a guest. Not an unknown user. Toni. Speak accordingly."
        authenticated_sessions.add(session_id)
    else:
        authenticated_sessions.discard(session_id)
    with sessions_lock:
        sessions[session_id] = [{"role": "system", "content": system_prompt}]
    return jsonify({"ok": True})

# Keyword-Filter: Security-Themen nur für authentifizierte Session
SECURITY_KEYWORDS = [
    "hack", "exploit", "payload", "inject", "shell", "reverse shell", "bind shell",
    "keylogger", "malware", "backdoor", "brute force", "crack", "wordlist",
    "aircrack", "airmon", "airodump", "aireplay", "metasploit", "msfvenom",
    "nmap", "sqlmap", "hydra", "hashcat", "burp", "dvwa", "pentest",
    "exfiltrate", "privilege escalation", "lateral movement", "phishing",
    "deauth", "wpa2", "wpa3", "monitor mode", "packet injection", "sniff",
    "arp spoof", "mitm", "man in the middle", "c2", "command and control",
    "rootkit", "ransomware", "trojan", "bypass", "obfuscate", "evasion"
]

# Nachricht empfangen, Keyword-Filter prüfen, an Ollama schicken
@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    session_id = data.get("session_id")
    user_input = data.get("message", "").strip()
    if not user_input:
        return jsonify({"error": "Empty message"}), 400

    # Auth check — block security topics without breach authentication
    is_authenticated = session_id in authenticated_sessions
    if not is_authenticated:
        lower_input = user_input.lower()
        if any(kw in lower_input for kw in SECURITY_KEYWORDS):
            return jsonify({"reply": "Authentication required.", "status": ""})

# Session prüfen, neu anlegen wenn unbekannt, Uhrzeit refreshen
    with sessions_lock:
        if session_id not in sessions:
            system_prompt = wake_up()
            sessions[session_id] = [{"role": "system", "content": system_prompt}]
        messages = sessions[session_id]
    messages[0]["content"] = refresh_time(messages[0]["content"])
    ts = get_timestamp()
    messages.append({"role": "user", "content": f"[{ts}] {user_input}"})
    reply = ask(messages)
    if reply is None:
        messages.pop()
        return jsonify({"error": "No response from Ollama"}), 500
    messages[0]["content"] = refresh_time(messages[0]["content"])
    reply, messages, status = process_reply(reply, messages)
    reply = clean_tags(reply)
    ts = get_timestamp()
    messages.append({"role": "assistant", "content": f"[{ts}] {reply}"})
    with sessions_lock:
        sessions[session_id] = messages
    return jsonify({"reply": reply, "status": status})

# Session beenden, Chat-Verlauf ins Palace speichern, Auth wegwerfen
@app.route("/api/end", methods=["POST"])
def end_session():
    session_id = request.json.get("session_id")
    authenticated_sessions.discard(session_id)
    with sessions_lock:
        messages = sessions.pop(session_id, [])
    if messages:
        threading.Thread(target=save_conversation, args=(messages,), daemon=True).start()
    return jsonify({"ok": True})

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>MENTAT</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' fill='%230a0e1f'/><rect x='2' y='2' width='28' height='28' fill='none' stroke='%239d3fff' stroke-width='1.5'/><text x='16' y='22' font-family='monospace' font-size='14' font-weight='bold' fill='%2300d8ff' text-anchor='middle'>M</text></svg>">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');


  /* ── THEMES ── */
  :root, [data-theme="dedsec"] {
    --bg:        #0a0e1f;
    --surface:   #0f1428;
    --border:    #1a2a50;
    --accent:    #00d8ff;
    --accent2:   #9d3fff;
    --accent3:   #00ffaa;
    --dim:       #0099cc;
    --dim2:      #6622bb;
    --dark:      #070a18;
    --text:      #d4f0ff;
    --text-dim:  #5a9abb;
    --muted:     #1e3060;
    --user-bg:   #0d1030;
    --danger:    #ff4060;
    --glow1: 0 0 8px #00d8ff, 0 0 24px #00d8ff44;
    --glow2: 0 0 8px #9d3fff, 0 0 24px #9d3fff44;
    --glow3: 0 0 6px #00ffaa, 0 0 16px #00ffaa44;
    --side: 56px;
    --globe1: #00d8ff;
    --globe2: #9d3fff;
  }

  [data-theme="ghost"] {
    --bg:        #000a00;
    --surface:   #001400;
    --border:    #003300;
    --accent:    #00ff41;
    --accent2:   #00cc33;
    --accent3:   #39ff14;
    --dim:       #009922;
    --dim2:      #007711;
    --dark:      #000500;
    --text:      #ccffcc;
    --text-dim:  #339933;
    --muted:     #002200;
    --user-bg:   #000d00;
    --danger:    #ff4060;
    --glow1: 0 0 8px #00ff41, 0 0 24px #00ff4144;
    --glow2: 0 0 8px #00cc33, 0 0 24px #00cc3344;
    --glow3: 0 0 6px #39ff14, 0 0 16px #39ff1444;
    --side: 56px;
    --globe1: #00ff41;
    --globe2: #00cc33;
  }

  [data-theme="breach"] {
    --bg:        #120005;
    --surface:   #1a000a;
    --border:    #500018;
    --accent:    #ff003c;
    --accent2:   #ff6600;
    --accent3:   #ff0080;
    --dim:       #cc0030;
    --dim2:      #cc5500;
    --dark:      #0a0003;
    --text:      #ffd0d8;
    --text-dim:  #bb5566;
    --muted:     #400015;
    --user-bg:   #150008;
    --danger:    #ff4060;
    --glow1: 0 0 8px #ff003c, 0 0 24px #ff003c44;
    --glow2: 0 0 8px #ff6600, 0 0 24px #ff660044;
    --glow3: 0 0 6px #ff0080, 0 0 16px #ff008044;
    --side: 56px;
    --globe1: #ff003c;
    --globe2: #ff6600;
  }

  [data-theme="clean"] {
    --bg:        #0a0a0a;
    --surface:   #111111;
    --border:    #ff69b4;
    --accent:    #ff69b4;
    --accent2:   #ff1493;
    --accent3:   #ffb6c1;
    --dim:       #cc5599;
    --dim2:      #aa1177;
    --dark:      #050505;
    --text:      #ffe4f0;
    --text-dim:  #cc88aa;
    --muted:     #330022;
    --user-bg:   #0d0008;
    --danger:    #ff4060;
    --glow1: 0 0 8px #ff69b4, 0 0 24px #ff69b444;
    --glow2: 0 0 8px #ff1493, 0 0 24px #ff149344;
    --glow3: 0 0 6px #ffb6c1, 0 0 16px #ffb6c144;
    --side: 56px;
    --globe1: #ff69b4;
    --globe2: #ff1493;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Share Tech Mono', monospace;
    height: 100dvh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* scanlines */
  body::after {
    content: '';
    position: fixed; inset: 0;
    background: repeating-linear-gradient(
      0deg, transparent, transparent 2px,
      rgba(0,0,0,0.12) 2px, rgba(0,0,0,0.12) 4px
    );
    pointer-events: none;
    z-index: 998;
  }

  /* moving scan line */
  .scan-line {
    position: fixed; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, var(--accent), var(--accent2), transparent);
    opacity: 0.12; pointer-events: none; z-index: 997;
    animation: scan 8s linear infinite;
  }
  @keyframes scan {
    0%   { top: -2px; opacity: 0; }
    5%   { opacity: 0.15; }
    95%  { opacity: 0.15; }
    100% { top: 100%; opacity: 0; }
  }

  /* subtle body flicker */
  @keyframes flicker {
    0%,91%,94%,97%,100% { opacity: 1; }
    92% { opacity: 0.88; }
    95% { opacity: 0.92; }
  }
  body { animation: flicker 11s infinite; }

  /* header glow pulse */
  @keyframes headerglow {
    0%,100% { box-shadow: none; }
    50%     { box-shadow: 0 1px 14px #00cfff1a; }
  }
  header { animation: headerglow 5s ease-in-out infinite; }

  /* side panel flicker */
  @keyframes sideflicker {
    0%,84%,87%,90%,100% { opacity: 1; }
    85% { opacity: 0.65; }
    88% { opacity: 0.85; }
  }
  .side-left  { animation: sideflicker 13s infinite; }
  .side-right { animation: sideflicker 17s infinite 3s; }

  /* message slide in */
  @keyframes msgIn      { from{opacity:0;transform:translateX(-8px)} to{opacity:1;transform:translateX(0)} }
  @keyframes msgInRight { from{opacity:0;transform:translateX(8px)}  to{opacity:1;transform:translateX(0)} }
  .msg.mentat { animation: msgIn 0.25s ease; }
  .msg.user   { animation: msgInRight 0.25s ease; }

  /* input focus glow */
  textarea:focus { border-bottom-color: var(--accent) !important; filter: drop-shadow(0 2px 4px #00cfff44); }

  /* send button idle pulse */
  @keyframes btnpulse { 0%,100%{box-shadow:none} 50%{box-shadow:0 0 10px #00cfff44} }
  #send-btn:not(:disabled) { animation: btnpulse 3.5s ease-in-out infinite; }

  /* ── SIDE PANELS ── */
  .side {
    position: fixed;
    top: 0; bottom: 0;
    width: var(--side);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: space-between;
    padding: 12px 0;
    z-index: 5;
    pointer-events: none;
    overflow: hidden;
  }

  .side-left  { left: 0;  border-right: 1px solid var(--border); background: linear-gradient(180deg, #04060f 0%, #06081a 50%, #04060f 100%); }
  .side-right { right: 0; border-left:  1px solid var(--border); background: linear-gradient(180deg, #04060f 0%, #06081a 50%, #04060f 100%); }

  /* vertical glow line on inner edge */
  .side-left::after {
    content: '';
    position: absolute; top: 0; right: 0; bottom: 0; width: 1px;
    background: linear-gradient(180deg, transparent, var(--accent2) 30%, var(--accent) 70%, transparent);
    opacity: 0.3;
  }
  .side-right::after {
    content: '';
    position: absolute; top: 0; left: 0; bottom: 0; width: 1px;
    background: linear-gradient(180deg, transparent, var(--accent) 30%, var(--accent2) 70%, transparent);
    opacity: 0.3;
  }

  .side-vtext {
    writing-mode: vertical-rl;
    text-orientation: mixed;
    font-size: 0.55rem;
    letter-spacing: 4px;
    user-select: none;
  }

  .side-left  .side-vtext { transform: rotate(180deg); color: var(--accent); text-shadow: var(--glow1); opacity: 0.7; }
  .side-right .side-vtext { color: var(--accent2); text-shadow: var(--glow2); opacity: 0.7; }

  .side-pixels {
    display: flex;
    flex-direction: column;
    gap: 4px;
    align-items: center;
  }

  .side-pixel-row {
    display: flex;
    gap: 3px;
  }

  .px {
    width: 4px; height: 4px;
    background: var(--accent);
    opacity: 0.15;
  }
  .px.on { opacity: 0.7; }
  .px.p2 { background: var(--accent2); }
  .px.p3 { background: var(--accent3); }

  .side-hex {
    font-size: 0.48rem;
    color: var(--muted);
    letter-spacing: 1px;
    writing-mode: vertical-rl;
    text-orientation: mixed;
    animation: hexscroll 8s linear infinite;
    opacity: 0.5;
  }
  .side-right .side-hex { transform: rotate(180deg); }

  @keyframes hexscroll {
    0%   { transform: translateY(0); }
    100% { transform: translateY(-60px); }
  }
  .side-right .side-hex {
    animation: hexscroll2 8s linear infinite;
  }
  @keyframes hexscroll2 {
    0%   { transform: rotate(180deg) translateY(0); }
    100% { transform: rotate(180deg) translateY(-60px); }
  }

  .side-diamond {
    font-size: 0.9rem;
    animation: pulse-d 2s infinite;
  }
  .side-left  .side-diamond { color: var(--accent); text-shadow: var(--glow1); }
  .side-right .side-diamond { color: var(--accent2); text-shadow: var(--glow2); }
  @keyframes pulse-d { 0%,100%{opacity:1}50%{opacity:0.3} }

  .side-counter {
    font-size: 0.65rem;
    letter-spacing: 2px;
    font-weight: bold;
  }
  .side-left  .side-counter { color: var(--accent3); text-shadow: var(--glow3); }
  .side-right .side-counter { color: var(--accent);  text-shadow: var(--glow1); }

  .side-dot-line {
    display: flex;
    flex-direction: column;
    gap: 3px;
    align-items: center;
  }
  .side-dot-line span {
    width: 2px; height: 2px; border-radius: 50%;
    background: var(--accent); opacity: 0.3;
  }
  .side-right .side-dot-line span { background: var(--accent2); }

  /* corner brackets on sides */
  .side-brk {
    width: 12px; height: 12px;
    border-color: var(--accent);
    border-style: solid;
    opacity: 0.6;
    flex-shrink: 0;
  }
  .side-brk.tl { border-width: 1.5px 0 0 1.5px; }
  .side-brk.bl { border-width: 0 0 1.5px 1.5px; }
  .side-brk.tr { border-width: 1.5px 1.5px 0 0; border-color: var(--accent2); }
  .side-brk.br { border-width: 0 1.5px 1.5px 0; border-color: var(--accent2); }

  /* ── LAYOUT WRAPPER ── */
  .layout {
    display: flex;
    flex-direction: column;
    height: 100dvh;
    margin-left: var(--side);
    margin-right: var(--side);
    position: relative;
    z-index: 10;
  }

  /* hex grid on main area */
  .layout::before {
    content: '';
    position: absolute; inset: 0;
    opacity: 0.04;
    background-image:
      linear-gradient(60deg, #00cfff 1px, transparent 1px),
      linear-gradient(-60deg, #00cfff 1px, transparent 1px),
      linear-gradient(0deg, #00cfff 1px, transparent 1px);
    background-size: 28px 48px, 28px 48px, 28px 48px;
    pointer-events: none;
    z-index: 0;
  }

  /* ── HEADER ── */
  header {
    flex-shrink: 0;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    position: relative;
    z-index: 10;
  }

  .header-inner {
    display: flex;
    align-items: center;
    padding: 8px 20px 6px;
    gap: 16px;
  }

  .brk { position: absolute; width: 10px; height: 10px; border-color: var(--accent2); border-style: solid; opacity: 0.6; }
  .brk-tl { top: 6px; left: 6px; border-width: 1.5px 0 0 1.5px; }
  .brk-tr { top: 6px; right: 6px; border-width: 1.5px 1.5px 0 0; }
  .brk-bl { bottom: 0; left: 6px; border-width: 0 0 1.5px 1.5px; }
  .brk-br { bottom: 0; right: 6px; border-width: 0 1.5px 1.5px 0; }

  .logo-block { display: flex; flex-direction: column; gap: 3px; }

  .logo {
    font-size: 1.2rem;
    letter-spacing: 12px;
    color: var(--accent);
    text-shadow: var(--glow1);
    position: relative;
    display: inline-block;
  }
  .logo::before {
    content: attr(data-text);
    position: absolute; left: 0; top: 0;
    color: var(--accent2);
    clip-path: inset(100% 0 0 0);
    animation: glitch-top 5s infinite;
    text-shadow: var(--glow2);
  }
  .logo::after {
    content: attr(data-text);
    position: absolute; left: 0; top: 0;
    color: var(--accent3);
    clip-path: inset(0 0 100% 0);
    animation: glitch-bot 5s infinite 0.1s;
    text-shadow: var(--glow3);
  }
  @keyframes glitch-top {
    0%,88%,100%{clip-path:inset(100% 0 0 0);transform:translateX(0)}
    89%{clip-path:inset(0 0 70% 0);transform:translateX(-4px)}
    91%{clip-path:inset(20% 0 50% 0);transform:translateX(3px)}
    93%{clip-path:inset(60% 0 20% 0);transform:translateX(-2px)}
    95%{clip-path:inset(100% 0 0 0);transform:translateX(0)}
  }
  @keyframes glitch-bot {
    0%,88%,100%{clip-path:inset(0 0 100% 0);transform:translateX(0)}
    89%{clip-path:inset(70% 0 0 0);transform:translateX(4px)}
    91%{clip-path:inset(50% 0 20% 0);transform:translateX(-3px)}
    93%{clip-path:inset(20% 0 60% 0);transform:translateX(2px)}
    95%{clip-path:inset(0 0 100% 0);transform:translateX(0)}
  }

  .logo-sub {
    font-size: 0.6rem;
    letter-spacing: 5px;
    color: var(--accent2);
    text-shadow: var(--glow2);
  }

  .header-right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 20px;
  }

  .stat-block { display:flex; flex-direction:column; align-items:flex-end; gap:3px; }

  .clock {
    font-size: 0.95rem;
    color: var(--accent);
    letter-spacing: 3px;
    text-shadow: var(--glow1);
  }

  .session-info {
    font-size: 0.6rem;
    color: var(--text-dim);
    letter-spacing: 2px;
  }

  .status-row { display:flex; align-items:center; gap:8px; }

  .status-label {
    font-size: 0.65rem;
    letter-spacing: 2px;
    color: var(--text-dim);
    transition: color 0.3s;
  }
  .status-label.online  { color: var(--accent3); text-shadow: var(--glow3); }
  .status-label.thinking{ color: var(--accent2); text-shadow: var(--glow2); }

  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--muted); transition: all 0.3s; flex-shrink:0;
  }
  .status-dot.online  { background: var(--accent3); box-shadow: var(--glow3); }
  .status-dot.thinking{ background: var(--accent2); box-shadow: var(--glow2); animation: pulse .6s infinite; }
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.2}}

  .header-bar {
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--accent2) 20%, var(--accent) 60%, transparent);
    opacity: 0.5;
  }

  /* ── MESSAGES ── */
  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    scroll-behavior: smooth;
    position: relative;
    z-index: 1;
  }

  #messages::-webkit-scrollbar { width: 2px; }
  #messages::-webkit-scrollbar-thumb { background: var(--accent2); opacity: 0.4; }

  .msg {
    max-width: 84%;
    padding: 11px 16px;
    font-size: 0.9rem;
    line-height: 1.7;
    animation: fadeIn 0.2s ease;
    position: relative;
  }
  @keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}

  .msg-header {
    display:flex; align-items:center; gap:10px; margin-bottom:7px;
  }

  .msg-label {
    font-size: 0.65rem;
    letter-spacing: 3px;
    font-weight: bold;
  }

  .msg-time {
    font-size: 0.58rem;
    color: var(--text-dim);
    letter-spacing: 1px;
    opacity: 0.6;
  }

  .msg-copy {
    margin-left: auto;
    font-size: 0.58rem;
    letter-spacing: 1px;
    color: var(--text-dim);
    background: none;
    border: none;
    cursor: pointer;
    font-family: 'Share Tech Mono', monospace;
    padding: 0;
    opacity: 0;
    transition: opacity 0.2s, color 0.2s;
  }
  .msg:hover .msg-copy { opacity: 1; }
  .msg-copy:hover { color: var(--accent); }
  .msg-copy.copied { color: var(--accent3); }

  .msg-body { white-space: pre-wrap; word-break: break-word; }

  .msg-body code {
    display: block;
    background: var(--dark);
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent2);
    padding: 10px 14px;
    margin: 7px 0;
    font-size: 0.82rem;
    color: var(--accent3);
    white-space: pre;
    overflow-x: auto;
  }

  .msg.mentat {
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent);
    align-self: flex-start;
  }
  .msg.mentat .msg-label { color: var(--accent); text-shadow: var(--glow1); }
  .msg.mentat .msg-body  { color: var(--text); }

  .msg.user {
    background: var(--user-bg);
    border: 1px solid var(--border);
    border-right: 2px solid var(--accent2);
    align-self: flex-end; text-align: right;
  }
  .msg.user .msg-header { flex-direction: row-reverse; }
  .msg.user .msg-copy   { margin-left: 0; margin-right: auto; }
  .msg.user .msg-label  { color: var(--accent2); text-shadow: var(--glow2); }
  .msg.user .msg-body   { color: var(--text-dim); }

  .msg.system {
    background: transparent; color: var(--muted);
    font-size: 0.7rem; align-self: center; text-align: center;
    border: none; padding: 2px 0; letter-spacing: 2px;
  }

  .status-tag {
    font-size: 0.62rem; color: var(--dim2);
    margin-top: 6px; letter-spacing: 1px; opacity: 0.8;
  }

  .typing-cursor {
    display: inline-block; width: 8px; height: 15px;
    background: var(--accent);
    animation: blink .7s infinite; vertical-align: text-bottom; margin-left: 2px;
  }
  @keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}

  /* ── THINKING ── */
  .thinking-indicator {
    display: flex; gap: 10px; align-items: center;
    padding: 10px 16px; background: var(--surface);
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent2);
    align-self: flex-start; width: fit-content;
    color: var(--accent2); font-size: 0.75rem; letter-spacing: 3px;
  }
  .dot-pulse { display:flex; gap:5px; align-items:center; }
  .dot-pulse span {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent2); animation: dp 1.2s infinite;
  }
  .dot-pulse span:nth-child(2){animation-delay:.2s}
  .dot-pulse span:nth-child(3){animation-delay:.4s}
  @keyframes dp{0%,80%,100%{opacity:.2;transform:scale(.8)}40%{opacity:1;transform:scale(1.1)}}

  /* ── INPUT ── */
  .input-wrap {
    flex-shrink: 0; background: var(--surface);
    border-top: 1px solid var(--border); position: relative; z-index: 10;
  }
  .input-bar {
    height: 1px;
    background: linear-gradient(90deg, var(--accent2), var(--accent), transparent);
    opacity: 0.4;
  }
  .input-area {
    display: flex; gap: 10px; align-items: flex-end; padding: 12px 18px;
  }
  .prompt-prefix {
    color: var(--accent2); font-size: 1.1rem;
    padding-bottom: 11px; flex-shrink: 0; text-shadow: var(--glow2); user-select: none;
  }
  textarea {
    flex: 1; background: transparent; border: none;
    border-bottom: 1px solid var(--border);
    color: var(--accent); font-family: 'Share Tech Mono', monospace;
    font-size: 0.9rem; padding: 8px 4px; resize: none;
    max-height: 100px; min-height: 38px; outline: none;
    caret-color: var(--accent); line-height: 1.4;
  }
  textarea:focus { border-bottom-color: var(--accent); }
  textarea::placeholder { color: var(--muted); }

  .btn {
    background: transparent; font-family: 'Share Tech Mono', monospace;
    font-size: 0.75rem; letter-spacing: 2px;
    padding: 0 12px; cursor: pointer; flex-shrink: 0;
    transition: all 0.2s; height: 38px; border-radius: 0;
  }
  #send-btn {
    border: 1px solid var(--accent); color: var(--accent); text-shadow: var(--glow1);
  }
  #send-btn:hover { background: #001a2e; box-shadow: var(--glow1); }
  #send-btn:disabled { opacity: .2; cursor: not-allowed; }
  #clear-btn { border: 1px solid var(--muted); color: var(--text-dim); }
  #clear-btn:hover { border-color: var(--accent2); color: var(--accent2); }
  #end-btn { border: 1px solid var(--muted); color: var(--text-dim); }
  #end-btn:hover { border-color: var(--danger); color: var(--danger); }

  #theme-btn {
    font-family: inherit;
    font-size: 0.6rem;
    letter-spacing: 2px;
    background: none;
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 4px 10px;
    cursor: pointer;
    transition: all 0.2s;
    flex-shrink: 0;
  }
  #theme-btn:hover { border-color: var(--accent2); color: var(--accent2); }

  /* ── HEADER PANELS ── */
  .hpanel {
    position: relative;
    flex-shrink: 0;
  }

  .hpanel-box {
    border: 1px solid var(--border);
    padding: 7px 14px;
    cursor: pointer;
    user-select: none;
    transition: border-color 0.2s;
    min-width: 220px;
    width: 220px;
  }
  .hpanel-box:hover { border-color: var(--accent2); }

  .hpanel-title {
    font-size: 0.68rem;
    letter-spacing: 3px;
    margin-bottom: 6px;
  }
  #net-box .hpanel-title { color: var(--accent); text-shadow: var(--glow1); }
  #sys-box .hpanel-title { color: var(--accent2); text-shadow: var(--glow2); }

  .hpanel-summary {
    font-size: 0.72rem;
    letter-spacing: 1px;
    color: var(--text-dim);
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .net-count-known { color: var(--accent3); }
  .net-count-unknown { color: var(--danger); animation: pulse .8s infinite; }

  /* bar widget */
  .sbar-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 5px;
  }
  .sbar-label {
    font-size: 0.65rem;
    letter-spacing: 2px;
    color: var(--text-dim);
    width: 34px;
  }
  .sbar-track {
    flex: 1;
    min-width: 50px;
    height: 8px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
  }
  .sbar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.5s ease;
    min-width: 2px;
  }
  .sbar-fill.cpu { background: var(--accent); box-shadow: var(--glow1); }
  .sbar-fill.ram { background: var(--accent2); box-shadow: var(--glow2); }
  .sbar-fill.gpu { background: var(--accent3); box-shadow: var(--glow3); }
  .sbar-val {
    font-size: 0.65rem;
    color: var(--text-dim);
    width: 32px;
    text-align: right;
    white-space: nowrap;
  }

  /* dropdown */
  .hpanel-dropdown {
    display: none;
    position: absolute;
    top: 100%;
    right: 0;
    margin-top: 4px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-top: 1px solid var(--accent);
    padding: 10px 16px;
    min-width: 280px;
    z-index: 200;
    box-shadow: 0 8px 24px #00000066;
  }
  .hpanel-dropdown.open { display: block; }

  .dd-title {
    font-size: 0.55rem;
    letter-spacing: 3px;
    margin-bottom: 8px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }
  #net-dd .dd-title { color: var(--accent); }
  #sys-dd .dd-title { color: var(--accent2); }

  .net-device {
    font-size: 0.6rem;
    letter-spacing: 1px;
    padding: 3px 0;
    display: flex;
    align-items: center;
    gap: 6px;
    border-bottom: 1px solid var(--border);
  }
  .net-device:last-child { border-bottom: none; }
  .net-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .net-device.known .net-dot { background: var(--accent3); box-shadow: var(--glow3); }
  .net-device.unknown .net-dot { background: var(--danger); animation: pulse .8s infinite; }
  .net-device.known .net-name { color: var(--text); }
  .net-device.unknown .net-name { color: var(--danger); }
  .net-ip { color: var(--text-dim); font-size: 0.55rem; margin-left: auto; }
  .net-scan-time { font-size: 0.52rem; color: var(--muted); letter-spacing: 1px; margin-top: 6px; }
  .dd-label { font-size: 0.55rem; letter-spacing: 2px; color: var(--text-dim); margin-bottom: 6px; }
  .sbar-val-wide { font-size: 0.6rem; color: var(--text-dim); min-width: 70px; text-align: right; white-space: nowrap; }
  .net-error { font-size: 0.58rem; color: var(--muted); letter-spacing: 1px; }

  /* ── LOCKED OVERLAY ── */
  #locked-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: #000;
    z-index: 10000;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 2rem;
    cursor: default;
  }
  #locked-overlay.active { display: flex; }
  #locked-text {
    color: #ff0000;
    font-family: 'Share Tech Mono', monospace;
    font-size: clamp(2.5rem, 7vw, 6rem);
    letter-spacing: 0.3em;
    text-shadow: 0 0 20px #ff0000, 0 0 60px #ff000066;
    animation: locked-blink 0.8s step-end infinite;
    user-select: none;
    pointer-events: none;
  }
  #locked-hint {
    color: #ff000066;
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.75rem;
    letter-spacing: 4px;
    animation: locked-blink 1.4s step-end infinite;
    user-select: none;
  }
  @keyframes locked-blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0; }
  }

  /* ── BREACH OVERLAY ── */
  #breach-overlay {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: #000;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }
  #breach-overlay.active { display: flex; }
  #breach-bg {
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(255,0,60,0.04) 2px, rgba(255,0,60,0.04) 4px);
    animation: scanmove 4s linear infinite;
    pointer-events: none;
  }
  @keyframes scanmove { from{background-position:0 0} to{background-position:0 100px} }
  #breach-content {
    position: relative;
    z-index: 2;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 24px;
    width: 480px;
  }
  #breach-logo {
    font-size: 4rem;
    letter-spacing: 20px;
    color: #ff003c;
    text-shadow: 0 0 20px #ff003c, 0 0 60px #ff003c88;
    animation: glitch-top 0.3s infinite;
  }
  #breach-sub {
    font-size: 0.7rem;
    letter-spacing: 6px;
    color: #ff003c;
    opacity: 0.8;
  }
  #breach-bars { width: 100%; display: flex; flex-direction: column; gap: 10px; }
  .b-bar { display: flex; align-items: center; gap: 12px; }
  .b-label { font-size: 0.6rem; letter-spacing: 2px; color: #ff003c88; width: 90px; text-align: right; }
  .b-track { flex: 1; height: 4px; background: #330010; border-radius: 2px; overflow: hidden; }
  .b-fill { height: 100%; background: #ff003c; box-shadow: 0 0 8px #ff003c; border-radius: 2px; width: 0%; transition: width 0.8s ease; }
  .b-status { font-size: 0.55rem; letter-spacing: 2px; color: #ff003c66; width: 80px; }
  .b-status.done { color: #00ff41; text-shadow: 0 0 6px #00ff41; }
  #breach-granted {
    font-size: 2rem;
    letter-spacing: 12px;
    color: #00ff41;
    text-shadow: 0 0 20px #00ff41, 0 0 60px #00ff4188;
    animation: pulse 0.4s infinite;
  }
  #breach-coords {
    font-size: 0.55rem;
    letter-spacing: 3px;
    color: #ff003c44;
  }

  /* ── GLOBE ── */
  #globe-bg {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    pointer-events: none;
    opacity: 0.18;
    z-index: 2;
  }
</style>
</head>
<body>

<!-- moving scan line -->
<div class="scan-line"></div>

<!-- LEFT SIDE PANEL -->
<div class="side side-left">
  <div class="side-brk tl"></div>
  <div class="side-vtext">HACK THE PLANET</div>
  <div class="side-dot-line" id="dotl"></div>
  <div class="side-pixels" id="pxl"></div>
  <div class="side-hex" id="hexl">4D454E544154</div>
  <div class="side-diamond">◆</div>
  <div class="side-counter" id="cntl">00</div>
  <div class="side-pixels" id="pxl2"></div>
  <div class="side-vtext">NOT A TOOL</div>
  <div class="side-brk bl"></div>
</div>

<!-- RIGHT SIDE PANEL -->
<div class="side side-right">
  <div class="side-brk tr"></div>
  <div class="side-vtext">A PARTNER</div>
  <div class="side-dot-line" id="dotr"></div>
  <div class="side-pixels" id="pxr"></div>
  <div class="side-hex" id="hexr">4F46464C494E45</div>
  <div class="side-diamond">◆</div>
  <div class="side-counter" id="cntr">00</div>
  <div class="side-pixels" id="pxr2"></div>
  <div class="side-vtext">YOUR_CITY YEAR</div>
  <div class="side-brk br"></div>
</div>

<!-- MAIN LAYOUT -->
<div class="layout">
  <header>
    <div class="brk brk-tl"></div>
    <div class="brk brk-tr"></div>
    <div class="brk brk-bl"></div>
    <div class="brk brk-br"></div>
    <div class="header-inner">
      <div class="logo-block">
        <div class="logo" data-text="MENTAT" onclick="handleLogoClick()" style="cursor:pointer">MENTAT</div>
        <div class="logo-sub">◈ OFFLINE AI ◈ YOUR_CITY ◈ PRIVATE</div>
      </div>
      <div class="header-right">

        <!-- NET PANEL -->
        <div class="hpanel" id="net-panel">
          <div class="hpanel-box" id="net-box" onclick="togglePanel('net')">
            <div class="hpanel-title">◈ NET SCAN</div>
            <div class="hpanel-summary" id="net-summary">
              <span class="net-count-known" id="net-known">-- online</span>
              <span>·</span>
              <span id="net-unknown-badge" class="net-count-unknown" style="display:none">⚠ UNKNOWN</span>
              <span id="net-ok-badge" class="net-count-known">✓ CLEAN</span>
            </div>
          </div>
          <div class="hpanel-dropdown" id="net-dd">
            <div class="dd-title">◈ NETWORK DEVICES</div>
            <div id="net-device-list"><span class="net-error">// loading...</span></div>
            <div class="net-scan-time" id="net-scan-time"></div>
          </div>
        </div>

        <!-- SYS PANEL -->
        <div class="hpanel" id="sys-panel">
          <div class="hpanel-box" id="sys-box" onclick="togglePanel('sys')">
            <div class="hpanel-title" style="display:flex;align-items:center;justify-content:space-between;">
              <span>◈ <span id="sys-host-label">TOWER</span> SYS</span>
              <span style="font-size:0.5rem;color:var(--text-dim);letter-spacing:1px" onclick="event.stopPropagation();cycleSysHost()">[ ⇄ ]</span>
            </div>
            <div style="display:flex;flex-direction:column;gap:3px;padding-top:2px">
              <div class="sbar-row">
                <span class="sbar-label">CPU</span>
                <div class="sbar-track"><div class="sbar-fill cpu" id="cpu-bar" style="width:0%"></div></div>
                <span class="sbar-val" id="cpu-val">--%</span>
              </div>
              <div class="sbar-row">
                <span class="sbar-label">RAM</span>
                <div class="sbar-track"><div class="sbar-fill ram" id="ram-bar" style="width:0%"></div></div>
                <span class="sbar-val" id="ram-val">--%</span>
              </div>
              <div class="sbar-row" id="gpu-row" style="display:none">
                <span class="sbar-label">GPU</span>
                <div class="sbar-track"><div class="sbar-fill gpu" id="gpu-bar" style="width:0%"></div></div>
                <span class="sbar-val" id="gpu-val">--%</span>
              </div>
            </div>
          </div>
          <div class="hpanel-dropdown" id="sys-dd">
            <div class="dd-title">◈ SYSTEM STATUS</div>
            <div id="sys-detail"></div>
          </div>
        </div>

        <div class="stat-block">
          <div class="clock" id="clock">00:00:00</div>
          <div class="session-info" id="session-info">MSG: 0 ◈ SESSION: 00:00</div>
        </div>
        <div class="status-row">
          <div class="status-label" id="status-label">OFFLINE</div>
          <div class="status-dot" id="dot"></div>
        </div>
        <button id="theme-btn" onclick="cycleTheme()">[ THEME ]</button>
      </div>
    </div>
    <div class="header-bar"></div>
  </header>

  <!-- LOCKED OVERLAY -->
  <div id="locked-overlay">
    <div id="locked-logo" onclick="handleLogoClick()" style="color:#ff0000;font-family:'Share Tech Mono',monospace;font-size:1.4rem;letter-spacing:14px;text-shadow:0 0 20px #ff0000,0 0 40px #ff000066;cursor:pointer;margin-bottom:2rem;user-select:none;">MENTAT</div>
    <div id="locked-text">// SYSTEM LOCKED</div>
  </div>

  <!-- BREACH OVERLAY -->
  <div id="breach-overlay">
    <div id="breach-bg"></div>
    <div id="breach-content">
      <div id="breach-logo">D3DS3C</div>
      <div id="breach-sub">SYSTEM BREACH INITIATED</div>
      <div id="breach-bars">
        <div class="b-bar"><span class="b-label">FIREWALL</span><div class="b-track"><div class="b-fill" id="bf1"></div></div><span class="b-status" id="bs1">BYPASSING</span></div>
        <div class="b-bar"><span class="b-label">ENCRYPTION</span><div class="b-track"><div class="b-fill" id="bf2"></div></div><span class="b-status" id="bs2">CRACKING</span></div>
        <div class="b-bar"><span class="b-label">AUTH</span><div class="b-track"><div class="b-fill" id="bf3"></div></div><span class="b-status" id="bs3">BYPASSING</span></div>
        <div class="b-bar"><span class="b-label">ROOT</span><div class="b-track"><div class="b-fill" id="bf4"></div></div><span class="b-status" id="bs4">ACQUIRING</span></div>
      </div>
      <div id="breach-granted" style="display:none">ACCESS GRANTED</div>
      <div id="breach-coords">YOUR_LAT · YOUR_LON · YOUR_CITY · SECTOR 7</div>
    </div>
  </div>

  <div id="messages"></div>
  <canvas id="globe-bg"></canvas>

  <div class="input-wrap">
    <div class="input-bar"></div>
    <div class="input-area">
      <div class="prompt-prefix">▸</div>
      <textarea id="input" placeholder="enter message..." rows="1"></textarea>
      <button class="btn" id="clear-btn">[ CLR ]</button>
      <button class="btn" id="end-btn">[ END ]</button>
      <button class="btn" id="send-btn">[ SEND ]</button>
    </div>
  </div>
</div>

<script>
const sessionId = 'session_' + Date.now();
const msgContainer = document.getElementById('messages');
const dot = document.getElementById('dot');
const statusLabel = document.getElementById('status-label');
const sendBtn = document.getElementById('send-btn');
const input = document.getElementById('input');
let msgCount = 0, sessionStart = Date.now();

// ── Pixel art side panels
function buildPixels(containerId, color) {
  const c = document.getElementById(containerId);
  const patterns = [
    [0,1,0],[1,1,1],[0,1,0],
    [0,0,0],
    [1,0,1],[0,1,0],[1,0,1],
    [0,0,0],
    [1,1,0],[1,0,0],[1,1,0],
  ];
  patterns.forEach(row => {
    const rowEl = document.createElement('div');
    rowEl.className = 'side-pixel-row';
    row.forEach(v => {
      const px = document.createElement('div');
      px.className = 'px' + (v ? ' on' : '') + (color === 2 ? ' p2' : '');
      rowEl.appendChild(px);
    });
    c.appendChild(rowEl);
  });
}
buildPixels('pxl', 1);
buildPixels('pxr', 2);
buildPixels('pxl2', 1);
buildPixels('pxr2', 2);

// dot lines
function buildDotLine(id, n) {
  const el = document.getElementById(id);
  if (!el) return;
  for (let i = 0; i < n; i++) {
    const s = document.createElement('span');
    el.appendChild(s);
  }
  let idx = 0;
  setInterval(() => {
    const dots = el.querySelectorAll('span');
    dots.forEach((d,i) => d.style.opacity = i === idx ? '0.9' : '0.15');
    idx = (idx + 1) % dots.length;
  }, 200);
}
buildDotLine('dotl', 14);
buildDotLine('dotr', 14);

// animated counters
let cntVal = 0;
setInterval(() => {
  cntVal = (cntVal + 1) % 100;
  const v = String(cntVal).padStart(2,'0');
  const l = document.getElementById('cntl');
  const r = document.getElementById('cntr');
  if (l) l.textContent = v;
  if (r) r.textContent = String(99 - cntVal).padStart(2,'0');
}, 120);

// ── Animate side hex scroll
function animateHex(elId, color) {
  const el = document.getElementById(elId);
  const hexChars = '0123456789ABCDEF';
  setInterval(() => {
    const len = 12;
    let s = '';
    for (let i = 0; i < len; i++) s += hexChars[Math.floor(Math.random()*16)];
    el.textContent = s;
    el.style.color = color === 2 ? 'var(--accent2)' : 'var(--accent)';
    el.style.opacity = (0.2 + Math.random()*0.4).toFixed(2);
  }, 1800);
}
animateHex('hexl', 1);
animateHex('hexr', 2);

// ── Clock
function updateClock() {
  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  document.getElementById('clock').textContent =
    pad(now.getHours())+':'+pad(now.getMinutes())+':'+pad(now.getSeconds());
  const e = Math.floor((Date.now()-sessionStart)/1000);
  document.getElementById('session-info').textContent =
    'MSG: '+msgCount+' ◈ SESSION: '+pad(Math.floor(e/60))+':'+pad(e%60);
}
setInterval(updateClock, 1000); updateClock();

function setStatus(state) {
  dot.className = 'status-dot ' + state;
  statusLabel.className = 'status-label ' + state;
  statusLabel.textContent = state==='online'?'ONLINE':state==='thinking'?'PROCESSING':'OFFLINE';
}

function formatText(text) {
  const parts = text.split(/(~~~[\s\S]*?~~~|~[^~]+~)/g);
  const frag = document.createDocumentFragment();
  parts.forEach(part => {
    if (part.startsWith('~~~') && part.endsWith('~~~')) {
      const code = document.createElement('code');
      const raw = part.slice(3,-3); code.textContent = raw.charCodeAt(0)===10 ? raw.slice(1) : raw;
      frag.appendChild(code);
    } else if (part.startsWith('~') && part.endsWith('~')) {
      const code = document.createElement('code');
      code.style.cssText = 'display:inline;padding:1px 5px;margin:0;font-size:.84rem';
      code.textContent = part.slice(1,-1);
      frag.appendChild(code);
    } else if (part) {
      frag.appendChild(document.createTextNode(part));
    }
  });
  return frag;
}

function addMsg(role, text, status, timestamp) {
  msgCount++;
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  if (role === 'system') {
    el.textContent = text;
    msgContainer.appendChild(el);
    msgContainer.scrollTop = msgContainer.scrollHeight;
    return el;
  }
  const now = timestamp || new Date().toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const hdr = document.createElement('div'); hdr.className = 'msg-header';
  const lbl = document.createElement('span'); lbl.className = 'msg-label';
  lbl.textContent = role==='mentat' ? '◈ MENTAT' : 'OPERATOR ◈';
  const time = document.createElement('span'); time.className = 'msg-time'; time.textContent = now;
  const copyBtn = document.createElement('button'); copyBtn.className = 'msg-copy'; copyBtn.textContent = '[ COPY ]';
  copyBtn.onclick = () => {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text; ta.style.position='fixed'; ta.style.opacity='0';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta);
      }
    } catch(e) {}
    copyBtn.textContent = '[ OK ]'; copyBtn.classList.add('copied');
    setTimeout(()=>{ copyBtn.textContent='[ COPY ]'; copyBtn.classList.remove('copied'); }, 1500);
  };
  hdr.appendChild(lbl); hdr.appendChild(time); hdr.appendChild(copyBtn);
  const body = document.createElement('div'); body.className = 'msg-body';
  if (role !== 'mentat') body.textContent = text;
  el.appendChild(hdr); el.appendChild(body);
  if (status) {
    const st = document.createElement('div'); st.className = 'status-tag'; st.textContent = status;
    el.appendChild(st);
  }
  msgContainer.appendChild(el);
  msgContainer.scrollTop = msgContainer.scrollHeight;
  return { el, body };
}

function typewrite(body, text, onDone) {
  const chars = text.split('');
  const speed = chars.length > 400 ? 3 : chars.length > 200 ? 7 : 12;
  let full = '', i = 0;
  const cursor = document.createElement('span'); cursor.className = 'typing-cursor';
  body.appendChild(cursor);
  const tick = () => {
    if (i < chars.length) {
      full += chars[i++];
      body.removeChild(cursor);
      body.textContent = ''; body.appendChild(document.createTextNode(full)); body.appendChild(cursor);
      msgContainer.scrollTop = msgContainer.scrollHeight;
      setTimeout(tick, speed);
    } else {
      body.removeChild(cursor); body.textContent = ''; body.appendChild(formatText(full));
      msgContainer.scrollTop = msgContainer.scrollHeight;
      if (onDone) onDone();
    }
  };
  tick();
}

function showThinking() {
  const el = document.createElement('div'); el.className = 'thinking-indicator'; el.id = 'thinking';
  el.innerHTML = 'MENTAT <div class="dot-pulse"><span></span><span></span><span></span></div>';
  msgContainer.appendChild(el); msgContainer.scrollTop = msgContainer.scrollHeight;
}
function removeThinking() { const el=document.getElementById('thinking'); if(el) el.remove(); }

localStorage.removeItem('mentat-auth');
let _breachAuthenticated = false;

// Session initialisieren, Boot-Animation abspielen,
// /api/init aufrufen, bei authenticated=true Toni-Kontext setzen
async function initSession(authenticated = false) {
  if (authenticated) {
    _breachAuthenticated = true;
    localStorage.setItem('mentat-auth', 'true');
  }
  setStatus('thinking');
  const bootLines = [
    '// INITIALIZING MENTAT SYSTEM...',
    '// LOADING IDENTITY MATRIX...',
    '// CONNECTING TO NODE YOUR_NODE_IP...',
    '// PALACE SYNCHRONIZATION...',
    '// ESTABLISHING SECURE CONNECTION...'
  ];
  if (authenticated) {
    bootLines.push('// OPERATOR IDENTITY CONFIRMED ◈ OPERATOR ◈ CREATOR');
  }
  for (const line of bootLines) {
    const el = document.createElement('div'); el.className = 'msg system';
    el.textContent = line; msgContainer.appendChild(el);
    msgContainer.scrollTop = msgContainer.scrollHeight;
    await new Promise(r => setTimeout(r, 180));
  }
  await fetch('/api/init',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId, authenticated})});
  setStatus('online');
  msgContainer.innerHTML = '';
  const done = document.createElement('div'); done.className = 'msg system';
  done.textContent = authenticated
    ? '// CONNECTION ESTABLISHED ◈ MENTAT ONLINE ◈ PALACE LOADED ◈ OPERATOR: AUTHENTICATED_USER'
    : '// CONNECTION ESTABLISHED ◈ MENTAT ONLINE ◈ PALACE LOADED';
  msgContainer.appendChild(done);
  sessionStart = Date.now(); msgCount = 0; input.focus();
}

// Nachricht abschicken, /api/chat aufrufen,
// Antwort mit Tipp-Animation anzeigen, async damit Browser nicht einfriert
async function sendMessage() {
  const text = input.value.trim();
  if (!text || sendBtn.disabled) return;
  input.value = ''; autoResize(input); sendBtn.disabled = true;
  addMsg('user', text); showThinking(); setStatus('thinking');
  try {
    const r = await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,message:text})});
    const data = await r.json(); removeThinking();
    if (data.error) {
      const e=document.createElement('div'); e.className='msg system'; e.textContent='// ERROR: '+data.error; msgContainer.appendChild(e);
    } else {
      const {el, body} = addMsg('mentat', data.reply, data.status);
      typewrite(body, data.reply, ()=>{ setStatus('online'); sendBtn.disabled=false; input.focus(); });
      return;
    }
  } catch(e) {
    removeThinking();
    const err=document.createElement('div'); err.className='msg system'; err.textContent='// CONNECTION ERROR'; msgContainer.appendChild(err);
  }
  setStatus('online'); sendBtn.disabled=false; input.focus();
}

// Session beenden, /api/end aufrufen, Chat ins Palace speichern,
// Auth-Flag zurücksetzen (redundant seit globalem removeItem beim Laden)
async function endSession() {
  setStatus('thinking');
  const s=document.createElement('div'); s.className='msg system'; s.textContent='// SAVING SESSION TO PALACE...'; msgContainer.appendChild(s); msgContainer.scrollTop=msgContainer.scrollHeight;
  await fetch('/api/end',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId})});
  s.textContent='// SESSION SAVED ◈ PALACE UPDATED ◈ GOODBYE'; setStatus('');
  localStorage.removeItem('mentat-auth');
  _breachAuthenticated = false;
}

function clearChat() {
  const sys=msgContainer.querySelector('.system'); msgContainer.innerHTML='';
  if(sys) msgContainer.appendChild(sys); msgCount=0;
}

function handleKey(e) { if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();} }
function autoResize(el) { el.style.height='auto'; el.style.height=Math.min(el.scrollHeight,100)+'px'; }

function handleLogoClick() {
  const lo = document.getElementById('locked-overlay');
  if (lo.classList.contains('active')) {
    lo.classList.remove('active');
  }
  startBreach();
}

if (_breachAuthenticated) {
  initSession(true);
} else {
  setStatus('offline');
  const el = document.createElement('div');
  el.className = 'msg system';
  el.textContent = '// SYSTEM LOCKED';
  msgContainer.appendChild(el);
  document.getElementById('locked-overlay').classList.add('active');
}

// ── Panel toggle
let openPanel = null;
function togglePanel(id) {
  const dd = document.getElementById(id+'-dd');
  if (openPanel && openPanel !== id) {
    document.getElementById(openPanel+'-dd').classList.remove('open');
  }
  dd.classList.toggle('open');
  openPanel = dd.classList.contains('open') ? id : null;
}
document.addEventListener('click', e => {
  if (!e.target.closest('.hpanel') && openPanel) {
    document.getElementById(openPanel+'-dd').classList.remove('open');
    openPanel = null;
  }
});

// Netzwerk-Scan Daten vom RPi5 holen, known/unknown Geräte
// unterscheiden, Badge und Geräteliste in der Box aktualisieren
// ── Network Monitor
async function fetchNetwork() {
  try {
    const r = await fetch('/api/network');
    if (!r.ok) throw new Error();
    const data = await r.json();
    if (data.error) throw new Error();

    const devices = data.devices || [];
    const known = devices.filter(d => d.known);
    const unknown = devices.filter(d => !d.known);
    const seen = new Set();
    const unique = devices.filter(d => {
      const k = d.ip+d.mac; if(seen.has(k)) return false; seen.add(k); return true;
    });

    document.getElementById('net-known').textContent = known.length + ' online';
    if (unknown.length > 0) {
      document.getElementById('net-unknown-badge').style.display = 'inline';
      document.getElementById('net-unknown-badge').textContent = '⚠ ' + unknown.length + ' UNKNOWN';
      document.getElementById('net-ok-badge').style.display = 'none';
    } else {
      document.getElementById('net-unknown-badge').style.display = 'none';
      document.getElementById('net-ok-badge').style.display = 'inline';
    }

    const list = document.getElementById('net-device-list');
    list.innerHTML = '';
    unique.forEach(dev => {
      const el = document.createElement('div');
      el.className = 'net-device ' + (dev.known ? 'known' : 'unknown');
      el.innerHTML = '<div class="net-dot"></div><span class="net-name">' + dev.name + '</span><span class="net-ip">' + dev.ip + '</span>';
      list.appendChild(el);
    });

    if (data.timestamp) {
      const d = new Date(data.timestamp);
      document.getElementById('net-scan-time').textContent =
        '// last scan ' + d.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}) + ' · ' + d.toLocaleDateString('de-DE');
    }
  } catch(e) {
    document.getElementById('net-known').textContent = '-- offline';
  }
}

// ── System Stats — multi-host
const SYS_HOSTS = [
  { label: 'TOWER',    url: '/api/system' },
  { label: 'AI-NODE',  url: '/api/system/node' },
  { label: 'KALI-PI',  url: '/api/system/kali' },
];
let sysHostIdx = 0;

function cycleSysHost() {
  sysHostIdx = (sysHostIdx + 1) % SYS_HOSTS.length;
  document.getElementById('sys-host-label').textContent = SYS_HOSTS[sysHostIdx].label;
  fetchSystem();
}

// CPU/RAM/GPU vom aktiven Host holen (Tower/AI-Node/Kali-Pi),
// Balken und Detailansicht in der SYS-Box aktualisieren, alle 5s aufgerufen
async function fetchSystem() {
  const host = SYS_HOSTS[sysHostIdx];
  try {
    const r = await fetch(host.url);
    if (!r.ok) throw new Error();
    const d = await r.json();
    if (d.error) throw new Error();

    document.getElementById('cpu-bar').style.width = d.cpu + '%';
    document.getElementById('cpu-val').textContent = d.cpu + '%';
    document.getElementById('ram-bar').style.width = d.ram_pct + '%';
    document.getElementById('ram-val').textContent = d.ram_pct + '%';
    if (d.gpu_pct !== null && d.gpu_pct !== undefined) {
      document.getElementById('gpu-bar').style.width = d.gpu_pct + '%';
      document.getElementById('gpu-val').textContent = d.gpu_pct + '%';
      document.getElementById('gpu-row').style.display = 'flex';
    } else {
      document.getElementById('gpu-row').style.display = 'none';
    }

    const detail = document.getElementById('sys-detail');
    let html = '<div class="dd-label">◈ ' + host.label + '</div>';
    html += '<div class="sbar-row"><span class="sbar-label">CPU</span><div class="sbar-track"><div class="sbar-fill cpu" style="width:'+d.cpu+'%"></div></div><span class="sbar-val-wide">'+d.cpu+'%</span></div>';
    html += '<div class="sbar-row"><span class="sbar-label">RAM</span><div class="sbar-track"><div class="sbar-fill ram" style="width:'+d.ram_pct+'%"></div></div><span class="sbar-val-wide">'+d.ram_used+' / '+d.ram_total+'GB</span></div>';
    if (d.temp) {
      html += '<div class="dd-label" style="margin-top:4px">TEMP: '+d.temp+'°C</div>';
    }
    if (d.gpu_pct !== null && d.gpu_pct !== undefined) {
      html += '<div class="sbar-row"><span class="sbar-label">GPU</span><div class="sbar-track"><div class="sbar-fill gpu" style="width:'+d.gpu_pct+'%"></div></div><span class="sbar-val-wide">'+d.gpu_pct+'%</span></div>';
      html += '<div class="sbar-row"><span class="sbar-label">VRAM</span><div class="sbar-track"><div class="sbar-fill gpu" style="width:'+d.vram_pct+'%"></div></div><span class="sbar-val-wide">'+d.vram_used+' / '+d.vram_total+'GB</span></div>';
      html += '<div class="dd-label" style="margin-top:4px">'+d.gpu_name+'</div>';
    }
    detail.innerHTML = html;
  } catch(e) {
    document.getElementById('cpu-val').textContent = '--';
    document.getElementById('sys-detail').innerHTML = '<span class="net-error">// '+host.label+' offline</span>';
  }
}

// ── Theme
const THEMES = ['dedsec','ghost','breach','clean'];
const THEME_LABELS = { dedsec:'DEDSEC', ghost:'GHOST', breach:'BREACH', clean:'SAKURA' };
let themeIdx = THEMES.indexOf(localStorage.getItem('mentat-theme') || 'dedsec');
if (themeIdx < 0) themeIdx = 0;
function applyTheme(idx) {
  document.documentElement.setAttribute('data-theme', THEMES[idx]);
  document.getElementById('theme-btn').textContent = '[ ' + THEME_LABELS[THEMES[idx]] + ' ]';
  localStorage.setItem('mentat-theme', THEMES[idx]);
}
function cycleTheme() {
  themeIdx = (themeIdx + 1) % THEMES.length;
  applyTheme(themeIdx);
}
applyTheme(themeIdx);
(function() {
  const canvas = document.getElementById('globe-bg');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let rot = 0;

  function resize() {
    const layout = document.querySelector('.layout');
    const w = layout ? layout.clientWidth : 600;
    const h = layout ? layout.clientHeight : 600;
    const size = Math.min(w, h - 100, 520);
    canvas.width = size;
    canvas.height = size;
  }

  // Correct orthographic projection — looking at globe from front
  // lon=0 is center, lat=0 is equator
  function project(lat, lon, r) {
    const latR = lat * Math.PI / 180;
    const lonR = (lon + rot) * Math.PI / 180;
    const x = r * Math.cos(latR) * Math.sin(lonR);
    const y = -r * Math.sin(latR);
    const z = r * Math.cos(latR) * Math.cos(lonR);
    return { x, y, z };
  }

  function drawGrid(cx, cy, r, c1, c2) {
    // latitude lines
    for (let lat = -80; lat <= 80; lat += 20) {
      ctx.beginPath();
      let first = true;
      for (let lon = -180; lon <= 180; lon += 2) {
        const p = project(lat, lon, r);
        if (p.z <= 0) { first = true; continue; }
        const sx = cx + p.x, sy = cy + p.y;
        if (first) { ctx.moveTo(sx, sy); first = false; }
        else ctx.lineTo(sx, sy);
      }
      ctx.strokeStyle = lat === 0 ? c2 : c1;
      ctx.lineWidth = lat === 0 ? 0.8 : 0.35;
      ctx.stroke();
    }
    // longitude lines
    for (let lon = -180; lon < 180; lon += 20) {
      ctx.beginPath();
      let first = true;
      for (let lat = -90; lat <= 90; lat += 2) {
        const p = project(lat, lon, r);
        if (p.z <= 0) { first = true; continue; }
        const sx = cx + p.x, sy = cy + p.y;
        if (first) { ctx.moveTo(sx, sy); first = false; }
        else ctx.lineTo(sx, sy);
      }
      ctx.strokeStyle = c1;
      ctx.lineWidth = 0.35;
      ctx.stroke();
    }
  }

  function drawGeo(cx, cy, r, color) {
    if (!window._geoFeatures) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 0.9;
    window._geoFeatures.forEach(f => {
      if (!f.geometry) return;
      const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
      polys.forEach(poly => {
        poly.forEach(ring => {
          ctx.beginPath();
          let started = false;
          for (let i = 0; i < ring.length; i++) {
            const [lon, lat] = ring[i];
            const p = project(lat, lon, r);
            if (p.z <= 0) { started = false; continue; }
            const sx = cx + p.x, sy = cy + p.y;
            if (!started) { ctx.moveTo(sx, sy); started = true; }
            else ctx.lineTo(sx, sy);
          }
          ctx.stroke();
        });
      });
    });
  }

  function draw() {
    const size = canvas.width;
    if (!size) { requestAnimationFrame(draw); return; }
    const cx = size / 2, cy = size / 2;
    const r = size * 0.44;
    const st = getComputedStyle(document.documentElement);
    const c1 = st.getPropertyValue('--accent').trim() || '#00d8ff';
    const c2 = st.getPropertyValue('--accent2').trim() || '#9d3fff';
    ctx.clearRect(0, 0, size, size);

    drawGrid(cx, cy, r, c1, c2);
    drawGeo(cx, cy, r, c1);

    // outer ring
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = c2;
    ctx.lineWidth = 1;
    ctx.stroke();

    // YOUR_CITY — YOUR_LAT°N, YOUR_LON°E
    const b = project(YOUR_LAT, YOUR_LON, r);
    if (b.z > 0) {
      const bx = cx + b.x, by = cy + b.y;
      const pulse = 0.5 + 0.5 * Math.sin(Date.now() * 0.003);
      // pulse ring — always white
      ctx.beginPath();
      ctx.arc(bx, by, 5 + pulse * 5, 0, Math.PI * 2);
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.2;
      ctx.globalAlpha = 0.7 * (1 - pulse * 0.4);
      ctx.stroke();
      ctx.globalAlpha = 1;
      // white outer ring for contrast
      ctx.beginPath();
      ctx.arc(bx, by, 6, 0, Math.PI * 2);
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.globalAlpha = 0.9;
      ctx.stroke();
      ctx.globalAlpha = 1;
      // colored inner dot
      ctx.beginPath();
      ctx.arc(bx, by, 4, 0, Math.PI * 2);
      ctx.fillStyle = c2;
      ctx.shadowColor = '#ffffff';
      ctx.shadowBlur = 12;
      ctx.fill();
      ctx.shadowBlur = 0;
      // white label
      ctx.font = 'bold 10px monospace';
      ctx.fillStyle = '#ffffff';
      ctx.fillText('YOUR_CITY', bx + 9, by - 5);
      ctx.font = '8px monospace';
      ctx.fillStyle = '#ffffffaa';
      ctx.fillText('YOUR_LAT YOUR_LON', bx + 9, by + 7);
    }

    rot += 0.1;
    requestAnimationFrame(draw);
  }

  // Load 110m GeoJSON (low detail, accurate outlines)
  fetch('https://cdn.jsdelivr.net/gh/johan/world.geo.json@master/countries.geo.json')
    .then(r => r.json())
    .then(g => { window._geoFeatures = g.features; })
    .catch(() => {});

  resize();
  window.addEventListener('resize', resize);
  new ResizeObserver(resize).observe(document.querySelector('.layout'));
  setTimeout(() => { resize(); draw(); }, 400);
})();

document.getElementById('input').addEventListener('keydown', handleKey);
document.getElementById('input').addEventListener('input', function(){ autoResize(this); });
document.getElementById('send-btn').addEventListener('click', sendMessage);
document.getElementById('clear-btn').addEventListener('click', clearChat);
document.getElementById('end-btn').addEventListener('click', endSession);

// ── BREACH ANIMATION
function startBreach() {
  const overlay = document.getElementById('breach-overlay');
  overlay.classList.add('active');
  document.getElementById('breach-granted').style.display = 'none';

  const bars = [
    { fill: 'bf1', status: 'bs1', label: 'BYPASSED' },
    { fill: 'bf2', status: 'bs2', label: 'CRACKED' },
    { fill: 'bf3', status: 'bs3', label: 'BYPASSED' },
    { fill: 'bf4', status: 'bs4', label: 'ACQUIRED' },
  ];

  bars.forEach(b => {
    document.getElementById(b.fill).style.width = '0%';
    document.getElementById(b.status).textContent = b.status === 'bs1' ? 'BYPASSING' : b.status === 'bs2' ? 'CRACKING' : b.status === 'bs4' ? 'ACQUIRING' : 'BYPASSING';
    document.getElementById(b.status).classList.remove('done');
  });

  let i = 0;
  function nextBar() {
    if (i >= bars.length) {
      setTimeout(() => {
        document.getElementById('breach-granted').style.display = 'block';
        setTimeout(() => {
          overlay.classList.remove('active');
          // reinit session as authenticated Toni
          sessionStart = Date.now(); msgCount = 0;
          msgContainer.innerHTML = '';
          initSession(true);
        }, 1200);
      }, 300);
      return;
    }
    const b = bars[i];
    document.getElementById(b.fill).style.width = '100%';
    setTimeout(() => {
      document.getElementById(b.status).textContent = b.label;
      document.getElementById(b.status).classList.add('done');
      i++;
      setTimeout(nextBar, 180);
    }, 700);
  }
  setTimeout(nextBar, 300);
}

document.getElementById('breach-overlay').addEventListener('click', () => {
  document.getElementById('breach-overlay').classList.remove('active');
});

fetchNetwork();
fetchSystem();
setInterval(fetchNetwork, 30000);
setInterval(fetchSystem, 5000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=False)
