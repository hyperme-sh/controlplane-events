"""Run the exact image with isolated owner-role PostgreSQL and no provider egress."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid
from urllib.parse import urlencode

PG='postgres:17@sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1'


def call(command,**kwargs):
    p=subprocess.run(command,text=True,capture_output=True,timeout=kwargs.pop('timeout',120),**kwargs)
    if p.returncode:
        raise RuntimeError('Isolated runtime command failed: '+p.stderr[-1500:])
    return p.stdout


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image');parser.add_argument('--prototype',action='store_true')
    parser.add_argument('--wheel',type=Path,required=True)
    args=parser.parse_args();prefix='events-runtime-'+uuid.uuid4().hex[:10]
    network=prefix+'-net';postgres=prefix+'-db';app=prefix+'-app';password=secrets.token_hex(24)
    settings={};created_network=False
    with tempfile.TemporaryDirectory(prefix='events-runtime-') as folder:
        root=Path(folder)
        def envfile(name,values):
            path=root/name;path.write_text(''.join(k+'='+v+'\n' for k,v in values.items()));path.chmod(0o600);return path
        pg_env=envfile('postgres.env',{'POSTGRES_PASSWORD':password})
        url='postgresql+psycopg://events_owner:'+password+'@database:5432/postgres'
        owner_file=root/'owner.url';owner_file.write_text(url);owner_file.chmod(0o444)
        producers=root/'producers.json';producers.write_text(json.dumps({'payment-service':'disposable-producer'}));producers.chmod(0o444)
        app_env=envfile('app.env',{'DATABASE_URL_FILE':'/run/owner.url','EVENT_PRODUCER_TOKENS_FILE':'/run/producers.json','EVENTS_LEGACY_LOG_FORWARD':'1'})
        def sql(query):return call(['docker','exec','-i',postgres,'psql','-U','postgres','-d','postgres','-v','ON_ERROR_STOP=1','-At'],input=query).strip()
        def migrate(module):
            return call(['docker','run','--rm','--pull','never','--network',network,'--user',str(os.getuid())+':'+str(os.getgid()),
                '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--mount','type=bind,src='+str(owner_file)+',dst=/run/owner.url,readonly',
                '--entrypoint','/app/.venv/bin/python',args.image,'-m',module,'--database-url-file','/run/owner.url'])
        try:
            call(['docker','network','create','--internal',network]);created_network=True
            call(['docker','run','--rm','-d','--name',postgres,'--network',network,'--network-alias','database',
                '--memory','1g','--pids-limit','256','--tmpfs','/var/lib/postgresql/data:rw','--env-file',str(pg_env),PG])
            for _ in range(80):
                p=subprocess.run(['docker','exec',postgres,'pg_isready','-h','127.0.0.1','-U','postgres'],capture_output=True)
                if p.returncode==0:break
                time.sleep(.25)
            else:raise RuntimeError('Disposable PostgreSQL failed readiness')
            sql("CREATE ROLE events_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '"+password+"'; CREATE SCHEMA events_owned AUTHORIZATION events_owner; ALTER ROLE events_owner SET search_path TO events_owned,public;")
            sql('CREATE TABLE public.controlplane_events (id bigserial PRIMARY KEY, event_type text, tenant_id uuid, envelope jsonb); GRANT INSERT ON public.controlplane_events TO events_owner; GRANT USAGE ON SEQUENCE public.controlplane_events_id_seq TO events_owner;')
            migrate('controlplane_events.migrate')
            # Replay must validate the same receipts without recreating the owner.
            migrate('controlplane_events.migrate')
            call(['docker','run','--rm','-d','--pull','never','--name',app,'--network',network,
                  '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','1g','--pids-limit','256',
                  '--tmpfs','/tmp','--mount','type=bind,src='+str(owner_file)+',dst=/run/owner.url,readonly',
                  '--mount','type=bind,src='+str(producers)+',dst=/run/producers.json,readonly','--env-file',str(app_env),args.image])
            probe="import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=2).read(); print('ready')"
            for _ in range(60):
                p=subprocess.run(['docker','exec',app,'/app/.venv/bin/python','-c',probe],capture_output=True,text=True,timeout=8)
                if p.returncode==0:break
                info=json.loads(call(['docker','inspect',app]))[0]
                if not info['State']['Running']:raise RuntimeError('Events runtime exited: '+call(['docker','logs',app])[-1500:])
                time.sleep(.5)
            else:raise RuntimeError('Events runtime did not become healthy')
            tid, eid = uuid.uuid4(), uuid.uuid4()
            body={'event_id':str(eid),'envelope':{'event_type':'payment.obligation.recorded.v1','service':'payment-service',
                'tenant_id':str(tid),'occurred_at':datetime.now(timezone.utc).isoformat(),'payload':{'tenant_id':str(tid),'lease_id':str(uuid.uuid4()),
                'billing_record_id':str(uuid.uuid4()),'source_event_id':str(uuid.uuid4()),'net_usdc_atoms':10}}}
            request=json.dumps(body)
            delivery="import json,urllib.request; body="+repr(request)+"; request=urllib.request.Request('http://127.0.0.1:8000/events',data=body.encode(),headers={'Authorization':'Bearer disposable-producer','Idempotency-Key':"+repr(str(eid))+",'Content-Type':'application/json'}); print(urllib.request.urlopen(request).read().decode())"
            first=json.loads(call(['docker','exec',app,'/app/.venv/bin/python','-c',delivery]))
            second=json.loads(call(['docker','exec',app,'/app/.venv/bin/python','-c',delivery]))
            if first['duplicate'] or not second['duplicate'] or sql('SELECT count(*) FROM public.controlplane_events') != '1':
                raise ValueError('Real HTTP compatibility append must commit exactly once')
            receipt=json.loads(call(['docker','exec',app,'cat','/opt/events-owner/runtime-receipt.json']))
            if receipt['service_wheel']!=args.wheel.name or receipt['wheel_sha256'][args.wheel.name]!=hashlib.sha256(args.wheel.read_bytes()).hexdigest():
                raise ValueError('Runtime wheel differs from tested artifact')
            source=Path(__file__).resolve().parents[1]
            for target,relative in {'selfhost/entrypoint.py':'selfhost/entrypoint.py',
                'owner-migrations/migrate.py':'src/controlplane_events/migrate.py',
                'owner-migrations/migrations/0001.sql':'src/controlplane_events/migrations/0001.sql'}.items():
                if receipt['source_resource_sha256'][target]!=hashlib.sha256((source/relative).read_bytes()).hexdigest():
                    raise ValueError('Runtime resource differs from qualified source')
            image=json.loads(call(['docker','image','inspect',args.image]))[0]
            if image['Config']['User']!='65532:65532':raise ValueError('Runtime must use its non-root account')
            if not args.prototype:
                if not receipt['source_clean'] or image['Config']['Labels']['org.opencontainers.image.revision']!=receipt['source_sha']:
                    raise ValueError('Runtime source identity differs')
            call(['docker','rm','-f',app])
            def must_refuse(name, database_url):
                private=envfile(name+'.env',{'DATABASE_URL':database_url})
                result=subprocess.run(['docker','run','--rm','--pull','never','--network',network,'--read-only','--cap-drop','ALL',
                    '--env-file',str(private),args.image,'/app/.venv/bin/python','-c',"print('must-not-run')"],capture_output=True,text=True,timeout=30)
                if result.returncode==0 or 'must-not-run' in result.stdout:raise ValueError(name+' was served')
            # A valid owner receipt must not authorize superuser or public-schema use.
            must_refuse('privileged-role','postgresql+psycopg://postgres:'+password+'@database:5432/postgres?'+urlencode({'options':'-c search_path=events_owned,public'}))
            must_refuse('public-schema',url+'?'+urlencode({'options':'-c search_path=public'}))
            # Corrupted receipts cannot be accepted as a healthy runtime.
            sql("UPDATE events_owned.owner_schema_version SET sha256='corrupted-fixture';")
            must_refuse('corrupted-receipt',url)
            sql('DROP TABLE events_owned.owner_schema_version;')
            must_refuse('missing-owner-receipt',url)
            print(json.dumps({'owner_migrations_idempotent':True,'default_entrypoint_healthy':True,'corrupt_receipt_refused':True,
                'nonprivileged_owner_role':True,'network':'internal-only','provider_egress':False,'prototype':args.prototype,'source_sha':receipt['source_sha']}))
        finally:
            for name in (app,postgres):subprocess.run(['docker','rm','-f',name],capture_output=True)
            if created_network:subprocess.run(['docker','network','rm',network],capture_output=True)


if __name__=='__main__':main()
