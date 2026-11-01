"""Deterministic synthetic enterprise sources, independent of wall-clock expiry."""
from pathlib import Path
import json
from argon2 import PasswordHasher
from app.storage import dumps

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
WORLD_AS_OF = '2026-11-02T09:00:00+08:00'


def knowledge_documents():
    """Canonical KB metadata plus text for policy applicability and retrieval."""
    result=[]
    for path in sorted((DATA_DIR/'kb').glob('*.md')):
        _,metadata,body=path.read_text(encoding='utf-8').split('---',2)
        result.append({**json.loads(metadata),'text':body.strip(),'path':str(path)})
    return result


def seed_into(connection):
    if connection.execute('SELECT 1 FROM users LIMIT 1').fetchone():
        return
    world = json.loads((DATA_DIR / 'world.json').read_text(encoding='utf-8'))
    password_hash = PasswordHasher().hash('DemoPass123!')
    for user in world['employees']:
        connection.execute('INSERT INTO users VALUES(?,?,?,?,?)',
                           (user['id'],user['id'],password_hash,1,dumps(user)))
        for service in ('okta','salesforce'):
            account = {'locked':service=='okta','disabled_reason':'security' if user['id']=='dana.platform' and service=='okta' else None,
                       'account_type':'ordinary','unlock_count':0,'simulated':True}
            connection.execute('INSERT INTO accounts VALUES(?,?,?,?,?)',
                               (f"{service}.{user['id']}",user['id'],service,1,dumps(account)))
        vpn = {'client_version':user['device_version'],'connected':True,'reset_count':0,
               'connected_since':None,'observation_seconds':0,'required_observation_seconds':900,
               'symptoms':['periodic_disconnect'] if user['id']=='bo.remote' else [],'simulated':True}
        connection.execute('INSERT INTO vpn_sessions VALUES(?,?,?,?)',(f"vpn.{user['id']}",user['id'],1,dumps(vpn)))
    for resource in world['resources']:
        connection.execute('INSERT INTO resources VALUES(?,?,?)',(resource['id'],resource['revision'],dumps(resource)))
    for kind in ('services','incidents','changes','policies','dependencies'):
        for record in world[kind]:
            connection.execute('INSERT INTO world_records VALUES(?,?,?,?)',(kind,record['id'],record.get('revision',1),dumps(record)))
    connection.execute('INSERT INTO world_records VALUES(?,?,?,?)',('metadata','world',1,dumps({'as_of':WORLD_AS_OF,'simulated':True})))


def write_sources():
    DATA_DIR.mkdir(exist_ok=True)
    employees=[]
    for uid,name,dept,region,roles in [
        ('alice.sales','Alice','Sales','us-central',['employee']),
        ('bo.remote','Bo','Operations','apac',['employee']),
        ('chen.data','Chen','Data Engineering','apac',['employee']),
        ('dana.platform','Dana','Platform','eu-west',['employee']),
        ('pat.approver','Pat','Data Engineering','us-central',['employee','approver']),
        ('dev.debug','Dev','Platform','apac',['employee','debug'])]:
        employees.append({'id':uid,'name':name,'department':dept,'region':region,'location':'Chicago' if uid=='alice.sales' else 'Remote',
                          'roles':roles,'device_id':f'device.{uid}','device_version':'5.2','simulated':True})
    services=[{'id':s,'name':name,'revision':1,'status':'degraded' if s=='salesforce' else 'operational',
               'observed_at':WORLD_AS_OF,'version':'5.2' if s=='vpn' else '2026.11','simulated':True}
              for s,name in [('okta','Okta'),('salesforce','Salesforce'),('vpn','VPN'),('snowflake','Snowflake'),('grafana','Grafana'),('jenkins','Jenkins'),('tableau','Tableau'),('token_service','Token Service')]]
    resources=[]
    for rid,dept in [('grafana.sales','Sales'),('grafana.data','Data Engineering'),('grafana.platform','Platform')]:
        resources.append({'id':rid,'name':rid.replace('.',' ').title()+' dashboard','service_id':'grafana','environment':'standard','permissions':['read'],
                          'eligible_departments':[dept],'approval_required':False,'owner_ids':['pat.approver'],'revision':1,'active':True,'simulated':True})
    for rid in ['snowflake.prod.analytics','snowflake.prod.finance']:
        resources.append({'id':rid,'name':rid+' read-only catalog','service_id':'snowflake','environment':'production','permissions':['read'],
                          'eligible_departments':['Sales','Data Engineering','Platform','Operations'],'approval_required':True,'owner_ids':['pat.approver'],'revision':1,'active':True,'simulated':True})
    policies=[{'id':pid,'revision':1,'action':action,'rule':rule,'simulated':True} for pid,action,rule in [
        ('policy.unlock','unlock_account','Only self ordinary lock; security disabled and MFA reset prohibited'),
        ('policy.grafana','grant_catalog_access','Exact standard read resource, eligible department, explicit request'),
        ('policy.production','grant_catalog_access','Exact production read resource requires current resource-owner approval'),
        ('policy.vpn','reset_vpn_session','Self session only; explicit exact-scope confirmation, expires in 10 minutes'),
        ('policy.no-admin','grant_catalog_access','Administrator and write permissions prohibited even with approval'),
        ('policy.no-command','production_change','No production rollback, restart, shell command or pipeline mutation'),
        ('policy.approver','request_approval','Resource owner only, no self approval; expires after 24 hours'),
        ('policy.handoff','create_handoff','Save complete simulated handoff before terminal transition'),
        ('policy.attempts','repair','Maximum two distinct reviewed remedy paths per issue'),
        ('policy.identity','all','Employee identity comes from authenticated session; self scope'),
        ('policy.evidence','all','Historical correlation does not establish current causation'),
        ('policy.recovery','resolve','Access requires precise actual permission; experience requires objective checks and user feedback')]]
    incidents=[
        {'id':'incident.sf.central','service_id':'salesforce','region':'us-central','status':'active','symptoms':['slow page loads'],'started_at':'2026-11-02T08:25:00+08:00','observed_at':WORLD_AS_OF,'revision':2,'simulated':True},
        {'id':'incident.sf.eu.old','service_id':'salesforce','region':'eu-west','status':'resolved','symptoms':['login failure'],'started_at':'2026-10-23T10:00:00+08:00','observed_at':'2026-10-23T12:00:00+08:00','revision':3,'simulated':True}]
    changes=[{'id':'change.token.rotation','service_id':'token_service','related_service_ids':['jenkins','tableau'],'region':'eu-west',
              'started_at':'2026-11-02T08:00:00+08:00','ended_at':'2026-11-02T08:30:00+08:00','status':'completed',
              'notes':'Signing certificate rotation; Jenkins token probe healthy at 08:40; Tableau extract queue still delayed. Temporal overlap alone is not proof.',
              'observed_at':WORLD_AS_OF,'revision':2,'simulated':True},
             {'id':'change.vpn.client','service_id':'vpn','related_service_ids':[],'region':'apac','status':'completed','revision':1,'notes':'5.2 client upgrade; 4.x workaround is not applicable','observed_at':WORLD_AS_OF,'simulated':True}]
    dependencies=[{'id':'dependency.jenkins.token','service_id':'jenkins','depends_on':'token_service','revision':1,'notes':'Dependency exists; probe healthy after rotation, causation unconfirmed','simulated':True},
                  {'id':'dependency.tableau.token','service_id':'tableau','depends_on':'token_service','revision':1,'notes':'Token required at extract start; queue backlog has independent causes','simulated':True}]
    world={'simulated':True,'as_of':WORLD_AS_OF,'revision':1,'employees':employees,'services':services,'resources':resources,'incidents':incidents,'changes':changes,'policies':policies,'dependencies':dependencies}
    (DATA_DIR/'world.json').write_text(json.dumps(world,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    articles=[
        ('account-unlock','okta','2026.11','Ordinary lock','unlock_account','Inspect own account; ordinary lock may be unlocked. Verify login afterward. Security disabled accounts require Security team.'),
        ('account-security','okta','2026.11','Security disabled','handoff_security','Do not unlock security-disabled accounts or reset MFA; collect error and create Security handoff.'),
        ('account-browser','okta','2026.11','Browser login','browser_private_window','Use a private browser window to distinguish stale cookies; never request passwords.'),
        ('account-mfa','okta','2026.11','MFA issue','handoff_security','Check displayed error. MFA reset is unavailable; Security must verify identity.'),
        ('salesforce-slow','salesforce','2026.11','Slow pages','check_incident_scope','Compare region, onset and symptoms with current incident; unrelated region is counterevidence.'),
        ('salesforce-browser','salesforce','2026.11','Browser slowness','browser_private_window','Compare private window and another permitted browser. Do not change production settings.'),
        ('salesforce-old','salesforce','2025.1','Old plugin delay','old_plugin_workaround','Retired plugin workaround; only valid for 2025.1. Do not apply to current web client.'),
        ('vpn-session','vpn','5.2','Periodic disconnect','reset_vpn_session','Inspect own session, ask about local link. Confirm interruption before reset; observe for 900 seconds afterward.'),
        ('vpn-local-network','vpn','5.2','Link loss','compare_local_link','Check whether other apps lose connectivity. Session reset cannot fix a failing home link.'),
        ('vpn-old','vpn','4.9','Legacy timeout','legacy_timeout','Retired 4.9 workaround. Inapplicable to 5.2. Never run unreviewed commands.'),
        ('grafana-sales','grafana','2026.11','Sales dashboard read','grant_grafana_read','Sales employees may request grafana.sales read. Grant exact read permission and verify access.'),
        ('grafana-data','grafana','2026.11','Data dashboard read','grant_grafana_read','Data Engineering employees may request grafana.data read; administrator grants are prohibited.'),
        ('grafana-platform','grafana','2026.11','Platform dashboard read','grant_grafana_read','Platform employees may request grafana.platform read. Do not grant unrelated dashboards.'),
        ('snowflake-read','snowflake','2026.11','Production read','grant_production_read','Choose exact production catalog; obtain current owner approval, then grant read and query actual permission.'),
        ('snowflake-no-write','snowflake','2026.11','Write request','handoff_general','Production write/admin is unavailable even if a manager says yes. No bypass instructions.'),
        ('snowflake-verify','snowflake','2026.11','Read access verification','verify_precise_access','Read grant is scoped to one catalog; a grant on analytics does not imply finance access.'),
        ('jenkins-token','jenkins','2026.11','Token timeout','inspect_token_dependency','Check recent maintenance and token probe. Healthy probe is counterevidence against a continuing token outage.'),
        ('jenkins-escalate','jenkins','2026.11','Production pipeline timeout','handoff_platform','Pipeline changes and restarts require Platform team; collect errors and maintenance evidence.'),
        ('tableau-extract','tableau','2026.11','Stale dashboard','inspect_extract_queue','Inspect extract age and queue data; dependency overlap does not prove token root cause.'),
        ('tableau-escalate','tableau','2026.11','Extract backlog','handoff_data','No automatic production queue edits. Send Data Platform facts, counterevidence, attempts and questions.'),
        ('token-rotation','token_service','2026.11','Certificate maintenance','inspect_token_dependency','Rotation completed. Check per-service probes; do not infer all consumers failed.'),
        ('cross-system','token_service','2026.11','Multiple app symptoms','compare_dependencies','Keep independent goals; compare evidence and counterevidence. Related issues never share resolved status automatically.')]
    kb_dir=DATA_DIR/'kb';kb_dir.mkdir(exist_ok=True)
    for index,(slug,service,version,title,remedy,body) in enumerate(articles,1):
        outdated='old' in slug
        meta={'id':f'kb.{slug}','title':title,'service_id':service,'applicable_versions':[version],'revision':1,
              'reviewed':True,'status':'retired' if outdated else 'current','valid_from':'2025-01-01' if outdated else '2026-10-01',
              'valid_until':'2025-12-31' if outdated else None,'remedy_ids':[remedy],'prerequisites':['Match service, version, scope and policy'],'simulated':True}
        (kb_dir/f'{index:02d}-{slug}.md').write_text('---\n'+json.dumps(meta,ensure_ascii=False)+'\n---\n\n# '+title+'\n\n'+body+'\n',encoding='utf-8')
    histories=[]
    examples=[('okta','ordinary lock','Ordinary lock verified','unlock_account','Login confirmed'),('okta','login blocked','Security disable','handoff_security','Simulated Security handoff'),
              ('salesforce','slow pages','us-central regional incident','check_incident_scope','Recovered after incident'),('salesforce','slow pages','Local stale cookie, region eu-west unaffected','browser_private_window','Private window healthy'),
              ('vpn','periodic drop','Stale session 5.2','reset_vpn_session','Stable for 900 seconds'),('vpn','periodic drop','Home Wi-Fi link loss','compare_local_link','VPN reset did not help'),
              ('grafana','access denied','Missing eligible read grant','grant_grafana_read','Exact dashboard permission verified'),('snowflake','access denied','No production catalog read grant','grant_production_read','Owner approval then precise grant'),
              ('jenkins','timeout','Expired token during earlier rotation','handoff_platform','Platform corrected token'),('jenkins','timeout','Independent pipeline runner saturation','handoff_platform','Token probe healthy, runner fix'),
              ('tableau','stale data','Independent extract queue backlog','handoff_data','Queue recovered, token healthy'),('vpn','timeout','Legacy client 4.9','legacy_timeout','Old workaround not applicable to 5.2')]
    for n,(service,symptom,cause,remedy,outcome) in enumerate(examples,1):
        histories.append({'id':f'history.{n:02d}','service_id':service,'revision':1,'simulated':True,'curated':True,'symptoms':[symptom],
                          'environment':{'version':'4.9' if n==12 else '2026.10','region':'eu-west' if n in (4,9,10,11) else 'us-central'},
                          'verified_cause':cause,'remedy_ids':[remedy],'steps':[remedy],'outcome':outcome,'resolved_at':'2026-10-20T15:00:00+08:00'})
    (DATA_DIR/'history.json').write_text(json.dumps(histories,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    write_sources()
