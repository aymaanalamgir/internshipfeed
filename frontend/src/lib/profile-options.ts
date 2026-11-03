// Define bounded profile choices and alias-aware skill comparison helpers.
export const GRADUATION_YEARS = [2026, 2027, 2028, 2029, 2030];
export const INTERNSHIP_TERMS = GRADUATION_YEARS.flatMap(year => ['Winter', 'Summer', 'Fall'].map(season => `${season} ${year}`));

export function skillKey(value: string, aliases: Record<string, string>): string {
  const key = value.normalize('NFKD').toLowerCase().replace(/\p{M}/gu, '').replace(/[^\p{L}\p{N}.+#]|_/gu, ' ').trim().replace(/\s+/g, ' ');
  return aliases[key] ?? key;
}
export function availableSkills(skills: string[], selected: string[], aliases: Record<string, string>): string[] {
  const existing = new Set(selected.map(value => skillKey(value, aliases)));
  return skills.filter(value => !existing.has(skillKey(value, aliases)));
}
export function addTerm(values: string[], term: string): string[] {
  return INTERNSHIP_TERMS.includes(term) && !values.some(value => value.trim().toLowerCase() === term.toLowerCase()) ? [...values, term] : values;
}
