import type { CandidateProfile, PendingTags, ProfileDraft, ProfileValues, TagField } from '../types/profile';

export const TAG_FIELDS: TagField[] = ['target_roles', 'skills', 'preferred_locations'];

export function emptyPending(): PendingTags {
  return { target_roles: '', skills: '', preferred_locations: '' };
}

export function addTag(values: string[], input: string): string[] {
  const value = input.trim();
  return !value || values.includes(value) ? values : [...values, value];
}

export function removeTag(values: string[], index: number): string[] {
  return values.filter((_, position) => position !== index);
}

export function toDraft(profile: CandidateProfile): ProfileDraft {
  return {
    target_roles: [...profile.target_roles], skills: [...profile.skills],
    preferred_locations: [...profile.preferred_locations], preferred_terms: [...profile.preferred_terms],
    graduation_year: profile.graduation_year === null ? '' : String(profile.graduation_year),
  };
}

export function graduationYearError(value: string): string | undefined {
  if (!value.trim()) return undefined;
  if (!/^\d{4}$/.test(value) || Number(value) < 1900 || Number(value) > 2200) {
    return 'Enter a year from 1900 to 2200, or leave it blank.';
  }
  return undefined;
}

export function buildProfilePayload(draft: ProfileDraft, pending: PendingTags): ProfileValues {
  const error = graduationYearError(draft.graduation_year);
  if (error) throw new Error(error);
  return {
    target_roles: addTag(draft.target_roles, pending.target_roles),
    skills: addTag(draft.skills, pending.skills),
    preferred_locations: addTag(draft.preferred_locations, pending.preferred_locations),
    preferred_terms: [...draft.preferred_terms],
    graduation_year: draft.graduation_year.trim() ? Number(draft.graduation_year) : null,
  };
}

export function validatePdf(file: Pick<File, 'name' | 'size' | 'type'>): string | undefined {
  if (!file.name.toLowerCase().endsWith('.pdf')) return 'Only PDF files are supported.';
  if (file.type && !['application/pdf', 'application/octet-stream'].includes(file.type.toLowerCase())) {
    return 'Only PDF files are supported.';
  }
  if (file.size > 5 * 1024 * 1024) return 'Choose a PDF of 5 MiB or less.';
  return undefined;
}
