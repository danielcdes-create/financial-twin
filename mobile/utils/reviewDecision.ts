const civilDay=(value:any)=>value?new Date(value).toLocaleDateString('en-CA',{timeZone:'America/Sao_Paulo'}):'';
export function reviewHasDifferences(review:any){
  const a=review.source,b=review.candidate;if(!a||!b)return false;
  return Math.abs(Number(a.valor||0))!==Math.abs(Number(b.valor||0))||
    (!!a.data_evento&&!!b.data_evento&&civilDay(a.data_evento)!==civilDay(b.data_evento))||
    ['natureza','fluxo','account_id','account_profile_id'].some(key=>(a[key]||b[key])&&String(a[key]||'')!==String(b[key]||''));
}
export function reviewDecisionBody(review:any,action:string,acknowledgeDifference=false){
  if(!review?.review_token||!review.source_token||!review.candidate_token)throw new Error('Recarregue a revisão para conferir as fontes atuais.');
  return {action,review_token:review.review_token,source_token:review.source_token,candidate_token:review.candidate_token,acknowledge_difference:acknowledgeDifference};
}
