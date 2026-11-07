import React from 'react';
export function QualityComparison({rows,open}:any){
 if(!rows)return <p role="status">Загружаем показатели качества…</p>;
 const statuses=['parsed','missing','parse_error','schema_error','id_conflict','duplicate'];
 const labels:any={parsed:'Успешно прочитан',missing:'Ответ отсутствует',parse_error:'Ошибка разбора',schema_error:'Нарушение схемы или типа',id_conflict:'Несовпадение ID',duplicate:'Дубликат'};
 const totals:any={BEFORE:0,AFTER:0};for(const r of rows)totals[r.SOURCE]+=r.CNT;
 const count=(status:string,side:string)=>rows.find((r:any)=>r.STATUS===status&&r.SOURCE===side)?.CNT||0;
 if(!rows.length)return <p role="status">Нет результатов качества по выбранным фильтрам.</p>;
 const issues=rows.filter((r:any)=>r.STATUS!=='parsed').reduce((n:number,r:any)=>n+r.CNT,0);
 return <div className="form-pad"><p role="status">{issues?'Найдены нарушения или несопоставленные ответы.':'Проверка завершена без нарушений.'} База — строки реестра импорта соответствующей версии, включая ожидаемые отсутствующие ответы.</p><div className="table-wrap"><table><thead><tr><th>Показатель</th><th>До · число / база · доля</th><th>После · число / база · доля</th><th>Разница, п. п.</th></tr></thead><tbody>{statuses.map(status=><tr key={status}><td>{labels[status]}</td>{['BEFORE','AFTER'].map(side=><td key={side}>{totals[side]?<button disabled={!count(status,side)} onClick={()=>open(status,side)}>{count(status,side)} / {totals[side]} · {(100*count(status,side)/totals[side]).toFixed(2)}%</button>:'Нет данных'}</td>)}<td>{totals.BEFORE&&totals.AFTER?(100*(count(status,'AFTER')/totals.AFTER-count(status,'BEFORE')/totals.BEFORE)).toFixed(2):'—'}</td></tr>)}</tbody></table></div></div>
}
