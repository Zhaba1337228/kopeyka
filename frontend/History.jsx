import React, {useState} from 'react';

export default function History({onOpen}) {
  const [items,setItems]=useState(null), [error,setError]=useState('');
  async function show() {
    try {const response=await fetch('/api/history'); if(!response.ok) throw new Error('История недоступна'); setItems(await response.json());}
    catch(e){setError(e.message)}
  }
  return <div className="history"><button onClick={show}>История сверок</button>{error && <p role="alert">{error}</p>}{items && <div className="history-panel"><div className="page-head"><h2>История сверок</h2><button onClick={()=>setItems(null)}>Закрыть</button></div>{!items.length && <p>Сохранённых сверок пока нет.</p>}{items.map(item=><button className="history-item" key={item.session} onClick={async()=>{try {const r=await fetch('/api/session/'+item.session); if(!r.ok) throw new Error('Сверка недоступна'); const data=await r.json(); if(!data.result && !data.candidates?.length) throw new Error(item.status); onOpen(data);setItems(null);} catch(e){setError(e.message)}}}><span>{new Date(item.created).toLocaleString('ru-RU')} · {item.contract}</span><strong>{item.status}</strong></button>)}</div>}</div>;
}
