"""Compatibility gates for bank provenance and scoped bank transaction references."""
import re
import unicodedata
from app.account_profiles import account_profiles

PACKAGE_BANKS = {
    'com.itau':'itau','com.itau.pers':'itau','com.santander.app':'santander',
    'br.com.intermedium':'inter','com.mercadopago.wallet':'mercado_pago',
    'com.nu.production':'nubank','com.bradesco':'bradesco','com.c6bank.app':'c6',
    'com.picpay':'picpay','br.com.gabba.Caixa':'caixa','br.com.bb.android':'banco_do_brasil',
}


def bank_name(value):
    plain=unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode().casefold()
    words=set(re.findall(r'[a-z0-9]+',plain))
    for word,key in (('itau','itau'),('santander','santander'),('inter','inter'),('btg','btg'),
                     ('nubank','nubank'),('bradesco','bradesco'),('c6','c6'),('picpay','picpay'),('caixa','caixa')):
        if word in words:return key
    if {'mercado','pago'}<=words:return 'mercado_pago'
    if {'banco','brasil'}<=words:return 'banco_do_brasil'
    return None


def candidate_bank(candidate, db):
    profile=candidate.get('account_profile_id')
    if profile:
        metadata=next((p for p in account_profiles(db) if p['key']==profile),None)
        return bank_name((metadata or {}).get('instituicao'))
    if candidate.get('account_id'):
        account=db.accounts.find_one({'_id':candidate['account_id']})
        return bank_name((account or {}).get('instituicao'))
    return None


def strong_reference(event, candidate):
    parsed=event.get('parsed_payload') or {}
    reference=parsed.get('bank_transaction_ref')
    account=parsed.get('account_identity')
    candidate_account=candidate.get('account_identity')
    return bool(reference and reference==candidate.get('bank_transaction_ref')
        and str(account or '').strip() and str(candidate_account or '').strip()
        and event.get('source_provider')==candidate.get('source_provider')
        and account==candidate_account)


def compatible(event, candidate, db=None):
    parsed=event.get('parsed_payload') or {}
    left,right=parsed.get('bank_transaction_ref'),candidate.get('bank_transaction_ref')
    if left and right:
        if left!=right:return False
        if not strong_reference(event,candidate):return False
    account=parsed.get('account_identity')
    if account and candidate.get('account_identity') and account!=candidate['account_identity']:return False
    profile=parsed.get('account_profile_id')
    if profile and candidate.get('account_profile_id') and profile!=candidate['account_profile_id']:return False
    if event.get('source_type')=='android_notification':
        package=(event.get('raw_payload') or {}).get('package') or event.get('source_provider')
        if package!=event.get('source_provider'):return False
        bank=PACKAGE_BANKS.get(package)
        if not bank or db is None or candidate_bank(candidate,db)!=bank:return False
    return True
