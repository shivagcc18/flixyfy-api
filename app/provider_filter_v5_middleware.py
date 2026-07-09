
# FLIXYFY_FILTERS_SPEED_AUDIT_FIX_V4
from __future__ import annotations
import hashlib, os, re, time
from typing import Any, Dict, Iterable, List, Optional, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception: pass
DATABASE_URL=os.getenv('DATABASE_URL')
SCHEMA_TTL=600; PLAN_TTL=600; RESPONSE_TTL=60
_SCHEMA_CACHE:Dict[str,Tuple[float,Any]]={}; _PLAN_CACHE:Dict[str,Tuple[float,Any]]={}; _RESPONSE_CACHE:Dict[str,Tuple[float,Dict[str,Any]]]={}
ALIASES={"":"all","all":"all","all provider":"all","all providers":"all","youtube":"youtube","yt":"youtube","you tube":"youtube","netflix":"netflix","prime":"prime_video","prime video":"prime_video","primevideo":"prime_video","prime_video":"prime_video","amazon prime":"prime_video","amazon prime video":"prime_video","jiohotstar":"jiohotstar","jio hotstar":"jiohotstar","hotstar":"jiohotstar","zee5":"zee5","zee 5":"zee5","sonyliv":"sonyliv","sony liv":"sonyliv","aha":"aha","sunnxt":"sun_nxt","sun nxt":"sun_nxt","sun_nxt":"sun_nxt","etvwin":"etv_win","etv win":"etv_win","etv_win":"etv_win","mxplayer":"mx_player","mx player":"mx_player","mx_player":"mx_player","apple tv":"apple_tv_store","apple_tv":"apple_tv_store","apple tv store":"apple_tv_store","amazon video":"amazon_video","amazon_video":"amazon_video","google tv":"google_tv","google_tv":"google_tv","disney":"disney_plus","disney+":"disney_plus","disney plus":"disney_plus","hulu":"hulu","max":"max","hbo max":"max","plex":"plex","viki":"viki","rakuten viki":"viki","kocowa":"kocowa","tving":"tving","wavve":"wavve","watcha":"watcha","tubi":"tubi_tv","tubi tv":"tubi_tv"}
LABEL={"youtube":"YouTube","netflix":"Netflix","prime_video":"Prime Video","jiohotstar":"JioHotstar","zee5":"ZEE5","sonyliv":"SonyLIV","aha":"Aha","sun_nxt":"Sun NXT","etv_win":"ETV Win","mx_player":"MX Player","apple_tv_store":"Apple TV","amazon_video":"Amazon Video","google_tv":"Google TV","disney_plus":"Disney+","hulu":"Hulu","max":"Max","plex":"Plex","viki":"Rakuten Viki","kocowa":"Kocowa","tving":"TVING","wavve":"Wavve","watcha":"Watcha","tubi_tv":"Tubi"}
BASE={
 'current':{'label':'Indian Movies','prefix':'/movie/','content':['current_movie_serving_v5_backend_compat','current_movie_serving_v5','media_serving_v8_expanded','media_serving_v7_final'],'availability':['current_availability_serving_v5','provider_availability_serving_v2','provider_availability_serving_v1','ott_availability_normalized_v2','ott_availability_normalized_v1']},
 'hollywood':{'label':'Global Movies','prefix':'/hollywood/','content':['hollywood_movie_serving_v5','hollywood_movie_serving_v5_backend_compat','hollywood_card_serving_v3','hollywood_serving_v3','hollywood_movie_serving_v1'],'availability':['hollywood_availability_serving_v5','hollywood_availability_serving_v3','hollywood_availability_serving_v2','hollywood_availability_serving_v1','provider_availability_serving_v2','provider_availability_serving_v1']},
 'historical':{'label':'Historical Movies','prefix':'/historical/','content':['historical_movie_serving_v5','historical_movie_serving_v5_backend_compat','historical_card_serving_v1','historical_serving_v2','historical_serving_v1'],'availability':['historical_availability_serving_v5','historical_availability_serving_v3','historical_availability_serving_v2','historical_availability_v2','provider_availability_serving_v2','provider_availability_serving_v1']},
 'webseries':{'label':'Webseries','prefix':'/webseries/','content':['webseries_series_serving_v5','webseries_serving_v5','webseries_card_serving_v1','webseries_serving_v1'],'availability':['webseries_availability_serving_v5','webseries_availability_serving_v3','webseries_availability_serving_v2','webseries_availability_serving_v1','webseries_availability_v1','provider_availability_serving_v2','provider_availability_serving_v1']}}
PATH_DOMAIN={'/api/v3/movies':'current','/api/v3/indian':'current','/api/v3/hollywood':'hollywood','/api/v3/global':'hollywood','/api/v3/historical':'historical','/api/v3/webseries':'webseries','/api/v3/web-series':'webseries','/api/v3/series':'webseries'}
def q(n:str)->str: return '"'+str(n).replace('"','""')+'"'
def clean(v:Any)->str: return re.sub(r'\s+',' ',re.sub(r'[_\-]+',' ',str(v or '').strip().lower().replace('+',' plus '))).strip()
def normp(v:Any)->str:
    r=clean(v); k=r.replace(' ','_'); return ALIASES.get(r,ALIASES.get(k,k or 'all'))
def needles(p:str)->List[str]:
    p=normp(p); vals={p,p.replace('_',' ')}
    for raw,m in ALIASES.items():
        if m==p and raw: vals.update([raw,raw.replace('_',' '),raw.replace(' ','_')])
    vals.update({'netflix':['netflix'],'prime_video':['prime','amazon prime','amazon_prime'],'jiohotstar':['hotstar','jiohotstar'],'zee5':['zee5','zee 5'],'sonyliv':['sony','sonyliv','sony liv'],'youtube':['youtube','youtu.be'],'sun_nxt':['sun nxt','sunnxt','sun_nxt'],'etv_win':['etv win','etvwin','etv_win'],'mx_player':['mx player','mxplayer','mx_player'],'apple_tv_store':['apple','itunes'],'amazon_video':['amazon video'],'google_tv':['google','google tv'],'disney_plus':['disney'],'max':['max','hbo'],'viki':['viki'],'kocowa':['kocowa'],'tving':['tving'],'wavve':['wavve'],'watcha':['watcha'],'tubi_tv':['tubi']}.get(p,[]))
    return sorted(x.lower() for x in vals if x)
def le(a,c): return f"LOWER(COALESCE(CAST({a}.{q(c)} AS TEXT),''))"
def se(a,c): return f"LOWER(regexp_replace(COALESCE(CAST({a}.{q(c)} AS TEXT),''), '[^a-zA-Z0-9]+', '_', 'g'))"
def conn():
    if not DATABASE_URL: raise RuntimeError('DATABASE_URL missing')
    return psycopg2.connect(DATABASE_URL,cursor_factory=RealDictCursor)
def cg(cache,key):
    it=cache.get(key); return it[1] if it and it[0]>time.time() else None
def cs(cache,key,val,ttl): cache[key]=(time.time()+ttl,val); return val
def cols(cur,t):
    h=cg(_SCHEMA_CACHE,'c:'+t)
    if h is not None: return h
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",[t])
    return cs(_SCHEMA_CACHE,'c:'+t,[r['column_name'] for r in cur.fetchall()],SCHEMA_TTL)
def count(cur,t):
    h=cg(_SCHEMA_CACHE,'n:'+t)
    if h is not None: return int(h)
    cur.execute(f'SELECT COUNT(*) AS c FROM public.{q(t)}')
    return int(cs(_SCHEMA_CACHE,'n:'+t,int((cur.fetchone() or {}).get('c') or 0),SCHEMA_TTL))
def existing(cur,names):
    out=[]
    for t in names:
        c=cols(cur,t)
        if c: out.append((t,c,count(cur,t)))
    return out
def pick(cands,cols):
    s=set(cols)
    for x in cands:
        if x in s: return x
    return None
def provider_sql(ac,p,params):
    p=normp(p)
    if p in ('','all'): return '1=1'
    pcols=['provider_key','provider','provider_name','provider_display_name','provider_label','ott_primary_key','ott_primary','platform','platform_name','source','source_name','watch_provider','watch_provider_name','provider_slug','provider_code']
    clauses=[]
    for c in pcols:
        if c not in ac: continue
        for n in needles(p):
            clauses.append(f'{le("a",c)} LIKE %s'); params.append('%'+n.replace('_',' ')+'%')
            clauses.append(f'{se("a",c)} LIKE %s'); params.append('%'+re.sub(r'[^a-z0-9]+','_',n.lower()).strip('_')+'%')
    if p=='youtube':
        for c in ['final_url','watch_url','youtube_url','video_url','url','deep_link']:
            if c in ac: clauses.append(f'{le("a",c)} LIKE %s'); params.append('%youtube%')
    return '('+' OR '.join(clauses)+')' if clauses else '1=0'
def availability_sql(ac,a,params):
    v=clean(a)
    if v in ('','all','all titles','all movies','all_titles'): return '1=1'
    if v in ('free','free to watch','free_to_watch','youtube'): return provider_sql(ac,'youtube',params)
    if v in ('ott','streaming','available'):
        cs2=[c for c in ['provider_key','provider','provider_name','provider_display_name','platform'] if c in ac]
        if cs2: return '('+' OR '.join([f"NULLIF(TRIM(CAST(a.{q(c)} AS TEXT)),'') IS NOT NULL" for c in cs2])+')'
    return '1=1'
def guard(ac,domain):
    vals={'current':['current','indian','movie','movies'],'hollywood':['hollywood','global','global_movie','global_movies'],'historical':['historical','historical_movie','historical_movies'],'webseries':['webseries','web_series','series','tv']}.get(domain,[domain])
    for c in ['domain','content_domain','media_domain','source_domain','content_type','media_type']:
        if c in ac: return f"LOWER(CAST(a.{q(c)} AS TEXT)) IN ("+','.join("'"+x.replace("'","''")+"'" for x in vals)+')'
    return '1=1'
def joins(mc,ac):
    out=[]
    for m,a in [('slug','slug'),('slug','content_slug'),('slug','movie_slug'),('slug','series_slug'),('content_slug','content_slug'),('movie_slug','movie_slug'),('series_slug','series_slug'),('tmdb_id','tmdb_id'),('tmdb_id','content_tmdb_id'),('imdb_id','imdb_id'),('imdb_id','content_imdb_id'),('id','content_id'),('content_id','content_id')]:
        if m in mc and a in ac: out.append((f'{m}={a}',f"NULLIF(CAST(m.{q(m)} AS TEXT),'') IS NOT NULL AND CAST(a.{q(a)} AS TEXT)=CAST(m.{q(m)} AS TEXT)"))
    mt=pick(['title','name','series_title','original_title','movie_title'],mc); at=pick(['title','name','content_title','movie_title','series_title','original_title'],ac)
    my=pick(['release_year','year','start_year','first_air_year'],mc); ay=pick(['release_year','year','content_year','movie_year','start_year','first_air_year'],ac)
    if mt and at and my and ay:
        out.append(('title+year',f"LOWER(TRIM(CAST(a.{q(at)} AS TEXT)))=LOWER(TRIM(CAST(m.{q(mt)} AS TEXT))) AND CAST(a.{q(ay)} AS TEXT)=CAST(m.{q(my)} AS TEXT)"))
        out.append(('slugtitle+year',f"{se('a',at)}={se('m',mt)} AND CAST(a.{q(ay)} AS TEXT)=CAST(m.{q(my)} AS TEXT)"))
    if mt and at:
        out.append(('title',f"LOWER(TRIM(CAST(a.{q(at)} AS TEXT)))=LOWER(TRIM(CAST(m.{q(mt)} AS TEXT)))")); out.append(('slugtitle',f"{se('a',at)}={se('m',mt)}"))
    return out
def content_sql(mc,qp,params):
    clauses=[]; query=str(qp.get('q') or '').strip()
    if query:
        cs2=[c for c in ['title','name','series_title','original_title','movie_title'] if c in mc]
        if cs2: clauses.append('('+' OR '.join([f'm.{q(c)} ILIKE %s' for c in cs2])+')'); params.extend(['%'+query+'%']*len(cs2))
    y=str(qp.get('year') or '').strip(); yc=pick(['release_year','year','start_year','first_air_year'],mc)
    if y and yc: clauses.append(f'CAST(m.{q(yc)} AS TEXT)=%s'); params.append(y)
    lang=str(qp.get('language') or qp.get('language_slug') or '').strip().lower().replace('_','-')
    if lang:
        lc=[c for c in ['language_slug','primary_language_slug','primary_language','language','original_language'] if c in mc]
        if lc: clauses.append('('+' OR '.join([f"LOWER(REPLACE(CAST(m.{q(c)} AS TEXT),'_','-'))=%s" for c in lc])+')'); params.extend([lang]*len(lc))
    return ' AND '.join(clauses) if clauses else '1=1'
def order_sql(mc,sort):
    s=clean(sort or 'popular'); arr=['release_date','release_year','year','created_at'] if s=='latest' else ['imdb_rating','rating','vote_average','tmdb_rating','flixyfy_score','popularity'] if s in ('rating','top','imdb') else ['flixyfy_score','popularity','vote_count','imdb_rating','rating','release_year','year','start_year']
    cs2=[c for c in arr if c in mc]; return ', '.join([f'm.{q(c)} DESC NULLS LAST' for c in cs2]) if cs2 else '1'
def choose(cur,domain,p):
    p=normp(p); key=f'plan:{domain}:{p}'; h=cg(_PLAN_CACHE,key)
    if h is not None: return h
    cfg=BASE[domain]; best=None; besthit=-1
    for ct,mc,_ in existing(cur,cfg['content']):
        for at,ac,_ in existing(cur,cfg['availability']):
            dg=guard(ac,domain); pp=[]; ps=provider_sql(ac,p,pp)
            for jn,js in joins(mc,ac):
                try:
                    cur.execute(f'SELECT 1 FROM public.{q(ct)} m WHERE EXISTS (SELECT 1 FROM public.{q(at)} a WHERE {js} AND {dg} AND {ps}) LIMIT 1',pp)
                    hit=1 if cur.fetchone() else 0
                except Exception:
                    cur.connection.rollback(); hit=-1
                if hit>besthit:
                    besthit=hit; best={'domain':domain,'content_table':ct,'content_cols':mc,'availability_table':at,'availability_cols':ac,'domain_guard_sql':dg,'join_name':jn,'join_sql':js,'probe_count':hit}
                if hit==1: return cs(_PLAN_CACHE,key,best,PLAN_TTL)
    if not best: raise RuntimeError('no provider plan')
    return cs(_PLAN_CACHE,key,best,PLAN_TTL)
def infer_domain(path,qp):
    if path in PATH_DOMAIN: return PATH_DOMAIN[path]
    if path not in ('/api/v3/global-search','/api/v3/search'): return None
    txt=clean(' '.join(str(qp.get(k) or '') for k in ['type','content_type','contentType','media_type','tab','scope','domain','category']))
    if 'webseries' in txt or 'web series' in txt or 'series' in txt: return 'webseries'
    if 'historical' in txt: return 'historical'
    if 'global' in txt or 'hollywood' in txt: return 'hollywood'
    if 'indian' in txt or 'current' in txt or 'movie' in txt: return 'current'
    if qp.get('provider') or qp.get('provider_key'): return 'current'
    return None
def cors(request):
    origin=request.headers.get('origin') or 'https://flixyfy.com'; allowed={'https://flixyfy.com','https://www.flixyfy.com','https://flixyfy-web.vercel.app','http://localhost:5173','http://127.0.0.1:5173','http://localhost:3000','http://127.0.0.1:3000'}
    if origin not in allowed: origin='https://flixyfy.com'
    return {'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Credentials':'true','Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'*','Vary':'Origin','X-Flixyfy-Provider-Filter':'speed-v4'}
def enrich(rows,domain,p):
    cfg=BASE[domain]; p=normp(p); lab=LABEL.get(p); out=[]
    for r in rows:
        it=dict(r); slug=it.get('slug') or it.get('content_slug') or it.get('movie_slug') or it.get('series_slug')
        if slug and not it.get('url_path'): it['url_path']=cfg['prefix']+str(slug)
        it.setdefault('domain',domain)
        if p and p!='all': it['ott_primary_key']=p; it['ott_primary']=lab or p.replace('_',' ').title(); it['has_ott']=True
        out.append(it)
    return out
class ProviderFilterV5Middleware(BaseHTTPMiddleware):
    async def dispatch(self,request:Request,call_next):
        if request.method=='OPTIONS': return JSONResponse({'ok':True},headers=cors(request))
        if request.method!='GET': return await call_next(request)
        path=request.url.path.rstrip('/'); domain=infer_domain(path,request.query_params)
        if not domain: return await call_next(request)
        provider=normp(request.query_params.get('provider') or request.query_params.get('provider_key') or ''); av=clean(request.query_params.get('availability') or request.query_params.get('has_ott') or '')
        if av in ('free','free to watch','free_to_watch','youtube'): provider='youtube'
        if provider in ('','all') and av in ('','all','all titles','all movies'): return await call_next(request)
        ck=hashlib.sha256((path+'?'+str(request.url.query)+'|d='+domain+'|p='+provider+'|a='+av).encode()).hexdigest(); hit=cg(_RESPONSE_CACHE,ck)
        if hit is not None: return JSONResponse(hit,headers={**cors(request),'X-Flixyfy-Cache':'HIT'})
        try:
            data=self.query(domain,request.query_params,provider,av); cs(_RESPONSE_CACHE,ck,data,RESPONSE_TTL); return JSONResponse(data,headers={**cors(request),'X-Flixyfy-Cache':'MISS'})
        except Exception as e:
            resp=await call_next(request); resp.headers.setdefault('X-Flixyfy-Provider-Filter-Fallback',type(e).__name__); return resp
    def query(self,domain,qp,provider,av):
        page=max(1,int(qp.get('page') or 1)); limit=max(1,min(100,int(qp.get('limit') or 24))); off=(page-1)*limit
        with conn() as db:
            with db.cursor(cursor_factory=RealDictCursor) as cur:
                plan=choose(cur,domain,provider); mc=plan['content_cols']; ac=plan['availability_cols']
                cp=[]; csql=content_sql(mc,qp,cp); pp=[]; psql=provider_sql(ac,provider,pp); ap=[]; asql=availability_sql(ac,av,ap)
                exists=f"EXISTS (SELECT 1 FROM public.{q(plan['availability_table'])} a WHERE {plan['join_sql']} AND {plan['domain_guard_sql']} AND {psql} AND {asql})"; params=cp+pp+ap
                cur.execute(f"SELECT COUNT(*) AS total FROM public.{q(plan['content_table'])} m WHERE {csql} AND {exists}",params); total=int((cur.fetchone() or {}).get('total') or 0)
                cur.execute(f"SELECT m.* FROM public.{q(plan['content_table'])} m WHERE {csql} AND {exists} ORDER BY {order_sql(mc,qp.get('sort') or 'popular')} LIMIT %s OFFSET %s",params+[limit,off]); rows=[dict(r) for r in cur.fetchall()]
        items=enrich(rows,domain,provider); cfg=BASE[domain]
        return {'page':page,'limit':limit,'total':total,'items':items,'results':items,'domain':domain,'label':cfg['label'],'provider':normp(provider),'availability':av,'source':'provider_filter_speed_v4','provider_plan':{'content_table':plan['content_table'],'availability_table':plan['availability_table'],'join':plan['join_name'],'probe_count':plan['probe_count']}}
def install_provider_filter_v5_middleware(app): return app
