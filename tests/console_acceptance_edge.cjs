'use strict';
// Acceptance-only CDP transport. Reuses local Edge, no third-party framework.
// Leaves all profiles/evidence inside this workspace and never deletes files.
const fs=require('node:fs'), path=require('node:path'), {spawn}=require('node:child_process');
const S=require('../docs/delivery/assistant-console/visual/shoot.cjs');
const base=path.resolve('inbox/console/P6a-r1/edge');fs.mkdirSync(base,{recursive:true});
const cfgfile=path.join(base,'connection.json');
(async()=>{
 const [op,...args]=process.argv.slice(2);
 if(op==='start'){
  const port=9783, exe='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
  if(await fetch('http://127.0.0.1:'+port+'/json/version').then(()=>true,()=>false))throw Error('Test port 9783 is already in use; do not attach to or terminate an unknown browser.');
  const proc=spawn(exe,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-address=127.0.0.1','--remote-debugging-port='+port,'--user-data-dir='+path.join(base,'profile'),'about:blank'],{stdio:'ignore',detached:true,windowsHide:true});proc.unref();
  fs.writeFileSync(cfgfile,JSON.stringify({port,pid:proc.pid}));
  for(let i=0;i<50;i++){await S.sleep(100);try{await fetch('http://127.0.0.1:'+port+'/json/list');console.log(JSON.stringify({ok:true,result:{port,pid:proc.pid}}));return;}catch{}}
  throw Error('Edge did not start');
 }
 const cfg=JSON.parse(fs.readFileSync(cfgfile));
 if(op==='stop'){process.kill(cfg.pid);console.log(JSON.stringify({ok:true,result:'stopped'}));return;}
 const targets=await (await fetch('http://127.0.0.1:'+cfg.port+'/json/list')).json();
 if(!cfg.mainTarget){cfg.mainTarget=targets.find(t=>t.type==='page'&&t.url.startsWith('file:'))?.id||targets.find(t=>t.type==='page').id;fs.writeFileSync(cfgfile,JSON.stringify(cfg));}
 const requested=args.includes('--page')?args[args.indexOf('--page')+1]:null;
 const target=targets.find(t=>t.id===(requested&&requested!=='edge-cdp'?requested:cfg.mainTarget));
 if(!target)throw Error('missing page '+requested);
 const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
 let seq=0;const pending=new Map();
 ws.onmessage=e=>{const d=JSON.parse(e.data);if(pending.has(d.id)){const [r,j]=pending.get(d.id);pending.delete(d.id);d.error?j(Error(JSON.stringify(d.error))):r(d.result);}};
 const c={ws,send:(method,params={})=>new Promise((r,j)=>{const id=++seq;pending.set(id,[r,j]);ws.send(JSON.stringify({id,method,params}));})};let result;
 await c.send('Emulation.setDeviceMetricsOverride',{width:cfg.width||1280,height:cfg.height||800,deviceScaleFactor:1,mobile:(cfg.width||1280)<600});
 await c.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:cfg.scheme||'light'},{name:'prefers-reduced-motion',value:cfg.reduced?'reduce':'no-preference'}]});
 await c.send('Network.enable');await c.send('Network.emulateNetworkConditions',{offline:!!cfg.offline,latency:0,downloadThroughput:-1,uploadThroughput:-1});
 const val=(key)=>args[args.indexOf(key)+1];
 try {
  if(op==='goto') {await c.send('Page.navigate',{url:val('--url')});await S.sleep(350); result={url:val('--url')};}
  else if(op==='eval') result={result:await S.evaluate(c,val('--expression'))};
  else if(op==='snapshot') {
   const ax=(await c.send('Accessibility.getFullAXTree')).nodes;let refs={},snapshot=[];
   ax.forEach((n,i)=>{if(n.ignored||!n.role)return;const role=n.role.value,name=n.name?.value||'';const id='e'+i;refs[id]={role,name,backend:n.backendDOMNodeId};snapshot.push(role+' "'+name+'"');});
   fs.writeFileSync(path.join(base,'refs.json'),JSON.stringify(refs)); result={refs,snapshot:snapshot.join('\n'),browserPageId:'edge-cdp'};
  }
  else if(op==='click-selector') {
   const b=await S.evaluate(c,`(()=>{const e=document.querySelector(${JSON.stringify(val('--selector'))});if(!e||!e.getClientRects().length)throw Error('target is hidden');e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
   await c.send('Input.dispatchMouseEvent',{type:'mousePressed',...b,button:'left',clickCount:1});
   await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',...b,button:'left',clickCount:1});
   result={done:true};
  }
  else if(['click','scrollintoview','fill','focus'].includes(op)) {
   const refs=JSON.parse(fs.readFileSync(path.join(base,'refs.json')));const ref=refs[val('--element').replace('@','')];if(!ref)throw Error('stale ref');
   const {object}=await c.send('DOM.resolveNode',{backendNodeId:ref.backend});
   await c.send('Runtime.callFunctionOn',{objectId:object.objectId,functionDeclaration:'function(){this.scrollIntoView({block:"center"});}'});
   if(op==='click'){
    const box=await c.send('DOM.getBoxModel',{backendNodeId:ref.backend});const b=box.model.content;const x=(b[0]+b[2]+b[4]+b[6])/4,y=(b[1]+b[3]+b[5]+b[7])/4;
    await c.send('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});
   } else if(op==='fill'){
    await c.send('Runtime.callFunctionOn',{objectId:object.objectId,functionDeclaration:'function(){this.focus();this.select();}'});await c.send('Input.insertText',{text:val('--value')});
   } else if(op==='focus')await c.send('DOM.focus',{backendNodeId:ref.backend});
   await S.sleep(60);result={done:true};
  }
  else if(op==='screenshot'){const p=await c.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});result={data:p.data};}
  else if(op==='set') {
   if(args[0]==='offline'){cfg.offline=val('--state')==='on';fs.writeFileSync(cfgfile,JSON.stringify(cfg));await c.send('Network.enable');await c.send('Network.emulateNetworkConditions',{offline:val('--state')==='on',latency:0,downloadThroughput:-1,uploadThroughput:-1});}
   else if(args[0]==='media'){cfg.scheme=val('--color-scheme')||cfg.scheme;await c.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:cfg.scheme||'light'},{name:'prefers-reduced-motion',value:cfg.reduced?'reduce':'no-preference'}]});fs.writeFileSync(cfgfile,JSON.stringify(cfg));}
   result={done:true};
  }
  else if(op==='exec'){
   const cmd=val('--command').split(' ');if(cmd[0]==='set'&&cmd[1]==='viewport'){cfg.width=+cmd[2];cfg.height=+cmd[3];fs.writeFileSync(cfgfile,JSON.stringify(cfg));await c.send('Emulation.setDeviceMetricsOverride',{width:+cmd[2],height:+cmd[3],deviceScaleFactor:1,mobile:+cmd[2]<600});result={done:true};}else throw Error('unsupported exec');
  }
  else if(op==='keypress'){await c.send('Input.dispatchKeyEvent',{type:'keyDown',key:val('--key'),code:val('--key'),windowsVirtualKeyCode:13});await c.send('Input.dispatchKeyEvent',{type:'keyUp',key:val('--key'),code:val('--key')});result={done:true};}
  else if(op==='tab'&&args[0]==='list'){const tabs=await(await fetch('http://127.0.0.1:'+cfg.port+'/json/list')).json();result={tabs:tabs.filter(t=>t.type==='page').map(t=>({...t,browserPageId:t.id}))};}
  else throw Error('unsupported '+op);
  console.log(JSON.stringify({ok:true,result}));
 }finally {c.ws.close();}
})().catch(e=>{console.log(JSON.stringify({ok:false,error:String(e.stack)}));process.exitCode=1;});
