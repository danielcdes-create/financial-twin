// Three-way rebase: only fields changed by the user can be reapplied. A full
// old form is never used as a patch against a newly loaded source.
export function rebaseDraft<T extends Record<string, any>>(base: T, current: T, draft: T) {
  const merged = {...current} as T;
  const conflicts: string[] = [];
  for (const field of Object.keys(draft)) {
    if (JSON.stringify(draft[field]) === JSON.stringify(base[field])) continue;
    if (JSON.stringify(current[field]) !== JSON.stringify(base[field]) && JSON.stringify(current[field]) !== JSON.stringify(draft[field])) conflicts.push(field);
    (merged as Record<string, any>)[field] = draft[field];
  }
  return {draft: merged, conflicts};
}
