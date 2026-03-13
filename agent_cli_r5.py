#!/usr/bin/env python3

import argparse
import difflib
import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from openai import OpenAI

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# -------------------------------------------------
# Load .env from current working dir and script dir
# -------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv()
load_dotenv(SCRIPT_DIR / ".env")

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

READABLE_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".sql",
    ".sh",
    ".bat",
    ".ps1",
    ".xml",
    ".pdf",
}

app = Flask(__name__)
AGENT = None


class Agent:
    def __init__(self, allow_paths):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY was not found.\n"
                "Add it to your .env like:\n"
                "OPENAI_API_KEY=sk-...\n"
            )

        self.client = OpenAI(api_key=api_key)
        self.allow_paths = [Path(p).expanduser().resolve() for p in allow_paths]
        self.context_files = []
        self.pending_changes = []

    def allowed(self, path):
        path = Path(path).expanduser().resolve()
        for root in self.allow_paths:
            try:
                path.relative_to(root)
                return True
            except Exception:
                continue
        return False

    def _ensure_allowed(self, path):
        path = Path(path).expanduser().resolve()
        if not self.allowed(path):
            raise PermissionError(f"Path not allowed: {path}")
        return path

    def _read_pdf(self, path):
        if PdfReader is None:
            return "[pypdf is not installed, cannot read PDF]"
        reader = PdfReader(str(path))
        parts = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                text = f"[Error reading page {i}: {e}]"
            parts.append(f"\n--- PDF Page {i} ---\n{text}")
        return "\n".join(parts).strip()

    def read_file(self, path):
        path = self._ensure_allowed(path)

        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)

        if path.suffix.lower() == ".pdf":
            return self._read_pdf(path)

        return path.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path, content):
        path = self._ensure_allowed(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def add_allowed(self, path):
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(p)
        if p not in self.allow_paths:
            self.allow_paths.append(p)

    def add_context(self, path):
        path = Path(path).expanduser().resolve()

        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise FileNotFoundError(f"Not a file: {path}")
        if not self.allowed(path):
            raise PermissionError(
                f"Path not allowed: {path}. Add its parent directory to allowed paths first."
            )

        s = str(path)
        if s not in self.context_files:
            self.context_files.append(s)

    def add_context_dir(self, directory):
        directory = Path(directory).expanduser().resolve()

        if not directory.exists():
            raise FileNotFoundError(directory)
        if not directory.is_dir():
            raise NotADirectoryError(directory)
        if not self.allowed(directory):
            raise PermissionError(
                f"Path not allowed: {directory}. Add directory to allowed paths first."
            )

        added = 0

        for file in directory.rglob("*"):
            if not file.is_file():
                continue
            if ".git" in file.parts:
                continue
            if file.suffix.lower() in READABLE_EXTENSIONS:
                file_str = str(file.resolve())
                if file_str not in self.context_files:
                    self.context_files.append(file_str)
                    added += 1

        return added

    def build_context(self):
        context_parts = []

        for file in self.context_files:
            try:
                content = self.read_file(file)
                context_parts.append(f"\nFILE: {file}\n{content}\n")
            except Exception as e:
                context_parts.append(f"\nFILE: {file}\n[Error reading file: {e}]\n")

        return "\n".join(context_parts)

    def _extract_text_from_response(self, response):
        try:
            text = getattr(response, "output_text", None)
            if text:
                return text
        except Exception:
            pass

        try:
            out = getattr(response, "output", None)
            if out and isinstance(out, list):
                pieces = []
                for item in out:
                    content = getattr(item, "content", None)
                    if content:
                        for c in content:
                            t = getattr(c, "text", None)
                            if t:
                                pieces.append(t)
                if pieces:
                    return "\n".join(pieces)
        except Exception:
            pass

        return str(response)

    def _find_json_in_text(self, text):
        if not text:
            return None

        try:
            return json.loads(text)
        except Exception:
            pass

        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        pass
        return None

    def ask(self, prompt):
        context = self.build_context()

        full_prompt = f"""
You are a careful local coding assistant.

You are helping on a local machine. You may inspect the repository context below.

Repository and reference files:

{context}

User request:
{prompt}

If code changes are needed, respond ONLY with valid JSON in exactly this format:

{{
  "writes": [
    {{
      "path": "/absolute/or/allowed/path/to/file.py",
      "content": "full replacement file content here"
    }}
  ]
}}

Rules:
- Use full replacement file contents, not patches.
- Only include files that should be written.
- If no file changes are needed, answer normally in plain text.
"""

        try:
            response = self.client.responses.create(
                model=MODEL,
                input=full_prompt,
            )
        except TypeError:
            response = self.client.responses.create(
                model=MODEL,
                input=[{"role": "user", "content": full_prompt}],
            )
        except Exception as e:
            err_text = f"[Error calling OpenAI: {e}]"
            print(err_text)
            return err_text

        text = self._extract_text_from_response(response)
        parsed = self._find_json_in_text(text)

        if parsed and isinstance(parsed, dict):
            writes = parsed.get("writes", [])
            if isinstance(writes, list):
                validated = []
                for change in writes:
                    if not isinstance(change, dict):
                        continue
                    path = change.get("path")
                    content = change.get("content")
                    if isinstance(path, str) and isinstance(content, str):
                        validated.append({"path": path, "content": content})
                self.pending_changes = validated

        return text

    def show_diff_text(self):
        if not self.pending_changes:
            return "No pending changes."

        output_parts = []

        for change in self.pending_changes:
            path = change["path"]
            new_content = change["content"]

            resolved_path = Path(path).expanduser().resolve()
            old_content = ""

            if resolved_path.exists():
                try:
                    old_content = resolved_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except Exception:
                    old_content = ""

            diff = difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile=f"{path} (old)",
                tofile=f"{path} (new)",
                lineterm="",
            )

            output_parts.append("\n".join(diff))

        return "\n\n".join(output_parts)

    def apply(self):
        if not self.pending_changes:
            return "No pending changes to apply."

        written = []
        for change in self.pending_changes:
            path = change["path"]
            content = change["content"]
            self.write_file(path, content)
            written.append(str(Path(path).expanduser().resolve()))

        self.pending_changes = []
        return f"Wrote {len(written)} file(s):\n" + "\n".join(written)

    def show_files_list(self):
        return list(self.context_files)

    def show_allowed(self):
        return [str(p) for p in self.allow_paths]


# -------------------------------------------------
# API routes
# -------------------------------------------------


@app.route("/api/list_dir")
def list_dir():
    path = request.args.get("path", ".")
    try:
        p = Path(path).expanduser().resolve()
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if not p.exists() or not p.is_dir():
        return jsonify({"error": "Path not found or not directory"}), 404

    items = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        items.append(
            {
                "name": child.name,
                "path": str(child),
                "is_dir": child.is_dir(),
            }
        )

    return jsonify({"path": str(p), "items": items})


@app.route("/api/add_allowed", methods=["POST"])
def api_add_allowed():
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"error": "path required"}), 400
    try:
        AGENT.add_allowed(path)
        return jsonify({"ok": True, "allowed": AGENT.show_allowed()})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/allowed")
def api_allowed():
    return jsonify({"allowed": AGENT.show_allowed()})


@app.route("/api/add_context", methods=["POST"])
def api_add_context():
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"error": "path required"}), 400
    try:
        AGENT.add_context(path)
        return jsonify({"ok": True, "context_files": AGENT.show_files_list()})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/add_context_dir", methods=["POST"])
def api_add_context_dir():
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"error": "path required"}), 400
    try:
        added = AGENT.add_context_dir(path)
        return jsonify(
            {"ok": True, "added": added, "context_files": AGENT.show_files_list()}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/context_files")
def api_context_files():
    return jsonify({"context_files": AGENT.show_files_list()})


@app.route("/api/ask", methods=["POST"])
def api_ask():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    try:
        text = AGENT.ask(prompt)
        return jsonify({"response": text, "pending": AGENT.pending_changes})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pending")
def api_pending():
    return jsonify({"pending": AGENT.pending_changes})


@app.route("/api/diff")
def api_diff():
    text = AGENT.show_diff_text()
    return Response(text, mimetype="text/plain")


@app.route("/api/apply", methods=["POST"])
def api_apply():
    data = request.get_json(silent=True) or {}
    confirm = data.get("confirm", True)
    if not confirm:
        return jsonify({"ok": False, "message": "Not confirmed"}), 400
    try:
        result = AGENT.apply()
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/cli", methods=["POST"])
def api_cli():
    data = request.get_json(silent=True) or {}
    cmd = (data.get("cmd") or "").strip()

    if not cmd:
        return jsonify({"error": "cmd required"}), 400

    try:
        if cmd == "/quit":
            return jsonify({"ok": True, "output": "(server continues running)"})

        if cmd == "/help":
            out = (
                "/help\n"
                "/add <file>\n"
                "/add-dir <dir>\n"
                "/files\n"
                "/allowed\n"
                "/diff\n"
                "/apply\n"
                "/quit\n"
            )
            return jsonify({"ok": True, "output": out})

        if cmd.startswith("/add "):
            path = cmd.split(" ", 1)[1]
            AGENT.add_context(path)
            return jsonify({"ok": True, "output": f"Added {path}"})

        if cmd.startswith("/add-dir "):
            path = cmd.split(" ", 1)[1]
            count = AGENT.add_context_dir(path)
            return jsonify({"ok": True, "output": f"Directory added: {count} file(s)"})

        if cmd == "/files":
            files = AGENT.show_files_list()
            return jsonify({"ok": True, "output": "\n".join(files) or "No context files."})

        if cmd == "/allowed":
            allowed = AGENT.show_allowed()
            return jsonify({"ok": True, "output": "\n".join(allowed)})

        if cmd == "/diff":
            return jsonify({"ok": True, "output": AGENT.show_diff_text()})

        if cmd == "/apply":
            return jsonify(
                {
                    "ok": True,
                    "output": "Use the Apply button or POST /api/apply with confirm=true."
                }
            )

        text = AGENT.ask(cmd)
        return jsonify({"ok": True, "output": text})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -------------------------------------------------
# Main UI route
# -------------------------------------------------


@app.route("/")
def index():
    return """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smith AI Access</title>
<style>
    :root{
        --bg:#0b0f0b;
        --panel:#07100a;
        --muted:#9aaea0;
        --accent:#4fff9a;
        --accent-2:#2bd372;
        --text:#e6f6ea;
        --danger:#ff6b6b;
        font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    html, body {
        height: 100%;
        margin: 0;
        background: linear-gradient(180deg, #04100a, #072014);
        color: var(--text);
    }
    header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 16px 24px;
        background: transparent;
    }
    .logo {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .logo svg {
        width: 48px;
        height: 48px;
        filter: drop-shadow(0 4px 12px rgba(0,0,0,0.6));
    }
    .title {
        font-size: 18px;
        font-weight: 700;
    }
    .subtitle {
        font-size: 12px;
        color: var(--muted);
    }
    .container {
        display: grid;
        grid-template-columns: 320px 1fr 360px;
        gap: 14px;
        padding: 18px;
        box-sizing: border-box;
    }
    .panel {
        background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
        border: 1px solid rgba(75,200,140,0.06);
        padding: 12px;
        border-radius: 10px;
        min-height: 60vh;
        box-sizing: border-box;
    }
    .dir-item {
        padding: 6px 8px;
        border-radius: 6px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .dir-item:hover {
        background: rgba(76,255,154,0.04);
    }
    button {
        background: transparent;
        border: 1px solid rgba(76,255,154,0.12);
        color: var(--accent);
        padding: 6px 8px;
        border-radius: 8px;
        cursor: pointer;
    }
    button:hover {
        background: rgba(76,255,154,0.06);
    }
    input, textarea {
        background: transparent;
        border: 1px solid rgba(255,255,255,0.07);
        color: var(--text);
        padding: 8px;
        border-radius: 8px;
        width: 100%;
        box-sizing: border-box;
    }
    .small {
        font-size: 12px;
        color: var(--muted);
    }
    .terminal {
        background: #05110a;
        border-radius: 8px;
        padding: 10px;
        height: 300px;
        overflow: auto;
        font-family: monospace;
        white-space: pre-wrap;
        box-sizing: border-box;
    }
    .btn-green {
        background: linear-gradient(90deg, var(--accent), var(--accent-2));
        color: #04110a;
        border: 0;
    }
    .controls {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
    }
    .file-list {
        max-height: 48vh;
        overflow: auto;
    }
    .context-list {
        max-height: 24vh;
        overflow: auto;
        margin-top: 8px;
    }
    .footer {
        padding: 12px;
        text-align: center;
        color: var(--muted);
    }
    pre {
        margin: 0;
    }
</style>
</head>
<body>
<header>
  <div class="logo">
    <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="g" x1="0" x2="1">
          <stop offset="0" stop-color="#4fff9a"/>
          <stop offset="1" stop-color="#2bd372"/>
        </linearGradient>
      </defs>
      <rect width="100" height="100" rx="18" fill="#09130f"/>
      <g transform="translate(16,16)">
        <circle cx="34" cy="18" r="6" fill="url(#g)" />
        <path d="M2 54 C18 32, 50 32, 66 54" stroke="url(#g)" stroke-width="6" fill="none" stroke-linecap="round"/>
        <rect x="0" y="0" width="68" height="68" rx="10" fill="none" stroke="rgba(255,255,255,0.03)"/>
      </g>
    </svg>
  </div>
  <div>
    <div class="title">Smith AI Access</div>
    <div class="subtitle">Local coding assistant · full UI restored</div>
  </div>
</header>

<div class="container">
  <div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div>
        <strong>File Browser</strong>
        <div class="small">Select files or folders to add to allowed list / context</div>
      </div>
      <div class="controls">
        <button id="btn-root" onclick="goHome()">Open CWD</button>
        <button id="btn-allowed" onclick="refreshAllowed()">Allowed</button>
      </div>
    </div>

    <div id="pathbar" class="small" style="margin-bottom:8px">Path: .</div>
    <div id="filelist" class="file-list"></div>

    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
      <input id="selectedPath" placeholder="Selected path" />
      <button onclick="addAllowed()">Add Allowed</button>
      <button onclick="addContext()">Add Context File</button>
      <button onclick="addContextDir()">Add Context Dir</button>
    </div>

    <div style="margin-top:12px">
      <strong>Context Files</strong>
      <div id="contextList" class="context-list"></div>
    </div>
  </div>

  <div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div>
        <strong>AI Assistant</strong>
        <div class="small">Ask for analysis or code changes. Pending writes can be diffed and applied.</div>
      </div>
      <div class="controls">
        <button onclick="getPending()">Pending</button>
        <button onclick="showDiff()">Diff</button>
        <button class="btn-green" onclick="applyPending()">Apply</button>
      </div>
    </div>

    <textarea id="prompt" rows="10" placeholder="Ask the agent..." style="margin-top:10px"></textarea>

    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="btn-green" onclick="sendPrompt()">Send</button>
      <button onclick="clearPrompt()">Clear</button>
    </div>

    <div style="margin-top:12px">
      <strong>AI Response</strong>
      <pre id="aiResponse" style="background:#04110a;border-radius:8px;padding:12px;min-height:220px;overflow:auto;white-space:pre-wrap;font-family:monospace"></pre>
    </div>
  </div>

  <div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div>
        <strong>Terminal / CLI</strong>
        <div class="small">Use /help, /files, /diff, /allowed, or plain prompts</div>
      </div>
      <div class="small">URL: <span id="serverUrl"></span></div>
    </div>

    <div id="terminal" class="terminal"></div>

    <div style="display:flex;gap:8px;margin-top:8px">
      <input id="cliInput" placeholder="Type /help or a prompt" onkeydown="if(event.key==='Enter'){sendCLI();event.preventDefault();}" />
      <button onclick="sendCLI()">Send</button>
    </div>
  </div>
</div>

<div class="footer">Smith AI Access</div>

<script>
let currentPath = '.';

function terminalOut(s){
    const t = document.getElementById('terminal');
    t.innerText += s + "\\n\\n";
    t.scrollTop = t.scrollHeight;
}

function safeJson(response){
    return response.json().catch(() => ({}));
}

function listDir(path){
    path = path || '.';
    fetch('/api/list_dir?path=' + encodeURIComponent(path))
        .then(r => r.json())
        .then(data => {
            if(data.error){
                terminalOut('Error listing dir: ' + data.error);
                return;
            }

            document.getElementById('pathbar').innerText = 'Path: ' + data.path;
            currentPath = data.path;

            const fl = document.getElementById('filelist');
            fl.innerHTML = '';

            data.items.forEach(it => {
                const div = document.createElement('div');
                div.className = 'dir-item';

                const left = document.createElement('div');
                left.innerText = (it.is_dir ? '📁 ' : '📄 ') + it.name;
                left.style.cursor = 'pointer';
                left.onclick = () => {
                    document.getElementById('selectedPath').value = it.path;
                    if(it.is_dir){ listDir(it.path); }
                };

                const right = document.createElement('div');
                const btn = document.createElement('button');
                btn.textContent = 'Select';
                btn.onclick = () => {
                    document.getElementById('selectedPath').value = it.path;
                };
                right.appendChild(btn);

                div.appendChild(left);
                div.appendChild(right);
                fl.appendChild(div);
            });
        })
        .catch(err => terminalOut('Error listing dir: ' + err));
}

function goHome(){
    listDir('.');
}

function addAllowed(){
    const p = document.getElementById('selectedPath').value || currentPath;
    fetch('/api/add_allowed', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({path: p})
    })
    .then(safeJson)
    .then(j => {
        if(j.error) terminalOut('Error: ' + j.error);
        else {
            terminalOut('Allowed paths updated.');
            refreshAllowed();
        }
    })
    .catch(err => terminalOut('Error adding allowed: ' + err));
}

function refreshAllowed(){
    fetch('/api/allowed')
        .then(r => r.json())
        .then(j => {
            terminalOut('Allowed:\\n' + (j.allowed || []).join('\\n'));
        })
        .catch(err => terminalOut('Error fetching allowed: ' + err));
}

function addContext(){
    const p = document.getElementById('selectedPath').value || currentPath;
    fetch('/api/add_context', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({path: p})
    })
    .then(safeJson)
    .then(j => {
        if(j.error) terminalOut('Error: ' + j.error);
        else {
            terminalOut('Context file added.');
            refreshContext();
        }
    })
    .catch(err => terminalOut('Error adding context: ' + err));
}

function addContextDir(){
    const p = document.getElementById('selectedPath').value || currentPath;
    fetch('/api/add_context_dir', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({path: p})
    })
    .then(safeJson)
    .then(j => {
        if(j.error) terminalOut('Error: ' + j.error);
        else {
            terminalOut('Context dir added. Files added: ' + j.added);
            refreshContext();
        }
    })
    .catch(err => terminalOut('Error adding context dir: ' + err));
}

function refreshContext(){
    fetch('/api/context_files')
        .then(r => r.json())
        .then(j => {
            const el = document.getElementById('contextList');
            el.innerHTML = '';
            (j.context_files || []).forEach(f => {
                const d = document.createElement('div');
                d.className = 'small';
                d.innerText = f;
                el.appendChild(d);
            });
        })
        .catch(err => terminalOut('Error refreshing context: ' + err));
}

function sendPrompt(){
    const p = document.getElementById('prompt').value.trim();
    if(!p) return;

    document.getElementById('aiResponse').innerText = '...thinking...';

    fetch('/api/ask', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({prompt: p})
    })
    .then(safeJson)
    .then(j => {
        if(j.error){
            document.getElementById('aiResponse').innerText = 'Error: ' + j.error;
            terminalOut('Error: ' + j.error);
            return;
        }

        document.getElementById('aiResponse').innerText = j.response || '';
        if(j.pending && j.pending.length){
            terminalOut('Pending changes: ' + j.pending.length);
        }
    })
    .catch(err => {
        document.getElementById('aiResponse').innerText = 'Error: ' + err;
        terminalOut('Error sending prompt: ' + err);
    });
}

function getPending(){
    fetch('/api/pending')
        .then(r => r.json())
        .then(j => terminalOut('Pending changes:\\n' + JSON.stringify(j.pending, null, 2)))
        .catch(err => terminalOut('Error getting pending: ' + err));
}

function showDiff(){
    fetch('/api/diff')
        .then(r => r.text())
        .then(t => terminalOut('Diff:\\n' + t))
        .catch(err => terminalOut('Error getting diff: ' + err));
}

function applyPending(){
    if(!confirm('Apply pending changes?')) return;

    fetch('/api/apply', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({confirm: true})
    })
    .then(safeJson)
    .then(j => {
        if(j.error) terminalOut('Error: ' + j.error);
        else terminalOut('Apply result:\\n' + (j.result || JSON.stringify(j)));
    })
    .catch(err => terminalOut('Error applying pending: ' + err));
}

function sendCLI(){
    const cmd = document.getElementById('cliInput').value.trim();
    if(!cmd) return;

    fetch('/api/cli', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({cmd: cmd})
    })
    .then(safeJson)
    .then(j => {
        if(j.error) terminalOut('Error: ' + j.error);
        else terminalOut(j.output || j.result || JSON.stringify(j));
        document.getElementById('cliInput').value = '';
    })
    .catch(err => terminalOut('Error sending CLI: ' + err));
}

function clearPrompt(){
    document.getElementById('prompt').value = '';
    document.getElementById('aiResponse').innerText = '';
}

window.onerror = function(msg, url, lineNo){
    terminalOut('JS Error: ' + msg + ' @ ' + url + ':' + lineNo);
};

document.addEventListener('DOMContentLoaded', function(){
    document.getElementById('serverUrl').innerText = window.location.origin;
    listDir('.');
    refreshContext();
    refreshAllowed();
});
</script>
</body>
</html>
"""


# -------------------------------------------------
# CLI REPL
# -------------------------------------------------


def repl(agent):
    print("\nOpenAI Local Coding Agent")
    print("Type /help for commands\n")

    while True:
        try:
            cmd = input("agent> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue

        if cmd == "/quit":
            break

        if cmd == "/help":
            print(
                "/help\n"
                "/add <file>\n"
                "/add-dir <dir>\n"
                "/files\n"
                "/allowed\n"
                "/diff\n"
                "/apply\n"
                "/quit\n"
            )
        elif cmd.startswith("/add "):
            file = cmd.split(" ", 1)[1]
            try:
                agent.add_context(file)
                print("Added", file)
            except Exception as e:
                print("Error:", e)
        elif cmd.startswith("/add-dir "):
            directory = cmd.split(" ", 1)[1]
            try:
                count = agent.add_context_dir(directory)
                print(f"Directory added: {count} file(s)")
            except Exception as e:
                print("Error:", e)
        elif cmd == "/files":
            files = agent.show_files_list()
            print("\n".join(files) if files else "No context files loaded.")
        elif cmd == "/allowed":
            print("\n".join(agent.show_allowed()))
        elif cmd == "/diff":
            print(agent.show_diff_text())
        elif cmd == "/apply":
            confirm = input("Apply changes? y/n ").strip().lower()
            if confirm == "y":
                try:
                    print(agent.apply())
                except Exception as e:
                    print("Error:", e)
        else:
            try:
                resp = agent.ask(cmd)
                print(resp)
            except Exception as e:
                print("Error:", e)


# -------------------------------------------------
# Server startup
# -------------------------------------------------


def start_server(host, port, open_browser=True):
    browser_host = "127.0.0.1" if host == "0.0.0.0" else host
    browser_url = f"http://{browser_host}:{port}/"

    print(f"Starting web UI at {browser_url} (binding on {host}:{port})")
    print("Loaded .env from current directory and script directory if present.")

    if open_browser:
        try:
            webbrowser.open(browser_url)
        except Exception as e:
            print(f"Could not open browser automatically: {e}")

    app.run(host=host, port=port, threaded=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow", action="append", default=[os.getcwd()])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--cli-only", action="store_true")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set")
        print("Check your .env file.")
        sys.exit(1)

    global AGENT
    AGENT = Agent(args.allow)

    if args.cli_only:
        repl(AGENT)
        return

    try:
        start_server(args.host, args.port, open_browser=not args.no_browser)
    except Exception as e:
        print("Error starting server:", e)
        print("Falling back to CLI mode.")
        repl(AGENT)


if __name__ == "__main__":
    main()
