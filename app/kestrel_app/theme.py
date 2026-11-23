"""The app's palette and its one style sheet.

The palette is blue-led: navy for the sidebar and headings, a brand blue for actions
and links, a light blue tint for selected states, and a warm gold used sparingly for
markers. Status colours always travel with a word. Every text pairing here was checked
for at least 4.5:1 contrast. The Streamlit theme in `.streamlit/config.toml` uses the
same values, so native widgets match the custom pieces below.
"""

from __future__ import annotations

NAVY = "#0B2A4A"
BLUE = "#0F62C4"
BLUE_DARK = "#0B4F9E"
TINT = "#E7F0FB"
GOLD = "#F0A202"
PAGE = "#F6F9FC"
CARD = "#FFFFFF"
BORDER = "#DCE5EF"
TEXT = "#14263B"
MUTED = "#4A5B70"

TONES: dict[str, tuple[str, str]] = {
    "ok": ("#0B7A66", "#E2F5F0"),
    "warn": ("#7A4B00", "#FFF3D1"),
    "bad": ("#A1301C", "#FDEBE7"),
    "neutral": ("#3C4A5C", "#EAF0F6"),
    "info": (BLUE_DARK, TINT),
}
"""Text colour and background for each status tone."""

ASK_TOP = "140px"
"""Viewport height kept outside the evidence pane on the Ask page: the app bar, page padding and header row."""
CONV_TOP = "248px"
"""The same for the conversation pane, which also leaves room for the chat input and privacy note below it."""

_RULES = """
:root{--k-navy:@NAVY@;--k-blue:@BLUE@;--k-blue-dark:@BLUE_DARK@;--k-tint:@TINT@;--k-gold:@GOLD@;
  --k-page:@PAGE@;--k-card:@CARD@;--k-border:@BORDER@;--k-text:@TEXT@;--k-muted:@MUTED@;
  --k-shadow:0 1px 2px rgba(11,42,74,.06),0 1px 6px rgba(11,42,74,.04)}
html,body{font-variant-numeric:tabular-nums}
.stMain .block-container{padding-top:4.6rem;padding-bottom:2rem;max-width:1680px}
.stMain h1{color:var(--k-navy);font-size:1.75rem;font-weight:700;letter-spacing:-.01em;padding:0 0 .1rem}
.stMain h2,.stMain h3{color:var(--k-navy);letter-spacing:-.005em}
.stMain h3{font-size:1.15rem}
.stMain p,.stMain li{font-size:15px;line-height:1.6}
[data-testid="stCaptionContainer"]{opacity:1!important}
.stMain [data-testid="stCaptionContainer"] p{font-size:13px;color:var(--k-muted);line-height:1.5}
:focus-visible{outline:2px solid var(--k-blue)!important;outline-offset:2px}

/* Sidebar */
[data-testid="stSidebar"]{border-right:none}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{color:#B4D0F2}
.k-brand{display:flex;align-items:center;gap:10px;margin:0 0 6px}
.k-brand-mark{width:30px;height:30px;border-radius:8px;background:var(--k-blue);display:flex;align-items:center;
  justify-content:center;color:#fff;font-weight:700;font-size:15px}
.k-brand-name{font-size:17px;font-weight:700;color:#fff;letter-spacing:.01em}
[data-testid="stSidebar"] .k-lbl{color:#B4D0F2}
[data-testid="stSidebar"] .k-chip{background:#143A63;border-color:#24507F;color:#E7F0FB}
[data-testid="stSidebar"] .k-chip.k-label{background:#143A63;border-color:#24507F;color:#E7F0FB}
[data-testid="stSidebarNav"],[data-testid="stSidebar"] [data-testid="stPageLink"] a{border-radius:8px}
[data-testid="stSidebar"] [data-testid="stPageLink"] a{padding:6px 10px;border-left:3px solid transparent}
[data-testid="stSidebar"] [data-testid="stPageLink"] a span{color:#E7F0FB}
[data-testid="stSidebar"] .st-key-nav-active [data-testid="stPageLink"] a{background:#16406B;border-left-color:var(--k-gold)}
[data-testid="stSidebar"] .st-key-nav-active [data-testid="stPageLink"] a p{color:#fff;font-weight:700}
[data-testid="stSidebar"] code{background:#143A63;color:#E7F0FB}
[data-testid="stSidebar"] hr{border-color:#24507F}

/* Chips, labels, badges */
.k-chips{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 4px}
.k-chip{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;font-size:12px;
  font-weight:500;border:1px solid var(--k-border);background:#fff;color:var(--k-muted);white-space:nowrap}
.k-chip.k-label{font-family:ui-monospace,Consolas,monospace;background:var(--k-tint);border-color:#C5D8F1;color:var(--k-blue-dark)}
.k-lbl{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--k-muted);margin:8px 0 3px}
.k-badge{display:inline-flex;align-items:center;padding:1px 9px;border-radius:6px;font-size:12px;font-weight:600}
.k-mono{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}

/* Bordered containers as cards */
[class*="st-key-panel-"]{background:var(--k-card);border-color:var(--k-border)!important;border-radius:12px!important;
  box-shadow:var(--k-shadow);padding:16px 18px!important}
[class*="st-key-edition-"]{background:var(--k-page);border-color:var(--k-border)!important}
[data-testid="stMetric"] [data-testid="stMetricValue"]{color:var(--k-navy);font-variant-numeric:tabular-nums}
[data-testid="stMetric"] label p{font-size:13px;color:var(--k-muted);font-weight:600}
[class*="st-key-mcard-"]{border:1px solid var(--k-border)!important;border-radius:12px!important;padding:14px 16px!important;
  background:var(--k-card);box-shadow:var(--k-shadow);border-top:3px solid var(--k-blue)!important}

/* Tabs */
.stMain [data-testid="stTabs"] [role="tab"] p{font-size:14px;font-weight:600}
.stMain [data-testid="stTabs"] [role="tab"][aria-selected="true"] p{color:var(--k-blue-dark)}

/* Quotes, calculations, data boxes */
.k-quote{margin:8px 0;padding:10px 14px;border-left:4px solid var(--k-gold);background:#FFFBF0;border-radius:0 8px 8px 0;
  font-size:15px;line-height:1.6;color:var(--k-text);white-space:pre-wrap}
.k-quote mark{background:#FFE7A3;color:var(--k-text);padding:0 2px;border-radius:3px}
.k-calc{background:var(--k-page);border:1px solid var(--k-border);border-radius:8px;padding:10px 14px;margin:6px 0}
.k-calc code{font-size:14px;background:none;color:var(--k-text)}
.k-calc .k-rule{color:var(--k-muted);font-size:12.5px;margin-left:6px}
.k-data{margin:6px 0;padding:8px 12px;background:#FFF9F1;border:1px dashed #A65F00;border-radius:6px;
  font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.5;color:var(--k-text);white-space:pre-wrap}
.k-data::before{content:attr(data-note);display:block;font-family:system-ui,sans-serif;
  font-size:11px;font-weight:700;color:#7A4B00;margin-bottom:4px}

/* Evaluation grid legend */
.k-legend{display:flex;flex-wrap:wrap;gap:10px;font-size:12.5px;color:var(--k-text);margin:4px 0}
.k-cell{display:inline-block;width:20px;height:20px;border-radius:5px;text-align:center;line-height:20px;
  font-size:12px;font-weight:700;margin-right:4px}

/* Ask page: two panes that fill the window, each with its own scroll area */
.stMain:has(.st-key-ask-grid) .block-container{padding:3.6rem 2rem .5rem;max-width:none}
.stMain:has(.st-key-ask-grid){overflow:hidden}
.st-key-ask-head h1{font-size:1.45rem;margin:0;padding:0;white-space:nowrap}
.st-key-conv{height:calc(100vh - @CONV_TOP@)!important;min-height:320px;overflow-y:auto;padding:12px 16px!important;
  background:var(--k-page);border:1px solid var(--k-border)!important;border-radius:12px}
[class*="st-key-evpane"]{height:calc(100vh - @ASK_TOP@)!important;min-height:400px;overflow-y:auto;padding:14px 18px!important;
  background:var(--k-card);border:1px solid var(--k-border)!important;border-radius:12px;box-shadow:var(--k-shadow)}
[data-testid="stLayoutWrapper"]:has(> .st-key-conv){height:calc(100vh - @CONV_TOP@)!important;min-height:320px;flex:0 0 auto!important}
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-evpane"]){height:calc(100vh - @ASK_TOP@)!important;min-height:400px;flex:0 0 auto!important}
.st-key-ask-input [data-testid="stChatInput"]{border-radius:12px}
.st-key-ask-privacy p{font-size:12.5px!important;margin:0}
@media (max-width:1100px){
  .stMain:has(.st-key-ask-grid){overflow:auto}
  .st-key-ask-grid [data-testid="stHorizontalBlock"]{flex-wrap:wrap}
  .st-key-ask-grid [data-testid="stColumn"]{flex:1 1 100%!important;width:100%!important;min-width:100%}
  .st-key-conv,[data-testid="stLayoutWrapper"]:has(> .st-key-conv){height:60vh!important}
  [class*="st-key-evpane"],[data-testid="stLayoutWrapper"]:has(> [class*="st-key-evpane"]){height:72vh!important}
}

/* Chat turns */
.st-key-conv [data-testid="stChatMessage"]{background:transparent;padding:4px 0}
.st-key-conv [data-testid="stChatMessageAvatarCustom"]{background:var(--k-blue);border-radius:8px}
.st-key-conv [data-testid="stChatMessageAvatarCustom"] span{color:#fff!important}
.st-key-conv [data-testid="stChatMessage"]:has(> [aria-label="Chat message from user"]){flex-direction:row-reverse}
.st-key-conv [data-testid="stChatMessage"]:has(> [aria-label="Chat message from user"]) [data-testid="stChatMessageAvatarCustom"]{background:var(--k-navy)}
.st-key-conv [aria-label="Chat message from user"]{background:var(--k-tint);border-radius:12px 12px 2px 12px;padding:10px 14px;
  flex:0 1 auto!important;max-width:85%;margin-left:auto}
.st-key-conv [data-testid="stChatMessageContent"] p{font-size:15.5px;line-height:1.6}
[data-testid="stHorizontalBlock"] .k-chips,[data-testid="stLayoutWrapper"] .k-chips{margin:0}
[class*="st-key-evpane"] [data-testid="stTabs"] [role="tab"] p{font-size:13.5px}
[class*="st-key-evpane"] [data-testid="stTabs"] [role="tablist"]{gap:8px}

/* Answer cards */
[class*="st-key-card-"]{background:var(--k-card)!important;border:1px solid var(--k-border)!important;border-radius:12px!important;
  padding:14px 16px!important;box-shadow:var(--k-shadow);border-left:4px solid var(--k-border)!important}
[class*="st-key-card-ok-"]{border-left-color:#0B7A66!important}
[class*="st-key-card-warn-"]{border-left-color:#B07A12!important;background:#FFFDF6!important}
[class*="st-key-card-bad-"]{border-left-color:#A1301C!important;background:#FFFBFA!important}
[class*="st-key-card-neutral-"]{border-left-color:#7D8DA1!important;background:#FBFCFE!important}
[class*="st-key-card-info-"]{border-left-color:var(--k-blue)!important}
[class*="st-key-card-"][class*="-sel-"]{box-shadow:0 0 0 2px var(--k-gold),var(--k-shadow)!important}
.k-explain{font-size:13.5px;color:var(--k-muted);margin:0 0 2px}
.k-answer{font-size:16px;line-height:1.65;color:var(--k-text);white-space:pre-wrap}
.k-caveat{font-size:13.5px;color:var(--k-muted);line-height:1.5;margin:2px 0}
.k-caveat b{color:var(--k-text);font-weight:600}
.k-selected{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:#7A4B00}
.k-selected::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--k-gold)}
[class*="st-key-flag-"] button,[class*="st-key-sel-"] button{min-height:30px;padding:2px 10px;font-size:13px}
[class*="st-key-flag-"] button p,[class*="st-key-sel-"] button p{font-size:13px}

/* Evidence pane */
.k-evhead{padding:2px 0 4px}
.k-evq{font-size:15px;font-weight:600;color:var(--k-navy);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:0 0 6px}
.k-badges{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.k-src-title{font-size:15px;font-weight:600;color:var(--k-text);margin:2px 0 0}
.k-src-file{font-size:12.5px;color:var(--k-muted)}
.k-supports{font-size:13.5px;color:var(--k-muted);margin:2px 0}
.k-supports b{color:var(--k-text);font-weight:600}
[class*="st-key-src-"]{background:#fff;border:1px solid var(--k-border);border-radius:12px;padding:12px 14px;box-shadow:var(--k-shadow)}
.k-empty{padding:14px 16px;border:1px dashed #C5D3E3;border-radius:12px;color:var(--k-muted);font-size:14px;background:var(--k-page)}
.k-empty b{color:var(--k-text)}
.k-tl{list-style:none;margin:6px 0 0;padding:0 0 0 4px}
.k-tl li{position:relative;padding:0 0 14px 26px;margin:0}
.k-tl li::before{content:"";position:absolute;left:5px;top:6px;width:10px;height:10px;border-radius:50%;
  background:#fff;border:2px solid var(--k-blue)}
.k-tl li::after{content:"";position:absolute;left:10px;top:20px;bottom:0;width:2px;background:var(--k-border)}
.k-tl li:last-child::after{display:none}
.k-tl .k-kind{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--k-muted)}
.k-tl .k-step{font-size:14.5px;font-weight:600;color:var(--k-text)}
.k-tl .k-detail{font-size:13.5px;color:var(--k-muted);line-height:1.5;word-break:break-word}
.k-tl li.k-bad::before{border-color:#A1301C}
.k-tl li.k-ok::before{border-color:#0B7A66}
.k-checklist{list-style:none;margin:4px 0;padding:0}
.k-checklist li{padding:8px 10px;border:1px solid var(--k-border);border-radius:8px;margin:0 0 6px;font-size:14px;background:#fff}
.k-checklist li b{display:block;font-size:13px}
.k-note{font-size:13px;color:var(--k-muted);border-top:1px solid var(--k-border);padding-top:10px;margin-top:12px;line-height:1.55}
"""

def _style_sheet() -> str:
    tokens = {
        "NAVY": NAVY, "BLUE": BLUE, "BLUE_DARK": BLUE_DARK, "TINT": TINT, "GOLD": GOLD, "PAGE": PAGE,
        "CARD": CARD, "BORDER": BORDER, "TEXT": TEXT, "MUTED": MUTED, "ASK_TOP": ASK_TOP, "CONV_TOP": CONV_TOP,
    }
    rules = _RULES
    for name, value in tokens.items():
        rules = rules.replace(f"@{name}@", value)
    return f"<style>{rules}</style>"


CSS = _style_sheet()
