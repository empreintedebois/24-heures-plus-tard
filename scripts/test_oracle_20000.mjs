import fs from 'node:fs';
import { chromium, webkit } from 'playwright';

const report = JSON.parse(fs.readFileSync('build-report.json','utf8'));
const html = fs.readFileSync('index-20000.html','utf8');
const results = { generatedAt:new Date().toISOString(), static:{}, browsers:[] };

function assert(condition,message){ if(!condition) throw new Error(message); }

assert(report.total===20000,'La base ne contient pas exactement 20 000 entrées');
assert(Array.isArray(report.hour_counts)&&report.hour_counts.length===24,'Le rapport ne contient pas 24 heures');
assert(Math.min(...report.hour_counts)>=100,'Une heure contient moins de 100 candidats');
assert(report.random_outside_categories>0,'Aucun film hors catégories');
assert(html.includes('var RANDOM_INDEX = '),'RANDOM_INDEX absent du HTML');
assert(html.includes('data-panel="random"'),'Onglet Film au hasard absent');
assert(html.includes('function renderRandom()'),'Moteur de sélection aléatoire absent');
assert(html.includes('function pitchFor(movie)'),'Pitches absents');
assert(html.includes('function replaceSlot(slot,action)'),'Historique vu/refusé absent');
assert(html.includes('function searchCatalog(query)'),'Recherche catalogue absente');
assert(html.includes('function addCustomRelation()'),'Relations personnalisées absentes');
results.static = {
  total:report.total,
  minHour:Math.min(...report.hour_counts),
  maxHour:Math.max(...report.hour_counts),
  hourCounts:report.hour_counts,
  randomOutsideCategories:report.random_outside_categories,
  ratingMin:report.rating_min,
  years:report.years
};

const scenarios = [
  {name:'Chromium iPhone 13',engine:chromium,viewport:{width:390,height:844},full:true},
  {name:'Chromium grand mobile',engine:chromium,viewport:{width:430,height:932},full:false},
  {name:'WebKit iPhone 13',engine:webkit,viewport:{width:390,height:844},full:true},
  {name:'WebKit iPad portrait',engine:webkit,viewport:{width:768,height:1024},full:false}
];

for (const scenario of scenarios) {
  const browser = await scenario.engine.launch({headless:true});
  const context = await browser.newContext({viewport:scenario.viewport,locale:'fr-FR'});
  const page = await context.newPage();
  const errors=[];
  page.on('pageerror',e=>errors.push('pageerror: '+e.message));
  page.on('console',m=>{ if(m.type()==='error') errors.push('console: '+m.text()); });
  const item={name:scenario.name,viewport:scenario.viewport,checks:{}};
  try {
    await page.goto('http://127.0.0.1:8000/index-20000.html',{waitUntil:'domcontentloaded',timeout:120000});
    await page.waitForFunction(()=>document.documentElement.className==='oracle-ready',{timeout:120000});
    await page.waitForTimeout(300);

    const catalog=(await page.locator('#catalogCount').innerText()).replace(/\s/g,'');
    assert(catalog==='20000','Compteur visuel différent de 20 000');
    assert(await page.locator('.ring-slot').count()===24,'L’horloge ne contient pas 24 cases');
    assert(await page.locator('.day-card').count()===24,'La bande journalière ne contient pas 24 cartes');
    assert((await page.locator('#filmCard h2').first().innerText()).trim().length>0,'Aucun film principal affiché');
    assert(await page.locator('#filmCard .justification').count()===1,'Justification principale absente');
    assert(await page.locator('#filmCard .pitch').count()===1,'Pitch principal absent');
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);
    assert(overflow<=5,'Débordement horizontal mobile de '+overflow+' px');
    item.checks.base=true;

    if(scenario.full){
      const before=parseInt((await page.locator('#historyCount').innerText()).replace(/\D/g,'')||'0',10);
      await page.locator('#seenButton').click();
      await page.waitForTimeout(250);
      const after=parseInt((await page.locator('#historyCount').innerText()).replace(/\D/g,'')||'0',10);
      assert(after===before+1,'Le bouton Vu n’incrémente pas l’historique');
      await page.locator('#undoButton').click();
      await page.waitForTimeout(250);
      const undone=parseInt((await page.locator('#historyCount').innerText()).replace(/\D/g,'')||'0',10);
      assert(undone===before,'Annuler ne restaure pas l’historique');
      item.checks.history=true;

      await page.locator('[data-panel="catalog"]').click();
      await page.locator('#catalogSearch').fill('Matrix');
      await page.waitForTimeout(250);
      assert(await page.locator('#catalogResults .list-item').count()>0,'La recherche catalogue ne retourne rien');
      assert(await page.locator('#catalogResults .list-pitch').first().count()===1,'Pitch absent du catalogue');
      await page.locator('#modalClose').click();
      item.checks.catalog=true;

      await page.locator('[data-panel="relations"]').click();
      assert(await page.locator('#relationTitle').count()===1,'Formulaire de relation absent');
      assert(await page.locator('#relationNumber option').count()===24,'La relation ne propose pas 24 nombres');
      await page.locator('#modalClose').click();
      item.checks.relations=true;

      await page.locator('[data-panel="random"]').click();
      for(let band=4;band<=9;band+=1){
        await page.locator('[data-random-band="'+band+'"]').click();
        await page.waitForTimeout(120);
        const title=await page.locator('#randomResult h2').innerText();
        assert(title.trim().length>0,'Aucun film aléatoire pour la tranche '+band);
        const txt=await page.locator('#randomResult').innerText();
        const match=txt.match(/IMDb\s+(\d+(?:[.,]\d+)?)/i);
        assert(match,'Note IMDb absente pour la tranche '+band);
        const rating=parseFloat(match[1].replace(',','.'));
        const upper=band===9?10.01:band+1;
        assert(rating>=band&&rating<upper,'Note '+rating+' hors tranche '+band);
        assert(await page.locator('#randomResult .pitch').count()===1,'Pitch aléatoire absent pour '+band);
        assert(await page.locator('#randomResult a.imdb').count()===1,'Lien IMDb aléatoire absent pour '+band);
      }
      await page.locator('#modalClose').click();
      item.checks.randomBands=[4,5,6,7,8,9];

      await page.reload({waitUntil:'domcontentloaded',timeout:120000});
      await page.waitForFunction(()=>document.documentElement.className==='oracle-ready',{timeout:120000});
      assert((await page.locator('#catalogCount').innerText()).replace(/\s/g,'')==='20000','Le rechargement perd la base');
      item.checks.reload=true;
    }

    assert(errors.length===0,'Erreurs navigateur : '+errors.join(' | '));
    item.errors=[];
    item.success=true;
  } catch(error) {
    item.success=false;
    item.error=error.message;
    item.errors=errors;
    results.browsers.push(item);
    await browser.close();
    fs.writeFileSync('test-report.json',JSON.stringify(results,null,2));
    throw error;
  }
  results.browsers.push(item);
  await browser.close();
}

assert(results.browsers.every(x=>x.success),'Un scénario navigateur a échoué');
fs.writeFileSync('test-report.json',JSON.stringify(results,null,2));
console.log(JSON.stringify(results,null,2));
