#!/usr/bin/env python3
"""
CBACK — CCAST in reverse: students send their computer's audio to the teacher's desk.

Zero dependencies: Python 3 standard library only.
  Teacher:   http://localhost:8090/desk        (opens in Chrome automatically)
  Students:  https://<this-mac's-ip>:8091      (shown big on the desk, with a QR)

A student's DAW plays into BlackHole; their browser captures BlackHole (ch 1-2,
processing off) and sends it straight to the teacher's browser over WebRTC
(stereo Opus 320 kb/s). No audio passes through Python. Audio only flows while
the teacher is listening to that student; otherwise just a level meter does.

Students need https: browsers only allow audio capture on secure pages. The
certificate is CCAST's own when it exists, so devices that already accepted it
for CCAST's Ultra link are not asked again.

Mailbox semantics are CCAST's: per-recipient sequence numbers, polls acknowledge,
and a "generation" that changes on restart so pages re-join by themselves.
"""
import json, os, socket, ssl, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON = "--daemon" in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT_WANTED = int(_args[0]) if _args else 8090
POLL_WAIT = 20
STALE = 60
MAX_QUEUE = 300
GEN = int(time.time())
LOCAL = ("127.0.0.1", "::1")

# ----------------------------------------------------------------- mailbox
_lock = threading.Condition()
_boxes = {}; _seq = {}; _seen = {}
_names = {}             # student id -> name (for the log)
_desk_hid = None        # the one desk tab allowed to act as the desk (the newest)


def _touch(cid):
    _boxes.setdefault(cid, []); _seq.setdefault(cid, 0)
    _seen[cid] = time.time()


def deliver(sender, to, data):
    with _lock:
        if to == "*":
            targets = [c for c in _boxes if c != sender]
        else:
            _touch(to); targets = [to]
        for t in targets:
            _seq[t] += 1
            _boxes[t].append((_seq[t], {"seq": _seq[t], "from": sender, "data": data}))
            del _boxes[t][:-MAX_QUEUE]
        _lock.notify_all()


def take(cid, after, wait):
    deadline = time.time() + wait
    with _lock:
        _touch(cid)
        _boxes[cid] = [(n, m) for n, m in _boxes[cid] if n > after]
        while not _boxes[cid]:
            remaining = deadline - time.time()
            if remaining <= 0:
                return []
            _lock.wait(remaining)
        return [m for _, m in _boxes[cid]]


def reset(cid):
    with _lock:
        _boxes[cid] = []; _seq[cid] = 0; _seen[cid] = time.time()


def prune():
    while True:
        time.sleep(10)
        with _lock:
            now = time.time()
            for cid in [c for c, t in _seen.items() if c != "desk" and now - t > STALE]:
                _boxes.pop(cid, None); _seq.pop(cid, None); _seen.pop(cid, None)
                if cid in _names:
                    log(f"{_names.pop(cid)} left")
                    deliver(cid, "desk", {"type": "bye"})


def log(msg):
    print(time.strftime("  %H:%M:%S  ") + msg, flush=True)


def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1)); ips.append(s.getsockname()[0]); s.close()
    except Exception:
        pass
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet ") and "127.0.0.1" not in line:
                ip = line.split()[1]
                if ip not in ips:
                    ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


# ----------------------------------------------------------------- pages
CSS = r"""
:root{
  --bg:#07080b;--card:#12151c;--line:#1f242e;--fg:#f4f5f7;--dim:#8b93a3;--mute:#5a6170;
  --live:#39ff88;--wait:#ffb340;--off:#ff4d5e;--air:#ff2d55;--ink:#062a15;--radius:22px;--shadow:0 20px 60px rgba(0,0,0,.45);
}
*{box-sizing:border-box}html{color-scheme:dark}
body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#141a26 0%,var(--bg) 60%);color:var(--fg);
 font:17px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,Segoe UI,Roboto,sans-serif;min-height:100vh;-webkit-font-smoothing:antialiased}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:6px 0 18px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#1fd1ff,var(--live));display:grid;place-items:center;color:#031;flex:none}
.logo svg{width:20px;height:20px}
.stname{font-weight:900;letter-spacing:.16em;font-size:18px}.sttag{color:var(--dim);font-size:13px;letter-spacing:.06em}
.pill{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;background:var(--card);border:1px solid var(--line);color:var(--dim);font-weight:600;font-size:14px;white-space:nowrap}
.pill b{color:var(--fg);font-size:18px;font-variant-numeric:tabular-nums}
.dot{width:10px;height:10px;border-radius:50%;background:var(--mute);flex:none}
.dot.live{background:var(--live);animation:pulse 1.6s infinite}.dot.air{background:var(--air);animation:pulse-air 1.6s infinite}
@keyframes pulse-air{0%{box-shadow:0 0 0 0 rgba(255,45,85,.6)}100%{box-shadow:0 0 0 12px rgba(255,45,85,0)}}.dot.wait{background:var(--wait)}.dot.off{background:var(--off)}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(57,255,136,.55)}100%{box-shadow:0 0 0 12px rgba(57,255,136,0)}}
.card{background:linear-gradient(180deg,#141821,#0f1218);border:1px solid var(--line);border-radius:var(--radius);padding:24px;box-shadow:var(--shadow)}
label{display:block;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);font-weight:700;margin:18px 0 8px}
button{font:inherit;font-weight:700;border:0;border-radius:14px;padding:14px 20px;cursor:pointer;background:#222736;color:var(--fg);transition:transform .08s,filter .15s}
button:hover{filter:brightness(1.12)}button:active{transform:translateY(1px)}button:disabled{opacity:.35;cursor:default;filter:none}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--dim)}
select,input[type=text]{font:inherit;width:100%;padding:13px 14px;border-radius:14px;background:#0b0d12;color:var(--fg);border:1px solid var(--line)}
select{appearance:none}
input[type=range]{accent-color:var(--live)}
.qr{background:#fff;padding:12px;border-radius:16px;line-height:0;flex:none}
.qr img,.qr canvas{display:block;width:170px;height:170px}
.err{display:none;margin-top:14px;padding:12px 14px;border-radius:12px;background:rgba(255,77,94,.1);border:1px solid rgba(255,77,94,.35);color:#ffb3ba;font-size:14px}
.hint{color:var(--dim);font-size:14px}.hint b{color:var(--fg)}
.onair{padding:10px 18px;border-radius:12px;border:2px solid #3a1a1f;background:#1a0c0f;color:#6b2a33;font-weight:900;letter-spacing:.22em;font-size:13px;transition:.25s;white-space:nowrap}
.onair.on{border-color:var(--air);background:var(--air);color:#fff;box-shadow:0 0 24px rgba(255,45,85,.7),0 0 60px rgba(255,45,85,.35);animation:glow 2.4s ease-in-out infinite}
@keyframes glow{50%{box-shadow:0 0 12px rgba(255,45,85,.5),0 0 30px rgba(255,45,85,.2)}}
/* meters */
.m{height:10px;border-radius:5px;background:#0a0c10;border:1px solid var(--line);position:relative;overflow:hidden}
.m i{position:absolute;inset:0;width:0;background:linear-gradient(90deg,#1fd1ff 0%,var(--live) 55%,#e9ff5c 82%,var(--off) 96%)}
.m b{position:absolute;top:0;bottom:0;width:2px;background:#fff;left:0;opacity:.85}
details{margin-top:20px;border-top:1px solid var(--line);padding-top:14px;color:var(--dim);font-size:14px}
details summary{cursor:pointer;font-weight:600;list-style:none}details summary::before{content:'▸ '}details[open] summary::before{content:'▾ '}
details ol{padding-left:20px}details li{margin:6px 0}details em{font-style:normal;color:var(--fg);font-weight:600}
"""

JS_COMMON = r"""
const $=s=>document.querySelector(s);const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const rnd=()=>Math.random().toString(36).slice(2,10);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
class Signal{
  constructor(id,extra){this.id=id;this.extra=extra||'';this.q=Promise.resolve();this.after=0;this.gen=null;this.online=null;this.dead=false;
    this.onmsg=async()=>{};this.onreset=()=>{};this.onoffline=()=>{};this.onstale=()=>{};}
  send(to,data){this.q=this.q.then(()=>this._post({from:this.id,to,data})).catch(()=>{});return this.q;}
  async _post(body){for(let i=0;i<4;i++){try{const r=await fetch('/api/msg',{method:'POST',body:JSON.stringify(body)});if(r.ok)return;}catch(e){}await sleep(250*(i+1));}}
  async run(){for(;;){if(this.dead)return;try{
    const ctl=new AbortController();const t=setTimeout(()=>ctl.abort(),40000);
    const r=await fetch(`/api/poll?id=${this.id}&after=${this.after}${this.extra}`,{signal:ctl.signal,cache:'no-store'});clearTimeout(t);
    if(!r.ok)throw new Error(r.status);const {gen,msgs,stale}=await r.json();
    if(stale){this.dead=true;this.onstale();return;}
    const restarted=this.gen!==null&&gen!==this.gen;const backOnline=this.online===false;
    this.gen=gen;this.online=true;if(restarted)this.after=0;
    if(restarted||backOnline)this.onreset();
    for(const m of msgs){this.after=Math.max(this.after,m.seq);try{await this.onmsg(m.from,m.data);}catch(e){console.warn('msg',e);}}
  }catch(e){if(this.online!==false){this.online=false;this.onoffline();}await sleep(1500);}}}
}
// Opus tuned for music (CCAST's recipe): full-band stereo, 320 kb/s, no DTX, in-band FEC.
const OPUS='stereo=1;sprop-stereo=1;maxaveragebitrate=320000;maxplaybackrate=48000;sprop-maxcapturerate=48000;cbr=0;usedtx=0;useinbandfec=1';
function stereo(sdp){const m=sdp.match(/a=rtpmap:(\d+) opus\/48000/i);if(!m)return sdp;const pt=m[1];
  const re=new RegExp('^a=fmtp:'+pt+' (.*)$','gm');
  if(re.test(sdp))return sdp.replace(re,(l,params)=>'a=fmtp:'+pt+' '+params.split(';').filter(kv=>!/^(stereo|sprop-stereo|maxaveragebitrate|maxplaybackrate|sprop-maxcapturerate|cbr|usedtx|useinbandfec)=/.test(kv.trim())).concat(OPUS.split(';')).join(';'));
  return sdp.replace(new RegExp('(a=rtpmap:'+pt+' opus\\/48000[^\\r\\n]*\\r?\\n)'),'$1a=fmtp:'+pt+' '+OPUS+'\r\n');}
// LAN only, no STUN: wait for host candidates instead of trickling them (a few ms).
const gathered=pc=>new Promise(r=>{if(pc.iceGatheringState==='complete')return r();
  const f=()=>{if(pc.iceGatheringState==='complete'){pc.removeEventListener('icegatheringstatechange',f);r();}};pc.addEventListener('icegatheringstatechange',f);setTimeout(r,2500);});
const dbw=db=>Math.max(0,Math.min(100,(db+60)/60*100));   // -60..0 dBFS -> 0..100 %
"""

ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M4 12h1M8 8v8M12 4v16M16 8v8M20 12h1"/></svg>'

# ----------------------------------------------------------------- desk (teacher)
DESK_CSS = r"""
.wrap{max-width:1240px;margin:0 auto;padding:22px 20px 60px}
.tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.tools select{width:auto;max-width:240px;padding:9px 12px;font-size:14px}
.vol{display:flex;align-items:center;gap:8px;color:var(--dim);font-size:13px;font-weight:700;letter-spacing:.1em}.vol input{width:110px}
.share{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:center;margin-bottom:22px}
.share h2{margin:0 0 4px;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
.url{font-size:40px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin:8px 0 12px;word-break:break-all}.url b{color:var(--live)}
body.compact .share{grid-template-columns:minmax(0,1fr) auto;padding:14px 20px}body.compact .url{font-size:22px;margin:2px 0}
body.compact .qr img,body.compact .qr canvas{width:84px;height:84px}body.compact .share .hint{display:none}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.tile{position:relative;padding:16px 16px 14px;border-radius:18px;background:#141821;border:1px solid var(--line);cursor:pointer;user-select:none;transition:border-color .15s,background .15s}
.tile:hover{border-color:#39415a}
.tile .top{display:flex;align-items:center;gap:10px}.tile .num{font-size:12px;color:var(--mute);font-weight:800;width:16px}
.tile .nm{flex:1;font-weight:800;font-size:19px;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .hand{display:none;font-size:20px;animation:wave 1.2s ease-in-out infinite}
@keyframes wave{50%{transform:rotate(14deg)}}
.tile.hand .hand{display:block}.tile.hand{border-color:var(--wait);box-shadow:inset 0 0 0 1px var(--wait)}
.tile .dev{color:var(--mute);font-size:12px;margin:4px 0 12px 26px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .ms{display:grid;gap:5px;margin-left:26px}
.tile .st{margin:12px 0 0 26px;font-size:13px;color:var(--dim);font-weight:600;display:flex;align-items:center;gap:8px}
.tile.on{background:#1d1015;border-color:var(--air);box-shadow:0 0 0 2px var(--air),0 0 30px rgba(255,45,85,.25)}
.tile.on .st{color:#ff8fa5}
.tile.gone{opacity:.4}.tile.muted .ms{opacity:.25}.tile.muted .nm{color:var(--dim)}
.tile .clip{position:absolute;right:14px;bottom:14px;font-size:10px;font-weight:800;letter-spacing:.12em;color:var(--mute)}.tile .clip.on{color:var(--off)}
.empty{grid-column:1/-1;text-align:center;padding:60px 20px;color:var(--mute);border:1px dashed var(--line);border-radius:18px}
.foot{margin-top:18px;color:var(--mute);font-size:13px;display:flex;gap:22px;flex-wrap:wrap}.foot b{color:var(--dim)}
.cover{position:fixed;inset:0;background:rgba(7,8,11,.94);display:none;place-items:center;z-index:9;text-align:center;padding:30px}
.cover.show{display:grid}
body.projector header .tools,body.projector .tiles,body.projector .foot{display:none}
body.projector .share{grid-template-columns:1fr;text-align:center;justify-items:center;padding:40px}
body.projector .url{font-size:min(6vw,72px)}body.projector .qr img,body.projector .qr canvas{width:min(46vh,520px);height:min(46vh,520px)}
body.projector .share .hint{display:block;font-size:20px}
@media(max-width:700px){.share{grid-template-columns:1fr}.url{font-size:26px}}
"""

DESK_HTML = r"""<!doctype html><html><head><meta charset=utf-8><title>CBACK · Desk</title>
<meta name=viewport content="width=device-width,initial-scale=1"><style>__CSS____DESK_CSS__</style></head><body>
<div class=wrap>
<header>
  <div class=brand><div class=logo>__ICON__</div><div><div class=stname>CBACK</div><div class=sttag>students → teacher</div></div>
    <span class=pill><span class=dot id=sdot></span><b id=count>0</b> online</span>
    <div class=onair id=onair>LISTENING</div></div>
  <div class=tools>
    <select id=out title="Listen on"><option value="">System output</option></select>
    <span class=vol>VOL<input type=range id=vol min=0 max=1 step=0.01 value=0.9></span>
    <button class=ghost id=proj>⛶ Projector</button>
  </div>
</header>
<div class="card share">
  <div><h2>Students open</h2><div class="url mono" id=url>…</div>
    <div class=hint>Pick <b>BlackHole 2ch</b> as input on that page, type a name, press <b>Join</b>.<br>
    First time only: Chrome says “not private” → <b>Advanced</b> → <b>Proceed</b> (it’s this Mac’s own certificate).</div></div>
  <div class=qr id=qr></div>
</div>
<div class=tiles id=tiles></div>
<div class=foot><span><b>Click</b> a student to listen</span><span><b>⇧-click</b> to add more</span><span><b>1–9</b> solo · <b>0</b>/<b>Esc</b> stop</span><span><b>H</b> next raised hand</span><span>🔇 = student not broadcasting</span><span id=net></span></div>
<div class=err id=err></div>
</div>
<div class=cover id=stale><div><h2>This desk moved to a newer tab</h2><p class=hint>Close this one.</p></div></div>
<script src="/qrcode.min.js"></script>
<script>__JS_COMMON__
const hid=rnd();const sig=new Signal('desk','&hid='+hid);
const students=new Map();   // id -> {id,name,dev,hand,pc,cid,audio,levels:[l,r],peak:[l,r],clip,listening,gone}
let sinkId=localStorage.cb_out||'';let vol=+(localStorage.cb_vol||0.9);$('#vol').value=vol;

function showErr(t){$('#err').style.display=t?'block':'none';$('#err').textContent=t||'';}
function sorted(){return [...students.values()].sort((a,b)=>a.name.localeCompare(b.name,undefined,{sensitivity:'base'}));}
function render(){const list=sorted();const box=$('#tiles');
  if(!list.length){box.innerHTML='<div class=empty>No students yet — they open the link above.</div>';}
  else{const have=new Set();list.forEach((s,i)=>{if(!s.el)makeTile(s);have.add(s.el);s.el.querySelector('.num').textContent=i<9?i+1:'';box.appendChild(s.el);paint(s);});
    [...box.children].forEach(c=>{if(!have.has(c))c.remove();});}
  const n=list.filter(s=>!s.gone).length;$('#count').textContent=n;$('#sdot').className='dot'+(n?' live':'');
  const on=list.filter(s=>s.listening&&s.live&&!s.gone);$('#onair').classList.toggle('on',on.length>0);$('#onair').textContent=on.length?'LISTENING · '+(on.length>1?on.length+' students':on[0].name.toUpperCase()):'LISTENING';
  document.body.classList.toggle('compact',list.length>0&&!document.body.classList.contains('projector'));}
function makeTile(s){const el=document.createElement('div');el.className='tile';
  el.innerHTML='<div class=top><span class=num></span><span class=nm></span><span class=hand>✋</span></div><div class=dev></div><div class=ms><div class=m><i></i><b></b></div><div class=m><i></i><b></b></div></div><div class=st></div><div class=clip>CLIP</div>';
  el.onclick=e=>{if(e.shiftKey||e.metaKey)toggle(s);else solo(s);};s.el=el;s.bars=el.querySelectorAll('.m');}
function paint(s){const el=s.el;if(!el)return;el.querySelector('.nm').textContent=s.name;el.querySelector('.dev').textContent=s.dev||'';
  el.classList.toggle('on',!!s.listening);el.classList.toggle('hand',!!s.hand&&!s.listening);el.classList.toggle('gone',!!s.gone);el.classList.toggle('muted',!s.live&&!s.gone);
  const st=s.gone?'offline':s.state!=='connected'?'connecting…':!s.live?(s.listening?'🔇 Waiting for them to broadcast':'🔇 Not broadcasting'):s.listening?'🎧 Listening':s.hand?'Wants you to listen':'Click to listen';
  el.querySelector('.st').innerHTML=(s.listening&&s.live&&!s.gone?'<span class="dot air"></span>':'')+st;}

// ---------- listening
function setListen(s,on){s.listening=on;s.audio.muted=!on;if(on){s.hand=false;s.audio.play().catch(()=>{});}
  sig.send(s.id,{type:'listen',on});paint(s);render();}
function solo(s){const others=[...students.values()].some(o=>o!==s&&o.listening);const was=s.listening;
  for(const o of students.values())if(o!==s&&o.listening)setListen(o,false);setListen(s,!was||others);}   // click again = stop; click while several = just this one
function toggle(s){setListen(s,!s.listening);}
function stopAll(){for(const s of students.values())if(s.listening)setListen(s,false);}

// ---------- peers (students offer; the desk answers)
function student(id){let s=students.get(id);if(!s){const audio=new Audio();audio.autoplay=true;audio.muted=true;audio.volume=vol;
    if(sinkId&&audio.setSinkId)audio.setSinkId(sinkId).catch(()=>{});
    s={id,name:'…',dev:'',hand:false,audio,levels:[-90,-90],peak:[-90,-90],clip:0,listening:false,gone:false,state:'new'};students.set(id,s);}
  return s;}
function drop(s){if(s.pc)s.pc.close();s.pc=null;s.audio.srcObject=null;students.delete(s.id);if(s.el)s.el.remove();render();}
async function onOffer(id,d){const s=student(id);Object.assign(s,{name:d.name||'Student',dev:d.dev||'',hand:!!d.hand,live:!!d.live,gone:false,state:'connecting'});clearTimeout(s.goneT);
  if(s.pc)s.pc.close();const pc=new RTCPeerConnection({iceServers:[]});s.pc=pc;s.cid=d.cid;
  pc.ontrack=e=>{s.audio.srcObject=e.streams[0]||new MediaStream([e.track]);for(const r of pc.getReceivers()){try{r.jitterBufferTarget=40;}catch(_){}}if(s.listening)s.audio.play().catch(()=>{});};
  pc.ondatachannel=e=>{const dc=e.channel;dc.binaryType='arraybuffer';dc.onmessage=m=>{const v=new Int8Array(m.data);s.levels=[v[0],v[1]];if(v[2])s.clip=performance.now();};};
  pc.onconnectionstatechange=()=>{if(s.pc!==pc)return;const st=pc.connectionState;s.state=st;
    if(st==='connected'){s.gone=false;clearTimeout(s.goneT);if(s.listening)sig.send(s.id,{type:'listen',on:true});}
    if(st==='failed'||st==='closed'||st==='disconnected'){s.goneT=setTimeout(()=>{if(s.pc===pc&&pc.connectionState!=='connected'){s.gone=true;if(s.listening)s.listening=false;paint(s);render();
        s.goneT=setTimeout(()=>{if(s.gone)drop(s);},30000);}},4000);}
    paint(s);render();};
  await pc.setRemoteDescription({type:'offer',sdp:d.sdp});
  const ans=await pc.createAnswer();await pc.setLocalDescription(ans);await gathered(pc);
  sig.send(id,{type:'answer',cid:d.cid,sdp:stereo(pc.localDescription.sdp)});render();}
sig.onmsg=async(from,d)=>{
  if(d.type==='offer')await onOffer(from,d);
  else if(d.type==='update'){const s=students.get(from);if(s){if('name' in d)s.name=d.name;if('dev' in d)s.dev=d.dev;if('hand' in d)s.hand=d.hand;if('live' in d){s.live=d.live;if(!s.live)s.levels=[-90,-90];}paint(s);render();}}
  else if(d.type==='bye'){const s=students.get(from);if(s)drop(s);}};
sig.onreset=()=>sig.send('*',{type:'desk-hello'});
sig.onoffline=()=>{$('#net').textContent='⚠ server not answering';};
sig.onstale=()=>{for(const s of students.values())if(s.pc)s.pc.close();$('#stale').classList.add('show');};

// ---------- meters (levels arrive from each student ~20×/s, so meters run even while nobody listens)
function frame(){const now=performance.now();
  for(const s of students.values()){if(!s.bars)continue;for(let c=0;c<2;c++){const v=s.gone?-90:s.levels[c];s.peak[c]=Math.max(v,s.peak[c]-0.6);
      s.bars[c].firstChild.style.width=dbw(v)+'%';s.bars[c].lastChild.style.left=dbw(s.peak[c])+'%';}
    s.el.querySelector('.clip').classList.toggle('on',now-s.clip<1500);}
  requestAnimationFrame(frame);}

// ---------- output device + volume
async function listOutputs(){try{const all=await navigator.mediaDevices.enumerateDevices();const sel=$('#out');
  sel.innerHTML='<option value="">System output</option>'+all.filter(d=>d.kind==='audiooutput'&&d.deviceId!=='default'&&d.deviceId).map(d=>`<option value="${esc(d.deviceId)}">${esc(d.label||'Output')}</option>`).join('');
  sel.value=[...sel.options].some(o=>o.value===sinkId)?sinkId:'';}catch(e){}}
$('#out').onchange=e=>{sinkId=e.target.value;localStorage.cb_out=sinkId;for(const s of students.values())if(s.audio.setSinkId)s.audio.setSinkId(sinkId).catch(err=>showErr('Output: '+err.message));};
$('#vol').oninput=e=>{vol=+e.target.value;localStorage.cb_vol=vol;for(const s of students.values())s.audio.volume=vol;};
$('#proj').onclick=()=>{document.body.classList.toggle('projector');render();};
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'&&e.target.type==='text')return;
  if(e.key==='Escape'){if(document.body.classList.contains('projector')){document.body.classList.remove('projector');render();}else stopAll();}
  else if(e.key==='0')stopAll();
  else if(/^[1-9]$/.test(e.key)){const s=sorted()[+e.key-1];if(s)solo(s);}
  else if(e.key==='h'||e.key==='H'){const s=sorted().find(x=>x.hand&&!x.gone);if(s)solo(s);}});

(async()=>{
  // One quick microphone permission on the desk: it lets Chrome show output device names,
  // and use plain LAN addresses (not mDNS names) when connecting to students.
  try{const st=await navigator.mediaDevices.getUserMedia({audio:true});st.getTracks().forEach(t=>t.stop());}catch(e){}
  listOutputs();navigator.mediaDevices.addEventListener('devicechange',listOutputs);
  const i=await (await fetch('/api/info')).json();
  if(i.https){$('#url').innerHTML=i.https.replace(/^https:\/\/([^:]+)/,'https://<b>$1</b>');try{new QRCode($('#qr'),{text:i.https,width:480,height:480,correctLevel:QRCode.CorrectLevel.M});}catch(e){}}
  else{$('#url').textContent='—';showErr('No https: students cannot capture audio without it. Is openssl available?');}
  await fetch('/api/reset?id=desk&hid='+hid);sig.run();sig.send('*',{type:'desk-hello'});render();requestAnimationFrame(frame);
})();
</script></body></html>"""

# ----------------------------------------------------------------- student
SEND_CSS = r"""
.send{max-width:560px;margin:0 auto;padding:24px 20px 60px}
.state{display:flex;align-items:center;gap:12px;font-size:22px;font-weight:800;letter-spacing:-.02em;margin:4px 0 2px}
.sub{color:var(--dim);font-size:15px;min-height:22px}
.join{width:100%;margin-top:22px;font-size:22px;padding:22px;border-radius:20px;background:var(--live);color:var(--ink)}
.devinfo{color:var(--dim);font-size:13px;margin-top:8px;min-height:18px}.devinfo b{color:var(--fg)}.devinfo .warn{color:var(--wait)}
.vu{margin:18px 0 6px;padding:14px 16px;border-radius:16px;background:#0a0c10;border:1px solid var(--line);display:grid;gap:8px}
.vu .row{display:grid;grid-template-columns:14px 1fr 54px;gap:10px;align-items:center;font-size:12px;color:var(--dim);font-weight:700}
.vu .m{height:16px;border-radius:6px}.vu .pk{text-align:right;color:var(--fg);font-variant-numeric:tabular-nums}
.btns{display:grid;gap:10px;margin-top:18px}
.hand{font-size:17px;padding:16px;border-radius:16px;background:#222736}.hand.on{background:var(--wait);color:#3a2a10}
.bcast{font-size:21px;padding:20px;border-radius:18px;background:var(--live);color:var(--ink);display:flex;align-items:center;justify-content:center;gap:12px}
.bcast.on{background:#2a1418;color:#fff;box-shadow:inset 0 0 0 2px var(--air)}.bcast .rec{width:14px;height:14px;border-radius:50%;background:currentColor}.bcast.on .rec{border-radius:3px;background:var(--air)}
body.listening .card{border-color:var(--air);box-shadow:0 0 0 2px var(--air),0 0 50px rgba(255,45,85,.3)}
#joined{display:none}body.in #joined{display:block}body.in #joinbox{display:none}
"""

SEND_HTML = r"""<!doctype html><html><head><meta charset=utf-8><title>CBACK</title>
<meta name=viewport content="width=device-width,initial-scale=1"><style>__CSS____SEND_CSS__</style></head><body>
<div class=send>
<header><div class=brand><div class=logo>__ICON__</div><div><div class=stname>CBACK</div><div class=sttag>send your audio to the teacher</div></div></div>
  <div class=onair id=onair>TEACHER LISTENING</div></header>
<div class=card>
  <div class=state><span class=dot id=dot></span><span id=state>Not joined</span></div>
  <div class=sub id=sub>Type your name and press Join.</div>
  <label for=name>Your name</label><input type=text id=name maxlength=32 autocomplete=off placeholder="First name">
  <div id=joinbox><button class=join id=join>Join</button></div>
  <div id=joined>
    <label for=dev>Send from</label><select id=dev></select>
    <div class=devinfo id=devinfo></div>
    <div class=vu><div class=row><span>L</span><div class=m><i></i><b></b></div><span class=pk id=pkL>−∞</span></div>
      <div class=row><span>R</span><div class=m><i></i><b></b></div><span class=pk id=pkR>−∞</span></div></div>
    <div class=devinfo id=silent style="display:none"><span class=warn>No signal — is your DAW playing into BlackHole? (See Setup below.)</span></div>
    <div class=btns><button class=bcast id=bcast><span class=rec></span><span id=bclbl>Start broadcast</span></button><button class=hand id=hand>✋ Ask teacher to listen</button></div>
  </div>
  <div class=err id=err></div>
  <details><summary>Setup — getting your DAW into BlackHole</summary><ol>
    <li>Your DAW plays into <em>BlackHole 2ch</em> — through the class device (Aggregate / Multi-Output with your headphones + BlackHole), so you still hear yourself.</li>
    <li>On this page, <em>Send from</em> = <em>BlackHole 2ch</em>. Chrome takes channels 1–2 of whatever device you pick, so pick BlackHole itself, not the Aggregate.</li>
    <li>Play something: the meters move. Your teacher sees them move too, even before listening.</li>
    <li>Nothing is sent until you press <em>Start broadcast</em> and the teacher clicks you; the card glows red while they listen. <em>Stop broadcast</em> mutes you at once.</li></ol></details>
</div></div>
<script>__JS_COMMON__
const ID=sessionStorage.cb_id||(sessionStorage.cb_id=rnd());const sig=new Signal(ID);
let name=localStorage.cb_name||'';$('#name').value=name;
let stream=null,track=null,pc=null,sender=null,dc=null,cid=null,ac=null,listening=false,hand=false,joined=false,live=false,answered=false,retryT=null,silentSince=0;

function showErr(t){$('#err').style.display=t?'block':'none';$('#err').textContent=t||'';}
function setState(cls,t,sub){$('#dot').className='dot '+cls;$('#state').textContent=t;if(sub!==undefined)$('#sub').textContent=sub;}
function devLabel(){return track?track.label.replace(/\s*\(.*?\)\s*$/,''):'';}
function ui(){const heard=live&&listening;document.body.classList.toggle('listening',heard);$('#onair').classList.toggle('on',heard);
  $('#hand').classList.toggle('on',hand);$('#hand').textContent=hand?'✋ Hand raised — click to lower':'✋ Ask teacher to listen';
  $('#bcast').classList.toggle('on',live);$('#bclbl').textContent=live?'Stop broadcast':'Start broadcast';
  if(!joined)return;
  if(sig.online===false)return setState('off','Reconnecting…','Lost the connection to the teacher’s Mac.');
  if(heard)return setState('air','Teacher is listening','Your audio is going to the teacher now. Stop broadcast mutes you.');
  const desk=pc&&pc.connectionState==='connected';
  if(desk&&!live)return setState('',listening?'Teacher wants to hear you':'Not broadcasting',listening?'Press Start broadcast to let them hear you.':'You’re on the teacher’s desk, muted. Press Start broadcast when you want to be heard.');
  if(desk)return setState('live','Broadcasting','The teacher sees your meters and hears you when they click your name.');
  setState('wait',answered?'Connecting…':'Waiting for the teacher','The teacher’s desk isn’t open yet — you’ll appear when it is.');}

// ---------- capture: BlackHole (or any input), channels 1-2, no speech processing
async function listDevices(){const all=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='audioinput');const sel=$('#dev');const cur=localStorage.cb_dev||'';
  sel.innerHTML=all.map(d=>`<option value="${esc(d.deviceId)}">${esc(d.label||'Input')}</option>`).join('');
  const pick=all.find(d=>d.deviceId===cur)||all.find(d=>/blackhole/i.test(d.label))||all.find(d=>/aggregate/i.test(d.label))||all[0];
  if(pick)sel.value=pick.deviceId;return pick&&pick.deviceId;}
async function capture(id){showErr('');
  const st=await navigator.mediaDevices.getUserMedia({audio:{deviceId:id?{exact:id}:undefined,channelCount:{ideal:2},sampleRate:{ideal:48000},echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
  if(stream)stream.getTracks().forEach(t=>t.stop());stream=st;track=st.getAudioTracks()[0];try{track.contentHint='music';}catch(e){}
  track.onended=()=>{if(stream===st){showErr('The input device went away. Pick another one.');}};
  const s=track.getSettings();const ch=s.channelCount;
  $('#devinfo').innerHTML=`<b>${esc(devLabel())}</b> · ch 1–2 · ${ch===1?'<span class=warn>mono</span>':'stereo'}`+(s.sampleRate?` · ${(s.sampleRate/1000).toFixed(1).replace('.0','')} kHz`:'')+
    (/aggregate/i.test(track.label)?' · <span class=warn>Aggregate: only its first two channels are sent — pick BlackHole directly if you hear nothing</span>':'');
  // meter: peaks measured on the audio thread, which Chrome never throttles (the page sits behind the DAW)
  if(mnode){mnode.port.onmessage=null;mnode.disconnect();}if(msrc)msrc.disconnect();
  msrc=ac.createMediaStreamSource(st);mnode=new AudioWorkletNode(ac,'peak',{numberOfInputs:1,numberOfOutputs:1,channelCount:2,channelCountMode:'explicit',channelInterpretation:'discrete'});
  msrc.connect(mnode);mnode.connect(mute);mnode.port.onmessage=e=>meter(e.data);
  flow();sig.send('desk',{type:'update',dev:devLabel()});}
// posts [peakL, peakR] every 2048 frames (~45 ms); outputs silence
const PEAK=`class P extends AudioWorkletProcessor{constructor(){super();this.n=0;this.m=[0,0];}
process(inp){const x=inp[0];if(x&&x[0]){const R=x[1]||x[0];for(let i=0;i<x[0].length;i++){const a=Math.abs(x[0][i]),b=Math.abs(R[i]);if(a>this.m[0])this.m[0]=a;if(b>this.m[1])this.m[1]=b;}
 this.n+=x[0].length;if(this.n>=2048){this.port.postMessage(this.m);this.m=[0,0];this.n=0;}}return true;}}registerProcessor('peak',P);`;
let msrc=null,mnode=null,mute=null,pk=[-90,-90],hold=[0,0];
async function initAudio(){ac=ac||new (window.AudioContext||window.webkitAudioContext)();ac.resume();
  if(!mute){await ac.audioWorklet.addModule(URL.createObjectURL(new Blob([PEAK],{type:'application/javascript'})));mute=ac.createGain();mute.gain.value=0;mute.connect(ac.destination);}}
function meter(m){const out=new Int8Array(3);const now=performance.now();const rows=document.querySelectorAll('.vu .m');
  for(let c=0;c<2;c++){const db=m[c]>0?Math.max(-90,20*Math.log10(m[c])):-90;out[c]=Math.round(db);if(m[c]>=0.999)out[2]=1;
    rows[c].firstChild.style.width=dbw(db)+'%';if(db>pk[c]||now-hold[c]>1500){pk[c]=db;hold[c]=now;}rows[c].lastChild.style.left=dbw(pk[c])+'%';
    $(c?'#pkR':'#pkL').textContent=pk[c]<=-89?'−∞':pk[c].toFixed(1);}
  if(dc&&dc.readyState==='open')try{dc.send(live?out.buffer:new Int8Array([-90,-90,0]).buffer);}catch(e){}
  if(Math.max(out[0],out[1])>-70)silentSince=0;else if(!silentSince)silentSince=now;
  $('#silent').style.display=silentSince&&now-silentSince>6000?'block':'none';}

// ---------- connection to the desk: this page offers, the desk answers
function flow(){if(sender)sender.replaceTrack(live&&listening&&track?track:null).catch(()=>{});}   // audio only when the student broadcasts AND the teacher listens
function teardown(){if(pc){pc.onconnectionstatechange=null;pc.close();}pc=null;sender=null;dc=null;cid=null;}
async function offer(){clearTimeout(retryT);if(!joined||!track)return;teardown();answered=false;
  const p=new RTCPeerConnection({iceServers:[]});pc=p;cid=rnd();
  sender=p.addTransceiver('audio',{direction:'sendonly',streams:[stream]}).sender;
  await sender.replaceTrack(live&&listening?track:null);
  dc=p.createDataChannel('lv',{ordered:false,maxRetransmits:0});dc.binaryType='arraybuffer';
  p.onconnectionstatechange=()=>{if(pc!==p)return;const st=p.connectionState;
    if(st==='connected'){try{const prm=sender.getParameters();prm.encodings=prm.encodings&&prm.encodings.length?prm.encodings:[{}];prm.encodings[0].maxBitrate=320000;prm.encodings[0].priority='high';prm.encodings[0].networkPriority='high';sender.setParameters(prm).catch(()=>{});}catch(e){}}
    if(st==='failed'||st==='disconnected'){listening=false;retryT=setTimeout(offer,st==='failed'?500:3000);}
    ui();};
  await p.setLocalDescription(await p.createOffer());await gathered(p);if(pc!==p)return;
  sig.send('desk',{type:'offer',cid,sdp:stereo(p.localDescription.sdp),name,dev:devLabel(),hand,live});
  retryT=setTimeout(()=>{if(pc===p&&!answered)offer();},15000);ui();}
sig.onmsg=async(from,d)=>{if(from!=='desk')return;
  if(d.type==='answer'&&pc&&d.cid===cid&&!answered){answered=true;await pc.setRemoteDescription({type:'answer',sdp:d.sdp});ui();}
  else if(d.type==='listen'){listening=!!d.on;if(listening&&hand){hand=false;sig.send('desk',{type:'update',hand});}
    flow();ui();}
  else if(d.type==='desk-hello'){listening=false;offer();}};
sig.onreset=()=>{ui();offer();};sig.onoffline=()=>ui();

// ---------- controls
$('#name').oninput=e=>{name=e.target.value.trim();localStorage.cb_name=name;$('#join').disabled=!name;if(joined)sig.send('desk',{type:'update',name:name||'Student'});};
$('#join').disabled=!name;
$('#join').onclick=async()=>{if(!name)return $('#name').focus();
  try{await initAudio();await capture(null);const id=await listDevices();if(id&&track.getSettings().deviceId!==id)await capture(id);}
  catch(e){return showErr(e.name==='NotAllowedError'?'Chrome blocked the microphone permission. Click the icon left of the address and allow it, then Join again.':'Could not open the input: '+e.message);}
  localStorage.cb_dev=$('#dev').value;joined=true;document.body.classList.add('in');ui();offer();};
$('#dev').onchange=async e=>{localStorage.cb_dev=e.target.value;try{await capture(e.target.value);}catch(err){showErr('Could not open that input: '+err.message);}};
function setLive(on){live=on;flow();sig.send('desk',{type:'update',live});ui();}
$('#bcast').onclick=()=>setLive(!live);
$('#hand').onclick=()=>{hand=!hand;sig.send('desk',{type:'update',hand});if(hand&&!live)setLive(true);ui();};   // asking to be heard starts the broadcast
navigator.mediaDevices.addEventListener('devicechange',()=>{if(joined)listDevices();});
addEventListener('pagehide',()=>navigator.sendBeacon('/api/msg',JSON.stringify({from:ID,to:'desk',data:{type:'bye'}})));
if(!window.isSecureContext)showErr('This page must be opened with https:// — ask your teacher for the link.');
sig.run();ui();
</script></body></html>"""


def _page(html, extra_css):
    return (html.replace("__CSS__", CSS).replace("__DESK_CSS__", extra_css).replace("__SEND_CSS__", extra_css)
                .replace("__JS_COMMON__", JS_COMMON).replace("__ICON__", ICON))


DESK = _page(DESK_HTML, DESK_CSS)
SEND = _page(SEND_HTML, SEND_CSS)
try:
    QR_JS = open(os.path.join(HERE, "qrcode.min.js"), "rb").read()
except OSError:
    QR_JS = b""


# ----------------------------------------------------------------- http
class H(BaseHTTPRequestHandler):
    TLS = False

    def log_message(self, *a):
        pass

    def handle(self):
        try:
            super().handle()
        except (ssl.SSLError, ConnectionResetError, BrokenPipeError):
            pass

    def _local(self):
        return self.client_address[0] in LOCAL

    def _send(self, code, body, ctype="application/json", headers=()):
        if isinstance(body, str):
            body = body.encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _redirect(self, to):
        self._send(302, "", "text/plain", [("Location", to)])

    def do_GET(self):
        global _desk_hid
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/":
            if self.TLS:
                return self._send(200, SEND, "text/html")
            return self._redirect("/desk" if self._local() else (URL_TLS or "/desk"))
        if u.path == "/desk":   # the teacher's Mac only: students must not listen to each other
            return self._send(200, DESK, "text/html") if self._local() else self._redirect(URL_TLS or "/")
        if u.path == "/qrcode.min.js":
            return self._send(200, QR_JS, "application/javascript")
        if u.path == "/api/info":
            return self._send(200, json.dumps({"ip": IPS[0], "port": PORT, "https": URL_TLS, "gen": GEN}))
        if u.path == "/api/poll":
            cid = q.get("id", [""])[0]
            if not cid:
                return self._send(400, "{}")
            if cid == "desk" and (not self._local() or q.get("hid", [""])[0] != _desk_hid):
                return self._send(200, json.dumps({"gen": GEN, "msgs": [], "stale": True}))
            after = int(q.get("after", ["0"])[0] or 0)
            return self._send(200, json.dumps({"gen": GEN, "msgs": take(cid, after, POLL_WAIT)}))
        if u.path == "/api/reset" and self._local():
            _desk_hid = q.get("hid", [""])[0]
            log("desk opened"); reset("desk"); return self._send(200, "{}")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/msg":
            return self._send(404, "{}")
        try:
            m = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            sender, to, data = str(m["from"])[:40], str(m["to"])[:40], m["data"]
            if sender == "desk" and not self._local():
                return self._send(403, "{}")
            deliver(sender, to, data)
            t = data.get("type") if isinstance(data, dict) else None
            if t == "offer":
                nm = str(data.get("name") or "student")[:32]
                if sender not in _names:
                    log(f"{nm} joined ({len(_names) + 1} online)")
                _names[sender] = nm
            elif t == "bye" and sender in _names:
                log(f"{_names.pop(sender)} left")
        except Exception as e:
            return self._send(400, json.dumps({"error": str(e)}))
        self._send(200, "{}")


class HS(H):
    TLS = True


RUNFILES = [os.path.join(HERE, ".cback.pid"), os.path.join(HERE, ".cback.port")]


def write_runfiles(port):
    for f, v in zip(RUNFILES, (os.getpid(), port)):
        with open(f, "w") as fh:
            fh.write(str(v))


def shutdown():
    for f in RUNFILES:
        try: os.remove(f)
        except OSError: pass
    os._exit(0)


# CCAST's certificate when it is there (devices that accepted it once are not asked again), else our own.
_CC = os.path.expanduser("~/ClassCast")
if os.path.exists(os.path.join(_CC, ".classcast-cert-v2.pem")):
    CERT, KEY = os.path.join(_CC, ".classcast-cert-v2.pem"), os.path.join(_CC, ".classcast-key-v2.pem")
else:
    CERT, KEY = os.path.join(HERE, ".cback-cert.pem"), os.path.join(HERE, ".cback-key.pem")


def ensure_cert(ip):
    if os.path.exists(CERT) and os.path.exists(KEY):
        return True
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", KEY, "-out", CERT, "-days", "397",
                        "-subj", "/CN=CBACK", "-addext", f"subjectAltName=IP:{ip},DNS:localhost"], check=True, capture_output=True)
        return True
    except Exception as e:
        log(f"no https (openssl): {e}"); return False


def bind(port_wanted):
    """A free pair: http on p (the desk), https on p+1 (students)."""
    for port in range(port_wanted, port_wanted + 20, 2):
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), H)
        except OSError:
            continue
        try:
            tls = ThreadingHTTPServer(("0.0.0.0", port + 1), HS)
        except OSError:
            srv.server_close(); continue
        return srv, tls, port
    sys.exit(f"No free ports between {port_wanted} and {port_wanted + 19}")


def daemonize():
    """Detach completely, like CCAST, so macOS doesn't think the Dock app is still running."""
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.chdir(HERE)
    logf = open(os.path.join(HERE, "cback.log"), "ab", buffering=0)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0); os.dup2(logf.fileno(), 1); os.dup2(logf.fileno(), 2)


if __name__ == "__main__":
    if DAEMON:
        daemonize()
    srv, tls, PORT = bind(PORT_WANTED)
    srv.daemon_threads = tls.daemon_threads = True
    IPS = lan_ips()
    URL_TLS = None
    if ensure_cert(IPS[0]):
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(CERT, KEY)
            tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
            threading.Thread(target=tls.serve_forever, daemon=True).start()
            URL_TLS = f"https://{IPS[0]}:{PORT + 1}"
        except Exception as e:
            log(f"no https: {e}")
    write_runfiles(PORT)
    threading.Thread(target=prune, daemon=True).start()
    box = [f"Students open   {URL_TLS or '- (no https)'}", f"You (teacher)   http://localhost:{PORT}/desk"]
    w = max(len(b) for b in box) + 4
    print("\n  ┌" + "─" * w + "┐\n  │" + "CBACK".center(w) + "│\n  ├" + "─" * w + "┤")
    for b in box:
        print("  │  " + b.ljust(w - 2) + "│")
    print("  └" + "─" * w + "┘\n  Close this window or press Ctrl-C to stop.\n", flush=True)

    def open_desk():
        url = f"http://localhost:{PORT}/desk"
        if subprocess.run(["open", "-a", "Google Chrome", url], capture_output=True).returncode != 0:
            webbrowser.open(url)
    if os.environ.get("BROWSER") != "none":
        threading.Timer(0.8, open_desk).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        shutdown()
