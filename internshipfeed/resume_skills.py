"""Controlled, phrase-aware résumé skills; no profile mutation or inference."""
import re
import unicodedata

from .enrich import VOCABULARY
from .matching import SKILL_ALIASES, SKILL_LABELS, TECHNICAL_CONCEPTS, normalize_skill


def skill_labels():
    values = [value for category, terms in VOCABULARY.items() for value in terms
              if category != 'concepts' or normalize_skill(value) in TECHNICAL_CONCEPTS]
    return {normalize_skill(value): SKILL_LABELS.get(normalize_skill(value), value) for value in values}


def skill_aliases():
    """Expose the matching system's normalization to the draft suggestion filter."""
    labels = skill_labels()
    return {alias: key for alias, key in SKILL_ALIASES.items() if key in labels}


def extract_resume_skills(text: str):
    labels = skill_labels()
    forms = {label: key for key, label in labels.items()}
    forms.update(skill_aliases())
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(char for char in text if not unicodedata.combining(char))
    candidates = []
    for form, key in forms.items():
        # Permit line breaks and common separators in multiword technical phrases.
        pattern = r'(?<![\w+#])' + r'[\s/_-]+'.join(re.escape(part) for part in re.split(r'[\s/_-]+', form)) + r'(?![\w+#])'
        if key == 'r':
            pattern += r'(?![&/])'
        # Bare C, R and Go are ambiguous in prose; retain existing vocabulary rules.
        flags = 0 if form in {'C', 'R', 'Go'} else re.I
        candidates.extend((match.start(), match.end(), key) for match in re.finditer(pattern, text, flags))
    # Longest phrases win: React Native must not imply React, C++ must not imply C.
    spans, found = [], set()
    for start, end, key in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0], item[2])):
        if any(start < right and end > left for left, right in spans):
            continue
        spans.append((start, end))
        found.add(key)
    return [labels[key] for key in sorted(found)]
