#!/usr/bin/env python3
import argparse, json, re
from collections import defaultdict
from pathlib import Path
from ollama import chat

SYSTEM_PROMPT = '''You are a strict Spanish insurance-policy extraction engine.
Extract only facts explicitly supported by the supplied Docling evidence. Never invent; if uncertain return null.
Preserve names, RFC/tax IDs, policy numbers, customer codes, Spanish plan names and coverage names exactly.
Normalize pesos -> MXN and dls/dólares norteamericanos -> USD only in normalized currency fields.
Never borrow sum insured, deductible, coinsurance, or service cost from an adjacent row. Values must belong to the same logical coverage row.
If row association is uncertain, return null and add a warning. Preserve every row of rule tables. Return JSON matching the supplied schema.'''

def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def warning_schema():
    return {"type":"object","properties":{
        "field":{"type":["string","null"]},"issue":{"type":["string","null"]},
        "severity":{"type":["string","null"],"enum":["low","medium","high",None]},
        "source_page":{"type":["integer","null"]}},
        "required":["field","issue","severity","source_page"]}

def money_schema():
    return {"type":"object","properties":{
        "raw_value":{"type":["string","null"]},"amount":{"type":["number","null"]},
        "currency":{"type":["string","null"]}},
        "required":["raw_value","amount","currency"]}

def condition_schema():
    return {"type":"object","properties":{
        "condition_type":{"type":["string","null"]},"scope":{"type":["string","null"]},
        "description":{"type":["string","null"]},
        "rules":{"type":"array","items":{"type":"object","properties":{
            "criteria":{"type":["string","null"]},"raw_value":{"type":["string","null"]},
            "amount":{"type":["number","null"]},"secondary_amount":{"type":["number","null"]},
            "currency":{"type":["string","null"]},"percentage":{"type":["number","null"]},
            "secondary_percentage":{"type":["number","null"]},"unit":{"type":["string","null"]},
            "effective_start_date":{"type":["string","null"]},"effective_end_date":{"type":["string","null"]},
            "notes":{"type":["string","null"]}},
            "required":["criteria","raw_value","amount","secondary_amount","currency","percentage","secondary_percentage","unit","effective_start_date","effective_end_date","notes"]}},
        "source_page":{"type":["integer","null"]}},
        "required":["condition_type","scope","description","rules","source_page"]}

def coverage_schema():
    return {"type":"object","properties":{
        "category":{"type":["string","null"]},"name":{"type":["string","null"]},
        "scope":{"type":["string","null"]},"status":{"type":["string","null"]},
        "sum_insured":money_schema(),"deductible":money_schema(),
        "coinsurance":{"type":"object","properties":{
            "raw_value":{"type":["string","null"]},"percentage":{"type":["number","null"]},
            "applies":{"type":["boolean","null"]}},"required":["raw_value","percentage","applies"]},
        "service_cost":{"type":"object","properties":{
            "raw_value":{"type":["string","null"]},"amount":{"type":["number","null"]},
            "currency":{"type":["string","null"]},"unit":{"type":["string","null"]}},
            "required":["raw_value","amount","currency","unit"]},
        "notes":{"type":["string","null"]},"source_page":{"type":["integer","null"]}},
        "required":["category","name","scope","status","sum_insured","deductible","coinsurance","service_cost","notes","source_page"]}

def schema_policy_meta():
    return {"type":"object","properties":{
        "document":{"type":"object","properties":{
            "document_type":{"type":["string","null"]},"language":{"type":["string","null"]},"page_count":{"type":["integer","null"]}},
            "required":["document_type","language","page_count"]},
        "policy":{"type":"object","properties":{
            "insurer":{"type":["string","null"]},"policy_number":{"type":["string","null"]},"version":{"type":["string","null"]},
            "renewal_number":{"type":["string","null"]},"product_line":{"type":["string","null"]},"plan_raw_text":{"type":["string","null"]},
            "plan_name":{"type":["string","null"]},"hospital_level":{"type":["string","null"]},"scheme_name":{"type":["string","null"]},
            "issue_date":{"type":["string","null"]},"coverage_start_date":{"type":["string","null"]},"coverage_end_date":{"type":["string","null"]},
            "term_days":{"type":["integer","null"]},"currency":{"type":["string","null"]},"movement_type":{"type":["string","null"]},
            "movement_description":{"type":["string","null"]},"source_page":{"type":["integer","null"]}},
            "required":["insurer","policy_number","version","renewal_number","product_line","plan_raw_text","plan_name","hospital_level","scheme_name","issue_date","coverage_start_date","coverage_end_date","term_days","currency","movement_type","movement_description","source_page"]},
        "policyholder":{"type":"object","properties":{
            "name":{"type":["string","null"]},"customer_code":{"type":["string","null"]},"tax_id":{"type":["string","null"]},
            "address":{"type":["string","null"]},"email":{"type":["string","null"]},"phone":{"type":["string","null"]},"source_page":{"type":["integer","null"]}},
            "required":["name","customer_code","tax_id","address","email","phone","source_page"]},
        "premium_summary":{"type":"object","properties":{
            "net_premium":{"type":["number","null"]},"installment_surcharge":{"type":["number","null"]},"policy_fee":{"type":["number","null"]},
            "tax_rate_percent":{"type":["number","null"]},"tax_amount":{"type":["number","null"]},"total_amount":{"type":["number","null"]},
            "payment_method":{"type":["string","null"]},"payment_channel":{"type":["string","null"]},"source_page":{"type":["integer","null"]}},
            "required":["net_premium","installment_surcharge","policy_fee","tax_rate_percent","tax_amount","total_amount","payment_method","payment_channel","source_page"]},
        "agent":{"type":"object","properties":{
            "name":{"type":["string","null"]},"agent_code":{"type":["string","null"]},"source_page":{"type":["integer","null"]}},
            "required":["name","agent_code","source_page"]},
        "regulatory":{"type":"object","properties":{
            "registration_number":{"type":["string","null"]},"registration_date":{"type":["string","null"]},"source_page":{"type":["integer","null"]}},
            "required":["registration_number","registration_date","source_page"]},
        "warnings":{"type":"array","items":warning_schema()}},
        "required":["document","policy","policyholder","premium_summary","agent","regulatory","warnings"]}

def schema_insured():
    return {"type":"object","properties":{
        "insured_number":{"type":["integer","null"]},"role":{"type":["string","null"]},"name":{"type":["string","null"]},
        "customer_code":{"type":["string","null"]},"birth_date":{"type":["string","null"]},"gender":{"type":["string","null"]},
        "seniority_date":{"type":["string","null"]},"coverage_scope":{"type":["string","null"]},
        "premium":{"type":"object","properties":{
            "net_premium":{"type":["number","null"]},"installment_surcharge":{"type":["number","null"]},"policy_fee":{"type":["number","null"]},
            "tax_amount":{"type":["number","null"]},"total_amount":{"type":["number","null"]}},
            "required":["net_premium","installment_surcharge","policy_fee","tax_amount","total_amount"]},
        "coverages":{"type":"array","items":coverage_schema()},
        "conditions":{"type":"array","items":condition_schema()},
        "source_page":{"type":["integer","null"]},"warnings":{"type":"array","items":warning_schema()}},
        "required":["insured_number","role","name","customer_code","birth_date","gender","seniority_date","coverage_scope","premium","coverages","conditions","source_page","warnings"]}

def schema_conditions():
    return {"type":"object","properties":{
        "policy_conditions":{"type":"array","items":condition_schema()},
        "warnings":{"type":"array","items":warning_schema()}},
        "required":["policy_conditions","warnings"]}

def call_structured(model, schema, prompt, tag='call', retries=2):
    last = None
    for attempt in range(1, retries + 2):
        try:
            r = chat(model=model,
                     messages=[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}],
                     format=schema,
                     think=False,
                     options={"temperature":0,"num_predict":4096,"num_ctx":32768})
            content = r.message.content
            return json.loads(content)
        except Exception as e:
            last = e
            raw = locals().get('content','')
            Path(f'debug_{tag}_attempt_{attempt}.txt').write_text(raw, encoding='utf-8')
            if attempt <= retries:
                print(f'    malformed output; retrying ({attempt}/{retries})...')
                continue
    raise RuntimeError(f'{tag} failed after retries: {last}')

def build_page_evidence(doc):
    pages = defaultdict(list)
    for item in doc.get('texts', []):
        text=(item.get('text') or item.get('orig') or '').strip()
        prov=item.get('prov') or []
        p=prov[0].get('page_no') if prov else None
        if text and p:
            pages[int(p)].append(f"[{item.get('label') or 'text'}] {text}")
    for idx, table in enumerate(doc.get('tables', [])):
        prov=table.get('prov') or []
        p=prov[0].get('page_no') if prov else None
        if not p: continue
        grid=(table.get('data') or {}).get('grid') or []
        rows=[]
        for row in grid:
            vals=[]
            for cell in row:
                vals.append(((cell.get('text') or '') if isinstance(cell,dict) else str(cell)).strip())
            rows.append(' | '.join(vals))
        if rows: pages[int(p)].append(f"[TABLE {idx}]\n"+'\n'.join(rows))
    return dict(sorted(pages.items()))

def render_pages(pages, nums):
    out=[]
    for p in nums:
        if p in pages:
            out.append(f'\n===== PAGE {p} =====')
            out.extend(pages[p])
    return '\n'.join(out)

def flat_page_text(pages,p): return '\n'.join(pages.get(p,[]))

def find_insured_blocks(pages):
    pattern=re.compile(r'\bAsegurado\s+(\d+)\b',re.I)
    starts=[]
    for p in sorted(pages):
        txt=flat_page_text(pages,p)
        m=pattern.search(txt)
        if m and 'CERTIFICADO DE COBERTURA' in txt.upper(): starts.append((int(m.group(1)),p))
    seen={}
    for num,p in starts: seen.setdefault(num,p)
    starts=sorted(seen.items(),key=lambda x:x[1])
    # For insured extraction we need only the certificate page itself.
    # Policy-wide condition pages are extracted separately below.
    return [(num, [start]) for num, start in starts]

def find_condition_pages(pages):
    keys=('CONDICIONES ESPECIALES','COBERTURA DE PREEXISTENCIA','TOPE DE COASEGURO','PERIODOS DE ESPERA','PENALIZACIÓN','PENALIZACION','TERAPIA GÉNICA','TERAPIA GENICA')
    out=[]; seen=set()
    for p in sorted(pages):
        txt=flat_page_text(pages,p); up=txt.upper()
        if not any(k in up for k in keys): continue
        norm=re.sub(r'\s+',' ',up)
        norm=re.sub(r'P[ÁA]GINA\s*:?\s*\d+\s*/\s*\d+','',norm)
        fp=norm[:2500]
        if fp not in seen:
            seen.add(fp); out.append(p)
    return out

def empty_final():
    return {
      'document':{'document_type':None,'language':None,'page_count':None},
      'policy':{'insurer':None,'policy_number':None,'version':None,'renewal_number':None,'product_line':None,'plan_raw_text':None,'plan_name':None,'hospital_level':None,'scheme_name':None,'issue_date':None,'coverage_start_date':None,'coverage_end_date':None,'term_days':None,'currency':None,'movement_type':None,'movement_description':None,'source_page':None},
      'policyholder':{'name':None,'customer_code':None,'tax_id':None,'address':None,'email':None,'phone':None,'source_page':None},
      'premium_summary':{'net_premium':None,'installment_surcharge':None,'policy_fee':None,'tax_rate_percent':None,'tax_amount':None,'total_amount':None,'payment_method':None,'payment_channel':None,'source_page':None},
      'agent':{'name':None,'agent_code':None,'source_page':None},
      'insureds':[], 'policy_conditions':[],
      'regulatory':{'registration_number':None,'registration_date':None,'source_page':None},
      'document_sections':[], 'validation':{'warnings':[]}}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('docling_json')
    ap.add_argument('--model',default='qwen3:8b')
    ap.add_argument('--output',default='insurance_v3_local.json')
    args=ap.parse_args()
    doc=load_json(args.docling_json)
    pages=build_page_evidence(doc)
    if not pages: raise RuntimeError('No page-aware evidence found in Docling JSON.')
    maxp=max(pages); final=empty_final()
    print(f'Pages detected: {len(pages)}')
    print(f'Model: {args.model}')

    meta_pages=[1]
    print('[1] Policy metadata from page 1...')
    meta=call_structured(args.model,schema_policy_meta(),f'''Extract policy-level information only.
'Línea Azul' is a product line if presented that way; do not automatically use it as plan_name.
Preserve the full explicit Plan text in plan_raw_text. Extract regulatory registration only if explicit.
EVIDENCE:\n{render_pages(pages,[1])}''', tag='meta')
    for k in ('document','policy','policyholder','premium_summary','agent','regulatory'): final[k]=meta[k]
    final['document']['page_count']=maxp
    final['validation']['warnings'].extend(meta.get('warnings',[]))

    blocks=find_insured_blocks(pages)
    if not blocks:
        final['validation']['warnings'].append({'field':'insureds','issue':'No insured certificate blocks automatically detected.','severity':'high','source_page':None})
    for i,(num,page_nums) in enumerate(blocks,start=2):
        print(f'[{i}] Insured {num} from pages {page_nums}...')
        insured=call_structured(args.model,schema_insured(),f'''Extract ONLY insured number {num} from this certificate block.
Coverage values must stay on the same logical row. "500.00 por servicio" is a service cost when tied to a service such as Membresía Médica Móvil, not a deductible unless explicitly labeled so.
For Emergencia Médica en el Extranjero, do not copy Nacional deductible/coinsurance.
conditions[] should be empty unless a condition is explicitly unique to this insured.
EVIDENCE:\n{render_pages(pages,page_nums)}''', tag=f'insured_{num}')
        warnings=insured.pop('warnings',[])
        final['insureds'].append(insured)
        final['validation']['warnings'].extend(warnings)
        Path(args.output).write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')

    cpages=find_condition_pages(pages)[:4]
    for j,pnum in enumerate(cpages,start=len(blocks)+2):
        print(f'[{j}] Policy conditions from page {pnum}...')
        cond=call_structured(args.model,schema_conditions(),f'''Extract ONLY policy-wide conditions/rules from this page.
Preserve every table row. Do not infer which row applies unless explicit. Avoid duplicates.
EVIDENCE:\n{render_pages(pages,[pnum])}''', tag=f'conditions_{pnum}')
        final['policy_conditions'].extend(cond.get('policy_conditions',[]))
        final['validation']['warnings'].extend(cond.get('warnings',[]))
        Path(args.output).write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')

    codes=[x.get('customer_code') for x in final['insureds'] if x.get('customer_code')]
    if len(codes)!=len(set(codes)):
        final['validation']['warnings'].append({'field':'insureds.customer_code','issue':'Duplicate customer codes detected.','severity':'high','source_page':None})
    totals=[x.get('premium',{}).get('total_amount') for x in final['insureds'] if x.get('premium',{}).get('total_amount') is not None]
    ptotal=final.get('premium_summary',{}).get('total_amount')
    if totals and ptotal is not None and abs(sum(totals)-ptotal)>0.05:
        final['validation']['warnings'].append({'field':'premium_summary.total_amount','issue':f'Sum of insured premiums ({sum(totals):.2f}) does not match policy total ({ptotal:.2f}).','severity':'high','source_page':1})

    Path(args.output).write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')
    print('\nDONE')
    print(f'Saved: {args.output}')
    print(f'Insureds found: {len(final["insureds"])}')
    print(f'Policy conditions: {len(final["policy_conditions"])}')
    print(f'Validation warnings: {len(final["validation"]["warnings"])}')

if __name__=='__main__': main()
