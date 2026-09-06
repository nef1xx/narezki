'use strict';
const $ = id => document.getElementById(id);
const state = {token:'',source:null,banner:null,settings:{},selected:'face',image:null,job:null,busy:false,frameRequest:0};
let saveTimer, seekTimer, pollTimer;
const sourceCanvas = $('source-canvas'), sourceCtx = sourceCanvas.getContext('2d');
const previewCanvas = $('preview-canvas'), previewCtx = previewCanvas.getContext('2d');
const clock = n => { n = Math.max(0, Math.floor(n || 0)); return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`; };
const seconds = n => `${Number(n.toFixed(1))} сек.`;
function error(message) { $('alert').textContent = message; $('alert').hidden = !message; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-App-Token':state.token},body:JSON.stringify(data)});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || 'Не удалось выполнить запрос');
  return result;
}
function guarded(fn) { return async (...args) => { try { error(''); await fn(...args); } catch(e) { error(e.message); } }; }
function syncControls() {
  for(const key of ['resolution','preset','face_share','face_mode','content_mode','ad_mode']) $(key.replaceAll('_','-')).value = state.settings[key];
  $('face-share-value').textContent = `${state.settings.face_share}%`;
  for(const key of ['title_top','title_bottom','title_size']) $(key.replaceAll('_','-')).value = state.settings[key];
  syncCoordinates(); syncBannerPosition(); draw();
}
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => api('/api/settings',{settings:state.settings}).catch(e=>error(e.message)),450);
}
function syncCoordinates() {
  const rect = state.settings[state.selected]; if(!rect) return;
  ['x','y','w','h'].forEach((key,i)=>$('rect-'+key).value = +(rect[i]*100).toFixed(1));
  $('selected-region').textContent = state.selected === 'face' ? '«Вебка»' : '«Контент»';
  $('select-face').setAttribute('aria-pressed',state.selected==='face');
  $('select-content').setAttribute('aria-pressed',state.selected==='content');
}
function updateReady() {
  const ready = state.source && state.banner && !state.busy;
  $('export').disabled = !ready; $('preview-button').disabled = !ready;
  if(state.source) {
    const count = Math.ceil((Math.ceil(state.source.duration*30-1e-6))/1800);
    $('export-summary').textContent = `${count} ${count===1?'клип':count<5?'клипа':'клипов'} · ${state.settings.resolution} × ${+state.settings.resolution*16/9} · 30 fps`;
  }
  if(state.banner) {
    $('ad-length').textContent = seconds(state.banner.duration);
    $('timeline-total').textContent = `До ${seconds(60+state.banner.duration)} на клип`;
  }
}
function mediaInfo(role, info) {
  state[role] = info;
  $(role+'-name').textContent = info.name; $(role+'-name').title = info.path; $(role+'-name').hidden = false;
  $(role+'-meta').textContent = `${clock(info.duration)} · ${info.width} × ${info.height}${info.audio?'':' · без звука'}`;
  $('pick-'+role).textContent = 'Заменить';
  if(role==='source') {
    state.image = null; draw();
    $('seek').disabled = false; $('seek').max = Math.max(0,info.duration-.1); $('seek').value=0;
    $('source-duration').textContent = clock(info.duration);
    loadFrame(0);
  }
  draw(); updateReady();
}
async function choose(role) {
  const button = $('pick-'+role); button.disabled = true;
  try { const {path} = await api('/api/pick', {kind:'video'}); if(path) mediaInfo(role,await api('/api/media',{path,role})); }
  finally { button.disabled = false; }
}
async function loadFrame(at) {
  const request = ++state.frameRequest;
  $('frame-loading').hidden=false; $('frame-time').textContent=clock(at);
  const img=new Image();
  img.onload=()=>{
    if(request!==state.frameRequest)return;
    state.image=img; sourceCanvas.width=img.naturalWidth;sourceCanvas.height=img.naturalHeight;
    $('source-stage').style.aspectRatio=`${img.naturalWidth} / ${img.naturalHeight}`;
    $('source-stage').style.maxHeight='none';
    $('frame-loading').hidden=true;draw();
  };
  img.onerror=()=>{if(request===state.frameRequest){$('frame-loading').hidden=true;error('Не удалось получить кадр. Выберите другую позицию на шкале.');}};
  img.src=`/frame?id=${state.source.id}&time=${Number(at).toFixed(2)}`;
}
function drawFit(ctx,img,rect,dx,dy,dw,dh,mode) {
  let [sx,sy,sw,sh]=rect.map((v,i)=>v*(i%2===0?img.naturalWidth:img.naturalHeight));
  // Match FFmpeg's even-pixel crop coordinates using original dimensions.
  if(state.source) {
    const W=state.source.width,H=state.source.height;
    const cw=Math.min(Math.floor(W/2)*2,Math.max(2,Math.floor(rect[2]*W/2)*2));
    const ch=Math.min(Math.floor(H/2)*2,Math.max(2,Math.floor(rect[3]*H/2)*2));
    sx=Math.min(W-cw,Math.floor(rect[0]*W/2)*2)/W*img.naturalWidth;
    sy=Math.min(H-ch,Math.floor(rect[1]*H/2)*2)/H*img.naturalHeight;
    sw=cw/W*img.naturalWidth;sh=ch/H*img.naturalHeight;
  }
  if(mode==='crop') {
    if(sw/sh>dw/dh){const w=sh*dw/dh;sx+=(sw-w)/2;sw=w;} else {const h=sw*dh/dw;sy+=(sh-h)/2;sh=h;}
  } else {const scale=Math.min(dw/sw,dh/sh);dx+=(dw-sw*scale)/2;dy+=(dh-sh*scale)/2;dw=sw*scale;dh=sh*scale;}
  ctx.drawImage(img,sx,sy,sw,sh,dx,dy,dw,dh);
}
function draw() {
  drawBannerPosition();
  $('source-empty').hidden=!!state.image; $('preview-empty').hidden=!!state.image;
  sourceCtx.clearRect(0,0,sourceCanvas.width,sourceCanvas.height);
  previewCtx.fillStyle='#080b09';previewCtx.fillRect(0,0,720,1280);
  if(!state.image)return;
  sourceCtx.drawImage(state.image,0,0,sourceCanvas.width,sourceCanvas.height);
  for(const key of ['content','face']) {
    const rect=state.settings[key]; if(!rect)continue;
    const [x,y,w,h]=rect.map((v,i)=>v*(i%2===0?sourceCanvas.width:sourceCanvas.height));
    const color=key==='face'?'#d6f586':'#75d9e1';
    sourceCtx.strokeStyle=color; sourceCtx.lineWidth=Math.max(2,sourceCanvas.width/380);
    sourceCtx.fillStyle=key==='face'?'#d6f58618':'#75d9e108';
    sourceCtx.fillRect(x,y,w,h);sourceCtx.setLineDash(key===state.selected?[]:[10,6]);sourceCtx.strokeRect(x+2,y+2,w-4,h-4);sourceCtx.setLineDash([]);
    const fontSize=Math.max(14,sourceCanvas.width/65),label=key==='face'?'ВЕБКА':'КОНТЕНТ';
    sourceCtx.font=`600 ${fontSize}px Segoe UI`;const tw=sourceCtx.measureText(label).width+16;
    sourceCtx.fillStyle=color;sourceCtx.fillRect(x+2,y+2,tw,fontSize+12);sourceCtx.fillStyle='#142017';sourceCtx.fillText(label,x+10,y+fontSize+6);
  }
  const outHeight=+state.settings.resolution*16/9;
  const split=Math.round(outHeight*state.settings.face_share/100/2)*2/outHeight*1280;
  drawFit(previewCtx,state.image,state.settings.face,0,0,720,split,state.settings.face_mode);
  drawFit(previewCtx,state.image,state.settings.content,0,split,720,1280-split,state.settings.content_mode);
  previewCtx.drawImage(titleCanvas(),0,0);
}
// Use the same transparent bitmap in the preview and FFmpeg export.
function titleCanvas() {
  const canvas=document.createElement('canvas');canvas.width=720;canvas.height=1280;
  const ctx=canvas.getContext('2d');
  const rows=[[state.settings.title_top,'#ffffff'],[state.settings.title_bottom,'#ffd029']].filter(([text])=>text?.trim());
  const size=Number(state.settings.title_size)||72;
  const outHeight=+state.settings.resolution*16/9;
  const split=Math.round(outHeight*state.settings.face_share/100/2)*2/outHeight*1280;
  rows.forEach(([text,color],i)=>{
    const y=split+(i-(rows.length-1)/2)*size*1.06;
    ctx.save();ctx.translate(360,y);ctx.transform(1,0,-.16,1,0,0);
    ctx.font=`900 ${size}px Impact, "Arial Black", sans-serif`;
    ctx.textAlign='center';ctx.textBaseline='middle';ctx.lineJoin='round';
    ctx.scale(Math.min(1,630/Math.max(1,ctx.measureText(text).width)),1);
    ctx.strokeStyle='#17120c';ctx.lineWidth=8;ctx.shadowColor='#000b';ctx.shadowBlur=5;ctx.shadowOffsetY=5;
    ctx.strokeText(text,0,0);ctx.shadowBlur=0;ctx.shadowOffsetY=0;
    ctx.fillStyle=color;ctx.fillText(text,0,0);ctx.restore();
  });
  return canvas;
}
const bannerPresets = {top:20,middle:50,bottom:80};
const bannerMarker = document.createElement('div');
bannerMarker.className='banner-marker';
bannerMarker.textContent='БАННЕР · ПЕРЕТАЩИТЕ';
bannerMarker.setAttribute('aria-hidden','true');
previewCanvas.parentElement.append(bannerMarker);
function syncBannerPosition() {
  for(const axis of ['x','y']) $('ad-'+axis).value=state.settings['ad_'+axis];
  for(const [name,y] of Object.entries(bannerPresets)) {
    $('ad-'+name).setAttribute('aria-pressed',state.settings.ad_x===50 && state.settings.ad_y===y);
  }
}
function bannerGeometry() {
  const W=Number(state.settings.resolution)||720,H=W*16/9;
  let w=Math.floor(W/20)*18,h=Math.floor(H/20)*6;
  if(state.banner && state.settings.ad_mode==='fit') {
    const scale=Math.min(w/state.banner.width,h/state.banner.height);
    w=Math.max(2,Math.floor(state.banner.width*scale/2)*2);
    h=Math.max(2,Math.floor(state.banner.height*scale/2)*2);
  }
  w/=W;h/=H;
  const x=Math.max(0,Math.min(1-w,(state.settings.ad_x??50)/100-w/2));
  const y=Math.max(0,Math.min(1-h,(state.settings.ad_y??72)/100-h/2));
  return {x,y,w,h};
}
function drawBannerPosition() {
  const {x,y,w,h}=bannerGeometry();
  Object.assign(bannerMarker.style,{left:`${x*100}%`,top:`${y*100}%`,width:`${w*100}%`,height:`${h*100}%`});
}
for(const [name,y] of Object.entries(bannerPresets)) $('ad-'+name).addEventListener('click',()=>{
  state.settings.ad_x=50;state.settings.ad_y=y;syncBannerPosition();draw();scheduleSave();
});
for(const axis of ['x','y']) $('ad-'+axis).addEventListener('change',e=>{
  const value=e.target.valueAsNumber;
  if(!Number.isFinite(value)||value<0||value>100) {
    error('Координата баннера должна быть от 0 до 100%.');syncBannerPosition();return;
  }
  error('');state.settings['ad_'+axis]=value;syncBannerPosition();draw();scheduleSave();
});
let bannerDrag=null;
bannerMarker.addEventListener('pointerdown',e=>{
  if(e.button!==0 || bannerDrag)return;
  const rect=previewCanvas.getBoundingClientRect(),g=bannerGeometry();
  bannerDrag={id:e.pointerId,rect,dx:(e.clientX-rect.left)/rect.width-g.x-g.w/2,dy:(e.clientY-rect.top)/rect.height-g.y-g.h/2,old:[state.settings.ad_x,state.settings.ad_y]};
  clearTimeout(saveTimer);bannerMarker.setPointerCapture(e.pointerId);e.preventDefault();
});
bannerMarker.addEventListener('pointermove',e=>{
  if(!bannerDrag||e.pointerId!==bannerDrag.id)return;
  const {rect,dx,dy}=bannerDrag,g=bannerGeometry();
  state.settings.ad_x=+(Math.max(g.w/2,Math.min(1-g.w/2,(e.clientX-rect.left)/rect.width-dx))*100).toFixed(1);
  state.settings.ad_y=+(Math.max(g.h/2,Math.min(1-g.h/2,(e.clientY-rect.top)/rect.height-dy))*100).toFixed(1);
  syncBannerPosition();drawBannerPosition();
});
function endBannerDrag(e) {
  if(!bannerDrag||e.pointerId!==bannerDrag.id)return;
  if(e.type==='pointercancel') [state.settings.ad_x,state.settings.ad_y]=bannerDrag.old;
  bannerDrag=null;syncBannerPosition();drawBannerPosition();scheduleSave();
}
for(const event of ['pointerup','pointercancel','lostpointercapture']) bannerMarker.addEventListener(event,endBannerDrag);
let drag=null;
function point(e) {const r=sourceCanvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];}
sourceCanvas.addEventListener('pointerdown',e=>{if(!state.image || e.button!==0)return;drag={start:point(e),old:[...state.settings[state.selected]]};sourceCanvas.setPointerCapture(e.pointerId);});
sourceCanvas.addEventListener('pointermove',e=>{if(!drag)return;const p=point(e),s=drag.start;const rect=[Math.min(s[0],p[0]),Math.min(s[1],p[1]),Math.abs(p[0]-s[0]),Math.abs(p[1]-s[1])];if(rect[2]>=.01&&rect[3]>=.01){state.settings[state.selected]=rect;syncCoordinates();draw();}});
sourceCanvas.addEventListener('pointerup',()=>{if(drag){drag=null;scheduleSave();}});
sourceCanvas.addEventListener('pointercancel',()=>{if(drag){state.settings[state.selected]=drag.old;drag=null;syncCoordinates();draw();}});
for(const role of ['source','banner']) $('pick-'+role).addEventListener('click',guarded(()=>choose(role)));
$('load-path').addEventListener('click',guarded(async()=>{const button=$('load-path');button.disabled=true;try{const role=$('file-role').value;mediaInfo(role,await api('/api/media',{path:$('file-path').value,role}));}finally{button.disabled=false;}}));
for(const region of ['face','content']) $('select-'+region).addEventListener('click',()=>{state.selected=region;syncCoordinates();draw();});
$('reset-crops').addEventListener('click',()=>{state.settings.face=[0,0,.3,.3];state.settings.content=[0,0,1,1];syncCoordinates();draw();scheduleSave();});
for(const key of ['x','y','w','h']) $('rect-'+key).addEventListener('change',()=>{
  const rect=['x','y','w','h'].map(k=>Number($('rect-'+k).value)/100);
  if(!rect.every(Number.isFinite)||Math.min(rect[0],rect[1])<0||Math.min(rect[2],rect[3])<.01||rect[0]+rect[2]>1.000001||rect[1]+rect[3]>1.000001){error('Область должна оставаться внутри кадра. X + ширина и Y + высота — не больше 100%.');syncCoordinates();return;}
  error('');state.settings[state.selected]=rect;draw();scheduleSave();
});
for(const key of ['resolution','preset','face_share','face_mode','content_mode','ad_mode']) $(key.replaceAll('_','-')).addEventListener('input',e=>{state.settings[key]=key==='face_share'?Number(e.target.value):e.target.value;$('face-share-value').textContent=`${state.settings.face_share}%`;draw();updateReady();scheduleSave();});
$('seek').addEventListener('input',e=>{const at=+e.target.value;$('frame-time').textContent=clock(at);clearTimeout(seekTimer);seekTimer=setTimeout(()=>loadFrame(at),250);});
for(const key of ['title_top','title_bottom','title_size']) $(key.replaceAll('_','-')).addEventListener('input',e=>{
  state.settings[key]=key==='title_size'?Number(e.target.value):e.target.value;draw();scheduleSave();
});
$('choose-output').addEventListener('click',guarded(async()=>{const {path}=await api('/api/pick',{kind:'directory'});if(path){const result=await api('/api/output-dir',{path});$('output-path').textContent=result.path;}}));
async function startRender(preview) {
  state.busy=true;updateReady();
  try {const job=await api('/api/render',{source:state.source.id,banner:state.banner.id,settings:state.settings,title_image:titleCanvas().toDataURL('image/png').split(',')[1],preview});state.job=job;renderJob(job);$('job-panel').scrollIntoView({behavior:'smooth',block:'nearest'});poll();}
  catch(e){state.busy=false;updateReady();throw e;}
}
$('export').addEventListener('click',guarded(()=>startRender(false)));
$('preview-button').addEventListener('click',guarded(()=>startRender(true)));
function play(url) {$('result-player').src=url;$('player-dialog').showModal();$('result-player').play().catch(()=>{});}
$('close-player').addEventListener('click',()=>$('player-dialog').close());
$('player-dialog').addEventListener('close',()=>{$('result-player').pause();$('result-player').removeAttribute('src');$('result-player').load();});
function renderJob(job) {
  state.job=job;$('job-panel').hidden=false;$('job-title').textContent=job.preview?'Пробный ролик':'Экспорт клипов';
  $('job-stage').textContent=job.stage;$('job-percent').textContent=`${job.progress}%`;$('progress').value=job.progress;
  $('cancel').hidden=job.status!=='running';$('cancel').disabled=false;
  $('job-error').hidden=!job.error;$('job-error').textContent=job.error||'';
  const results=$('results');results.replaceChildren();
  for(const file of job.files) {
    const row=document.createElement('div');row.className='result-item';
    const button=document.createElement('button');button.textContent=`▷ ${file.name}`;button.addEventListener('click',()=>play(file.url));
    const link=document.createElement('a');link.href=file.url;link.download=file.name;link.textContent='Скачать ↓';
    row.append(button,link);results.append(row);
  }
}
async function poll() {
  clearTimeout(pollTimer);
  try {const job=await api('/api/jobs/'+state.job.id);renderJob(job);state.busy=job.status==='running';updateReady();if(state.busy)pollTimer=setTimeout(poll,900);else if(job.preview&&job.status==='done')play(job.files[0].url);}
  catch(e){error('Связь с приложением потеряна. Проверяем повторно…');pollTimer=setTimeout(poll,3000);}
}
$('cancel').addEventListener('click',guarded(async()=>{await api('/api/cancel',{id:state.job.id});$('cancel').disabled=true;$('job-stage').textContent='Останавливаем…';}));
$('open-output').addEventListener('click',guarded(()=>api('/api/open-folder',{id:state.job.id})));
async function init() {
  const data=await api('/api/state');state.token=data.token;state.settings={title_top:'',title_bottom:'',title_size:72,ad_x:50,ad_y:72,...data.settings};syncControls();
  $('output-path').textContent=data.output_dir;$('engine-status').textContent=data.ffmpeg?'FFmpeg готов · H.264 / AAC':'Ожидаем установку FFmpeg…';
  if(!data.ffmpeg) {
    const waitForEngine=setInterval(async()=>{
      try { const next=await api('/api/state');if(next.ffmpeg){$('engine-status').textContent='FFmpeg готов · H.264 / AAC';clearInterval(waitForEngine);} } catch(e) { /* The next tick retries while the local server starts. */ }
    },5000);
  }
  if(data.banner)mediaInfo('banner',data.banner);
  const job=data.jobs.find(j=>j.status==='running')||data.jobs.at(-1);
  if(job){renderJob(job);if(job.status==='running'){state.busy=true;poll();}}
  updateReady();
}
guarded(init)();
