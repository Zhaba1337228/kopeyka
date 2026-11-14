import React, {useEffect, useRef, useState} from 'react';
import {Icon} from './Controls';
import './history.css';

export default function History({onOpen}) {
  const dialog=useRef(null);
  const [items,setItems]=useState([]), [error,setError]=useState('');
  const [open,setOpen]=useState(false), [loading,setLoading]=useState(false), [opening,setOpening]=useState('');
  useEffect(()=>{
    if(!open)return;
    const previous=document.body.style.overflow;
    document.body.style.overflow='hidden';
    return ()=>{document.body.style.overflow=previous};
  },[open]);
  async function show() {
    setError('');setItems([]);setLoading(true);setOpen(true);dialog.current.showModal();
    try {const response=await fetch('/api/history'); if(!response.ok) throw new Error('История недоступна'); setItems(await response.json());}
    catch(e){setError(e.message)}
    finally{setLoading(false)}
  }
  async function restore(item) {
    setError('');setOpening(item.session);
    try {
      const response=await fetch('/api/session/'+item.session);
      if(!response.ok)throw new Error('Сверка недоступна');
      const data=await response.json();
      if(!data.result&&!data.candidates?.length)throw new Error(item.status);
      onOpen(data);dialog.current.close();
    } catch(e){setError(e.message)}finally{setOpening('')}
  }
  function backdrop(e) {
    if(e.target!==dialog.current)return;
    const rect=dialog.current.getBoundingClientRect();
    if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)dialog.current.close();
  }
  return <div className="history">
    <button onClick={show}><Icon name="clock"/>История сверок</button>
    <dialog ref={dialog} className="history-dialog" aria-labelledby="history-heading" aria-describedby="history-description" onClose={()=>setOpen(false)} onClick={backdrop}>
      <div className="history-header"><div><h2 id="history-heading">История сверок</h2><p id="history-description">Выберите проверку, чтобы вернуться к документам и результатам.</p></div><button className="history-close" aria-label="Закрыть историю" autoFocus onClick={()=>dialog.current.close()}><Icon name="close"/></button></div>
      {error && <div className="history-error" role="alert">{error}</div>}
      <div className="history-list" aria-busy={loading}>
        {loading?<p className="history-empty" role="status">Загружаем историю…</p>:!items.length&&!error?<div className="history-empty"><Icon name="clock"/><strong>Здесь появятся ваши сверки</strong><span>Загрузите документы и запустите первую проверку.</span></div>:items.map(item=><button className="history-entry" key={item.session} disabled={!!opening} onClick={()=>restore(item)}><span className="history-entry-main"><strong>Договор {item.contract} · версия {item.version||1}</strong><span>{item.operation||'Первичная сверка'}</span><time dateTime={item.created}>{new Date(item.created).toLocaleString('ru-RU',{day:'2-digit',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit'})}</time></span><span className="history-entry-status">{opening===item.session?'Открываем…':item.status}</span><Icon name="arrow"/></button>)}
      </div>
      <div className="history-footer"><span>{loading?'':`Сохранено сверок: ${items.length}`}</span><button onClick={()=>dialog.current.close()}>Закрыть</button></div>
    </dialog>
  </div>;
}
