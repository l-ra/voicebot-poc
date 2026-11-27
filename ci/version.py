"""Derive immutable build identities from GitHub event metadata."""
import os
import re

STABLE = r'(?:0|[1-9][0-9]*)'
RELEASE = re.compile(rf'({STABLE})\.({STABLE})\.({STABLE})(?:-rc\.([1-9][0-9]*))?\Z')

def resolve(ref, event, run, attempt):
    if ref == 'refs/heads/main' and event in ('push','workflow_dispatch'):
        version = f'0.0.0-dev.{int(run)}.{int(attempt)}'
        return dict(version=version, image_tag=version, release='false', prerelease='true')
    if event == 'push' and ref.startswith('refs/tags/v'):
        version = ref.removeprefix('refs/tags/v')
        match = RELEASE.fullmatch(version)
        if not match or tuple(map(int, match.groups()[:3])) == (0,0,0):
            raise ValueError('Expected vX.Y.Z or vX.Y.Z-rc.N (not 0.0.0)')
        return dict(version=version, image_tag=version, release='true', prerelease=str(bool(match.group(4))).lower())
    raise ValueError('Unsupported publication event')

if __name__ == '__main__':
    data=resolve(os.environ['GITHUB_REF'],os.environ['GITHUB_EVENT_NAME'],os.environ['GITHUB_RUN_NUMBER'],os.environ['GITHUB_RUN_ATTEMPT'])
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:
        for key,value in data.items(): print(f'{key}={value}',file=out)
