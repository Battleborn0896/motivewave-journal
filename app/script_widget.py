"""Opt-in custom statistics scripts in a time-limited, network-denied Web Worker.

Only the already-filtered display sample is supplied. No Python execution, credentials, files,
parent DOM, downloads, forms, popups, external libraries or broker actions are exposed.
"""
import base64
import functools
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import common
from mwjournal import fonts

FONT_FILE = Path(__file__).resolve().parent / 'static' / fonts.FOLDER / fonts.FACES[0][0]    # Regular

STARTER = """const sum = journalTrades.reduce((n, trade) => n + (trade.net ?? 0), 0);
return {type: 'text', text: `${journalTrades.length} filtered trades · net ${sum.toFixed(2)}`};"""


@functools.lru_cache(maxsize=1)
def font_face() -> str:
    """IBM Plex Sans written into the page itself: the page may load nothing, not even from this app."""
    try:
        data = base64.b64encode(FONT_FILE.read_bytes()).decode('ascii')
    except OSError:
        return ''                          # the fallbacks after IBM Plex Sans draw the text
    return (f'@font-face{{font-family:"{fonts.FAMILY}";font-style:normal;font-weight:400;font-display:block;'
            f'src:url(data:font/woff2;base64,{data}) format("woff2")}}')


def document(rows, tags, code):
    fields = ('id', 'symbol', 'market', 'direction', 'opened', 'closed', 'net', 'gross', 'r', 'tags', 'tag_ids', 'tag_score', 'ratings')
    safe_rows = [{key: row.get(key) for key in fields} for row in rows]
    def data(value):
        return json.dumps(value, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    # User code runs only inside a worker, with no document/location/parent frame access.
    # The worker inherits this CSP. The trusted renderer uses textContent/canvas, never user HTML.
    return ('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
            'content="default-src \'none\'; script-src \'unsafe-inline\'; worker-src blob:; style-src \'unsafe-inline\'; '
            'font-src data:; img-src \'none\'; frame-src \'none\'; connect-src \'none\'; form-action \'none\'; '
            'base-uri \'none\'">'
            '<style>' + font_face() + 'body{background:#1d1e1f;color:#e8e8e5;font:13px ' + fonts.STACK +
            ';margin:12px}canvas{width:100%;height:230px}</style>'
            '<div id="root">Calculating…</div><canvas id="chart" hidden></canvas><script>const input=' +
            data({'rows': safe_rows, 'tags': tags, 'code': code, 'font': fonts.STACK}) + ';' + RENDERER + '</script>')


RENDERER = r"""
const root=document.getElementById('root'),canvas=document.getElementById('chart');
const source='self.onmessage=function(e){try{const result=(function(journalTrades,journalTags){'+input.code+
             '\n})(e.data.rows,e.data.tags);self.postMessage(result);}catch(e){self.postMessage({type:"text",text:"Script error: "+e.message});}}';
const url=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));
const worker=new Worker(url);URL.revokeObjectURL(url);
const timer=setTimeout(()=>{worker.terminate();root.textContent='Script exceeded the two-second limit.';},2000);
worker.onerror=()=>{clearTimeout(timer);worker.terminate();root.textContent='The script could not run. Use the documented return format.';};
worker.onmessage=e=>{clearTimeout(timer);worker.terminate();const result=e.data;
 if(!result||!['text','line','bar'].includes(result.type)){root.textContent='Return {type: "text", text: "…"} or {type: "line"/"bar", labels: [...], values: [...]}.';return;}
 if(result.type==='text'){root.textContent=String(result.text??'').slice(0,10000);return;}
 if(!Array.isArray(result.values)||result.values.length>5000||!result.values.length||!result.values.every(v=>typeof v==='number'&&Number.isFinite(v))){root.textContent='Return 1–5000 finite numeric values.';return;}
 root.textContent=String(result.title??'Custom series').slice(0,200);
 // A canvas keeps the font it drew with: draw once IBM Plex Sans is ready.
 const draw=()=>{canvas.hidden=false;canvas.width=900;canvas.height=230;
 const ctx=canvas.getContext('2d'),vals=result.values,low=Math.min(0,...vals),high=Math.max(0,...vals),range=high-low||1;
 const y=v=>200-(v-low)/range*175,x=i=>35+i*840/Math.max(1,vals.length-1);
 ctx.strokeStyle='#414345';ctx.beginPath();ctx.moveTo(25,y(0));ctx.lineTo(890,y(0));ctx.stroke();ctx.strokeStyle='#e2cf32';ctx.fillStyle='#e2cf32';
 if(result.type==='line'){ctx.beginPath();vals.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.stroke();}
 else{const width=840/vals.length;vals.forEach((v,i)=>ctx.fillRect(35+i*width,Math.min(y(v),y(0)),Math.max(1,width-2),Math.max(1,Math.abs(y(v)-y(0)))));}
 ctx.fillStyle='#959a94';ctx.font='12px '+input.font;ctx.fillText(high.toFixed(2),2,15);ctx.fillText(low.toFixed(2),2,217);
 if(Array.isArray(result.labels)){ctx.fillText(String(result.labels[0]??'').slice(0,30),35,227);ctx.fillText(String(result.labels.at(-1)??'').slice(0,30),720,227);}
 };document.fonts.load('12px '+input.font).then(draw,draw);
};worker.postMessage({rows:input.rows,tags:input.tags});
"""


def render(journal, rows, widget, key):
    from mwjournal.model import digest
    signature = digest([widget.get('code', ''), rows])
    armed = key + '-approved-script'
    st.caption('Custom statistics worker · two-second limit · no DOM / files / account IDs / network. '
               'Return text or a numeric line/bar series. Review your code before running it.')
    with st.expander('Review Script'):
        st.code(widget.get('code') or STARTER, language='javascript')
    if st.button('Run Local Script', key=key + '-run'):
        st.session_state[armed] = signature
    if st.session_state.get(armed) == signature:
        if hasattr(st, 'iframe'):
            st.iframe(document(rows, journal.tag_library()['tags'], widget.get('code') or STARTER), height=280)
        else:  # the documented >=1.49 lower bound predates st.iframe
            components.html(document(rows, journal.tag_library()['tags'], widget.get('code') or STARTER), height=280)
    else:
        st.caption('Not running. Edited scripts or a changed trade sample require a fresh click. External CDN libraries are not loaded.')
