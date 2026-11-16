"""Deterministic data-only classification. Never execute post text or visit links."""
import datetime,re
from urllib.parse import urlparse
MINT=r'(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])'
def fields(p):
 t=p['text'];plain=re.sub(r'https?://[^\s<>]+',' ',t);m=sorted(set(re.findall(MINT,plain)));pairs=[]
 for link in p.get('links',[]):
  u=urlparse(link);parts=u.path.strip('/').split('/')
  if len(parts)==2 and re.fullmatch(MINT,parts[1]):
   if u.hostname=='solscan.io' and parts[0]=='token':m.append(parts[1])
   if u.hostname=='dexscreener.com' and parts[0]=='solana':pairs.append(parts[1])
 m=sorted(set(m));tick=sorted(set(x.upper() for x in re.findall(r'\$([A-Za-z][A-Za-z0-9]{1,14})\b',t)))
 neg=bool(re.search(r"\b(don.t|do not|not|never|avoid|no|would|could|might|if|maybe)\b",t,re.I))
 recap=bool(re.search(r'\b(recap|yesterday|previous|earlier|called|bought|was|ago|last week)\b|\b\d+(?:\.\d+)?\s*x\b|\bto\s*\$?\d+(?:\.\d+)?\s*[mk]\b',t,re.I))
 sell=bool(re.search(r'\b(take profits?|taking profits?|trim|trimmed|sell|sold|exit|exited|close|closed)\b',t,re.I))
 kind='observe'
 if not neg:
  if sell:kind='exit' if re.search(r'\b(exit|exited|close|closed|sell all|sold all)\b',t,re.I) else 'trim'
  elif not recap and (re.search(r'\b(buy|buying|entry|added|adding)\b',t,re.I) or (len(tick)==1 and re.search(r'\b(looks free to me|looks bottomed out|chart has bottomed out|send (?:this|it))\b',t,re.I))):kind='call'
 return dict(kind=kind,mints=m,tickers=tick,negated=neg,recap=recap,pairs=sorted(set(pairs)))
def epoch(p):return datetime.datetime.fromisoformat(p['timestamp']).timestamp()
MAX_CHAIN_DEPTH=5
TICKER_LOOKBACK_SECONDS=30*86400
def chain_identity(p,ticker,history,depth=MAX_CHAIN_DEPTH):
 """Walk reply_to links up to `depth` hops for IDENTITY ONLY (never a new trade).
 Returns (mint,pair,context_ids) or None. Any conflicting ticker or multiple identities -> None."""
 if not ticker:return None
 cur=p;seen={p['id']} if 'id' in p else set();ctx=[]
 for _ in range(depth):
  rid=cur.get('reply_to')
  if not rid or rid in seen:return None
  seen.add(rid);parent=history.get(rid)
  # Parent not stored: fall back to the quoted text embedded in the child (cannot walk further).
  if parent is None:parent=dict(id=rid,text=cur.get('reply_text') or '',links=[],reply_to=None)
  pf=fields(parent);ctx.append(rid)
  if pf['tickers'] and pf['tickers']!=[ticker]:return None
  if len(pf['mints'])>1 or len(pf['pairs'])>1 or (pf['mints'] and pf['pairs']):return None
  if pf['mints'] or pf['pairs']:
   if pf['tickers']!=[ticker]:return None # mint-bearing ancestor must name the same ticker
   return (pf['mints'][0] if pf['mints'] else None,pf['pairs'][0] if pf['pairs'] else None,ctx)
  cur=parent
 return None
def ticker_identity(p,ticker,history):
 """$TICKER lookup against EARLIER stored posts naming exactly that ticker plus one mint.
 Identity only; all matches must agree on a single mint, else None."""
 if not ticker:return None
 try:t0=epoch(p)
 except Exception:t0=None
 found={}
 for hid,h in history.items():
  if hid>=p.get('id',0):continue
  try:
   if t0 is not None and t0-epoch(h)>TICKER_LOOKBACK_SECONDS:continue
  except Exception:pass
  hf=fields(h)
  if hf['tickers']==[ticker] and len(hf['mints'])==1:found.setdefault(hf['mints'][0],[]).append(hid)
 if len(found)!=1:return None
 mint,ids=found.popitem();return (mint,sorted(ids)[-3:])
def plan(posts,eligible,now,history=None):
 """eligible contains ONLY first-seen forward IDs; baseline/edits never eligible.
 history: id->post of any stored posts (incl. baseline), used for identity lookup only."""
 out=[];used=set();history=dict(history or {})
 for x in posts:history.setdefault(x['id'],x)
 for p in posts:
  if p['id'] not in eligible or not 0<=now-epoch(p)<=120:continue
  f=fields(p)
  if f['kind'] in ('exit','trim'):
   if len(f['mints'])<=1 and len(f['tickers'])<=1:out.append(dict(kind=f['kind'],ids=[p['id']],mint=f['mints'][0] if f['mints'] else None,ticker=f['tickers'][0] if f['tickers'] else None))
   continue
  if f['kind']!='call' or len(f['tickers'])>1 or len(f['mints'])>1:continue
  ids=[p['id']];mint=f['mints'][0] if f['mints'] else None;context=[];pair=None
  if len(f['pairs'])>1 or (f['pairs'] and mint):continue
  if f['pairs']:pair=f['pairs'][0]
  ticker=f['tickers'][0] if len(f['tickers'])==1 else None
  if not mint and not pair and p.get('reply_to'):
   r=chain_identity(p,ticker,history) # multi-level reply chain, identity evidence ONLY
   if r:mint,pair,context=r
  if not mint and not pair:
   # Reply linkage first; otherwise strictly adjacent IDs within 30 seconds.
   replies=[x for x in posts if x['id'] in eligible and (x.get('reply_to')==p['id'] or p.get('reply_to')==x['id']) and abs(epoch(x)-epoch(p))<=120]
   near=replies or [x for x in posts if x['id'] in eligible and abs(x['id']-p['id'])==1 and abs(epoch(x)-epoch(p))<=30]
   companions=[]
   for x in near:
    xf=fields(x)
    # Only a bare mint / CA label allowed as companion, not a second recommendation.
    rem=re.sub(MINT,'',x['text']).strip()
    if len(xf['mints'])==1 and re.fullmatch(r'(?i)(?:CA|contract|mint|address|solana|sol|[\s:;=-])*',rem):companions.append(x)
   # A nearby competing call makes implicit association ambiguous.
   competitors=[x for x in posts if x['id']!=p['id'] and x['id'] in eligible and abs(x['id']-p['id'])<=2 and abs(epoch(x)-epoch(p))<=30 and fields(x)['kind']=='call']
   if len(companions)==1 and (replies or not competitors):
    x=companions[0];mint=fields(x)['mints'][0];ids.append(x['id'])
   elif not companions:
    r=ticker_identity(p,ticker,history) # earlier post naming same $TICKER with one mint
    if not r:continue
    mint,context=r
   else:continue
  if any(i in used for i in ids):continue
  used.update(ids);out.append(dict(kind='call',ids=sorted(ids),mint=mint,ticker=f['tickers'][0] if f['tickers'] else None,context_ids=context,pair=pair))
 return out
