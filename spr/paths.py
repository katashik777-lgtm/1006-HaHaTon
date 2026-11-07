"""Portable project references for storage managed by Скорифай."""
from pathlib import PurePosixPath


def storage_ref(kind, project, dataset, ident):
    parts = (project, dataset, ident)
    if any(not part or part in ('.', '..') or any(c in part for c in '/\\:') for part in parts):
        raise ValueError('Недопустимый идентификатор в хранилище')
    return PurePosixPath('var', kind, *parts)


def storage_path(root, kind, project, dataset, ident):
    return root.joinpath(*storage_ref(kind, project, dataset, ident).parts[1:])


def portable_snapshot(snapshot, project, dataset):
    snapshot = snapshot or {}
    return {**snapshot, 'files': [
        {**item, 'path': storage_ref('sources', project, dataset, item['id']).as_posix()}
        for item in snapshot.get('files', [])
    ]}


def portable_origin(body):
    origin = (body or {}).get('origin')
    if not isinstance(origin, str):
        return body
    normalized = origin.replace('\\', '/')
    if normalized.startswith('/') or (len(normalized) > 1 and normalized[1] == ':'):
        return {**body, 'origin': 'Импорт из каталога'}
    return body
