import datetime,fcntl,html,json,math,os,re,signal,time,urllib.request
from pathlib import Path
import signals,hashlib
RULE_VERSION=4
ROOT=Path(__file__).resolve().parent
PREVIEW='https://t.me/s/cryptotalkwithfrog'
PROBE='Bv1a7cEj5aRnJQhMw4kRQqAFesMSd5LwcDnUzq52BMmr'
RULES=dict(experimental=True,bankroll=25,bet_total_cost=6,reserve=7,max_positions=3,max_coin_cumulative=9,initial_stop=.5,first_partial_at=1.5,first_fraction=1/3,trail=.2,recover_at=2,second_at=3,second_fraction=.2,significant_runner_value=6,significant_fraction=.5,preview_seconds=60,new_monitor_seconds=15,mature_monitor_seconds=60,mature_after_seconds=3600,fee=.003,slippage=.01,gas_usd=.01,min_liquidity=100000,min_volume24=10000,max_top1=.10,max_top10=.40,max_quote_age_seconds=30,max_new_post_age_seconds=120,adds_enabled=False,all_in_enabled=False,global_loss_limit=None)
def stamp():return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).isoformat()
def get(url):
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'PublicPaperResearch/1.0'}),timeout=10) as r:return r.read().decode()
def event(kind,**kw):
 with (ROOT/'events.jsonl').open('a') as f:f.write(json.dumps(dict(time=stamp(),kind=kind,**kw))+'\n')
def parse(s):
 out=[]
 for part in re.split(r'(?=<div class="tgme_widget_message_wrap)',s):
  mid=re.search(r'data-post="cryptotalkwithfrog/(\d+)"',part)
  dt=re.search(r'<time[^>]*datetime="([^"]+)"',part)
  if not mid or not dt:continue
  texts=re.findall(r'<div class="tgme_widget_message_text(?![^>]*js-message_reply_text)[^>]*>(.*?)</div>',part,re.S)
  reply=re.search(r'class="tgme_widget_message_reply[^>]*href="https://t.me/cryptotalkwithfrog/(\d+)"',part)
  text=' '.join(html.unescape(re.sub('<[^>]+>',' ',t)) for t in texts)
  quotes=re.findall(r'<div class="tgme_widget_message_text js-message_reply_text"[^>]*>(.*?)</div>',part,re.S)
  reply_text=' '.join(html.unescape(re.sub('<[^>]+>',' ',x)) for x in quotes)
  own=part[part.find('</a>',part.find('tgme_widget_message_reply'))+4:] if reply else part
  links=[html.unescape(x) for x in re.findall(r'href="(https?://[^"]+)"',own) if any(h in x for h in ('dexscreener.com/','solscan.io/'))]
  fingerprint=hashlib.sha256(json.dumps([text,reply_text,links]).encode()).hexdigest()
  out.append(dict(id=int(mid[1]),timestamp=dt[1],text=text,reply_to=int(reply[1]) if reply else None,reply_text=reply_text,links=links,fingerprint=fingerprint,edited=('edited &nbsp;' in part),media_flags=dict(photo='tgme_widget_message_photo_wrap' in own,video='tgme_widget_message_video' in own,unsupported='Please open Telegram to view this post' in own)))
 return sorted(out,key=lambda p:p['id'])
def identity(text):
 m=re.findall(r'(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])',text)
 if len(set(m))!=1:return None
 if not re.search(r'\b(solana|sol)\b',text,re.I):return None
 if not re.search(r'\b(buy|bought|call|entry|added|bag)\b',text,re.I):return None
 return m[0]
def market(mint):
 data=json.loads(get('https://api.dexscreener.com/token-pairs/v1/solana/'+mint))
 eligible=[p for p in data if p.get('chainId')=='solana' and p.get('baseToken',{}).get('address')==mint and p.get('quoteToken',{}).get('symbol') in ('SOL','USDC','USDT') and float(p.get('priceUsd',0))>0]
 if not eligible:raise ValueError('no_exact_mint_market')
 p=max(eligible,key=lambda x:x.get('liquidity',{}).get('usd',0));price=float(p['priceUsd']);liq=float(p.get('liquidity',{}).get('usd',0))
 if not math.isfinite(price) or not math.isfinite(liq):raise ValueError('invalid_market')
 return dict(price=price,liquidity=liq,volume=float(p.get('volume',{}).get('h24',0)),pool=p['pairAddress'],observed=time.time(),freshness='request-time only; provider quote timestamp unavailable')
def resolve_pair(pair):
 d=json.loads(get('https://api.dexscreener.com/latest/dex/pairs/solana/'+pair))
 ps=[p for p in d.get('pairs',[]) if p.get('chainId')=='solana' and p.get('pairAddress')==pair]
 m={p.get('baseToken',{}).get('address') for p in ps}
 if len(m)!=1 or None in m:raise ValueError('ambiguous_or_missing_pair_identity')
 return m.pop()
def security(mint):
 d=json.loads(get('https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses='+mint))
 if d.get('code')!=1 or not d.get('result',{}).get(mint):raise ValueError('missing_safety')
 return d['result'][mint]
def safety_errors(q,d):
 reasons=[]
 if not q or time.time()-q.get('observed',0)>30:return ['missing_or_stale_market']
 if not all(math.isfinite(float(q.get(k,0))) and float(q.get(k,0))>0 for k in ('price','liquidity','volume')):return ['invalid_market_fields']
 if q['liquidity']<RULES['min_liquidity'] or q['volume']<RULES['min_volume24']:reasons.append('illiquid')
 if not d:return reasons+['missing_safety']
 for field in ('mintable','freezable','balance_mutable_authority','closable','default_account_state_upgradable','transfer_fee_upgradable','transfer_hook_upgradable'):
  if d.get(field,{}).get('status')!='0':reasons.append('unsafe_or_missing_'+field)
 if d.get('non_transferable',d.get('none_transferable'))!='0':reasons.append('transfer_restricted_or_unknown')
 if d.get('default_account_state')!='1':reasons.append('account_state_unknown')
 if 'transfer_hook' not in d or d['transfer_hook']!=[]:reasons.append('hooks_or_unknown')
 # Fail closed on fee configurations until fully supported, not assumed zero.
 if 'transfer_fee' not in d or d['transfer_fee']!={}:reasons.append('transfer_fee_or_unknown')
 hs=d.get('holders')
 try:
  vals=[float(h['percent']) for h in hs]
  if len(vals)<10 or not all(math.isfinite(v) and 0<=v<=1 for v in vals):raise ValueError()
  if max(vals)>.1 or sum(vals)>.4:reasons.append('holder_concentration')
 except (TypeError,KeyError,ValueError):reasons.append('missing_holder_concentration')
 return reasons
# Cost model: $6 includes entry gas+fee+slippage; fixed conservative estimates, not swap quotes.
def proceeds(units,price):return max(0,units*price*(1-RULES['slippage'])*(1-RULES['fee'])-RULES['gas_usd'])
def open_position(s,mint,q,d):
 if safety_errors(q,d):return False
 if len([p for p in s['positions'] if p['units']>1e-12])>=3 or s['cash']-6<7:return False
 if mint in s['committed']:return False # reentry disabled: cannot fit another $6 inside $9 cumulative
 price=q['price'];units=(6-.01)/(price*1.01*1.003)
 p=dict(rule_version=RULE_VERSION,symbol=d.get('metadata',{}).get('symbol','').upper(),mint=mint,entry=price,original=units,units=units,cost=6,allocated=0,realized=0,net_proceeds=0,peak=price,stop=price*.5,first=False,recovered=False,second=False,significant=False,opened=time.time(),last_check=0,last_price=price,blocked=False)
 s['positions'].append(p);s['cash']-=6;s['committed'][mint]=6;return True
def sell(s,p,units,price,reason):
 u=min(max(0,units),p['units'])
 if u<=1e-12:return
 net=proceeds(u,price);basis=p['cost']*u/p['original'];p['units']-=u;p['allocated']+=basis;p['net_proceeds']+=net;p['realized']+=net-basis;s['cash']+=net
 event('paper_sell',mint=p['mint'],units=u,price=price,net=net,basis=basis,reason=reason)
def manage(s,p,q,d):
 errors=safety_errors(q,d)
 if errors:p['blocked']=True;event('exit_blocked_no_phantom_fill',mint=p['mint'],reasons=errors);return
 p['blocked']=False;price=q['price'];p['last_price']=price;p['peak']=max(p['peak'],price)
 # Existing stop checked first; fill at observed price, never at stale stop price.
 if price<=p['stop']:sell(s,p,p['units'],price,'stop_observed_gap_price');return
 if not p['first'] and price>=p['entry']*1.5:
  sell(s,p,p['original']/3,price,'plus50_one_third_original');p['first']=True
 if not p['recovered'] and price>=p['entry']*2:
  need=max(0,p['cost']-p['net_proceeds'])
  if need>0:sell(s,p,(need+.01)/(price*.99*.997),price,'2x_recover_total_cost_less_prior_net_proceeds')
  p['recovered']=True
 if not p['second'] and price>=p['entry']*3:
  sell(s,p,p['original']*.2,price,'3x_twenty_percent_original');p['second']=True
 if p['first']:p['stop']=max(p['stop'],p['peak']*.8)
 # Runner is remainder after recovery AND 3x distribution, not the initial position.
 if p['recovered'] and p['second'] and not p['significant'] and p['units']*price>=6:
  sell(s,p,p['units']/2,price,'significant_runner_half_once');p['significant']=True
 # Only the existing post-+50% 20% trail can raise the stop (no +30% break-even rule; removed Nov 9 2026).
 if p['units']>1e-12 and price<=p['stop']:sell(s,p,p['units'],price,'trail_after_plus50')
def add_allowed(s,p,amount,metrics):
 return bool(RULES['adds_enabled'] and metrics.get('booked_profit') and metrics.get('healthy_pullback') and metrics.get('buying_strengthens') and metrics.get('stable_liquidity') and metrics.get('no_holder_dump') and s['committed'].get(p['mint'],0)+amount<=9 and s['cash']-amount>=7)
def initial():return dict(version=1,cash=25.,positions=[],committed={},seen=[],baseline=False,last_preview=0,preview_ok=False,last_probe=0)
def save(s):
 tmp=ROOT/'state.tmp';tmp.write_text(json.dumps(s));os.replace(tmp,ROOT/'state.json')
def status(s):
 marks=sum(proceeds(p['units'],p['last_price']) for p in s['positions'] if p['units']>1e-12)
 realized=sum(p['realized'] for p in s['positions']);basis=sum(p['cost']-p['allocated'] for p in s['positions'] if p['units']>1e-12)
 if s['cash']+marks>=250 and not s.get('target_reached'):
  s['target_reached']=stamp();event('estimated_equity_milestone',target=250,equity=s['cash']+marks,realized=realized,not_cash_guarantee=True)
 v=dict(time=stamp(),pid=os.getpid(),mode='forward_paper_only',cash=s['cash'],net_equity_estimated=s['cash']+marks,realized=realized,unrealized_estimated=marks-basis,positions=sum(p['units']>1e-12 for p in s['positions']),seen=len(s['seen']),preview_ok=s['preview_ok'],last_preview=s['last_preview'],probe=s.get('probe'),entry_readiness='conditional: fresh direct/safely linked mint calls; exact Solana market and safety metadata identity verification; all gates required; no executable sell quote',adds='disabled: no flow/holder history',global_loss_limit='not specified',quote_limit='provider quote timestamps unavailable; marks are estimates',local_alerts_only=True,rule_version=RULE_VERSION,target_equity=250,target_progress=(s['cash']+marks)/250,completed_positions=sum(p['units']<=1e-12 for p in s['positions']),started=s.get('started'),last_new_post=s.get('last_new_post'),entry_count=len(s['positions']))
 tmp=ROOT/'status.tmp';tmp.write_text(json.dumps(v,indent=2));os.replace(tmp,ROOT/'status.json')
def main():
 lock=(ROOT/'sim.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 (ROOT/'sim.pid').write_text(str(os.getpid()))
 (ROOT/'rules.json').write_text(json.dumps(RULES,indent=2))
 s=json.loads((ROOT/'state.json').read_text()) if (ROOT/'state.json').exists() else initial()
 running=True
 def stop(*_):
  nonlocal running
  running=False
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 if 'started' not in s:
  with (ROOT/'events.jsonl').open() as f:
   first=next((json.loads(line)['time'] for line in f if json.loads(line).get('kind')=='start'),stamp())
  s['started']=first
 if s.get('rule_version')!=RULE_VERSION:s['last_probe']=0;s['last_preview']=0
 s['rule_version']=RULE_VERSION
 event('start',rules=RULES,version=RULE_VERSION)
 while running:
  now=time.time()
  if now-s['last_preview']>=60:
   try:
    posts=parse(get(PREVIEW))
    if not posts:raise ValueError('empty_preview')
    ids={p['id'] for p in posts}
    fingerprints=s.setdefault('fingerprints',{})
    context=s.setdefault('context',{})
    for post in posts:
     # Identity-only context for reply-chain / $TICKER mint lookup; first-seen text kept, never replayed.
     context.setdefault(str(post['id']),{k:post.get(k) for k in ('id','timestamp','text','reply_to','reply_text','links')})
    for k in sorted(context,key=int)[:-400]:del context[k]
    for post in posts:
     key=str(post['id']);previous=fingerprints.get(key)
     if previous and previous!=post['fingerprint']:event('post_edit_diagnostic_only_no_replay',id=post['id'],evidence=post)
     fingerprints[key]=post['fingerprint']
    event('preview_delivery',count=len(posts),newest_id=max(ids))
    if not s['baseline']:s['seen']=sorted(ids);s['baseline']=True;event('baseline_no_backfill',ids=s['seen'])
    else:
     old=set(s['seen']);new=[p for p in posts if p['id'] not in old]
     if old and min(ids)>max(old)+1:event('history_gap',last=max(old),first=min(ids))
     # Persist only first-seen FORWARD posts as linkable pending context; baseline is excluded.
     pending=s.setdefault('pending',[])
     for post in new:
      s['seen']=sorted(set(s['seen'])|{post['id']});pending.append(post);s['last_new_post']=post['timestamp']
      event('new_post_classified',id=post['id'],fields=signals.fields(post),evidence=post,age_seconds=now-signals.epoch(post),fresh_entry_eligible=(0<=now-signals.epoch(post)<=120))
     pending[:]=[p for p in pending if 0<=now-signals.epoch(p)<=120]
     save(s)
     actions=signals.plan(pending,{p['id'] for p in pending},now,history={int(k):v for k,v in s.get('context',{}).items()})
     linked={i for a in actions for i in a['ids']}
     for post in new:
      if post['id'] not in linked:event('skip_or_pending_link',id=post['id'],reason='no_unambiguous_fresh_whitelisted_action')
     for a in actions:
      key=','.join(map(str,a['ids']))+':'+a['kind']
      if key in s.setdefault('processed_actions',[]):continue
      s['processed_actions'].append(key);save(s) # at-most-once, edit-safe across restart
      try:
       if a['kind']=='call':
        if not a['mint'] and a.get('pair'):a['mint']=resolve_pair(a['pair'])
        q=market(a['mint']);d=security(a['mint']);errors=safety_errors(q,d)
        symbol=d.get('metadata',{}).get('symbol','').upper()
        if not symbol or (a['ticker'] and symbol!=a['ticker']):errors.append('authoritative_symbol_mismatch_or_missing')
        if errors:event('skip_candidate',ids=a['ids'],mint=a['mint'],reasons=errors)
        elif open_position(s,a['mint'],q,d):event('paper_entry',ids=a['ids'],context_ids=a.get('context_ids',[]),mint=a['mint'],total_cost=6,quote=q,reasons=['explicit_forward_call','bounded_link_or_direct','exact_mint_market','safety_gates_passed','metadata_symbol_verified'],rule_version=RULE_VERSION)
        else:event('skip_candidate',ids=a['ids'],reason='reserve_capacity_or_cumulative_coin_cap')
       else:
        matches=[p for p in s['positions'] if p['units']>1e-12 and ((a['mint'] and p['mint']==a['mint']) or (not a['mint'] and a['ticker'] and p.get('symbol')==a['ticker']))]
        if len(matches)!=1:event('skip_followup',ids=a['ids'],reason='no_unique_held_identity');continue
        p=matches[0]
        if a['ticker'] and p.get('symbol')!=a['ticker']:event('skip_followup',ids=a['ids'],reason='conflicting_mint_ticker');continue
        q=market(p['mint']);d=security(p['mint']);errors=safety_errors(q,d)
        if errors:event('followup_exit_blocked',ids=a['ids'],reasons=errors);continue
        # Only whitelist de-risk semantics: half trim or full exit. Never adds/hype buys.
        sell(s,p,p['units']*(1 if a['kind']=='exit' else .5),q['price'],'channel_'+a['kind']+'_independent_price')
        p['last_price']=q['price']
      except Exception as e:event('skip_action',ids=a['ids'],reason=type(e).__name__+':'+str(e))
    s['preview_ok']=True
   except Exception as e:s['preview_ok']=False;event('preview_error',error=str(e))
   s['last_preview']=time.time()
  # Fixed historical mint is a DATA HEALTH PROBE ONLY, never an entry or backtest.
  if now-s['last_probe']>=300:
   try:
    q=market(PROBE);d=security(PROBE);s['probe']=dict(time=stamp(),market=q,safety_errors=safety_errors(q,d),historical_mint_never_traded=True)
   except Exception as e:s['probe']=dict(time=stamp(),error=str(e))
   s['last_probe']=time.time()
  for p in s['positions']:
   interval=15 if not(p['recovered'] and now-p['opened']>=3600) else 60
   if p['units']>1e-12 and now-p['last_check']>=interval:
    try:manage(s,p,market(p['mint']),security(p['mint']))
    except Exception as e:p['blocked']=True;event('position_feed_gap_no_fill',mint=p['mint'],error=str(e))
    p['last_check']=time.time()
  save(s);status(s)
  for _ in range(5):
   if not running:break
   time.sleep(1)
 save(s);event('stopped')
if __name__=='__main__':main()
