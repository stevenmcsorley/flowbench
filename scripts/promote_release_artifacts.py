"""Promote already tested installers to a draft release with source-tree and digest checks."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def gh(*args):
    return subprocess.check_output(['gh', *map(str, args)])


def api(path):
    return json.loads(gh('api', path))


def tree(ref):
    return subprocess.check_output(['git', 'rev-parse', '--verify', ref + '^{tree}'], text=True).strip()


def promote(repo, tag, native_run, windows_run, verify_only=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository')
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?', tag):
        raise ValueError('Use an existing version tag')
    if min(native_run, windows_run) < 1:
        raise ValueError('Use positive workflow run IDs')
    release = next(r for r in api(f'repos/{repo}/releases?per_page=30') if r['tag_name'] == tag)
    if not release['draft']:
        raise ValueError('Only draft releases can receive promoted artifacts')
    expected_tree = tree(tag)
    version = json.loads(subprocess.check_output(['git', 'show', tag + ':desktop/package.json']))['version']
    if version != tag[1:]:
        raise ValueError('Tag and application versions differ')
    plan = [(native_run, 'flowbench-ubuntu-22.04', 2),
            (native_run, 'flowbench-macos-15', 1),
            (native_run, 'flowbench-macos-15-intel', 1),
            (windows_run, 'flowbench-windows-installer', 1)]
    evidence = []
    for run_id, name, count in plan:
        run = api(f'repos/{repo}/actions/runs/{run_id}')
        if run['conclusion'] != 'success' or not re.fullmatch(r'[a-f0-9]{40}', run['head_sha']):
            raise ValueError('Every source workflow must have passed')
        if tree(run['head_sha']) != expected_tree:
            raise ValueError('A tested source tree differs from the release tag')
        artifacts = api(f'repos/{repo}/actions/runs/{run_id}/artifacts')['artifacts']
        artifact = next(a for a in artifacts if a['name'] == name)
        if artifact['expired']:
            raise ValueError('A tested artifact has expired')
        evidence.append({'workflow_run': run['html_url'], 'run_id': run_id, 'head_sha': run['head_sha'],
                         'artifact_id': artifact['id'], 'artifact_name': name,
                         'artifact_digest': artifact.get('digest'), 'packages': count})
    print('Verified successful workflow provenance and matching source tree:', expected_tree, flush=True)
    if verify_only:
        return
    hashes = {}
    for item in evidence:
        with tempfile.TemporaryDirectory(prefix='flowbench-promote-') as directory:
            gh('run', 'download', item['run_id'], '--name', item['artifact_name'],
               '--dir', directory, '--repo', repo)
            files = [p for p in Path(directory).rglob('*') if p.suffix in ('.deb', '.AppImage', '.dmg', '.exe')]
            if len(files) != item['packages']:
                raise ValueError('Unexpected installer count in artifact')
            for file in files:
                if file.is_symlink() or not file.is_file() or version not in file.name or file.stat().st_size < 1_000_000:
                    raise ValueError('Unexpected installer file')
                with file.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if file.name in hashes:
                    raise ValueError('Duplicate installer name')
                hashes[file.name] = digest
                existing = next((a for a in api(f'repos/{repo}/releases/{release["id"]}')['assets'] if a['name'] == file.name), None)
                if existing and existing['state'] == 'starter':
                    gh('api', '--method', 'DELETE', f'repos/{repo}/releases/assets/{existing["id"]}')
                    existing = None
                if existing:
                    if existing.get('digest') != 'sha256:' + digest:
                        raise ValueError('Existing installer differs; refusing to overwrite it')
                else:
                    gh('release', 'upload', tag, file, '--repo', repo)
                print('Promoted:', file.name, flush=True)
    uploaded = api(f'repos/{repo}/releases/{release["id"]}')['assets']
    packages = [a for a in uploaded if a['name'].endswith(('.exe', '.deb', '.AppImage', '.dmg'))]
    actual = {a['name']: a.get('digest') for a in packages}
    if len(hashes) != 5 or actual != {name: 'sha256:' + digest for name, digest in hashes.items()}:
        raise ValueError('Uploaded package set or digests differ from tested files')
    with tempfile.TemporaryDirectory(prefix='flowbench-release-metadata-') as directory:
        sums = Path(directory) / f'SHA256SUMS-{version}.txt'
        sums.write_text(''.join(digest + '  ' + name + '\n' for name, digest in sorted(hashes.items())), encoding='utf-8')
        provenance = Path(directory) / f'BUILD-PROVENANCE-{version}.json'
        provenance.write_text(json.dumps({'release': tag, 'tested_tree': expected_tree, 'artifacts': evidence,
                                          'package_sha256': hashes}, indent=2) + '\n', encoding='utf-8')
        gh('release', 'upload', tag, sums, provenance, '--repo', repo, '--clobber')
    print('All five installer digests verified. Checksums and provenance uploaded; release remains a draft.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--native-run', type=int, required=True)
    parser.add_argument('--windows-run', type=int, required=True)
    parser.add_argument('--verify-only', action='store_true')
    promote(**vars(parser.parse_args()))
