const normalized=(value:string)=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR').replace(/[^a-z0-9]+/g,' ').trim();

const bigrams=(value:string)=>{
  const text=` ${normalized(value)} `;
  return Array.from({length:Math.max(0,text.length-1)},(_,index)=>text.slice(index,index+2));
};

const editSimilarity=(left:string,right:string)=>{
  if(!left||!right)return 0;
  const row=Array.from({length:right.length+1},(_,index)=>index);
  for(let i=1;i<=left.length;i++){let diagonal=row[0];row[0]=i;for(let j=1;j<=right.length;j++){const previous=row[j];row[j]=Math.min(row[j]+1,row[j-1]+1,diagonal+(left[i-1]===right[j-1]?0:1));diagonal=previous}}
  return 1-row[right.length]/Math.max(left.length,right.length);
};

const similarity=(query:string,candidate:string)=>{
  const left=normalized(query),right=normalized(candidate);
  if(!left)return .1;
  if(left===right)return 1;
  if(right.startsWith(left)||left.startsWith(right))return .94;
  if(right.includes(left)||left.includes(right))return .86;
  const compactRight=right.replace(/ /g,'');
  const chunkScore=Math.max(0,...left.split(' ').map(chunk=>editSimilarity(chunk,compactRight)));
  if(chunkScore>=.7)return .82+chunkScore*.14;
  const a=bigrams(left),b=bigrams(right),remaining=[...b];let matches=0;
  for(const pair of a){const index=remaining.indexOf(pair);if(index>=0){matches++;remaining.splice(index,1)}}
  return a.length+b.length?2*matches/(a.length+b.length):0;
};

export const rankedOptions=(query:string,values:string[],limit=6)=>values
  .map(value=>({value,score:similarity(query,value)}))
  .filter(item=>!query.trim()||item.score>=.28)
  .sort((a,b)=>b.score-a.score||a.value.localeCompare(b.value,'pt-BR'))
  .slice(0,limit)
  .map(item=>item.value);

// Typed text wins. An empty review field ranks from the captured name instead of the alphabetical head.
export const suggestionQuery=(field:string,typed:string,captured?:{merchant_raw?:string;plataforma_sugerida?:string}|null)=>{
  const written=String(typed??'');
  if(written.trim())return written;
  if(field==='vendedor')return String(captured?.merchant_raw||'').trim();
  if(field==='plataforma')return String(captured?.plataforma_sugerida||'').trim();
  return '';
};

export const closestOption=(query:string,values:string[],minimum=.72)=>{
  const candidate=rankedOptions(query,values,1)[0];
  return candidate&&similarity(query,candidate)>=minimum?candidate:'';
};

const brasiliaParts=(value:string)=>{
  if(!value)return null;
  const source=/[zZ]|[+-]\d\d:\d\d$/.test(value)?value:`${value}Z`;
  const date=new Date(source);if(Number.isNaN(date.getTime()))return null;
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(date);
  const get=(type:string)=>parts.find(part=>part.type===type)?.value||'';
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}`;
};

export const toBrasiliaInput=(value:string)=>brasiliaParts(value)||value;
export const fromBrasiliaInput=(value:string)=>{
  if(!value)return value;
  if(/[zZ]|[+-]\d\d:\d\d$/.test(value))return new Date(value).toISOString();
  const parsed=new Date(`${value}-03:00`);
  return Number.isNaN(parsed.getTime())?value:parsed.toISOString();
};

export const formatBrasilia=(value:string)=>{
  const local=brasiliaParts(value);if(!local)return '—';
  const [date,time]=local.split('T');const [year,month,day]=date.split('-');
  return `${day}/${month}/${year} ${time.slice(0,5)}`;
};

// A civil due date stays a date. An instant belongs to the financial day in Brasília.
export const financialDateIso=(value:string)=>{
  if(/^\d{4}-\d{2}-\d{2}$/.test(value||''))return value;
  return brasiliaParts(value)?.slice(0,10)||'';
};
export const financialDateLabel=(value:string)=>{
  const iso=financialDateIso(value);
  return iso?iso.split('-').reverse().join('/'):'—';
};
