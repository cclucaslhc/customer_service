"""One-shot bootstrap ONLY for the explicitly provisioned empty Hyper-V lab."""
from datetime import datetime,timedelta,timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
STATE=Path('/home/labops/cs-lab/state')
DATABASE='cs_test_two_nodes_20261026'
PASSWORD='synthetic-two-node-only'
NODE1='172.24.168.13'
NODE2='172.24.168.125'


def command(args,*,text=None,env=None):
    result=subprocess.run(args,input=text,text=True,capture_output=True,env=env,timeout=90)
    if result.returncode:
        raise RuntimeError('Lab operation failed: '+str(args[0]))
    return result.stdout


def private(name,content):
    path=STATE/name
    with path.open('xb') as output:
        output.write(content.encode() if isinstance(content,str) else content)
    path.chmod(0o600)
    return path


def main():
    if socket.gethostname()!='cs-s2s6-node1' or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Explicit disposable lab node1 required')
    if STATE.exists():
        raise RuntimeError('Refusing to replace existing lab state')
    existing=command(['sudo','-u','postgres','psql','-X','-At','-c',
        "SELECT count(*) FROM pg_database WHERE datname NOT IN ('postgres','template0','template1')"])
    if existing.strip()!='0':
        raise RuntimeError('Only a pristine disposable PostgreSQL cluster is allowed')
    os.umask(0o077)
    STATE.mkdir(mode=0o700)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
    now=datetime.now(timezone.utc)
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Disposable customer service VM lab CA')])
    ca=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
        .not_valid_after(now+timedelta(days=7)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .sign(ca_key,hashes.SHA256()))
    server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    server=(x509.CertificateBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME,'cs-lab.internal')])).issuer_name(ca_name)
        .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=7))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('cs-lab.internal'),
            *[x509.IPAddress(ipaddress.ip_address(address)) for address in (NODE1,NODE2,'127.0.0.1')]]),False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),False)
        .sign(ca_key,hashes.SHA256()))
    private('pg-ca.crt',ca.public_bytes(serialization.Encoding.PEM))
    private('https.crt',server.public_bytes(serialization.Encoding.PEM))
    private('https.key',server_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    # CA private material is never transferred or printed.
    private('lab-ca.key',ca_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    for source,target in [('https.crt','cs-lab.crt'),('https.key','cs-lab.key')]:
        command(['sudo','install','-o','postgres','-g','postgres','-m','600',
            str(STATE/source),'/var/lib/postgresql/16/main/'+target])
    conf=private('postgres.conf',f"listen_addresses='127.0.0.1,{NODE1}'\nssl=on\n"
        "ssl_cert_file='/var/lib/postgresql/16/main/cs-lab.crt'\n"
        "ssl_key_file='/var/lib/postgresql/16/main/cs-lab.key'\n"
        "hba_file='/etc/postgresql/16/main/cs-lab-hba.conf'\npassword_encryption='scram-sha-256'\n")
    hba=private('postgres-hba.conf','local all all peer\n'+''.join(
        f'hostssl {DATABASE} cs_lab_owner,cs_lab_api,cs_lab_worker,cs_lab_backup {address}/32 scram-sha-256\n'
        for address in (NODE1,NODE2,'127.0.0.1')))
    for source,target in [(conf,'/etc/postgresql/16/main/conf.d/cs-lab.conf'),
            (hba,'/etc/postgresql/16/main/cs-lab-hba.conf')]:
        command(['sudo','install','-o','postgres','-g','postgres','-m','600',str(source),target])
    command(['sudo','systemctl','restart','postgresql@16-main'])
    for role in ('owner','api','worker','backup'):
        command(['sudo','-u','postgres','psql','-X','-v','ON_ERROR_STOP=1','-c',
            f"CREATE ROLE cs_lab_{role} LOGIN PASSWORD '{PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION"])
    command(['sudo','-u','postgres','createdb','--owner=cs_lab_owner',DATABASE])
    command(['sudo','-u','postgres','psql','-X','-v','ON_ERROR_STOP=1','-d',DATABASE,'-c','CREATE EXTENSION vector'])
    def url(role,address=NODE1):
        return (f'postgresql+psycopg://cs_lab_{role}:{PASSWORD}@{address}:5432/{DATABASE}'
            f'?sslmode=verify-full&sslrootcert={STATE}/pg-ca.crt')
    os.environ['MIGRATION_DATABASE_URL']=url('owner','127.0.0.1')
    from alembic import command as alembic_command
    from alembic.config import Config
    alembic_command.upgrade(Config(str(ROOT/'alembic.ini')),'head')
    command(['/usr/lib/postgresql/16/bin/psql','-X','-q','-v','ON_ERROR_STOP=1',
        '-h','127.0.0.1','-U','cs_lab_owner','-d',DATABASE,
        '-v','migration_role=cs_lab_owner','-v','app_role=cs_lab_api',
        '-v','worker_role=cs_lab_worker','-v','backup_role=cs_lab_backup',
        '-f',str(ROOT/'ops/postgresql/grant-runtime.sql')],env={**os.environ,
            'PGPASSWORD':PASSWORD,'PGSSLMODE':'verify-full','PGSSLROOTCERT':str(STATE/'pg-ca.crt')})
    from app.persistence.bundle import PostgreSQLRepositories
    from app.database import utc_now
    bundle=PostgreSQLRepositories.from_url(url('owner','127.0.0.1'),pool_size=1)
    try:
        tenant=bundle.identity.create_tenant('Synthetic two-VM acceptance only')
        for username,role in [('synthetic.lab.admin','admin'),('synthetic.lab.customer','customer')]:
            bundle.identity.provision_user(username,'synthetic-password-only',tenant,role=role)
        token=bundle.identity.login('synthetic.lab.admin','synthetic-password-only',tenant).token
        bundle.config.authenticated(token).initialize_tenant(utc_now())
        bundle.identity.logout(token)
    finally:
        bundle.close()
    for role in ('api','worker'):
        private(role+'-url',url(role)+'\n')
    private('master.key',os.urandom(32))
    private('storage-policy.json',json.dumps([dict(name='unconfigured-test-only',
        endpoint='https://s3.lab.invalid',bucket='synthetic-unused',region='us-east-1')]))
    private('public.json',json.dumps({'tenant_id':tenant,'database':DATABASE,'origin':'https://cs-lab.internal'}))
    print(json.dumps({'schema':'0037_rag_phase_clock','tenant_id':tenant,'tls':'verify-full',
        'roles':'owner/api/worker/backup separate','scope':'synthetic two-VM test only'}))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('Linux lab setup failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
