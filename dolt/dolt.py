#!/usr/bin/env python3
"""Dolt 2.0.7: private embedded SQL BEFORE any listener; persistent explicit paths.
Source: dolthub/dolt 36c11fe9 utils.go:294-315, sqlserver/server.go:472-529.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import sys
from lib import Failure, run, write_private
BASE=Path('/var/lib/dolt')
DATA=BASE/'data'; CFG=BASE/'config'

def credential(name):
    # Passwords come from the compose environment (.env.secrets via
    # DOLT_DB_PASSWORD / DOLT_ROOT_PASSWORD).
    var={'db-password':'DOLT_DB_PASSWORD','root-password':'DOLT_ROOT_PASSWORD'}[name]
    secret=os.environ.get(var,'').strip()
    # Deliberately narrow alphabet eliminates SQL mode/escaping ambiguity.
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',secret): raise Failure('DB passwords require 32-128 URL-safe characters')
    return secret

def identifier(name):
    value=os.environ[name]
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}',value) or value=='root': raise Failure('invalid dedicated DB/user identifier')
    return value

def env(password=None):
    e={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(BASE/'private-home'),'LANG':'C.UTF-8'}
    if password is not None: e['DOLT_CLI_PASSWORD']=password
    return e

def local():
    return ['dolt','--data-dir',str(DATA),'--doltcfg-dir',str(CFG),'--privilege-file',str(CFG/'privileges.db'),'--branch-control-file',str(CFG/'branch-control.db')]

def main():
    database=identifier('DOLT_DATABASE'); user=identifier('DOLT_DB_USER')
    password=credential('db-password')
    op=sys.argv[1] if len(sys.argv)>1 else 'start'
    if op=='health':
        run(['dolt','--host','127.0.0.1','--port','3306','--user',user,'--no-tls','--use-db',database,'sql'],env=env(password),input='SELECT 1;\n',timeout=4)
        return
    if op!='start': raise Failure('unsupported operation')
    fd=os.open(BASE/'entrypoint.lock',os.O_RDWR|os.O_CREAT,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); os.set_inheritable(fd,True)
    expected={'version':'2.0.7','database':database,'user':user}
    marker=BASE/'initialized.json'
    if marker.exists():
        if json.loads(marker.read_text())!=expected or not (CFG/'privileges.db').is_file(): raise Failure('initialization identity/privileges mismatch; repair offline')
    else:
        # Any partial data fails closed; never reset or adopt unknown state.
        if DATA.exists() or CFG.exists(): raise Failure('partial initialization; preserve volume and repair offline')
        DATA.mkdir(mode=0o700); CFG.mkdir(mode=0o700); (BASE/'private-home').mkdir(mode=0o700)
        root_password=credential('root-password')
        # Unspecified embedded credentials already install ephemeral root@localhost.
        # Password/persist that user; duplicate CREATE fails on exact Dolt2.0.7.
        sql=(f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{root_password}';\n"
             "GRANT ALL ON *.* TO 'root'@'localhost' WITH GRANT OPTION;\n"
             # Lead-authorized numeric-loopback auth compatibility (Dolt utils.go475-479).
             f"CREATE USER 'root'@'127.0.0.1' IDENTIFIED BY '{root_password}';\n"
             "GRANT ALL ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;\n"

             f"CREATE DATABASE `{database}`;\n"
             f"CREATE USER '{user}'@'%' IDENTIFIED BY '{password}';\n"
             f"GRANT ALL ON `{database}`.* TO '{user}'@'%';\n")
        run(local()+['sql'],env=env(),input=sql,timeout=120)
        # Explicit local credentials validate persisted privileges, not ephemeral root.
        run(local()+['--user','root','--use-db',database,'sql'],env=env(root_password),input='SELECT 1;\n')
        run(local()+['--user',user,'--use-db',database,'sql'],env=env(password),input='SELECT 1;\n')
        write_private(marker,json.dumps(expected))
    # Allow agents to create project-isolated Beads databases (beads_<project>).
    # This also upgrades grants and syncs the password from DOLT_DB_PASSWORD when
    # restarting an existing Dolt volume.
    # Dolt ignores wildcard database grants for CREATE DATABASE, so grant server-wide
    # (no GRANT OPTION); this server is dedicated to Beads. Use the embedded
    # superuser: password root maps to root@127.0.0.1, which cannot grant.
    run(local()+['sql'],env=env(),input=(f"ALTER USER '{user}'@'%' IDENTIFIED BY '{password}';\n"
                                         f"GRANT ALL ON *.* TO '{user}'@'%';\n"),timeout=30)
    config={'log_level':'warning','listener':{'host':'0.0.0.0','port':3306},'data_dir':str(DATA),'cfg_dir':str(CFG),'privilege_file':str(CFG/'privileges.db'),'branch_control_file':str(CFG/'branch-control.db'),'behavior':{'dolt_transaction_commit':False}}
    write_private(BASE/'server.yaml',json.dumps(config))
    # JSON is YAML; no secret in config. Credentials intentionally absent server env.
    os.execvpe('dolt',['dolt','sql-server','--config',str(BASE/'server.yaml')],env())
if __name__=='__main__':
    try: main()
    except Exception: print('Dolt operation failed; preserve volume and inspect private initialization',file=sys.stderr); sys.exit(1)
