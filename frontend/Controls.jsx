import React, {useId, useRef, useState} from 'react';

export function Icon({name, ...props}) {
  const paths = {
    upload: 'M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4',
    file: 'M14 3H5v18h14V8l-5-5v5h5M8 12h8M8 16h6',
    layers: 'm12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5',
    calendar: 'M5 5h14v16H5V5Zm3-3v6m8-6v6M5 10h14',
    arrow: 'M4 12h16m-6-6 6 6-6 6',
    check: 'm5 12 4 4L19 6',
    clock: 'M12 8v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18',
    close: 'm6 6 12 12M6 18 18 6',
  };
  return <svg {...props} aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name] || paths.file}/></svg>;
}

export function UploadZone({title, caption, accept, files, onChange, multiple=false, disabled=false}) {
  const id=useId(), input=useRef(null), [drag,setDrag]=useState(false);
  const count=files.length;
  return <div className={`upload-zone ${drag?'dragging':''} ${count?'has-files':''}`}
    onDragOver={e=>{e.preventDefault();if(!disabled)setDrag(true)}} onDragLeave={()=>setDrag(false)}
    onDrop={e=>{e.preventDefault();setDrag(false);if(!disabled)onChange(Array.from(e.dataTransfer.files).slice(0,multiple?500:1))}}>
    <div className="upload-heading"><span className="file-symbol"><Icon name={multiple?'layers':'file'}/></span><div><label htmlFor={id}>{title}</label><p>{caption}</p></div>{count>0 && <span className="file-ready" aria-label="Файлы выбраны"><Icon name="check"/></span>}</div>
    <input id={id} ref={input} className="sr-only" type="file" accept={accept} multiple={multiple} disabled={disabled} onChange={e=>onChange(Array.from(e.target.files))}/>
    <div className="drop-content"><button type="button" className="choose-file" disabled={disabled} onClick={()=>input.current.click()}><Icon name="upload"/>{count?'Изменить выбор':multiple?'Выбрать документы':'Выбрать файл'}</button><span>или перетащите сюда</span></div>
    <div className="file-summary" aria-live="polite">{count ? <><strong>{multiple?`${count} файл(ов) выбрано`:files[0].name}</strong><span>{(files.reduce((sum,f)=>sum+f.size,0)/1024/1024).toLocaleString('ru-RU',{maximumFractionDigits:1})} МБ</span></> : <span>{multiple?'Можно загрузить весь комплект одним ZIP-архивом':'Карточка с итоговыми суммами за выбранный период'}</span>}</div>
  </div>;
}

export function DateField({label,value,onChange}) {
  const dialog=useRef(null), trigger=useRef(null), [month,setMonth]=useState(value.slice(0,7));
  const [year,number]=month.split('-').map(Number);
  const days=new Date(year,number,0).getDate(), offset=(new Date(year,number-1,1).getDay()+6)%7;
  function shift(delta){const date=new Date(year,number-1+delta,1);setMonth(`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}`)}
  function close(){dialog.current.close();trigger.current.focus()}
  return <div className="date-field"><span>{label}</span><button type="button" ref={trigger} className="date-trigger" onClick={()=>{setMonth(value.slice(0,7));dialog.current.showModal()}}>{value.split('-').reverse().join('.')}<Icon name="calendar"/></button>
    <dialog ref={dialog} className="calendar-dialog" aria-label={label} onClick={e=>{if(e.target===dialog.current)close()}}><div className="calendar-heading"><strong>{label}</strong><button type="button" aria-label="Закрыть календарь" onClick={close}><Icon name="close"/></button></div><div className="calendar-month"><button type="button" aria-label="Предыдущий месяц" onClick={()=>shift(-1)}>‹</button><label className="sr-only" htmlFor={`${label}-month`}>Месяц и год</label><input id={`${label}-month`} type="month" value={month} onChange={e=>{if(/^\d{4}-\d{2}$/.test(e.target.value))setMonth(e.target.value)}}/><button type="button" aria-label="Следующий месяц" onClick={()=>shift(1)}>›</button></div><div className="calendar-grid">{['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(d=><span key={d}>{d}</span>)}{Array.from({length:offset},(_,i)=><span key={'blank'+i}/>)}{Array.from({length:days},(_,i)=>{const date=`${month}-${String(i+1).padStart(2,'0')}`;return <button type="button" key={date} aria-pressed={value===date} className={value===date?'selected':''} onClick={()=>{onChange(date);close()}}>{i+1}</button>})}</div></dialog>
  </div>;
}
