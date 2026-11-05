import hashlib
import json
import zipfile

import pytest
from export_release_contract import snapshot
from verify_release_candidate import verify


def wheel(tmp_path, runtime=None, version='0.24.0', metadata_version=None):
    p=tmp_path/'events.whl'
    files={'controlplane_events/__init__.py': b'__all__ = ["Public"]\nPublic = 1\n',
           'controlplane_events/schemas/event.json': b'{"type":"object"}'}
    files.update(runtime or {})
    with zipfile.ZipFile(p,'w') as z:
        for name,value in files.items():z.writestr(name,value)
        z.writestr(f'controlplane_events-{version}.dist-info/METADATA',f'Name: controlplane-events\nVersion: {metadata_version or version}\n')
    return p


def test_every_packaged_runtime_byte_is_covered(tmp_path):
    original=json.loads(snapshot(wheel(tmp_path))[0])
    changed=json.loads(snapshot(wheel(tmp_path,{'controlplane_events/new.py':b'PUBLIC = 1'}))[0])
    assert original!=changed
    assert changed['packaged_file_sha256']['controlplane_events/new.py']==hashlib.sha256(b'PUBLIC = 1').hexdigest()
    assert changed['event_schemas']['controlplane_events/schemas/event.json']=={'type':'object'}


@pytest.mark.parametrize('extra',['../escape.py','/absolute.py','another_package/code.py'])
def test_unsafe_or_uncovered_contents_refused(tmp_path,extra):
    with pytest.raises(ValueError):snapshot(wheel(tmp_path,{extra:b'bad'}))


def test_metadata_version_must_match_distribution_directory(tmp_path):
    with pytest.raises(ValueError):snapshot(wheel(tmp_path,metadata_version='0.25.0'))


def test_duplicate_entries_refused(tmp_path):
    p=wheel(tmp_path)
    with zipfile.ZipFile(p,'a') as z,pytest.warns(UserWarning):z.writestr('controlplane_events/__init__.py',b'__all__=[]')
    with pytest.raises(ValueError):snapshot(p)


def test_candidate_binds_actual_artifact_and_contract(tmp_path):
    contract,evidence=snapshot(wheel(tmp_path))
    c=tmp_path/'contract.json';c.write_bytes(contract)
    e=tmp_path/'evidence.json';e.write_text(json.dumps(evidence))
    component={'version':'0.24.0','contract':'contract.json','contract_sha256':evidence['contract_sha256'],
               'artifact':{'kind':'package','uri':'urn:sha256:'+evidence['wheel_sha256'],'sha256':evidence['wheel_sha256']}}
    manifest=tmp_path/'release.json'
    def save():manifest.write_text(json.dumps({'schema_version':1,'line':'0.24','components':{'controlplane-events':component}}))
    save();verify(manifest,c,e,'controlplane-events')
    component['artifact']['sha256']='0'*64;save()
    with pytest.raises(ValueError,match='artifact'):verify(manifest,c,e,'controlplane-events')
