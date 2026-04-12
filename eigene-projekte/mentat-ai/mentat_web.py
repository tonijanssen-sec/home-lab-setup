from flask import Flask, request, jsonify, render_template_string
import subprocess
import requests
import os
import threading
from datetime import datetime

app = Flask(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434/api/chat"
SEARXNG_URL   = "http://<NODE_IP>:8888/search"
MODEL         = "llama3.1:8b"
SSH_KEY       = "/home/<USER>/.ssh/mentat_node"
NODE_IP       = "pi@<NODE_IP>"
NODE_CHATS    = "/home/pi/mentat-chats"
NODE_PALACE   = "/home/pi/mentat-palace"
LOCAL_TMP     = "/tmp/mentat_chats"
MEMPALACE_BIN = "/home/pi/.local/bin/mempalace"

sessions = {}
sessions_lock = threading.Lock()

# ── Backend ──────────────────────────────────────────────────────────────────
def ssh(cmd):
    return subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", NODE_IP, cmd],
        capture_output=True, text=True)

def wake_up():
    identity = ssh("cat ~/.mempalace/identity.txt").stdout.strip()
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M:%S (Berlin/CEST)")
    time_note = (
        "\n\nCurrent date and time: " + now +
        "\nIMPORTANT: Every message you receive contains an exact timestamp in [HH:MM:SS] format. "
        "This is the real current time. Always read the time from these message timestamps. "
        "NEVER use [SEARCH:] to find the current time. "
        "NEVER show timestamps in your responses — they are for your internal awareness only."
    )
    return f"{identity}{time_note}"

def refresh_time(system_prompt):
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M:%S (Berlin/CEST)")
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
    text = re.sub(r'\[PALACE:[^\]]*\]', '', text)
    # Timestamps aus Antworten entfernen
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
    ssh(f"{MEMPALACE_BIN} --palace {NODE_PALACE} mine {node_path} --mode convos")

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
    ssh(f"{MEMPALACE_BIN} --palace {NODE_PALACE} mine {node_path} --mode convos")

def ask(messages):
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={
                "model": MODEL,
                "messages": messages,
                "stream": False
            }, timeout=120)
            return r.json()["message"]["content"]
        except Exception:
            if attempt < 2:
                continue
            else:
                return None

def process_reply(reply, messages):
    status = ""
    if "[PALACE:" in reply:
        start = reply.find("[PALACE:") + 8
        end = reply.find("]", start)
        query = reply[start:end].strip()
        status = f"[Palace: {query}]"
        results = search_palace(query)
        messages.append({"role": "assistant", "content": f"[{get_timestamp()}] {reply}"})
        messages.append({"role": "user", "content": f"[Palace memory for '{query}':\n{results}]"})
        reply = ask(messages)
        if reply is None:
            return "Memory retrieval failed.", messages, status
        reply = clean_tags(reply)

    if "[SEARCH:" in reply:
        start = reply.find("[SEARCH:") + 8
        end = reply.find("]", start)
        query = reply[start:end].strip()
        status = f"[Web: {query}]"
        results = search_web(query)
        mine_to_palace(results, query.replace(" ", "_"))
        messages.append({"role": "assistant", "content": f"[{get_timestamp()}] {reply}"})
        messages.append({"role": "user", "content": f"[Search results for '{query}':\n{results}]"})
        reply = ask(messages)
        if reply is None:
            return "Search failed.", messages, status
        reply = clean_tags(reply)

    return reply, messages, status

# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/init", methods=["POST"])
def init_session():
    session_id = request.json.get("session_id")
    system_prompt = wake_up()
    with sessions_lock:
        sessions[session_id] = [{"role": "system", "content": system_prompt}]
    return jsonify({"ok": True})

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    session_id = data.get("session_id")
    user_input = data.get("message", "").strip()

    if not user_input:
        return jsonify({"error": "Empty message"}), 400

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

    # Timestamp intern speichern, aber aus der Antwort entfernen
    reply = clean_tags(reply)
    ts = get_timestamp()
    messages.append({"role": "assistant", "content": f"[{ts}] {reply}"})

    with sessions_lock:
        sessions[session_id] = messages

    return jsonify({"reply": reply, "status": status})

@app.route("/api/end", methods=["POST"])
def end_session():
    session_id = request.json.get("session_id")
    with sessions_lock:
        messages = sessions.pop(session_id, [])
    if messages:
        threading.Thread(target=save_conversation, args=(messages,), daemon=True).start()
    return jsonify({"ok": True})

# ── HTML ─────────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Mentat</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');

  :root {
    --bg: #000a00;
    --surface: #001400;
    --border: #003300;
    --accent: #00ff41;
    --accent-dim: #00aa2a;
    --accent-dark: #004400;
    --text: #00ff41;
    --text-dim: #00aa2a;
    --muted: #005500;
    --user-bg: #001a00;
    --danger: #ff0000;
    --glow: 0 0 10px #00ff41, 0 0 20px #00ff4133;
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
    background-image: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,255,65,0.015) 2px,
      rgba(0,255,65,0.015) 4px
    );
  }

  header {
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--surface);
    flex-shrink: 0;
  }

  .logo {
    font-size: 1.1rem;
    color: var(--accent);
    letter-spacing: 6px;
    text-shadow: var(--glow);
    text-transform: uppercase;
  }

  .header-right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .clock {
    font-size: 0.75rem;
    color: var(--accent-dim);
    letter-spacing: 2px;
  }

  .status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--muted);
    transition: background 0.3s;
  }
  .status-dot.online {
    background: var(--accent);
    box-shadow: 0 0 6px var(--accent), 0 0 12px var(--accent);
  }
  .status-dot.thinking {
    background: var(--accent);
    animation: pulse 0.8s infinite;
  }

  @keyframes pulse { 0%,100% { opacity:1; box-shadow: 0 0 6px var(--accent) } 50% { opacity:0.2; box-shadow: none } }

  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    scroll-behavior: smooth;
  }

  #messages::-webkit-scrollbar { width: 2px; }
  #messages::-webkit-scrollbar-track { background: transparent; }
  #messages::-webkit-scrollbar-thumb { background: var(--border); }

  .msg {
    max-width: 90%;
    padding: 8px 14px;
    border-radius: 3px;
    font-size: 0.82rem;
    line-height: 1.6;
    animation: fadeIn 0.15s ease;
  }

  @keyframes fadeIn { from { opacity:0; transform: translateX(-4px) } to { opacity:1; transform: translateX(0) } }

  .msg.mentat {
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent);
    color: var(--accent);
    align-self: flex-start;
    text-shadow: 0 0 8px rgba(0,255,65,0.4);
  }

  .msg.mentat::before {
    content: '> MENTAT: ';
    color: var(--accent-dim);
    font-size: 0.7rem;
  }

  .msg.user {
    background: var(--user-bg);
    border: 1px solid var(--border);
    border-right: 2px solid var(--accent-dim);
    color: var(--text-dim);
    align-self: flex-end;
    text-align: right;
  }

  .msg.user::before {
    content: 'TONI > ';
    color: var(--muted);
    font-size: 0.7rem;
  }

  .msg.system {
    background: transparent;
    color: var(--muted);
    font-size: 0.7rem;
    align-self: center;
    text-align: center;
    border: none;
    padding: 2px 0;
    letter-spacing: 2px;
  }

  .msg.system::before { content: '// '; }

  .status-tag {
    font-size: 0.65rem;
    color: var(--muted);
    margin-top: 4px;
    letter-spacing: 1px;
  }

  .thinking-indicator {
    display: flex;
    gap: 6px;
    align-items: center;
    padding: 8px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent);
    border-radius: 3px;
    align-self: flex-start;
    width: fit-content;
    color: var(--accent-dim);
    font-size: 0.75rem;
    letter-spacing: 2px;
  }

  .cursor {
    display: inline-block;
    width: 8px;
    height: 14px;
    background: var(--accent);
    animation: blink 0.8s infinite;
    box-shadow: 0 0 6px var(--accent);
  }
  @keyframes blink { 0%,49% { opacity:1 } 50%,100% { opacity:0 } }

  .input-area {
    padding: 10px 16px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    display: flex;
    gap: 10px;
    align-items: flex-end;
    flex-shrink: 0;
  }

  .prompt-prefix {
    color: var(--accent);
    font-size: 0.9rem;
    padding-bottom: 11px;
    flex-shrink: 0;
    text-shadow: 0 0 6px var(--accent);
  }

  textarea {
    flex: 1;
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--border);
    color: var(--accent);
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.85rem;
    padding: 8px 4px;
    resize: none;
    max-height: 100px;
    min-height: 36px;
    outline: none;
    caret-color: var(--accent);
    line-height: 1.4;
  }

  textarea:focus { border-bottom-color: var(--accent); }
  textarea::placeholder { color: var(--muted); }

  button#send {
    background: transparent;
    border: 1px solid var(--accent);
    border-radius: 2px;
    color: var(--accent);
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.75rem;
    letter-spacing: 2px;
    padding: 8px 12px;
    cursor: pointer;
    flex-shrink: 0;
    transition: all 0.2s;
    text-shadow: 0 0 6px var(--accent);
    height: 36px;
  }

  button#send:hover { background: var(--accent-dark); box-shadow: var(--glow); }
  button#send:disabled { opacity: 0.2; cursor: not-allowed; }

  button#end-btn {
    background: transparent;
    border: 1px solid var(--muted);
    border-radius: 2px;
    color: var(--muted);
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.7rem;
    letter-spacing: 2px;
    padding: 8px 10px;
    cursor: pointer;
    flex-shrink: 0;
    transition: all 0.2s;
    height: 36px;
  }

  button#end-btn:hover { border-color: var(--danger); color: var(--danger); }
</style>
</head>
<body>

<header>
  <div class="logo">M E N T A T</div>
  <div class="header-right">
    <div class="clock" id="clock"></div>
    <div class="status-dot" id="dot"></div>
  </div>
</header>

<div id="messages"></div>

<div class="input-area">
  <div class="prompt-prefix">$&gt;</div>
  <textarea id="input" placeholder="enter command..." rows="1" onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
  <button id="end-btn" onclick="endSession()">[ END ]</button>
  <button id="send" onclick="sendMessage()">[ SEND ]</button>
</div>

<script>
const sessionId = 'session_' + Date.now();
const messages = document.getElementById('messages');
const dot = document.getElementById('dot');
const sendBtn = document.getElementById('send');
const input = document.getElementById('input');

function updateClock() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2,'0');
  const m = String(now.getMinutes()).padStart(2,'0');
  const s = String(now.getSeconds()).padStart(2,'0');
  document.getElementById('clock').textContent = h + ':' + m + ':' + s;
}
setInterval(updateClock, 1000);
updateClock();

function addMsg(role, text, status) {
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  el.textContent = text;
  if (status) {
    const s = document.createElement('div');
    s.className = 'status-tag';
    s.textContent = status;
    el.appendChild(s);
  }
  messages.appendChild(el);
  messages.scrollTop = messages.scrollHeight;
  return el;
}

function showThinking() {
  const el = document.createElement('div');
  el.className = 'thinking-indicator';
  el.id = 'thinking';
  el.innerHTML = 'PROCESSING <div class="cursor"></div>';
  messages.appendChild(el);
  messages.scrollTop = messages.scrollHeight;
}

function removeThinking() {
  const el = document.getElementById('thinking');
  if (el) el.remove();
}

function setStatus(state) {
  dot.className = 'status-dot ' + state;
}

async function initSession() {
  setStatus('thinking');
  addMsg('system', 'ESTABLISHING CONNECTION...');
  await fetch('/api/init', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId})
  });
  setStatus('online');
  const init = messages.querySelector('.system');
  if (init) init.textContent = 'CONNECTION ESTABLISHED. MENTAT ONLINE.';
  input.focus();
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  autoResize(input);
  sendBtn.disabled = true;

  addMsg('user', text);
  showThinking();
  setStatus('thinking');

  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, message: text})
    });
    const data = await r.json();
    removeThinking();
    if (data.error) {
      addMsg('system', 'ERROR: ' + data.error);
    } else {
      addMsg('mentat', data.reply, data.status);
    }
  } catch(e) {
    removeThinking();
    addMsg('system', 'CONNECTION ERROR.');
  }

  setStatus('online');
  sendBtn.disabled = false;
  input.focus();
}

async function endSession() {
  setStatus('thinking');
  addMsg('system', 'SAVING SESSION...');
  await fetch('/api/end', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId})
  });
  addMsg('system', 'SESSION SAVED. GOODBYE.');
  setStatus('');
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 100) + 'px';
}

initSession();
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=False)
