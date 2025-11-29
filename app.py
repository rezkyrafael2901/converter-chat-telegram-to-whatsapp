# app.py — FINAL (Merge multi-file + centered UI)
# BAGIAN 1/2

from flask import Flask, request, send_file, render_template_string, jsonify
from bs4 import BeautifulSoup
import datetime, io, traceback, pyminizip, tempfile, os, shutil, re, sys, unicodedata

app = Flask(__name__)

# -----------------------
# Utilities (name cleaning, emoji removal, etc.)
# -----------------------
def remove_emoji(text):
    if not text:
        return text
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text)

def remove_zero_width(text):
    if not text:
        return text
    return re.sub(r'[\u200B\u200C\u200D\uFEFF]', '', text)

def sanitize_filename(name):
    if not name:
        return "converted_chat"
    name = name.replace('\x00','')
    name = os.path.basename(name)
    name = re.sub(r'[\\/:\*\?"<>\|]', '_', name)
    name = name.strip()
    return name if name else "converted_chat"

def normalize_name_for_key(name):
    """
    Strong normalization for counting keys:
      - unicode NFKC
      - remove emojis & zero-width/control chars
      - remove bracket contents [], {}
      - split on ' | ' and take left part
      - remove bracketed tags like [Me], (Bot)
      - collapse whitespace and lower
    """
    if not name:
        return "deleted account"
    s = unicodedata.normalize("NFKC", name)
    s = remove_emoji(s)
    s = remove_zero_width(s)
    s = "".join(ch for ch in s if ch.isprintable())

    # remove square/curly bracket contents like [xxx] or {yyy}
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"\{.*?\}", "", s)
    # remove any remaining stray ']' or '}'
    s = s.replace("]", "").replace("}", "")

    # split on pipe ' | '
    if " | " in s:
        s = s.split(" | ")[0]

    # remove parenthesis contents and tags like (Bot)
    s = re.sub(r"\(.*?\)", "", s)

    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return "deleted account"
    return s.lower()

def display_name_cleanup(name):
    """
    Make user-facing display name:
     - remove emoji, zero-width
     - remove bracket contents [], {}
     - remove stray closing brackets
     - cut after " | "
     - collapse whitespace, fallback to "Deleted Account"
    """
    if not name:
        return "Deleted Account"
    s = remove_emoji(name)
    s = remove_zero_width(s)
    s = "".join(ch for ch in s if ch.isprintable())

    # remove [] and {} contents
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"\{.*?\}", "", s)
    s = s.replace("]", "").replace("}", "")

    # cut after pipe
    if " | " in s:
        s = s.split(" | ")[0].strip()

    # remove parentheses content
    s = re.sub(r"\(.*?\)", "", s)

    s = re.sub(r"\s+", " ", s).strip()
    return s if s else "Deleted Account"

def parse_dt_from_title(t):
    if not t:
        return None
    # try common Telegram title format: "dd.mm.YYYY HH:MM:SS UTC..."
    try:
        parts = t.split(" UTC")[0]
        return datetime.datetime.strptime(parts, "%d.%m.%Y %H:%M:%S")
    except Exception:
        # fallback: try alternative like "MM/DD/YY, HH:MM AM/PM" or "03/06/25, 07:12 PM"
        try:
            return datetime.datetime.strptime(t.strip(), "%m/%d/%y, %I:%M %p")
        except Exception:
            return None

def fmt(dt):
    return dt.strftime("%m/%d/%y, %I:%M %p")

# -----------------------
# Spam helpers & patterns (copied from final rules)
# -----------------------
def is_spam_like(text):
    if not text:
        return False
    t = text.strip()
    if re.search(r"(.)\1{4,}", t.lower()):
        return True
    if re.search(r"([!?.,])\1{3,}", t):
        return True
    letters = [c for c in t if c.isalpha()]
    if letters:
        caps = sum(1 for c in letters if c.isupper())
        if caps > len(letters) * 0.6:
            return True
    if len(t) > 350:
        return True
    return False

SINGLE_WORD_SPAM_RE = re.compile(r"^[/\\]?(up|yo|ok|utc|cek|check|ping|pm|bump|push|help|upvote|vote|voteup)[!?.]*$", re.IGNORECASE)
SHORT_WHITELIST = {"hai", "iya"}
PRICE_RE = re.compile(r"\b\d+(\.\d+)?\s*(k|rb)\b", re.IGNORECASE)
IP_COUNT_RE = re.compile(r"\b\d+\s*ip\b", re.IGNORECASE)
DURATION_RE = re.compile(r"\b\d+\s*(hari|day|bulan|month)\b", re.IGNORECASE)
LINK_RE = re.compile(r"(http|https|www\.|\.com|\.net|\.id|\.co)", re.IGNORECASE)

CATALOG_KEYWORDS = [
    "proxy", "ip:", "port", "user:pass", "residential", "static",
    "bandwidth", "masa aktif", "ready", "note:", "package", "bandwith"
]
PROMO_KEYWORDS = [
    "jual", "jualan", "promosi", "promo", "lowongan", "loker",
    "jasa", "sewa", "autoscript install rdp", "vps", "garansi", "1 bulan"
]
PROMO_EMOJI = {"💥","🔥","⚡","💸","⭐","🎁","🎉"}

RDP_KEYWORDS = [
    "detail information", "speed download", "speed upload",
    "linux", "ubuntu", "debian", "centos", "rockylinux", "almalinux",
    "windows server", "cpu", "ram", "bandwidth", "rdp", "server", "vps",
    "speed", "download", "upload", "durasi"
]

SENDER_HARD_BLOCK = ["deleted", "burnfp"]
BOT_NAME_SUBSTRINGS = ["uxuy", "rose", "agent", "bot"]

def msg_has_bot_elements(msg):
    try:
        if msg.select_one("table.bot_buttons_table"):
            return True
        if msg.find("blockquote"):
            return True
        a_onclick = msg.find("a", onclick=True)
        if a_onclick and "ShowBotCommand" in (a_onclick.get("onclick") or ""):
            return True
        if msg.select_one(".bot_inline_keyboard") or msg.select_one(".bot-buttons"):
            return True
        reply = msg.select_one(".reply_to")
        if reply:
            text = reply.get_text(" ", strip=True).lower()
            if any(b in text for b in BOT_NAME_SUBSTRINGS):
                return True
    except Exception:
        pass
    return False

# -----------------------
# Parse one HTML soup into a list of entries (dt, line)
# We return list of tuples (dt, line). If dt missing, we assign fallback dt
# -----------------------
def parse_soup_to_entries(soup, fallback_dt=None):
    """
    Parse messages from a BeautifulSoup object and return list of (dt, line) entries
    dt: datetime object (if cannot parse, fallback_dt or current time)
    """
    entries = []
    seen = set()  # (sender_norm, content) for per-soup dedupe
    user_counter = {}
    last_user_norm = None

    for msg in soup.select(".message.default"):
        date_el = msg.select_one(".date")
        name_el = msg.select_one(".from_name")
        # skip entries missing essential pieces
        if not date_el or name_el is None:
            continue

        # parse dt robustly
        dt = None
        title = date_el.get("title") or date_el.get_text(" ", strip=True) or ""
        dt = parse_dt_from_title(title)
        if dt is None:
            # try parse if date_el text is like '03/06/25, 07:12 PM -'
            try:
                textdt = date_el.get_text(" ", strip=True)
                dt = parse_dt_from_title(textdt)
            except:
                dt = None
        if dt is None:
            dt = fallback_dt or datetime.datetime.now()

        raw_name = name_el.get_text(strip=True) if name_el else ""
        display_name = display_name_cleanup(raw_name)
        name_norm = normalize_name_for_key(raw_name)
        if not name_norm or name_norm.strip() == "":
            name_norm = "deleted account"
            display_name = "Deleted Account"
        name_norm_lower = name_norm.lower()

        # hard block senders
        if any(block in name_norm_lower for block in SENDER_HARD_BLOCK):
            continue

        # bot name heuristic
        if any(b in name_norm_lower for b in BOT_NAME_SUBSTRINGS):
            continue

# End of BAGIAN 1/2
# BAGIAN 2/2 — lanjutan parse + merge + frontend + routes + run

        # bot elements inside message
        if msg_has_bot_elements(msg):
            continue

        # update consecutive counter
        if last_user_norm == name_norm:
            user_counter[name_norm] = user_counter.get(name_norm, 1) + 1
        else:
            user_counter[name_norm] = 1
            last_user_norm = name_norm
        count_now = user_counter.get(name_norm, 1)
        if count_now > 2:
            continue  # strict rule: delete beyond 2 consecutive

        # media
        media = msg.select_one(".media_wrap")
        if media:
            line = f"{fmt(dt)} - {display_name}: <Media omitted>"
            entries.append((dt, line))
            continue

        text_el = msg.select_one(".text")
        if not text_el:
            continue

        raw_text = text_el.get_text("\n", strip=True)
        for part in raw_text.split("\n"):
            content = part.strip()
            if not content:
                continue
            lc = content.lower().strip()

            # bot phrases
            bot_phrases = ["click below", "see details", "join now", "congrat", "congrats", "already checked", "you have won", "check in", "daily reward", "bonus claim"]
            if any(p in lc for p in bot_phrases):
                continue

            # single word spam
            if SINGLE_WORD_SPAM_RE.fullmatch(lc):
                continue

            # short messages
            if len(lc) <= 3 and lc not in SHORT_WHITELIST:
                continue

            # catalog/price/ip/duration
            if re.match(r"^\s*[-•*]\s+", content):
                continue
            if PRICE_RE.search(lc):
                continue
            if IP_COUNT_RE.search(lc):
                continue
            if any(k in lc for k in CATALOG_KEYWORDS):
                continue
            if DURATION_RE.search(lc):
                continue

            # rdp/vps keywords
            if any(k in lc for k in RDP_KEYWORDS):
                continue

            # links/promos
            if LINK_RE.search(content):
                continue
            if any(k in lc for k in PROMO_KEYWORDS):
                continue
            if any(e in content for e in PROMO_EMOJI):
                continue

            # spam pattern
            if is_spam_like(content):
                continue

            # dedupe per sender & content
            key = (name_norm, content)
            if key in seen:
                continue
            seen.add(key)

            line = f"{fmt(dt)} - {display_name}: {content}"
            entries.append((dt, line))

    return entries

# -----------------------
# Merge multiple files: given list of file-like objects, produce merged text
# -----------------------
def process_and_merge_files(filelist):
    all_entries = []
    earliest = None

    for f in filelist:
        try:
            content = f.read()
            if isinstance(content, bytes):
                soup = BeautifulSoup(content, "html.parser")
            else:
                soup = BeautifulSoup(str(content), "html.parser")
        except Exception:
            continue

        fallback_dt = datetime.datetime.now()
        entries = parse_soup_to_entries(soup, fallback_dt=fallback_dt)
        for dt, line in entries:
            all_entries.append((dt, line))
            if earliest is None or dt < earliest:
                earliest = dt

    # sort entries by datetime
    all_entries.sort(key=lambda x: x[0])
    merged_lines = [line for dt, line in all_entries]
    merged_text = "\n".join(merged_lines)
    return merged_text, earliest

# -----------------------
# FRONTEND HTML (centered UI) — embedded here as INDEX_HTML
# -----------------------
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Telegram → WhatsApp Converter (Merge Multi-HTML)</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  :root{--bg:#ffffff;--card:#fff;--text:#111;--muted:#666;--accent:#007bff}
  [data-theme="dark"]{--bg:#0f1720;--card:#0b1220;--text:#e6eef8;--muted:#9fb0c9;--accent:#3ea1ff}
  body{
    background:var(--bg);
    color:var(--text);
    font-family:Arial, sans-serif;
    margin:0;
    padding:0;
    min-height:100vh;
    display:flex;
    justify-content:center;
    align-items:center;
  }
  .wrapper{
    width:100%;
    max-width:680px;
    padding:20px;
    box-sizing:border-box;
  }
  .header{text-align:center;margin-bottom:18px}
  .card{
    background:var(--card);
    padding:22px;
    border-radius:14px;
    box-shadow:0 6px 18px rgba(2,6,23,0.12);
  }
  .row{margin:14px 0}
  label.small{font-size:13px;color:var(--muted)}
  input[type=text]{padding:10px 12px;width:100%;border-radius:8px;border:1px solid rgba(0,0,0,0.06);box-sizing:border-box}
  input[type=file]{display:none}
  .btn{padding:10px 14px;border-radius:8px;border:none;background:var(--accent);color:white;cursor:pointer;margin-right:8px}
  #loader{display:none;margin-left:6px}
  #status{white-space:pre-wrap;margin-top:12px;color:var(--muted);text-align:center}
  .dropzone{border:2px dashed rgba(0,0,0,0.10);border-radius:10px;padding:24px;text-align:center;color:var(--muted)}
  .dropzone.dragover{background:rgba(0,0,0,0.05);border-color:var(--accent);color:var(--text)}
  .theme-toggle{background:transparent;border:1px solid rgba(0,0,0,0.20);padding:6px 10px;border-radius:8px;cursor:pointer;margin-top:10px}
  .note{font-size:13px;color:var(--muted);margin-top:8px;text-align:center}
</style>
</head>
<body data-theme="light">
<div class="wrapper">
  <div class="header">
    <h2>Telegram HTML → WhatsApp TXT (Merge Multiple HTMLs)</h2>
    <button id="themeBtn" class="theme-toggle">Dark Mode</button>
  </div>

  <div class="card">
    <div class="row">
      <div id="drop" class="dropzone">
        <div id="dropText">Drop Telegram HTML files here (multiple allowed), or <label for="fileInput" style="color:var(--accent);cursor:pointer">browse</label></div>
        <input id="fileInput" type="file" accept=".html" multiple />
      </div>
      <div class="note">You can drop/upload 2 or more HTML export files. They will be merged by timestamp.</div>
    </div>

    <div class="row">
      <label class="small">Nama file hasil (.txt):</label><br/>
      <input type="text" id="filename" value="Chat Whatsapp dengan " />
    </div>

    <div class="row">
      <button id="btn" class="btn">Convert & Download TXT (Merge)</button>
      <button id="btnZip" class="btn">Convert & Encrypt ZIP (Merge)</button>
      <span id="loader">⏳ Processing...</span>
    </div>

    <div class="row">
      <label class="small">ZIP Password (opsional):</label><br/>
      <input type="text" id="zipPass" placeholder="password untuk ZIP" />
    </div>

    <div id="status"></div>
  </div>
</div>

<script>
(function(){
  const body=document.body,themeBtn=document.getElementById('themeBtn');
  const drop=document.getElementById('drop'),dropText=document.getElementById('dropText');
  const fileInput=document.getElementById('fileInput');
  const btn=document.getElementById('btn'),btnZip=document.getElementById('btnZip');
  const loader=document.getElementById('loader'),status=document.getElementById('status');
  const filenameInput=document.getElementById('filename');
  let currentFiles = [];

  function setTheme(t){
    body.setAttribute("data-theme",t);
    themeBtn.textContent=t==="dark"?"Light Mode":"Dark Mode";
    localStorage.setItem("theme",t);
  }
  setTheme(localStorage.getItem("theme")||"light");
  themeBtn.onclick=()=>setTheme(body.getAttribute("data-theme")==="light"?"dark":"light");

  function setStatus(msg){ status.textContent=msg; }

  drop.ondragover=e=>{ e.preventDefault(); drop.classList.add("dragover"); };
  drop.ondragleave=()=>drop.classList.remove("dragover");
  drop.ondrop=e=>{
    e.preventDefault(); drop.classList.remove("dragover");
    currentFiles = Array.from(e.dataTransfer.files);
    dropText.textContent = currentFiles.length + " file(s) ready: " + currentFiles.map(f=>f.name).join(", ");
  };
  fileInput.onchange=()=>{
    currentFiles = Array.from(fileInput.files);
    dropText.textContent = currentFiles.length + " file(s) ready: " + currentFiles.map(f=>f.name).join(", ");
  };

  async function sendRequest(url, formData, dl){
    loader.style.display="inline";
    btn.disabled = btnZip.disabled = true;
    try{
      let r = await fetch(url, { method: "POST", body: formData });
      if(!r.ok){
        let txt = "";
        try { txt = await r.text(); } catch(e){ txt = "unknown error"; }
        setStatus("Error: " + txt);
        return;
      }
      let blob = await r.blob();
      let a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = dl;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(a.href);
      setStatus("Berhasil: " + dl);
    }catch(err){
      setStatus("Exception: " + err.toString());
    } finally {
      loader.style.display="none";
      btn.disabled = btnZip.disabled = false;
    }
  }

  btn.onclick=()=>{
    if(!currentFiles || currentFiles.length===0){ setStatus("Pilih/drop minimal 1 file HTML terlebih dahulu."); return; }
    let base = filenameInput.value.trim();
    let txt = base ? base + ".txt" : "converted_whatsapp.txt";
    let fd = new FormData();
    for(let i=0;i<currentFiles.length;i++){
      fd.append("file", currentFiles[i], currentFiles[i].name);
    }
    fd.append("filename", txt);
    sendRequest("/convert", fd, txt);
  };

  btnZip.onclick=()=>{
    if(!currentFiles || currentFiles.length===0){ setStatus("Pilih/drop minimal 1 file HTML terlebih dahulu."); return; }
    let base = filenameInput.value.trim();
    let txt = base ? base + ".txt" : "converted_whatsapp.txt";
    let zip = base ? base + ".zip" : "converted_chat.zip";
    let fd = new FormData();
    for(let i=0;i<currentFiles.length;i++){
      fd.append("file", currentFiles[i], currentFiles[i].name);
    }
    fd.append("filename", txt);
    fd.append("password", document.getElementById('zipPass').value || "");
    sendRequest("/convert_zip", fd, zip);
  };
})();
</script>
</body>
</html>
"""

# -----------------------
# Routes (multi-file handling)
# -----------------------
@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_HTML)


@app.route("/convert", methods=["POST"])
def convert_txt():
    try:
        files = request.files.getlist("file")
        if not files or len(files) == 0:
            return "file not found", 400

        requested_name = sanitize_filename(request.form.get("filename", "converted_whatsapp.txt"))
        if not requested_name.lower().endswith(".txt"):
            requested_name += ".txt"

        merged_text, earliest = process_and_merge_files(files)

        buf = io.BytesIO(merged_text.encode("utf-8"))
        buf.seek(0)
        return send_file(buf, mimetype="text/plain; charset=utf-8", as_attachment=True, download_name=requested_name)

    except Exception as e:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        return jsonify({"error": str(e), "trace": tb}), 500


@app.route("/convert_zip", methods=["POST"])
def convert_zip():
    try:
        files = request.files.getlist("file")
        if not files or len(files) == 0:
            return "file not found", 400

        password = request.form.get("password", "")
        requested_name = sanitize_filename(request.form.get("filename", "converted_whatsapp.txt"))
        if not requested_name.lower().endswith(".txt"):
            requested_name += ".txt"

        zip_basename = requested_name[:-4] if requested_name.lower().endswith(".txt") else requested_name

        tmpdir = tempfile.mkdtemp()
        try:
            txt_path = os.path.join(tmpdir, requested_name)
            merged_text, earliest = process_and_merge_files(files)
            with open(txt_path, "w", encoding="utf-8") as wf:
                wf.write(merged_text)
            zip_path = os.path.join(tmpdir, zip_basename + ".zip")
            pyminizip.compress(txt_path, None, zip_path, password, 5)
            with open(zip_path, "rb") as zf:
                data = zf.read()
            bio = io.BytesIO(data)
            bio.seek(0)
            shutil.rmtree(tmpdir)
            return send_file(bio, mimetype="application/zip", as_attachment=True, download_name=zip_basename + ".zip")
        except Exception:
            try:
                shutil.rmtree(tmpdir)
            except:
                pass
            raise

    except Exception as e:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        return jsonify({"error": str(e), "trace": tb}), 500


# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
