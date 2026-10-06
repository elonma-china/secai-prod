"""SQLite review ledger. A connection/transaction per operation, never shared by threads."""
from __future__ import annotations
import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from ..api.review_schemas import RUBRIC


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

class ReviewError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)

from .review_training_store import TrainingLedger

class ReviewStore(TrainingLedger):
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS live_settings (id INTEGER PRIMARY KEY CHECK(id=1), sample_percent INTEGER NOT NULL CHECK(sample_percent BETWEEN 0 AND 100));
            CREATE TABLE IF NOT EXISTS review_meta (schema_version INTEGER NOT NULL);
            INSERT INTO review_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM review_meta);
            CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, title TEXT NOT NULL, rubric_version TEXT NOT NULL, state TEXT NOT NULL, creator_id TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id), external_id TEXT NOT NULL, snapshot TEXT NOT NULL, content_sha256 TEXT NOT NULL, UNIQUE(campaign_id,external_id));
            CREATE TABLE IF NOT EXISTS assignments (id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), reviewer_id TEXT NOT NULL, left_candidate_id TEXT NOT NULL, right_candidate_id TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(item_id,reviewer_id));
            CREATE TABLE IF NOT EXISTS judgments (id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL UNIQUE REFERENCES assignments(id), payload TEXT NOT NULL, normalized TEXT NOT NULL, submitted_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS adjudications (id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE REFERENCES items(id), payload TEXT NOT NULL, admin_id TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL, entity_id TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS assignments_reviewer ON assignments(reviewer_id);
            ''')
            self.init_training_ledger(db)
            if db.execute('SELECT schema_version FROM review_meta').fetchone()[0] != 1:
                raise ValueError('unsupported review schema version')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def audit(self, db, actor, action, entity, details=None):
        db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?)', (str(uuid4()), actor, action, entity, now(), encode(details or {})))

    def live_settings(self, default_percent):
        with self.connection() as db:
            row = db.execute('SELECT sample_percent FROM live_settings WHERE id=1').fetchone()
            return {'sample_percent': row['sample_percent'] if row else default_percent}

    def set_live_settings(self, percent, default_percent, actor):
        with self.connection(True) as db:
            row = db.execute('SELECT sample_percent FROM live_settings WHERE id=1').fetchone()
            before = row['sample_percent'] if row else default_percent
            db.execute('INSERT INTO live_settings VALUES (1,?) ON CONFLICT(id) DO UPDATE SET sample_percent=excluded.sample_percent', (percent,))
            self.audit(db, actor, 'live.settings.update', 'global', {'before_percent': before, 'after_percent': percent})
            return {'sample_percent': percent}

    def campaign(self, db, cid, writable=False):
        row = db.execute('SELECT * FROM campaigns WHERE id=?', (cid,)).fetchone()
        if row is None: raise ReviewError(404, 'campaign not found')
        if writable and row['state'] != 'open': raise ReviewError(409, 'campaign is closed')
        return dict(row)

    def item(self, db, iid, writable=False, mutable=False):
        row = db.execute('SELECT * FROM items WHERE id=?', (iid,)).fetchone()
        if row is None: raise ReviewError(404, 'item not found')
        self.campaign(db, row['campaign_id'], writable)
        if mutable and db.execute('SELECT 1 FROM adjudications WHERE item_id=?', (iid,)).fetchone():
            raise ReviewError(409, 'adjudicated item is immutable')
        return {'id':row['id'], 'campaign_id':row['campaign_id'], **json.loads(row['snapshot']), 'content_sha256':row['content_sha256']}

    def assignment(self, db, aid, reviewer=None):
        row = db.execute('SELECT * FROM assignments WHERE id=?', (aid,)).fetchone()
        if row is None or (reviewer is not None and row['reviewer_id'] != reviewer):
            raise ReviewError(404, 'assignment not found')
        return dict(row)

    def create_campaign(self, title, rubric_version, actor_id):
        c = dict(id=str(uuid4()), title=title, rubric_version=rubric_version, state='open', creator_id=actor_id, created_at=now())
        with self.connection(True) as db:
            db.execute('INSERT INTO campaigns VALUES (?,?,?,?,?,?)', tuple(c.values()))
            self.audit(db, actor_id, 'campaign.create', c['id'])
        return c

    def list_campaigns(self, reviewer=None):
        with self.connection() as db:
            if reviewer is None: rows = db.execute('SELECT * FROM campaigns ORDER BY created_at,id').fetchall()
            else: rows = db.execute('SELECT DISTINCT c.* FROM campaigns c JOIN items i ON i.campaign_id=c.id JOIN assignments a ON a.item_id=i.id WHERE a.reviewer_id=? ORDER BY c.created_at,c.id', (reviewer,)).fetchall()
            return [dict(r) for r in rows]

    def set_campaign_state(self, cid, state, actor):
        with self.connection(True) as db:
            self.campaign(db,cid)
            db.execute('UPDATE campaigns SET state=? WHERE id=?', (state,cid))
            self.audit(db,actor,'campaign.state',cid,{'state':state})
            return self.campaign(db,cid)

    def import_items(self, cid, items, actor_id):
        imported = skipped = 0
        with self.connection(True) as db:
            self.campaign(db,cid,True)
            for value in items:
                snapshot = encode(value.model_dump())
                digest = hashlib.sha256(snapshot.encode()).hexdigest()
                old = db.execute('SELECT content_sha256 FROM items WHERE campaign_id=? AND external_id=?', (cid,value.external_id)).fetchone()
                if old:
                    if old[0] != digest: raise ReviewError(409, f'external_id conflict: {value.external_id}')
                    skipped += 1
                else:
                    db.execute('INSERT INTO items VALUES (?,?,?,?,?)', (str(uuid4()),cid,value.external_id,snapshot,digest))
                    imported += 1
            self.audit(db,actor_id,'items.import',cid,{'imported':imported,'skipped':skipped})
        return {'imported':imported,'skipped':skipped}

    def adjudication(self, db, iid):
        row = db.execute('SELECT * FROM adjudications WHERE item_id=?', (iid,)).fetchone()
        if not row: return None
        return {'id':row['id'],'item_id':iid,**json.loads(row['payload']), 'admin_id':row['admin_id'],'created_at':row['created_at']}

    def judgments(self, db, iid):
        rows = db.execute('SELECT j.*,a.reviewer_id FROM judgments j JOIN assignments a ON a.id=j.assignment_id WHERE a.item_id=? ORDER BY j.id', (iid,)).fetchall()
        return [{'id':r['id'],'assignment_id':r['assignment_id'],'reviewer_id':r['reviewer_id'],**json.loads(r['normalized']),'submitted_at':r['submitted_at']} for r in rows]

    def item_status(self, db, iid):
        judgments = self.judgments(db,iid)
        choices = {(j['decision'] if j['winning_candidate_id'] is None else 'winner',j['winning_candidate_id']) for j in judgments}
        assignments = [{'id':r['id'],'reviewer_id':r['reviewer_id'],'status':'submitted' if r['submitted'] else 'pending'} for r in db.execute('SELECT a.id,a.reviewer_id,j.id submitted FROM assignments a LEFT JOIN judgments j ON j.assignment_id=a.id WHERE a.item_id=? ORDER BY a.id',(iid,)).fetchall()]
        return {'assignments':assignments,'assignment_count':db.execute('SELECT count(*) FROM assignments WHERE item_id=?',(iid,)).fetchone()[0], 'submitted_count':len(judgments), 'adjudication':self.adjudication(db,iid), 'disagreement':len(choices)>1}

    def list_items(self, cid, disagreement=False):
        with self.connection() as db:
            self.campaign(db,cid)
            values = [dict(self.item(db,r[0]),**self.item_status(db,r[0])) for r in db.execute('SELECT id FROM items WHERE campaign_id=? ORDER BY id',(cid,)).fetchall()]
            return [v for v in values if not disagreement or v['disagreement']]

    def assign(self, iid, reviewers, actor):
        with self.connection(True) as db:
            item = self.item(db,iid,True,True)
            result = []
            for reviewer in dict.fromkeys(reviewers):
                old = db.execute('SELECT * FROM assignments WHERE item_id=? AND reviewer_id=?',(iid,reviewer)).fetchone()
                if old: result.append(dict(old)); continue
                candidates = [c['id'] for c in item['candidates']]
                if secrets.randbelow(2): candidates.reverse()
                a = dict(id=str(uuid4()),item_id=iid,reviewer_id=reviewer,left_candidate_id=candidates[0],right_candidate_id=candidates[1],created_at=now())
                db.execute('INSERT INTO assignments VALUES (?,?,?,?,?,?)',tuple(a.values()))
                self.audit(db,actor,'assignment.create',a['id'],{'reviewer_id':reviewer})
                result.append(a)
            return result

    def delete_assignment(self, aid, actor):
        with self.connection(True) as db:
            a = self.assignment(db,aid)
            self.item(db,a['item_id'],True,True)
            if db.execute('SELECT 1 FROM judgments WHERE assignment_id=?',(aid,)).fetchone(): raise ReviewError(409,'submitted assignment is immutable')
            db.execute('DELETE FROM assignments WHERE id=?',(aid,))
            self.audit(db,actor,'assignment.delete',aid,{'item_id':a['item_id'],'reviewer_id':a['reviewer_id']})

    def list_assignments(self, reviewer, status=None, campaign_id=None):
        with self.connection() as db:
            rows = db.execute('SELECT a.id,a.item_id,a.reviewer_id,a.created_at,c.id campaign_id,c.title campaign_title,i.external_id,CASE WHEN j.id IS NULL THEN "pending" ELSE "submitted" END status FROM assignments a JOIN items i ON i.id=a.item_id JOIN campaigns c ON c.id=i.campaign_id LEFT JOIN judgments j ON j.assignment_id=a.id WHERE a.reviewer_id=? ORDER BY a.created_at,a.id',(reviewer,)).fetchall()
            return [dict(r) for r in rows if (not status or r['status']==status) and (not campaign_id or r['campaign_id']==campaign_id)]

    def judgment(self, db, aid):
        row = db.execute('SELECT * FROM judgments WHERE assignment_id=?',(aid,)).fetchone()
        return None if row is None else {'id':row['id'],'assignment_id':aid,**json.loads(row['payload']),'submitted_at':row['submitted_at']}

    def assignment_detail(self, aid, reviewer):
        with self.connection() as db:
            a = self.assignment(db,aid,reviewer)
            item = self.item(db,a['item_id'])
            c = self.campaign(db,item['campaign_id'])
            candidates = {v['id']:v['text'] for v in item['candidates']}
            j = self.judgment(db,aid)
            return {'id':aid,'item_id':item['id'],'campaign_id':c['id'],'campaign_title':c['title'],'external_id':item['external_id'],'prompt_messages':item['prompt_messages'],'evidence':item['evidence'],'left':{'text':candidates[a['left_candidate_id']]},'right':{'text':candidates[a['right_candidate_id']]},'rubric_version':c['rubric_version'],'rubric':RUBRIC,'judgment':j,'status':'submitted' if j else 'pending'}

    def submit_judgment(self, aid, reviewer_id, payload):
        with self.connection(True) as db:
            a = self.assignment(db,aid,reviewer_id)
            self.item(db,a['item_id'],True)
            old = self.judgment(db,aid)
            serialized = encode(payload.model_dump())
            if old:
                previous = {k:old[k] for k in ('decision','error_tags','comment')}
                if encode(previous) != serialized: raise ReviewError(409,'submitted judgment is immutable')
                return old
            self.item(db,a['item_id'],mutable=True)
            winner = a[f'{payload.decision}_candidate_id'] if payload.decision in {'left','right'} else None
            normalized = {'decision':payload.decision,'winning_candidate_id':winner,'candidate_error_tags':{a['left_candidate_id']:payload.error_tags.left,a['right_candidate_id']:payload.error_tags.right},'comment':payload.comment}
            db.execute('INSERT INTO judgments VALUES (?,?,?,?,?)',(str(uuid4()),aid,serialized,encode(normalized),now()))
            self.audit(db,reviewer_id,'judgment.submit',aid)
            return self.judgment(db,aid)

    def item_judgments(self, iid):
        with self.connection() as db:
            self.item(db,iid)
            return {'item_id':iid,'judgments':self.judgments(db,iid),'adjudication':self.adjudication(db,iid)}

    def adjudicate(self, iid, payload, actor):
        with self.connection(True) as db:
            item = self.item(db,iid,True)
            old = self.adjudication(db,iid)
            serialized = encode(payload.model_dump())
            if old:
                if encode({k:old[k] for k in ('outcome','winning_candidate_id','reason')}) != serialized: raise ReviewError(409,'adjudication is immutable')
                return old
            if payload.winning_candidate_id and payload.winning_candidate_id not in {c['id'] for c in item['candidates']}: raise ReviewError(422,'unknown winning candidate')
            status = self.item_status(db,iid)
            if not status['assignment_count'] or status['assignment_count'] != status['submitted_count']: raise ReviewError(409,'all assignments must be submitted before adjudication')
            db.execute('INSERT INTO adjudications VALUES (?,?,?,?,?)',(str(uuid4()),iid,serialized,actor,now()))
            self.audit(db,actor,'item.adjudicate',iid)
            return self.adjudication(db,iid)

    def stats(self, cid):
        items = self.list_items(cid)
        return {'items':len(items),'assignments':sum(i['assignment_count'] for i in items),'submitted':sum(i['submitted_count'] for i in items),'pending_adjudication':sum(bool(i['assignment_count']) and i['assignment_count']==i['submitted_count'] and not i['adjudication'] for i in items),'adjudicated':sum(bool(i['adjudication']) for i in items),'disagreements':sum(i['disagreement'] for i in items),'multi_reviewed_items':sum(i['submitted_count']>=2 for i in items)}

    def export_snapshot(self, cid):
        with self.connection() as db:
            campaign = self.campaign(db,cid)
            items = []
            for row in db.execute('SELECT id FROM items WHERE campaign_id=? ORDER BY id',(cid,)).fetchall():
                iid = row[0]
                items.append({**self.item(db,iid),'assignments':[dict(a) for a in db.execute('SELECT * FROM assignments WHERE item_id=? ORDER BY id',(iid,)).fetchall()],'judgments':self.judgments(db,iid),'adjudication':self.adjudication(db,iid)})
            return {'campaign':campaign,'items':items,'exported_at':now(),'schema_version':1}

    def create_live_assignment(self, value, actor_id):
        """One transaction creates the server-owned campaign, snapshot and assignment."""
        cid = '641f3b8e-8bbe-4603-bf12-fbb181e86d20'
        with self.connection(True) as db:
            db.execute('INSERT OR IGNORE INTO campaigns VALUES (?,?,?,?,?,?)',(cid,'Đánh giá từ chat','security_qa_v1','open','review-live-system',now()))
            self.campaign(db,cid,True)
            iid, aid = str(uuid4()), str(uuid4())
            snapshot=encode(value.model_dump())
            db.execute('INSERT INTO items VALUES (?,?,?,?,?)',(iid,cid,value.external_id,snapshot,hashlib.sha256(snapshot.encode()).hexdigest()))
            candidates=[candidate.id for candidate in value.candidates]
            if secrets.randbelow(2): candidates.reverse()
            db.execute('INSERT INTO assignments VALUES (?,?,?,?,?,?)',(aid,iid,actor_id,candidates[0],candidates[1],now()))
            self.audit(db,actor_id,'live_pair.create',iid,{'assignment_id':aid})
        return aid

    def live_campaign_is_open(self):
        with self.connection() as db:
            row=db.execute('SELECT state FROM campaigns WHERE id=?',('641f3b8e-8bbe-4603-bf12-fbb181e86d20',)).fetchone()
            return row is None or row['state']=='open'
