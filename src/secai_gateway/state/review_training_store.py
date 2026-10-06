"""Additive batch ledger in the existing review database."""
import hashlib
import json
from uuid import uuid4

ACTIVE = {'dispatching','queued','waiting_gpu','loading','training','evaluating','cleanup'}
SOURCE = "CASE WHEN c.creator_id='review-live-system' THEN 'live' WHEN lower(c.title) LIKE '%qa%' OR lower(c.title) LIKE '%demo%' OR lower(c.title) LIKE '%e2e%' THEN 'qa' ELSE 'import' END"
BASE_QUERY = f"""SELECT i.id,i.campaign_id,i.snapshot,c.title campaign_title,c.state campaign_state,
 {SOURCE} source, d.payload approval, d.created_at approved_at,
 (SELECT count(*) FROM assignments a WHERE a.item_id=i.id) assignment_count,
 (SELECT count(*) FROM judgments j JOIN assignments a ON a.id=j.assignment_id WHERE a.item_id=i.id) submitted_count,
 (SELECT max(j.submitted_at) FROM judgments j JOIN assignments a ON a.id=j.assignment_id WHERE a.item_id=i.id) submitted_at,
 (SELECT b.batch_id FROM training_batch_items b WHERE b.item_id=i.id) batch_id
 FROM items i JOIN campaigns c ON c.id=i.campaign_id LEFT JOIN adjudications d ON d.item_id=i.id"""


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def error(status, message):
    from .review_store import ReviewError
    raise ReviewError(status,message)


class TrainingLedger:
    def init_training_ledger(self, db):
        db.executescript('''
        CREATE INDEX IF NOT EXISTS assignments_item ON assignments(item_id);
        CREATE INDEX IF NOT EXISTS items_campaign ON items(campaign_id);
        CREATE TABLE IF NOT EXISTS training_policy (purpose TEXT PRIMARY KEY,minimum_pairs INTEGER NOT NULL,version INTEGER NOT NULL);
        INSERT OR IGNORE INTO training_policy VALUES ('demo',1000,1),('production',1000,1);
        CREATE TABLE IF NOT EXISTS training_batches (id TEXT PRIMARY KEY,request_key TEXT NOT NULL UNIQUE,actor_id TEXT NOT NULL,purpose TEXT NOT NULL,created_at TEXT NOT NULL,policy_version INTEGER NOT NULL,snapshot_sha256 TEXT NOT NULL,sample_count INTEGER NOT NULL,snapshot TEXT NOT NULL,partitions TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_batch_items (batch_id TEXT NOT NULL REFERENCES training_batches(id),item_id TEXT NOT NULL UNIQUE REFERENCES items(id),snapshot TEXT NOT NULL,PRIMARY KEY(batch_id,item_id));
        CREATE TABLE IF NOT EXISTS training_runs (id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES training_batches(id),request_key TEXT NOT NULL UNIQUE,actor_id TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,status TEXT NOT NULL,remote_job_id TEXT,remote TEXT,error TEXT);
        CREATE INDEX IF NOT EXISTS training_runs_batch ON training_runs(batch_id,created_at);
        CREATE TABLE IF NOT EXISTS training_group_splits (purpose TEXT NOT NULL,group_key TEXT NOT NULL,partition TEXT NOT NULL,PRIMARY KEY(purpose,group_key));
        ''')

    def _overview_rows(self,db,status,source,search,offset=None,limit=None):
        clauses=['submitted_count>0']; args=[]
        if status=='pending': clauses.append('approval IS NULL')
        elif status=='approved': clauses.append("json_extract(approval,'$.outcome')='winner' AND batch_id IS NULL")
        elif status=='excluded': clauses.append("approval IS NOT NULL AND json_extract(approval,'$.outcome')!='winner'")
        if source!='all': clauses.append('source=?'); args.append(source)
        if search: clauses.append("instr(lower(json_extract(snapshot,'$.prompt_messages')),lower(?))>0"); args.append(search)
        query='SELECT * FROM ('+BASE_QUERY+') WHERE '+' AND '.join(clauses)
        total=db.execute('SELECT count(*) FROM ('+query+')',args).fetchone()[0]
        query+=' ORDER BY submitted_at DESC,id'
        if limit is not None: query+=' LIMIT ? OFFSET ?'; args.extend([limit,offset])
        return db.execute(query,args).fetchall(),total

    def _row(self,db,r,detail=False):
        snapshot=json.loads(r['snapshot']); judgments=self.judgments(db,r['id'])
        winner={j['winning_candidate_id'] for j in judgments if j['winning_candidate_id']}
        clear=len(winner)==1 and all(j['winning_candidate_id'] for j in judgments)
        choice=next(iter(winner)) if clear else None
        labels={c['id']:chr(65+i) for i,c in enumerate(snapshot['candidates'])}
        user_choice=labels.get(choice) if clear else (judgments[0]['decision'] if len(judgments)==1 else f"{len(judgments)} phiếu")
        return {'id':r['id'],'question':snapshot['prompt_messages'][-1]['content'],'source':r['source'],
            'submitted_at':r['submitted_at'],'user_choice':user_choice,'vote_count':len(judgments),
            'complete':r['assignment_count']==r['submitted_count'],'campaign_state':r['campaign_state'],
            'adjudication':json.loads(r['approval']) if r['approval'] else None,
            'user_winner_id':choice,'batch_id':r['batch_id'],
            'admin_choice':labels.get(json.loads(r['approval']).get('winning_candidate_id')) if r['approval'] else None,
            **({'item':self.item(db,r['id']),'judgments':judgments} if detail else {})}

    def overview(self,status='pending',source='all',search='',offset=0,limit=20):
        with self.connection() as db:
            rows,total=self._overview_rows(db,status,source,search,offset,limit)
            return {'items':[self._row(db,r) for r in rows],'total':total,'offset':offset,'limit':limit}

    def overview_detail(self,iid):
        with self.connection() as db:
            row=db.execute('SELECT * FROM ('+BASE_QUERY+') WHERE id=?',(iid,)).fetchone()
            if row is None: error(404,'item not found')
            return self._row(db,row,True)

    def _bulk(self,db,source,search):
        rows,total=self._overview_rows(db,'pending',source,search)
        eligible=[]
        for r in rows:
            v=self._row(db,r)
            if v['complete'] and v['user_winner_id'] and v['campaign_state']=='open': eligible.append(v)
        return eligible,{'eligible_count':len(eligible),'skipped_count':total-len(eligible),
            'snapshot_version':digest([(v['id'],v['user_winner_id'],v['submitted_at'],v['vote_count']) for v in eligible])}

    def bulk_preview(self,source,search):
        with self.connection() as db: return self._bulk(db,source,search)[1]

    def bulk_approve(self,source,search,version,actor):
        from .review_store import encode,now
        with self.connection(True) as db:
            rows,preview=self._bulk(db,source,search)
            if version!=preview['snapshot_version']: error(409,'feedback changed; refresh the confirmation')
            for r in rows:
                payload={'outcome':'winner','winning_candidate_id':r['user_winner_id'],'reason':'Admin xác nhận hàng loạt theo lựa chọn nhất quán của user.'}
                db.execute('INSERT INTO adjudications VALUES (?,?,?,?,?)',(str(uuid4()),r['id'],encode(payload),actor,now()))
                self.audit(db,actor,'item.adjudicate.bulk',r['id'],{'winning_candidate_id':r['user_winner_id']})
            return {'approved':len(rows),'skipped':preview['skipped_count']}

    def training_policy(self,purpose):
        with self.connection() as db:
            row=db.execute('SELECT * FROM training_policy WHERE purpose=?',(purpose,)).fetchone()
            if row is None: error(422,'unknown purpose')
            return dict(row)

    def _eligible(self,db,purpose):
        from ..reward.dataset import deduplicate, prompt_key, validate_record
        policy=db.execute('SELECT * FROM training_policy WHERE purpose=?',(purpose,)).fetchone()
        if policy is None: error(422,'unknown purpose')
        rows,_=self._overview_rows(db,'approved','qa' if purpose=='demo' else 'live','')
        records=[]; snapshots={}; reasons=[]
        for r in rows:
            item=self.item(db,r['id']); approval=self.adjudication(db,r['id']); judgments=self.judgments(db,r['id'])
            if r['assignment_count']!=r['submitted_count']: continue
            winner=approval['winning_candidate_id']; cs=item['candidates']
            record={'item_id':item['id'],'adjudication_id':approval['id'],'prompt':item['prompt_messages'],
                'chosen':next(c['text'] for c in cs if c['id']==winner),'rejected':next(c['text'] for c in cs if c['id']!=winner),
                'source_group':item['source_group'],'rubric_version':'security_qa_v1'}
            try: validate_record(record)
            except ValueError: continue
            records.append(record)
            snapshots[item['id']]={**item,'adjudication':approval,'judgments':judgments,'assignments':[dict(a) for a in db.execute('SELECT * FROM assignments WHERE item_id=?',(item['id'],))]}
        try: records=deduplicate(records)
        except ValueError: records=[]; reasons.append('Có các lựa chọn mâu thuẫn cho cùng câu hỏi/đáp án.')
        # Connected prompt/source components. Existing keys pin every subsequent batch.
        components=[]
        for record in records:
            keys={'prompt:'+prompt_key(record['prompt'])}
            keys.update('source:'+g for g in record['source_groups'] if g.casefold() not in {'unknown','none','null'})
            touching=[c for c in components if c[0]&keys]
            merged=keys.copy(); members=[record]
            for c in touching: merged|=c[0]; members+=c[1]; components.remove(c)
            components.append((merged,members))
        partitions={}; bindings={}
        for keys,members in components:
            old={r[0] for key in keys for r in db.execute('SELECT partition FROM training_group_splits WHERE purpose=? AND group_key=?',(purpose,key))}
            if len(old)>1:
                reasons.append('Nhóm nguồn nối các tập holdout khác nhau; cần xử lý riêng.'); continue
            split=next(iter(old)) if old else ('test' if int(digest(sorted(keys))[:8],16)%10<2 else 'validation' if int(digest(sorted(keys))[:8],16)%10<4 else 'train')
            bindings.update({key:split for key in keys})
            partitions.update({r['item_id']:split for r in members})
        counts={name:sum(p==name for p in partitions.values()) for name in ('train','validation','test')}
        if len(records)<policy['minimum_pairs']: reasons.append(f"Cần {policy['minimum_pairs']} cặp hợp lệ mới.")
        if any(n==0 for n in counts.values()): reasons.append('Cần nhóm độc lập trong cả train, validation và test.')
        if purpose=='production': reasons.append('Chưa nối danh tính IAS và policy đánh giá production.')
        if db.execute("SELECT 1 FROM training_runs WHERE status IN ("+','.join('?' for _ in ACTIVE)+") LIMIT 1",tuple(ACTIVE)).fetchone(): reasons.append('Đang có một lượt huấn luyện; xem trong lịch sử.')
        items=[snapshots[r['item_id']] for r in records]
        version=digest({'policy':dict(policy),'items':items,'partitions':partitions})
        preview={'purpose':purpose,'minimum_pairs':policy['minimum_pairs'],'policy_version':policy['version'],
            'valid_new_count':len(records),'group_count':len(components),'counts':counts,'eligible':not reasons,
            'blocked_reasons':list(dict.fromkeys(reasons)),'snapshot_version':version}
        return preview,items,partitions,bindings

    def training_eligibility(self,purpose):
        with self.connection() as db: return self._eligible(db,purpose)[0]

    def _new_run(self,db,bid,key,actor):
        from .review_store import now
        rid=str(uuid4()); ts=now()
        db.execute('INSERT INTO training_runs VALUES (?,?,?,?,?,?,?,?,?,?)',(rid,bid,key,actor,ts,ts,'dispatching',None,None,None))
        self.audit(db,actor,'reward.training.attempt',rid,{'batch_id':bid})
        return self._run(db,rid)

    def _run(self,db,rid):
        r=db.execute('SELECT * FROM training_runs WHERE id=?',(rid,)).fetchone()
        if not r: error(404,'run not found')
        value=dict(r); value['remote']=json.loads(value['remote']) if value['remote'] else None
        return value

    def reserve_training_batch(self,key,purpose,version,actor):
        from .review_store import now,encode
        with self.connection(True) as db:
            old=db.execute('SELECT id,purpose FROM training_batches WHERE request_key=?',(key,)).fetchone()
            if old:
                if old['purpose']!=purpose: error(409,'request key conflict')
                return self._batch(db,old['id'])
            p,items,partitions,bindings=self._eligible(db,purpose)
            if p['snapshot_version']!=version: error(409,'dataset changed; refresh the confirmation')
            if not p['eligible']: error(409,' '.join(p['blocked_reasons']))
            bid=str(uuid4()); ts=now()
            snapshot={'campaign':{'id':bid,'title':'QA frozen batch '+bid,'rubric_version':'security_qa_v1'},'items':items,'exported_at':ts,'schema_version':1}
            db.execute('INSERT INTO training_batches VALUES (?,?,?,?,?,?,?,?,?,?)',(bid,key,actor,purpose,ts,p['policy_version'],digest(snapshot),len(items),encode(snapshot),encode(partitions)))
            for item in items:
                db.execute('INSERT INTO training_batch_items VALUES (?,?,?)',(bid,item['id'],encode(item)))
            for group,split in bindings.items(): db.execute('INSERT OR IGNORE INTO training_group_splits VALUES (?,?,?)',(purpose,group,split))
            self._new_run(db,bid,key,actor)
            self.audit(db,actor,'reward.batch.freeze',bid,{'sample_count':len(items),'snapshot_sha256':digest(snapshot)})
            return self._batch(db,bid)

    def _batch(self,db,bid):
        row=db.execute('SELECT * FROM training_batches WHERE id=?',(bid,)).fetchone()
        if not row: error(404,'batch not found')
        result={k:v for k,v in dict(row).items() if k not in {'snapshot','partitions'}}
        result['runs']=[self._run(db,r[0]) for r in db.execute('SELECT id FROM training_runs WHERE batch_id=? ORDER BY created_at,id',(bid,))]
        result['status']=result['runs'][-1]['status']
        return result

    def batch_detail(self,bid):
        with self.connection() as db: return self._batch(db,bid)

    def batch_snapshot(self,bid):
        with self.connection() as db:
            row=db.execute('SELECT snapshot FROM training_batches WHERE id=?',(bid,)).fetchone()
            if not row: error(404,'batch not found')
            return json.loads(row[0])

    def batch_partitions(self,bid):
        with self.connection() as db:
            row=db.execute('SELECT partitions FROM training_batches WHERE id=?',(bid,)).fetchone()
            if not row: error(404,'batch not found')
            return json.loads(row[0])

    def batch_items(self,bid,offset,limit):
        with self.connection() as db:
            self._batch(db,bid)
            rows=db.execute('SELECT snapshot FROM training_batch_items WHERE batch_id=? ORDER BY item_id LIMIT ? OFFSET ?',(bid,limit,offset))
            return {'items':[json.loads(r[0]) for r in rows],'total':db.execute('SELECT count(*) FROM training_batch_items WHERE batch_id=?',(bid,)).fetchone()[0],'offset':offset,'limit':limit}

    def list_batches(self,offset=0,limit=20):
        with self.connection() as db:
            rows=db.execute('SELECT id FROM training_batches ORDER BY created_at DESC,id LIMIT ? OFFSET ?',(limit,offset))
            return {'items':[self._batch(db,r[0]) for r in rows],'total':db.execute('SELECT count(*) FROM training_batches').fetchone()[0],'offset':offset,'limit':limit}

    def training_run(self,rid):
        with self.connection() as db: return self._run(db,rid)

    def update_training_run(self,rid,remote):
        from .review_store import now,encode
        with self.connection(True) as db:
            run=self._run(db,rid)
            if remote.get('id') and run['remote_job_id'] and remote['id']!=run['remote_job_id']: error(409,'remote identity mismatch')
            if remote['status'] not in ACTIVE|{'succeeded','failed','interrupted'}: error(502,'unknown remote status')
            db.execute('UPDATE training_runs SET status=?,remote_job_id=?,remote=?,error=?,updated_at=? WHERE id=?',
                (remote['status'],remote.get('id',run['remote_job_id']),encode(remote),remote.get('error'),now(),rid))
            return self._run(db,rid)

    def retry_batch(self,bid,key,actor):
        with self.connection(True) as db:
            old=db.execute('SELECT id,batch_id FROM training_runs WHERE request_key=?',(key,)).fetchone()
            if old:
                if old['batch_id']!=bid: error(409,'request key conflict')
                return self._run(db,old['id'])
            batch=self._batch(db,bid)
            if batch['status'] not in {'failed','interrupted'}: error(409,'only failed or interrupted batches can retry')
            if db.execute('SELECT 1 FROM training_runs WHERE status IN ('+','.join('?' for _ in ACTIVE)+')',tuple(ACTIVE)).fetchone(): error(409,'another run is active')
            return self._new_run(db,bid,key,actor)
