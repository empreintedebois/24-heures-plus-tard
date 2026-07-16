#!/usr/bin/env python3
import csv, gzip, json, math, os, re, urllib.request
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'index.html'
OUT=ROOT/'index-20000.html'
REPORT=ROOT/'build-report.json'
CACHE=ROOT/'.imdb-cache'
CACHE.mkdir(exist_ok=True)
URLS={
 'basics':'https://datasets.imdbws.com/title.basics.tsv.gz',
 'ratings':'https://datasets.imdbws.com/title.ratings.tsv.gz',
 'akas':'https://datasets.imdbws.com/title.akas.tsv.gz',
}
TARGET_TOTAL=20000
TARGET_PER_HOUR=150
MIN_PER_HOUR=100
YEARS=(1990,2025)
TYPES={'movie','tvMovie','video','short','tvShort'}
FR=['zero','un','deux','trois','quatre','cinq','six','sept','huit','neuf','dix','onze','douze','treize','quatorze','quinze','seize','dix sept','dix huit','dix neuf','vingt','vingt et un','vingt deux','vingt trois','vingt quatre']
EN=['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen','twenty','twenty one','twenty two','twenty three','twenty four']
ROM=['','I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII','XIII','XIV','XV','XVI','XVII','XVIII','XIX','XX','XXI','XXII','XXIII','XXIV']

def download(name):
 p=CACHE/(name+'.tsv.gz')
 if not p.exists() or p.stat().st_size<1000:
  print('download',URLS[name],flush=True)
  urllib.request.urlretrieve(URLS[name],p)
 return p

def norm(s):
 import unicodedata
 s=''.join(c for c in unicodedata.normalize('NFD',str(s or '').lower().replace('’',"'").replace('–','-').replace('—','-')) if unicodedata.category(c)!='Mn')
 return re.sub(r'\s+',' ',s).strip()

def values(target): return [0,24] if target==0 else [target]

def relation(title,target):
 raw=str(title or ''); n=norm(raw)
 if target==7 and re.search(r'\bse7en\b',raw,re.I): return (1,raw,'graphie')
 for m in re.finditer(r'\d+',n):
  token=m.group(); val=int(token)
  if val in values(target):
   if m.end()==len(n) and n[:m.start()].rstrip(' :-'):
    return (3,raw,'opus')
   return (1,raw,'exact')
  if any(str(v) in token for v in values(target)):
   return (2,raw,'inclus')
 padded=' '+re.sub(r'[^a-z0-9]+',' ',n)+' '
 for v in values(target):
  words=[FR[v],EN[v]]+(['une'] if v==1 else [])
  for w in words:
   if ' '+w+' ' in padded: return (1,raw,'mot')
  rv=ROM[v]
  if rv:
   pat=(r'\b(?:part|episode|chapter)\s+'+re.escape(rv)+r'$') if v==1 else (r'(?:\b(?:part|episode|chapter)\s+|[\s:])'+re.escape(rv)+r'$')
   if re.search(pat,raw,re.I): return (3,raw,'opus')
 return None

def best_relation(names,target):
 best=None
 for title,kind in names:
  r=relation(title,target)
  if r:
   item=(r[0],r[1],kind,r[2])
   if best is None or (item[0],0 if kind=='principal' else 1,len(item[1])) < (best[0],0 if best[2]=='principal' else 1,len(best[1])): best=item
 return best

def score(m):
 r=m['rating']; v=m['votes']; quality=0
 if r>=5.5 and v>=100: quality=4
 elif r>=5.5 and v>=10: quality=3
 elif r>=4.0 and v>=100: quality=2
 else: quality=1
 return (quality,r+math.log10(v+10)*0.55,v,-len(m['title']))

def main():
 ratings={}
 with gzip.open(download('ratings'),'rt',encoding='utf-8',newline='') as f:
  next(f)
  for line in f:
   t,r,v=line.rstrip('\n').split('\t')
   r=float(r); v=int(v)
   if r>=4.0 and v>=10: ratings[t]=(r,v)
 print('ratings',len(ratings),flush=True)
 movies={}
 with gzip.open(download('basics'),'rt',encoding='utf-8',newline='') as f:
  rd=csv.DictReader(f,delimiter='\t')
  for row in rd:
   t=row['tconst']
   if t not in ratings or row['isAdult']!='0' or row['titleType'] not in TYPES: continue
   y=row['startYear']
   if not y.isdigit() or not (YEARS[0]<=int(y)<=YEARS[1]): continue
   r,v=ratings[t]
   movies[t]={'id':t,'title':row['primaryTitle'],'original':row['originalTitle'],'year':int(y),'rating':r,'votes':v,'genres':'' if row['genres']=='\\N' else row['genres'],'type':row['titleType'],'aliases':[]}
 print('eligible basics',len(movies),flush=True)
 # Keep only alternate titles that can support at least one strict hour.
 with gzip.open(download('akas'),'rt',encoding='utf-8',newline='') as f:
  rd=csv.DictReader(f,delimiter='\t')
  for row in rd:
   m=movies.get(row['titleId'])
   if not m or len(m['aliases'])>=12: continue
   title=row['title']
   if title==m['title'] or title==m['original']: continue
   if any(relation(title,h) for h in range(24)):
    region=row['region']; lang=row['language']; typ=row['types']
    priority=0 if region in {'FR','US','GB','CA','AU'} or 'imdbDisplay' in typ else 1
    m['aliases'].append((priority,title,region,lang))
 for m in movies.values(): m['aliases'].sort(key=lambda x:(x[0],len(x[1])))
 candidates=[[] for _ in range(24)]
 evidences={}
 for m in movies.values():
  names=[(m['title'],'principal')]
  if m['original']!=m['title']: names.append((m['original'],'original'))
  names += [(a[1],'alternatif') for a in m['aliases']]
  for h in range(24):
   b=best_relation(names,h)
   if b:
    candidates[h].append(m)
    evidences[(m['id'],h)]=b
 for h in range(24): candidates[h].sort(key=score,reverse=True)
 counts_all=[len(x) for x in candidates]
 print('candidate counts',counts_all,flush=True)
 if min(counts_all)<MIN_PER_HOUR:
  raise RuntimeError('Couverture insuffisante: '+str(counts_all))
 selected={}
 # Category coverage has priority.
 for h in range(24):
  for m in candidates[h][:TARGET_PER_HOUR]: selected[m['id']]=m
 # Fill with the best rated/popular films, preferring rating >= 5.5.
 all_movies=sorted(movies.values(),key=score,reverse=True)
 for m in all_movies:
  if len(selected)>=TARGET_TOTAL: break
  if m['rating']>=5.5: selected[m['id']]=m
 for m in all_movies:
  if len(selected)>=TARGET_TOTAL: break
  selected[m['id']]=m
 if len(selected)<TARGET_TOTAL: raise RuntimeError('Seulement %d films'%len(selected))
 chosen=list(selected.values())[:TARGET_TOTAL]
 chosen.sort(key=lambda m:(m['year'],m['title'],m['id']))
 pos={m['id']:i for i,m in enumerate(chosen)}
 title_index=[]; category_ids=set(); final_counts=[]
 for h in range(24):
  arr=[]
  for m in candidates[h]:
   if m['id'] not in pos: continue
   tier,ev,kind,mode=evidences[(m['id'],h)]
   arr.append([pos[m['id']],tier,ev,kind,mode])
   category_ids.add(m['id'])
  arr.sort(key=lambda x:(x[1],-score(chosen[x[0]])[0],-chosen[x[0]]['rating'],-chosen[x[0]]['votes']))
  title_index.append(arr); final_counts.append(len(arr))
 if min(final_counts)<MIN_PER_HOUR:
  raise RuntimeError('La sélection finale perd la couverture: '+str(final_counts))
 random_index=[i for i,m in enumerate(chosen) if m['id'] not in category_ids]
 records=[[m['id'],m['title'],m['year'],int(round(m['rating']*10)),m['votes'],m['genres'],0,m['type']] for m in chosen]
 html=BASE.read_text(encoding='utf-8')
 html=re.sub(r'var APP_VERSION = "[^"]+";','var APP_VERSION = "11.0.0-imdb-20000";',html,1)
 html=re.sub(r'var STORAGE_KEY = "[^"]+";','var STORAGE_KEY = "oracle24-github-pages-v11";',html,1)
 html=re.sub(r'var RAW_MOVIES = .*?;\n  var TITLE_INDEX = .*?;',lambda _: 'var RAW_MOVIES = '+json.dumps(records,ensure_ascii=False,separators=(',',':'))+';\n  var TITLE_INDEX = '+json.dumps(title_index,ensure_ascii=False,separators=(',',':'))+';\n  var RANDOM_INDEX = '+json.dumps(random_index,separators=(',',':'))+';',html,count=1,flags=re.S)
 html=html.replace('5 000 films','20 000 films').replace('5 000','20 000').replace('5000','20000')
 html=html.replace('<button data-panel="data">Données</button>','<button data-panel="random">Film au hasard</button>\n  <button data-panel="data">Données</button>')
 html=html.replace('  function titleRelation(movie, target) {','  function indexedRelation(movie,target,entry) {\n    var evidence=entry[2]||movie.t; var kind=entry[3]||"principal"; var mode=entry[4]||"exact"; var label=kind==="alternatif"?"titre alternatif IMDb":(kind==="original"?"titre original":"titre principal"); var type=mode==="opus"?"Numéro de suite ou d’opus":(mode==="inclus"?"Nombre inclus dans un nombre du titre":(mode==="mot"?"Nombre écrit dans le titre":"Nombre exact dans le titre")); return {tier:entry[1],type:type,reason:"La relation avec "+(target===0?"0/24":target)+" apparaît dans le "+label+" « "+evidence+" ».",source:"title"};\n  }\n\n  function titleRelation(movie, target) {')
 html=html.replace('relation = titleRelation(movie,target);','relation = indexedRelation(movie,target,entry) || titleRelation(movie,target);')
 html=html.replace('    } else {\n      renderData();\n    }\n  }','    } else if (type === "random") {\n      renderRandom();\n    } else {\n      renderData();\n    }\n  }',1)
 random_js=r'''  function randomBand(movie,band) { var r=movie.q/10; return r>=band && r<(band===9?10.01:band+1); }
  function randomMovieByBand(band) { var eligible=[],i,m; for(i=0;i<RANDOM_INDEX.length;i+=1){m=MOVIES[RANDOM_INDEX[i]];if(randomBand(m,band))eligible.push(m);} if(!eligible.length)return null; return eligible[Math.floor(Math.random()*eligible.length)]; }
  function showRandomMovie(band) { var m=randomMovieByBand(band),body=el("randomResult"); if(!m){body.innerHTML='<p class="data-note">Aucun film disponible dans cette tranche.</p>';return;} body.innerHTML='<div class="film-card"><div class="film-head"><div class="poster">'+esc(m.t)+'</div><div class="film-info"><h2>'+esc(m.t)+'</h2><div class="pills"><span class="pill">'+m.y+'</span><span class="pill">'+esc(ratingText(m))+'</span><span class="pill">'+Number(m.v||0).toLocaleString("fr-FR")+' votes</span><span class="pill">'+esc(m.g||"genre non renseigné")+'</span></div><div class="pitch"><b>Pitch.</b> '+esc(pitchFor(m))+'<span class="note">Film hors catégories horaires</span></div></div></div><div class="main-links"><a class="imdb" href="'+imdbUrl(m)+'" target="_blank" rel="noopener">IMDb · fiche exacte</a><a href="https://www.justwatch.com/fr/recherche?q='+encodeURIComponent(m.t)+'" target="_blank" rel="noopener">JustWatch</a><button id="randomAgain" type="button">Autre film</button></div></div>'; el("randomAgain").onclick=function(){showRandomMovie(band);}; }
  function renderRandom() { var h='<div class="warning"><b>Films hors catégories horaires.</b> Choisissez une tranche de note IMDb. Le bouton 9 couvre de 9,0 à 10.</div><div class="toolbar">',b; el("modalTitle").innerHTML="Film au hasard"; for(b=4;b<=9;b+=1)h+='<button class="ghost" type="button" data-random-band="'+b+'">Note '+b+'</button>'; h+='</div><div id="randomResult"><p class="data-note">Sélectionnez une tranche de note.</p></div>'; el("modalBody").innerHTML=h; el("modalBody").onclick=function(e){var t=e.target||e.srcElement,v=t.getAttribute&&t.getAttribute("data-random-band");if(v!==null&&v!==undefined)showRandomMovie(parseInt(v,10));}; }

'''
 html=html.replace('  function renderData() {',random_js+'  function renderData() {',1)
 html=html.replace('Période : 1990–2025.','Période : 1990–2025.<br>Minimum garanti : 100 films strictement éligibles par heure.<br>Films hors catégories disponibles dans « Film au hasard ».')
 OUT.write_text(html,encoding='utf-8')
 report={'source':'IMDb Non-Commercial Datasets','generated_utc':__import__('datetime').datetime.utcnow().isoformat()+'Z','total':len(chosen),'years':[min(m['year'] for m in chosen),max(m['year'] for m in chosen)],'rating_min':min(m['rating'] for m in chosen),'hour_counts':final_counts,'candidate_counts_before_selection':counts_all,'random_outside_categories':len(random_index),'types':dict(__import__('collections').Counter(m['type'] for m in chosen))}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__': main()
