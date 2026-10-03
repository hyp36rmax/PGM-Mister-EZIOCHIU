"""Pure, closed vocabulary for artifact descriptions. No source access or I/O."""
import re


TITLES = {
    'Knights of Valour': ('kov',),
    'Knights of Valour Plus': ('kovplus',),
    'Knights of Valour Super Heroes': ('kovsh',),
    'Knights of Valour Super Heroes Plus': ('kovshp',),
    'Knights of Valour 2': ('kov2',),
    'Knights of Valour 2 Plus': ('kov2p',),
    'Oriental Legend': ('orlegend',),
    'Oriental Legend Super': ('olds',),
    'Oriental Legend Super Plus': ('oldsplus',),
    'Demon Front': ('dmnfrnt',),
    'DoDonPachi II': ('ddp2',),
    'Martial Masters': ('martmast',),
    'The Killing Blade': ('killbld',),
    'The Killing Blade Plus': ('killbldp',),
    'The Gladiator': ('theglad',),
    'Spectral vs Generation': ('svg', 's.v.g.'),
    'Dragon World': ('drgw2', 'drgw3', 'dw2001', 'dwpc', 'chuugokuryuu 2001', 'chuugokuryuu pretty chance'),
    'Photo Y2K': ('photoy2k', 'py2k2'),
    'Puzzli 2': ('puzzli2',),
    'Puzzle Star': ('puzlstar',),
    'Happy 6-in-1': ('happy6', 'huanle liuhe yi'),
}
ALIASES = sorted(((alias.casefold(), title) for title, aliases in TITLES.items()
                  for alias in (title,) + aliases), key=lambda pair: (-len(pair[0]), pair[0]))


def title_at_start(text):
    text = text.casefold().lstrip('_').removesuffix('.mra')
    for alias, title in ALIASES:
        if text == alias or text.startswith(alias + ' ') or text.startswith(alias + '-'):
            # Distinct numbered families must never collapse into the base family.
            tail = text[len(alias):].strip(' -')
            if tail and re.match(r'(?:plus|super|ii|iii|[0-9]+)\b', tail) and title not in ('Dragon World', 'Photo Y2K'):
                return None
            return title
    return None


def artifact_title(path):
    parts = path.split('/')
    filename = title_at_start(parts[-1])
    if path.startswith('_PGM/_alternatives/'):
        hierarchy = {title_at_start(part) for part in parts[2:-1]} - {None}
        if len(hierarchy) > 1 or (filename and hierarchy and filename not in hierarchy):
            return None
        return filename or next(iter(hierarchy), None)
    return filename


def parse_meaning(subject):
    """Accept only artifact nouns, known titles and conservative action words."""
    if len(subject) > 120 or not re.fullmatch(r'[A-Za-z0-9 ,&-]+', subject):
        return None
    text = subject.casefold()
    actions = re.findall(r'\b(update|fix|correct|resolve|refresh|add|remove)\b', text)
    if not actions or not text.startswith(actions[0] + ' '):
        return None
    if len(actions) > 1 and not (actions == ['fix', 'update'] and ' and update ' in text):
        return None
    titles = set()
    for alias, title in ALIASES:
        pattern = r'(?<![a-z0-9-])' + re.escape(alias) + r'(?![a-z0-9-])'
        if re.search(pattern, text):
            titles.add(title)
            text = re.sub(pattern, ' title ', text)
    words = re.findall(r'[a-z0-9-]+', text)
    allowed = {'update', 'fix', 'correct', 'resolve', 'refresh', 'add', 'remove',
               'title', 'mra', 'mras', 'file', 'files', 'definition', 'definitions',
               'game', 'games', 'primary', 'alternate', 'alternative', 'alternatives',
               'revision', 'revisions', 'pgm', 'pgm-027a', 'core', 'cores', 'rbf',
               'implementation', 'and'}
    if set(words) - allowed or any(words.count(w) > 1 for w in ('core', 'cores', 'mra', 'mras')):
        return None
    groups = set()
    alternative = bool(set(words) & {'alternate', 'alternative', 'alternatives', 'revision', 'revisions'})
    if titles or set(words) & {'mra', 'mras', 'definition', 'definitions'}:
        groups.add('Alternative MRA' if alternative else 'MRA')
        if alternative and 'primary' in words:
            groups.add('MRA')
    if set(words) & {'core', 'cores', 'rbf', 'implementation'}:
        groups.add('Cores')
    if not groups:
        return None
    return actions[0], groups, titles, 'pgm-027a' in words, 'revision' in words or 'revisions' in words


def description(group, titles, count, specific_core=False, revision=False):
    if group == 'Cores':
        return ('PGM-027A' if specific_core else 'PGM') + ' core'
    if not titles or len(titles) > 2:
        return 'alternative MRA files' if group == 'Alternative MRA' else 'PGM MRA files'
    title = ' and '.join(sorted(titles))
    if group == 'Alternative MRA':
        noun = 'MRA revision' if revision else 'alternative MRA'
        return title + ' ' + noun + ('s' if count > 1 else '')
    return title + (' MRAs' if len(titles) > 1 else ' MRA files' if count > 1 else ' MRA')


def normalized(subject, active, baseline=None):
    meaning = parse_meaning(subject)
    if meaning is None:
        return None
    action, groups, titles, specific_core, revision = meaning
    if groups != set(active):
        return None
    mras = [p for g, paths in active.items() if g != 'Cores' for p in paths]
    actual_titles = {artifact_title(p) for p in mras}
    if titles and (None in actual_titles or titles != actual_titles):
        return None
    if specific_core and (not active.get('Cores') or not all(
            re.fullmatch(r'PGM-027A(?:[- ][A-Za-z0-9-]+)?\.rbf', p.rsplit('/', 1)[-1], re.I)
            for p in active['Cores'])):
        return None
    contents = [content for paths in active.values() for content in paths.values()]
    verb = 'Update'
    if action == 'remove':
        if any(content is not None for content in contents):
            return None
        verb = 'Remove'
    elif action == 'add':
        if baseline is None or any(p in baseline or content is None for paths in active.values()
                                   for p, content in paths.items()):
            return None
        verb = 'Add'
    elif any(content is None for content in contents):
        return None
    parts = [description(g, {artifact_title(p) for p in paths} if titles and g != 'Cores' else set(),
                         len(paths), specific_core, revision)
             for g, paths in active.items()]
    target = verb + ' ' + ' and '.join(parts)
    return target if len(target) <= 72 else None


def release_context(subjects, changes):
    """Match exact preservation templates to the net diff; ignore arbitrary prose."""
    claims = set()
    for subject in subjects:
        meaning = parse_meaning(subject)
        if meaning is None or not subject.startswith(('Update ', 'Add ', 'Remove ')):
            continue
        action, groups, titles, specific_core, revision = meaning
        state = {'update': 'Updated', 'add': 'Added', 'remove': 'Removed'}.get(action)
        if state is None or (not titles and not specific_core):
            continue
        # Reconstruct a finite public template rather than reusing the input.
        parts = []
        pending = []
        valid = True
        for group in ('MRA', 'Alternative MRA', 'Cores'):
            if group not in groups:
                continue
            label = 'Primary MRA' if group == 'MRA' else group
            paths = changes[label][state]
            matched = [p for p in paths if (not specific_core or re.fullmatch(
                r'PGM-027A(?:[- ][A-Za-z0-9-]+)?\.rbf', p.rsplit('/', 1)[-1], re.I))] if group == 'Cores' else [
                p for p in paths if artifact_title(p) in titles]
            matched_titles = {artifact_title(p) for p in matched} if group != 'Cores' else set()
            if (group != 'Cores' and matched_titles != titles) or (group == 'Cores' and not matched):
                valid = False
                break
            part = description(group, matched_titles, len(matched), specific_core, revision)
            parts.append(part)
            if group == 'Cores':
                pending.append((state, group, part))
            else:
                for title in matched_titles:
                    count = sum(artifact_title(p) == title for p in matched)
                    pending.append((state, group, description(group, {title}, count, revision=revision)))
        if not valid or not pending:
            continue
        # Singular/plural wording may differ after several daily changes accumulate.
        templates = {action.capitalize() + ' ' + ' and '.join(parts)}
        for counts in (1, 2):
            templates.add(action.capitalize() + ' ' + ' and '.join(
                description(g, titles if g != 'Cores' else set(), counts, specific_core, revision)
                for g in ('MRA', 'Alternative MRA', 'Cores') if g in groups))
        if subject not in templates:
            continue
        claims.update(pending)
    # Large batches retain generic counted highlights instead of a title catalogue.
    if len(claims) > 6:
        return []
    return [state + ' ' + part for state, group, part in sorted(claims,
            key=lambda c: (('Cores', 'MRA', 'Alternative MRA').index(c[1]), c[0], c[2]))]
