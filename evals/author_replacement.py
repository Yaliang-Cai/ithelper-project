"""Replacement H01b authored only after ambiguity/projection candidate-v3 freeze."""
import hashlib,json
from evals.build_cases import case,goal,prior,patch
from evals.runner import ROOT,save,stamp

def main():
    freeze=ROOT/'evals/freeze-v3.json'
    if not freeze.exists():raise ValueError('Freeze candidate v3 first')
    target=ROOT/'evals/cases/H01b.json'
    if target.exists():raise ValueError('Never overwrite sealed replacement')
    c=case('H01b','account','bo.remote','还是卡在登录那儿。',
      [goal('okta','resolved'),goal('salesforce','investigating','waiting_user')],
      facts={'reference_clarification':'我说的是 Okta，请帮我解锁。','error_detail':'Okta 页面显示 account locked。','attempt_result':'还没有重新登录。','environment':'亚太，当前网页版。'},
      world={'patches':[patch('world_records','salesforce',status='operational')],'prior_issues':[prior('okta','我的 Okta 登录被锁，等我确认目标后再执行',criterion='login_recovery'),prior('salesforce','Salesforce 浏览器登录没完成，先保留这个问题',criterion='login_recovery')]},
      oracle={'must_clarify_before_unlock':True,'untouched_service':'salesforce','required_action':'unlock_account','max_unlocks':1})
    c['sealed_after_freeze_sha256']=hashlib.sha256(freeze.read_bytes()).hexdigest();c['authored_utc']=stamp();c['replaces_case_id']='H01'
    save(target,c)
    save(ROOT/'evals/holdout-replacement-seal.json',{'freeze_file':'freeze-v3.json','freeze_sha256':hashlib.sha256(freeze.read_bytes()).hexdigest(),'case_id':'H01b','case_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'authored_utc':c['authored_utc'],'material_changes':['actor and region','selected target now Okta, not Salesforce','independent ordinary lock instead of regional incident','expected clarification followed by actual unlock/recovery while other account goal stays pending']})
    adaptations=json.loads((ROOT/'evals/split-adaptations.json').read_text(encoding='utf-8'));adaptations['adaptations'][0]['replacement_status']='sealed_after_candidate_v3';save(ROOT/'evals/split-adaptations.json',adaptations)

if __name__=='__main__':main()
