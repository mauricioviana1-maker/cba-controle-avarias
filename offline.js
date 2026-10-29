/* Piloto offline com IndexedDB. Nunca armazene dados reais corporativos neste modo. */
(() => {
  const id = name => document.getElementById(name);
  const status = message => { id('status').textContent = message; };
  const dbPromise = new Promise((resolve,reject)=>{
    const req = indexedDB.open('cba-piloto-offline-v3',1);
    req.onupgradeneeded = () => { if(!req.result.objectStoreNames.contains('pending'))req.result.createObjectStore('pending',{keyPath:'offline_key'}); };
    req.onsuccess=()=>resolve(req.result); req.onerror=()=>reject(req.error);
  });
  const transact = async (mode, callback) => {
    const database=await dbPromise;
    return new Promise((resolve,reject)=>{
      const tx=database.transaction('pending',mode);
      let result;
      tx.oncomplete=()=>resolve(result); tx.onerror=()=>reject(tx.error);
      callback(tx.objectStore('pending'),v=>result=v);
    });
  };
  const pending=()=>transact('readonly',(store,set)=>{const r=store.getAll();r.onsuccess=()=>set(r.result);});
  const save=payload=>transact('readwrite',store=>store.put(payload));
  const remove=key=>transact('readwrite',store=>store.delete(key));
  let onlineUser = null;
  async function identity() {
    if(!navigator.onLine) return null;
    try {
      const res=await fetch('/api/session',{credentials:'same-origin',cache:'no-store'});
      if(!res.ok) return null;
      const user=await res.json();
      if(user.authenticated){onlineUser=user.username;localStorage.setItem('cba_offline_owner',user.username);return user;}
    } catch(err){}
    return null;
  }
  async function render(){
    id('connection').textContent=navigator.onLine?'Conectado à internet':'Sem conexão — os dados permanecerão na fila';
    const all=await pending();
    id('queue').textContent=all.length?`${all.length} registro(s) aguardando sincronização: ${all.map(x=>x.sku).join(', ')}`:'Nenhum registro pendente.';
  }
  async function sync(){
    const who=await identity();
    if(!who){status('Entre no CBA pelo navegador e retorne aqui com internet para sincronizar.');return;}
    const all=await pending(); let sent=0;
    for(const data of all){
      if(data.owner!==who.username){status('Há dados de outra conta neste dispositivo. Não foram enviados.');continue;}
      try{
        const response=await fetch('/api/offline/damages',{method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/json','X-CSRF-Token':who.csrf_token},
          body:JSON.stringify(data)});
        if(response.status===401||response.status===302){status('Sessão encerrada. Faça login novamente.');break;}
        const result=await response.json();
        if(response.ok){await remove(data.offline_key);sent++;status(`Sincronizado: protocolo #${result.id}.`);}
        else{status(`Registro ${data.sku} não enviado: ${result.error||response.status}.`);}
      }catch(err){status('Sem conexão durante a sincronização; registros preservados.');break;}
    }
    if(sent) status(`${sent} ocorrência(s) enviada(s) com sucesso.`);
    await render();
  }
  id('sync').addEventListener('click',sync);
  window.addEventListener('online',()=>{render();sync();});
  window.addEventListener('offline',render);
  if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(()=>{});}
  identity().then(render).catch(err=>status('Falha ao preparar o modo offline: '+err.message));
})();
