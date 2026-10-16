export async function loadCursorItems(api:(path:string,init?:RequestInit)=>Promise<any>,path:string,init?:RequestInit){
  const items:any[]=[];const seen=new Set<string>();let next=path;
  while(next){
    const page=await api(next,init);
    if(!Array.isArray(page?.items))throw new Error('O servidor não confirmou a lista.');
    items.push(...page.items);
    if(!page.has_more)break;
    if(!page.next_cursor||seen.has(page.next_cursor))throw new Error('A continuação da lista está incompleta. Tente atualizar novamente.');
    seen.add(page.next_cursor);next=`${path}${path.includes('?')?'&':'?'}cursor=${encodeURIComponent(page.next_cursor)}`;
  }
  return items;
}
