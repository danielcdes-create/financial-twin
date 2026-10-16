type Api = (path: string) => Promise<any>;
export const commitmentListSources = [
  {key: 'managed', path: '/v1/commitments', label: 'Compromissos cadastrados'},
  {key: 'installments', path: '/v1/installments', label: 'Parcelamentos importados'},
  {key: 'obligations', path: '/v1/future-commitments/obligations', label: 'Obrigações futuras'},
] as const;

export async function loadCommitmentLists(api: Api) {
  const results = await Promise.allSettled(commitmentListSources.map(async source => {
    const response = await api(source.path);
    if (!Array.isArray(response?.items)) throw new Error('O servidor não confirmou a lista.');
    const items = [...response.items] as any[];
    const cutoff = typeof response.commitment_cutoff_date === 'string' ? response.commitment_cutoff_date : null;
    let page = response;
    let previous = 0;
    while (page.has_more) {
      if (!Number.isInteger(page.next_offset) || page.next_offset <= previous) throw new Error('Continuação da lista inválida.');
      previous = page.next_offset;
      page = await api(`${source.path}?offset=${previous}`);
      if (!Array.isArray(page?.items)) throw new Error('O servidor não confirmou a continuação da lista.');
      items.push(...page.items);
    }
    return {items, cutoff};
  }));
  const lists: Partial<Record<typeof commitmentListSources[number]['key'], any[]>> = {};
  const errors: string[] = [];
  let commitmentCutoff: string | null = null;
  results.forEach((result, index) => {
    const source = commitmentListSources[index];
    if (result.status === 'fulfilled') {
      lists[source.key] = result.value.items;
      if (source.key === 'managed' && result.value.cutoff) commitmentCutoff = result.value.cutoff;
    }
    else errors.push(`${source.label}: ${result.reason instanceof Error ? result.reason.message : 'Não foi possível atualizar.'}`);
  });
  return {lists, errors, commitmentCutoff};
}
