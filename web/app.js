"use strict";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="studio-token"]').content;
const state = {mode:"generate", view:"preview", refs:[], maskBase:null, maskDirty:false,
  drawing:false, erasing:false, busy:false, selected:null, outputs:[], seen:new Set(), toastTimer:null};
const fieldMap = {prompt:"prompt",negative_prompt:"negative-prompt",width:"width",height:"height",
  steps:"steps",cfg:"cfg",seed:"seed",count:"count",batch_size:"batch-size",sampler:"sampler",transparent:"transparent",
  preserve_outside:"preserve-outside",mask_feather:"mask-feather",reference_resolution:"reference-resolution",
  profile:"profile",use_kv_cache:"use-kv-cache",cache_text:"cache-text"};
const boolKeys = new Set(["transparent","preserve_outside","use_kv_cache","cache_text"]);
const textKeys = new Set(["prompt","negative_prompt","profile","sampler"]);
function element(tag, text, cls) {const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e;}
function toast(message, error=false) {clearTimeout(state.toastTimer); $("toast").textContent=message; $("toast").className=error?"error":""; $("toast").hidden=false; state.toastTimer=setTimeout(()=>$("toast").hidden=true,error?12000:5500);}
async function api(url, options={}) {
  const headers = new Headers(options.headers||{});
  if(options.method && options.method!=="GET") headers.set("X-Studio-Token",token);
  const response=await fetch(url,{...options,headers});
  if(!response.ok){let msg=`HTTP ${response.status}`;try{const obj=await response.json();msg=Array.isArray(obj.detail)?obj.detail.map(x=>`${x.loc?.slice(1).join(".")||"入力"}: ${x.msg}`).join("\n"):(obj.detail||msg);}catch{} throw Error(msg);}
  return response.json();
}
async function uploadBlob(blob, mask=false) {return api(`/api/uploads?mask=${mask}`,{method:"POST",headers:{"Content-Type":"application/octet-stream"},body:blob});}
function imageLoaded(url) {return new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(Error("画像を読み込めません。再度追加してください。"));img.src=url;});}
function selectView(view) {state.view=view;for(const id of ["preview","history","mask"]){$(`${id}-view`).hidden=id!==view;}document.querySelectorAll("[data-view]").forEach(b=>b.classList.toggle("selected",b.dataset.view===view));if(view==="history")loadHistory().catch(e=>toast(e.message,true));}
function selectMode(mode, resetSize=true) {
  if(state.busy)return;
  state.mode=mode;
  document.querySelectorAll("[data-mode]").forEach(b=>{const on=b.dataset.mode===mode;b.classList.toggle("selected",on);b.setAttribute("aria-selected",String(on));});
  $("reference-section").hidden=mode==="generate";$("mask-options").hidden=mode!=="local";$("mask-tab").hidden=mode!=="local";
  $("generate").textContent=mode==="generate"?"✦ 画像を生成する":mode==="edit"?"✦ 画像を編集する":"✦ 指定した範囲を編集する";
  $("generate-top").textContent=$("generate").textContent;
  if(resetSize){$("width").value=mode==="generate"?1024:768;$("height").value=$("width").value;syncPreset();}
  renderReferences();
  if(mode==="local"){selectView("mask");if(resetSize)prepareMask().catch(e=>toast(e.message,true));}else if(state.view==="mask"){selectView("preview");}
}
function syncPreset(){syncSliders();const val=`${$("width").value},${$("height").value}`;$("size-preset").value=[...$("size-preset").options].some(x=>x.value===val)?val:"custom";}
function fields(){const request={mode:state.mode};for(const [key,id] of Object.entries(fieldMap)){const input=$(id);request[key]=boolKeys.has(key)?input.checked:textKeys.has(key)?input.value:Number(input.value);}request.references=state.mode==="generate"?[]:state.refs.map(x=>x.id);return request;}
function countPrompt(){$("prompt-count").textContent=`${$("prompt").value.length} / 4096`;}
function updateCFG(){const off=Number($("cfg").value)<=1;$("cfg-hint").textContent=off?"CFG 1でも通常の指示は有効です。ネガティブを反映するにはCFGを1より大きくしてください。":"通常の指示を強調し、ネガティブで避けたい要素を指定できます。高すぎると色や形が不自然になる場合があります。";}
function syncSliders(){document.querySelectorAll("[data-range-for]").forEach(range=>range.value=$(range.dataset.rangeFor).value);updateBatchTotal();}
function updateBatchTotal(){const count=Number($("count").value),size=Number($("batch-size").value);$("batch-total").textContent=`${count}回 × 同時${size}枚 = 合計${count*size}枚`;}
function setGenerateDisabled(disabled){$("generate").disabled=disabled;$("generate-top").disabled=disabled;}
function settingChanged(id){
  if(id==="cfg")updateCFG();
  if(id==="count"||id==="batch-size")updateBatchTotal();
  if(["cfg","steps","sampler"].includes(id)){$("quality-preset").value="custom";$("quality-hint").textContent="手動設定 / Custom settings";}
}
for(const range of document.querySelectorAll("[data-range-for]")){
  const number=$(range.dataset.rangeFor);
  const fromRange=()=>{number.value=range.value;settingChanged(number.id);};
  const fromNumber=()=>{if(number.value!=="")range.value=number.value;settingChanged(number.id);};
  for(const event of ["input","change"]){range.addEventListener(event,fromRange);number.addEventListener(event,fromNumber);}
}
function renderReferences(){
  $("references").replaceChildren();const limit=state.mode==="local"?9:10;$("reference-count").textContent=`${state.refs.length} / ${limit}`;
  state.refs.forEach((ref,index)=>{
    const card=element("div",undefined,"reference");const img=element("img");img.src=ref.url;img.alt=`参照画像${index+1}`;
    const label=element("div",undefined,"ref-label");label.append(element("span",`画像 ${index+1}`),element("span",`${ref.width}×${ref.height}`));
    const actions=element("div",undefined,"ref-actions");
    for(const [text,title,delta] of [["←","左へ移動",-1],["→","右へ移動",1],["×","画像を外す",0]]){
      const button=element("button",text);button.title=title;button.setAttribute("aria-label",`画像${index+1} ${title}`);
      button.disabled=state.busy||(delta<0&&index===0)||(delta>0&&index===state.refs.length-1);
      button.onclick=()=>{if(state.busy)return;if(delta){const other=index+delta;[state.refs[index],state.refs[other]]=[state.refs[other],state.refs[index]];}else state.refs.splice(index,1);renderReferences();prepareMask().catch(e=>toast(e.message,true));};actions.append(button);
    }
    card.append(img,label,actions);$("references").append(card);
  });
}
async function addFiles(files){
  if(state.busy)return;
  const limit=state.mode==="local"?9:10;
  if(state.refs.length+files.length>limit){toast(`このモードの参照画像は最大${limit}枚です。`,true);return;}
  state.busy=true;setGenerateDisabled(true);
  try{for(const file of files){if(file.size>32*1024*1024)throw Error("1枚32 MiB以下にしてください。");const ref=await uploadBlob(file);state.refs.push(ref);renderReferences();}await prepareMask();}
  catch(e){toast(e.message,true);}finally{state.busy=false;setGenerateDisabled(false);$("file-input").value="";renderReferences();}
}
async function prepareMask(){
  if(state.mode!=="local")return;
  const ref=state.refs[0];
  if(!ref){state.maskBase=null;state.maskDirty=false;$("mask-stack").hidden=true;$("mask-empty").hidden=false;return;}
  if(state.maskBase===ref.id)return;
  const img=await imageLoaded(ref.url);if(state.refs[0]?.id!==ref.id)return;
  $("mask-base").src=ref.url;const canvas=$("mask-canvas");canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;
  state.maskBase=ref.id;state.maskDirty=false;$("mask-stack").hidden=false;$("mask-empty").hidden=true;
}
const canvas=$("mask-canvas"), ctx=canvas.getContext("2d");
function point(event){const r=canvas.getBoundingClientRect();return[(event.clientX-r.left)*canvas.width/r.width,(event.clientY-r.top)*canvas.height/r.height];}
let lastPoint=null;
function stroke(event){if(!state.drawing||state.busy)return;const p=point(event);ctx.globalCompositeOperation=state.erasing?"destination-out":"source-over";ctx.strokeStyle="white";ctx.fillStyle="white";ctx.lineCap="round";ctx.lineJoin="round";ctx.lineWidth=Number($("brush-size").value)*canvas.width/canvas.getBoundingClientRect().width;ctx.beginPath();ctx.moveTo(...lastPoint);ctx.lineTo(...p);ctx.stroke();lastPoint=p;state.maskDirty=true;}
canvas.addEventListener("pointerdown",event=>{if(state.busy)return;state.drawing=true;canvas.setPointerCapture(event.pointerId);lastPoint=point(event);ctx.globalCompositeOperation=state.erasing?"destination-out":"source-over";ctx.fillStyle="white";ctx.beginPath();ctx.arc(...lastPoint,Number($("brush-size").value)*canvas.width/canvas.getBoundingClientRect().width/2,0,Math.PI*2);ctx.fill();state.maskDirty=true;});
canvas.addEventListener("pointermove",stroke);for(const ev of ["pointerup","pointercancel","lostpointercapture"]){canvas.addEventListener(ev,()=>{state.drawing=false;lastPoint=null;});}
$("eraser").onclick=()=>{state.erasing=!state.erasing;$("eraser").setAttribute("aria-pressed",String(state.erasing));};
$("clear-mask").onclick=()=>{if(state.busy)return;ctx.clearRect(0,0,canvas.width,canvas.height);state.maskDirty=false;};
function canvasBlob(c){return new Promise((resolve,reject)=>c.toBlob(blob=>blob?resolve(blob):reject(Error("マスクを書き出せません。画像を縮小してください。")),"image/png"));}
async function submit(){
  if(state.busy)return;
  for(const input of document.querySelectorAll('.controls input[type="number"]')){if(!input.value||!input.reportValidity())return;}
  const request=fields();if(!request.prompt.trim()){toast("プロンプトを入力してください。",true);$("prompt").focus();return;}
  if(request.mode!=="generate"&&!request.references.length){toast("参照画像を追加してください。",true);return;}
  if(request.mode==="local"&&(!state.maskDirty||state.maskBase!==state.refs[0]?.id)){toast("編集する領域を白く塗ってください。",true);selectView("mask");return;}
  state.busy=true;setGenerateDisabled(true);
  try{
    if(request.mode==="local"){
      const binary=document.createElement("canvas");binary.width=canvas.width;binary.height=canvas.height;const bctx=binary.getContext("2d");bctx.fillStyle="black";bctx.fillRect(0,0,binary.width,binary.height);bctx.drawImage(canvas,0,0);
      const mask=await uploadBlob(await canvasBlob(binary),true);request.mask_id=mask.id;binary.width=1;binary.height=1;
    }
    await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});
    selectView("preview");toast("キューに追加しました。初回はモデルの読み込みがあります。");await pollJobs();
  }catch(e){toast(e.message,true);}finally{state.busy=false;setGenerateDisabled(false);renderReferences();}
}
function showResult(row){
  state.selected=row;$("preview-image").src=row.url;$("preview-image").hidden=false;$("empty-state").hidden=true;$("result-bar").hidden=false;
  $("result-size").textContent=`${row.final_width} × ${row.final_height} · ${row.has_transparent_pixels?"透過あり":"PNG"}`;
  $("result-detail").textContent=`Seed ${row.seed} · ${row.inference_seconds}s${row.timing_scope==="batch"?" / batch":""} · peak ${row.peak_allocated_gib} GiB${row.text_cache_hit?" · text cache":""}`;
  $("download-image").href=row.url+"?download=true";$("download-image").download=row.file;renderThumbnails();
}
function renderThumbnails(){const host=$("result-thumbnails");host.replaceChildren();for(const row of state.outputs.slice(-16)){const b=element("button");const img=element("img");img.src=row.url;img.alt=`Seed ${row.seed}`;b.title=`Seed ${row.seed}`;b.onclick=()=>showResult(row);b.append(img);host.append(b);}}
const statusLabel={queued:"待機中",running:"処理中",completed:"完了",failed:"エラー",cancelled:"停止"};
async function pollJobs(){
  const jobs=await api("/api/jobs");const pending=jobs.filter(j=>["running","queued"].includes(j.state));$("queue-count").textContent=`${pending.length}件 待機 / 実行中`;
  if(jobs.length){$("jobs").replaceChildren();for(const job of jobs){
    const card=element("div",undefined,`job ${job.state==="failed"?"error":""}`);const top=element("div",undefined,"job-top");const title=element("div",job.request.prompt,"job-title");title.title=job.request.prompt;
    const stat=element("span",`${statusLabel[job.state]} · ${job.image_index}/${job.count}枚 · ${job.elapsed_seconds}s`,"job-state");top.append(title,stat);
    if(["running","queued"].includes(job.state)){const cancel=element("button","停止","secondary");cancel.onclick=async()=>{try{await api(`/api/jobs/${job.id}/cancel`,{method:"POST"});await pollJobs();}catch(e){toast(e.message,true);}};top.append(cancel);}
    card.append(top,element("p",`${job.request.width}×${job.request.height} · ${job.request.steps} steps · CFG ${job.request.cfg} · ${job.request.sampler||"euler"} · ${job.request.count}回 × ${job.request.batch_size||1}枚`,"job-message"),element("p",job.error||job.message,"job-message"));
    if(job.state==="running"){const p=element("progress");if(job.total_steps>0){p.max=job.total_steps;p.value=job.step;}p.setAttribute("aria-label",`${job.step}/${job.total_steps}ステップ`);card.append(p);}$("jobs").append(card);
  }}
  let newest=null;
  for(const job of [...jobs].reverse())for(const result of job.results){if(!state.seen.has(result.file)){state.outputs.push(result);newest=result;}}
  state.outputs=state.outputs.slice(-16);
  state.seen=new Set(jobs.flatMap(job=>job.results.map(result=>result.file)));
  if(newest)showResult(newest);
  $("unload").disabled=pending.length>0;
}
async function loadHistory(){
  const rows=await api("/api/history");const host=$("history-grid");host.replaceChildren();if(!rows.length)host.append(element("p","まだ生成結果はありません。","hint"));
  for(const row of rows){const card=element("article",undefined,"history-card"),b=element("button"),img=element("img");img.src=row.url;img.loading="lazy";img.alt=row.request.prompt;b.onclick=()=>{showResult(row);selectView("preview");};b.append(img);card.append(b,element("p",row.request.prompt),element("small",`${row.final_width}×${row.final_height} · Seed ${row.seed}`));host.append(card);}
}
async function restoreSettings(data){
  if(state.busy)throw Error("アップロードまたは設定処理が終わってから操作してください。");
  const request=data.request||data;if(!request||typeof request!=="object"||!request.prompt)throw Error("生成設定のJSONを指定してください。");
  if(!["generate","edit","local"].includes(request.mode))throw Error("モードが不正です。");
  state.refs=[];state.maskBase=null;state.maskDirty=false;
  selectMode(request.mode,false);
  state.busy=true;setGenerateDisabled(true);
  try{
    $("batch-size").value=1;$("sampler").value="euler";
    for(const [key,id] of Object.entries(fieldMap)){if(request[key]===undefined)continue;if(boolKeys.has(key))$(id).checked=request[key]===true;else $(id).value=request[key];}
    // The sidecar records the ACTUAL seed separately from a request seed of -1.
    if(data.request && Number.isInteger(data.seed) && data.seed>=0 && data.seed<=4294967295)$("seed").value=data.seed;
    for(const id of (Array.isArray(request.references)?request.references:[]).slice(0,request.mode==="local"?9:10)){
      if(!/^[0-9a-f]{32}$/.test(id))continue;
      try{const url=`/api/uploads/${id}.png`,img=await imageLoaded(url);state.refs.push({id,url,width:img.naturalWidth,height:img.naturalHeight});}catch{toast("一部の参照画像がありません。もう一度追加してください。",true);}
    }
    $("gpu-preset").value="custom";$("quality-preset").value="custom";$("quality-hint").textContent="復元した設定 / Restored settings";syncPreset();countPrompt();updateCFG();$("feather-value").value=$("mask-feather").value;renderReferences();await prepareMask();
    if(request.mode==="local"&&state.maskBase&&request.mask_id&&/^[0-9a-f]{32}$/.test(request.mask_id)){
      try{const mask=await imageLoaded(`/api/uploads/${request.mask_id}.png`);if(mask.naturalWidth!==canvas.width||mask.naturalHeight!==canvas.height)throw Error("マスクの寸法が一致しません。");ctx.globalCompositeOperation="source-over";ctx.drawImage(mask,0,0);const px=ctx.getImageData(0,0,canvas.width,canvas.height);for(let i=0;i<px.data.length;i+=4){const a=px.data[i];px.data[i]=px.data[i+1]=px.data[i+2]=255;px.data[i+3]=a;}ctx.putImageData(px,0,0);state.maskDirty=true;}catch{toast("マスクを再度描いてください。",true);}
    }
  }finally{state.busy=false;setGenerateDisabled(false);renderReferences();}
}
async function pollStatus(){const s=await api("/api/status");const gpu=s.hardware.gpu||{};$("gpu-info").textContent=gpu.name||"GPU診断未取得 / Diagnose.batを実行";$("memory-info").textContent=`RAM ${s.ram_used_gib} / ${s.ram_total_gib} GiB · 空き ${s.disk_free_gib} GiB`;$("model-status").textContent=s.engine.loaded?`モデル読込済 · text cache ${s.engine.text_cache_entries}件`:"モデル未読込";}
const qualityPresets = {
  standard:{steps:40,cfg:1,sampler:"euler",hint:"40ステップ・CFG 1。基本の出発点です。"},
  draft:{steps:20,cfg:1,sampler:"euler",hint:"20ステップ・CFG 1。構図確認用。細部は仕上げ時に比較してください。"},
  detail:{steps:60,cfg:1,sampler:"euler",hint:"60ステップ・CFG 1。計算を増やして細部を比較する設定です。"},
  faithful:{steps:50,cfg:3,sampler:"euler",hint:"50ステップ・CFG 3。指示の強調を強めます。過剰ならCFGを下げてください。"},
  negative:{steps:40,cfg:2.5,sampler:"euler",hint:"40ステップ・CFG 2.5。ネガティブ欄に避けたい要素を入力してください。"}
};
$("quality-preset").onchange=()=>{const p=qualityPresets[$("quality-preset").value];if(!p)return;for(const key of ["steps","cfg","sampler"])$(key).value=p[key];$("quality-hint").textContent=p.hint;syncSliders();updateCFG();};
$("sampler").onchange=()=>settingChanged("sampler");
$("swap-size").onclick=()=>{const width=$("width").value;$("width").value=$("height").value;$("height").value=width;$("gpu-preset").value="custom";syncPreset();};
const gpuPresets = {
  "32": {width:1024,height:1024,reference_resolution:768,profile:"balanced",use_kv_cache:true,cache_text:true},
  "24": {width:1024,height:1024,reference_resolution:768,profile:"low_memory",use_kv_cache:true,cache_text:false},
  "16": {width:768,height:768,reference_resolution:512,profile:"low_memory",use_kv_cache:false,cache_text:false},
  "12": {width:640,height:640,reference_resolution:512,profile:"low_memory",use_kv_cache:false,cache_text:false},
  "8": {width:512,height:512,reference_resolution:512,profile:"low_memory",use_kv_cache:false,cache_text:false}
};
$("gpu-preset").onchange=()=>{const p=gpuPresets[$("gpu-preset").value];if(!p)return;for(const [key,value] of Object.entries(p)){const e=$(fieldMap[key]);if(boolKeys.has(key))e.checked=value;else e.value=value;}syncPreset();};
for(const key of Object.keys(gpuPresets["32"]))$(fieldMap[key]).addEventListener("change",()=>$("gpu-preset").value="custom");
const examples={
  "pigeon-toast":"可愛いポップな漫画イラスト。公園のベンチの上で、丸く太った鳩の首に四角い食パンが一枚すっぽりはまっている。食パンの中央の穴から鳩の顔が出ており、鳩は目を丸くして頬を赤らめ、両翼を横へ広げて慌てている。足元にはパンくず。右手前にはスマートフォンで鳩を撮影する人の手があり、スマホ画面にも食パンにはまった鳩が映る。全体は白いSNS投稿カードの中のイラストで、下部にハートと吹き出しのアイコン。太い輪郭線、黄色と水色とピンク、シンプルで読みやすい構図。",
  "pigeon-soda":"可愛いポップな漫画イラスト。屋外カフェのテーブルで、丸くデフォルメされた鳩がソーダの紙コップを片足で踏み、ピンク色のソーダが噴水のように真上へ噴き出した瞬間。鳩はのけぞり、両翼を大きく広げ、頭には外れたコップの蓋が帽子のように載っている。大きく見開いた目、赤い頬、周囲に水滴と驚きの線。画面の左手前に、この失敗を撮影するスマートフォンと人の手。白いSNS投稿カードの枠と、下部にハートとコメントのアイコン。明るいミントグリーンとピンク、太い輪郭、躍動感のある一コマ。",
  "pigeon-detective":"可愛いポップな漫画イラスト。小さな探偵帽と茶色いケープを着た丸い鳩が、虫眼鏡を持ち、公園の石畳に落ちたパンくずの列を真剣に調べている。パンくずの列は鳩自身の足元まで続き、鳩のくちばしには大きなパンのかけらがくっついている。犯人が自分だと気づいていない得意げな表情。右手前に、鳩の間抜けな姿をスマートフォンで撮影する人の手。鳩の全身、虫眼鏡、パンくずの道が一枚で分かる少し引いた構図。全体をSNS投稿カードで囲み、下部にハートと吹き出しのアイコン。太い輪郭線、鮮やかな黄色とターコイズ、ユーモラスな絵本風。",
  product:"A refined studio photograph of a small ceramic desk lamp on a neutral background. Matte ivory ceramic, soft diffused light, accurate materials, minimal composition with generous negative space, no text.",
  icon:"A single friendly mechanical robot icon, three-quarter view, rounded blue and white parts, clean readable silhouette, soft studio lighting. No text, no border, transparent background.",
  recolor:"衣装の形状、パーツの位置、縫い目、素材の模様、陰影、構図をできるだけ維持して、主要な布の色だけを深いネイビーに変更してください。新しいパーツや装飾は追加しないでください。",
  graphic:'A clean infographic about a simple three-step design process, titled "IDEA TO IMAGE". Three clearly separated panels labeled "01 IDEA", "02 CREATE", "03 REFINE". Minimal geometric icons, excellent typography, white background, balanced generous margins.'
};
document.querySelectorAll("[data-mode]").forEach(b=>b.onclick=()=>selectMode(b.dataset.mode));document.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>selectView(b.dataset.view));
$("prompt").addEventListener("input",countPrompt);$("cfg").addEventListener("input",updateCFG);
$("mask-feather").addEventListener("input",()=>$("feather-value").value=$("mask-feather").value);
$("size-preset").onchange=()=>{if($("size-preset").value!=="custom"){const [w,h]=$("size-preset").value.split(",");$("width").value=w;$("height").value=h;}};
for(const id of ["width","height"]){$(id).addEventListener("input",syncPreset);}
$("example").onchange=()=>{const key=$("example").value;if(state.busy){$("example").value="";return;}if(key.startsWith("pigeon-")){selectMode("generate",false);$("transparent").checked=false;$("quality-preset").value="standard";$("quality-preset").onchange();toast("鳩のサンプルを適用：40 steps / CFG 1 / Euler。サイズ・枚数は現在の設定です。 / Pigeon preset applied; size and count kept.");}if(examples[key]){$("prompt").value=examples[key];countPrompt();if(key==="icon")$("transparent").checked=true;if(key==="recolor")selectMode("edit");}$("example").value="";};
$("dropzone").onclick=()=>$("file-input").click();$("dropzone").onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();$("file-input").click();}};$("file-input").onchange=()=>addFiles([...$("file-input").files]);
for(const ev of ["dragenter","dragover"]){$("dropzone").addEventListener(ev,e=>{e.preventDefault();$("dropzone").classList.add("drag-over");});}
for(const ev of ["dragleave","drop"]){$("dropzone").addEventListener(ev,e=>{e.preventDefault();$("dropzone").classList.remove("drag-over");if(ev==="drop")addFiles([...e.dataTransfer.files]);});}
$("generate").onclick=submit;$("generate-top").onclick=submit;document.addEventListener("keydown",e=>{if((e.ctrlKey||e.metaKey)&&e.key==="Enter"){e.preventDefault();submit();}});
$("unload").onclick=async()=>{try{await api("/api/unload",{method:"POST"});toast("GPU上のモデルと文章キャッシュを解放しました。");await pollStatus();}catch(e){toast(e.message,true);}};
$("send-edit").onclick=async()=>{if(!state.selected||state.busy)return;try{const response=await fetch(state.selected.url);if(!response.ok)throw Error("出力画像を読み込めません。");const file=new File([await response.blob()],"result.png",{type:"image/png"});state.refs=[];selectMode("edit");await addFiles([file]);toast("生成画像を参照画像1に追加しました。");}catch(e){toast(e.message,true);}};
$("reuse-settings").onclick=()=>state.selected&&restoreSettings(state.selected).then(()=>toast("設定を再利用しました。Seedも復元しています。")).catch(e=>toast(e.message,true));
$("refresh-history").onclick=()=>loadHistory().catch(e=>toast(e.message,true));
$("import-settings").onclick=()=>$("settings-file").click();$("settings-file").onchange=async()=>{try{const file=$("settings-file").files[0];if(!file)return;if(file.size>1024*1024)throw Error("設定JSONは1 MiB以下にしてください。");await restoreSettings(JSON.parse(await file.text()));toast("設定を読み込みました。");}catch(e){toast(e.message,true);}finally{$("settings-file").value="";}};
$("export-settings").onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(fields(),null,2)],{type:"application/json"}));const a=element("a");a.href=url;a.download="cvELD-image-settings.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);if(state.mode==="local")toast("描画中のマスクは含みません。生成済みの設定JSONにはマスクIDが記録されます。");};
async function loop(){try{await pollJobs();}catch{ $("queue-count").textContent="接続待ち / Run.batを確認"; }setTimeout(loop,1200);}
async function statusLoop(){try{await pollStatus();}catch{$("model-status").textContent="サーバーに未接続";}setTimeout(statusLoop,5000);}
syncSliders();updateCFG();loop();statusLoop();
