import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mock_player.aggregation import aggregate
from mock_player.engine import Engine, second_data
from mock_player.models import ToolError
from mock_player.storage import Store, parse_time, iso
from mock_player.tasks import Tasks
from mock_player import results
from mock_player.cli import parser


class PlayerTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/"test-data";base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base);self.store=Store(self.temp.name)
        self.clock=parse_time("2026-10-12T09:00:00+08:00")
        self.tasks=Tasks(self.store,lambda:self.clock)

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def fill(self,count=10,scenario="B",start=None,closed=True):
        if start is None:
            latest=self.store.data_status()["committed_through"]
            start=parse_time(latest)+10_000_000 if latest else self.clock
        stamp=datetime.fromisoformat(iso(start))
        for sec in range(count):
            data=second_data(scenario,sec,stamp)
            self.store.commit(scenario,sec,data,aggregate(sec,data["cv"],data["sensor"],data["position"]),start,False)
        self.store.update(state="completed" if closed else "running",started_at=iso(start),scenario=scenario,current_second=count)
        return start

    def value(self,task,name="result"):
        return dict(schema_version=3,result_id=name,agent_id="platform-agent",summary="处理完成",events=[],evidence=[])

    def test_one_database_all_scenarios_and_flat_tables(self):
        for scenario in "ABCDE":self.fill(30,scenario)
        self.assertEqual(self.store.data_status()["counts"],dict(cv=11250,sensor=450,position=450,ppe=450))
        self.assertEqual(len(list(Path(self.temp.name).glob("*.sqlite3"))),1)
        with self.store.db() as db:
            tables=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'").fetchall()
            self.assertEqual(len(tables),12)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0],"wal")
            self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())

    def test_frozen_pagination_and_incremental_pull(self):
        start=self.fill(1)
        filters=dict(source="cv",from_time=None,to_time=None,person_id=None,sensor_id=None)
        first=self.store.records(filters,limit=10)
        self.fill(1,start=start+1_000_000)
        token=first["next_cursor"];seen=first["items"]
        while True:
            page=self.store.records(filters,token,10);seen.extend(page["items"]);token=page["next_cursor"]
            if not page["has_more"]:break
        self.assertEqual(len(seen),75)
        live=self.store.records(filters,limit=1000,live=True)
        self.assertEqual(len(live["items"]),150)
        self.fill(1,start=start+2_000_000)
        self.assertEqual(len(self.store.records(filters,live["next_cursor"],1000,True)["items"]),75)
        with self.assertRaises(ToolError):self.store.records({**filters,"source":"sensor"},token)

    def test_time_boundaries_and_gaps(self):
        start=self.fill(2);self.fill(1,start=start+5_000_000)
        window=self.store.window(iso(start),iso(start+2_000_000))
        self.assertTrue(window["completeness"]["complete"])
        self.assertEqual(len(window["ppe"]),6);self.assertEqual(len(window["sensor"]),6)
        self.assertEqual(len(window["position"]),6)
        self.assertFalse(self.store.window(iso(start),iso(start+6_000_000))["completeness"]["complete"])
        filtered=self.store.records(dict(source="sensor",sensor_id="gas_detector_03",from_time=iso(start),to_time=iso(start+1_000_000)))
        self.assertEqual(len(filtered["items"]),1)
        with self.assertRaises(ToolError):parse_time("2026-10-12T09:00:00")

    def test_concurrent_claims_are_distinct(self):
        self.fill(30)
        self.assertEqual(self.tasks.list()['items'],[])
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses=list(pool.map(lambda worker:self.tasks.claim(5,str(worker)),range(8)))
        claimed=[r for r in responses if r['status']=='claimed']
        self.assertEqual(len(claimed),1)
        self.assertEqual(sum(r['status']=='blocked' for r in responses),7)
        self.assertEqual(self.tasks.seconds()['counts']['processing'],5)

    def test_renew_expire_reclaim_and_fencing(self):
        self.fill(5)
        task=self.tasks.claim(5,lease_seconds=10)["task"];tid=task["task_id"];old=task["lease_token"]
        self.clock+=5_000_000
        renewed=self.tasks.heartbeat(tid,old,20)
        self.assertEqual(renewed["lease_expires_at_us"],self.clock+20_000_000)
        self.clock+=21_000_000
        with self.assertRaises(ToolError):self.tasks.complete(tid,old,self.value(task))
        again=self.tasks.claim(5)["task"]
        self.assertEqual(again["task_id"],tid);self.assertNotEqual(again["lease_token"],old)
        with self.assertRaises(ToolError):self.tasks.heartbeat(tid,old)
        self.tasks.complete(tid,again["lease_token"],self.value(again))

    def test_completion_atomic_idempotent_and_contiguous_progress(self):
        self.fill(10);a=self.tasks.claim(5)['task']
        bad=self.value(a,'first-window');bad['evidence']=[dict(table='sensor_readings',id=999999)]
        with self.assertRaises(ToolError):self.tasks.complete(a['task_id'],a['lease_token'],bad)
        self.assertEqual(self.tasks.get(a['task_id'])['state'],'processing')
        good=self.value(a,'first-window');data=self.store.window(task_id=a['task_id']);good['evidence']=[data['sensor'][0]['record_ref']]
        with patch.object(self.tasks,'_progress',side_effect=RuntimeError('simulated transaction failure')):
            with self.assertRaises(RuntimeError):self.tasks.complete(a['task_id'],a['lease_token'],good)
        self.assertEqual(self.tasks.get(a['task_id'])['state'],'processing')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_results').fetchone()[0],0)
        done=self.tasks.complete(a['task_id'],a['lease_token'],good)
        self.assertFalse(done['receipt']['duplicate'])
        self.assertTrue(self.tasks.complete(a['task_id'],a['lease_token'],good)['receipt']['duplicate'])
        self.assertEqual(self.tasks.seconds()['completed_through'],a['time_range'][1])
        with self.assertRaises(ToolError):self.tasks.complete(a['task_id'],a['lease_token'],{**good,'summary':'different'})
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_evidence').fetchone()[0],1)
        marked=self.store.records(dict(source='sensor',task_id=a['task_id']))
        self.assertTrue(all(r['processing']['state']=='completed' for r in marked['items']))
        b=self.tasks.claim(3)['task'];self.tasks.complete(b['task_id'],b['lease_token'],self.value(b,'second-window'))
        self.assertEqual(self.tasks.seconds()['completed_through'],b['time_range'][1])

    def test_failure_backoff_exhaustion_manual_retry(self):
        self.fill(5);a=self.tasks.claim(5)['task']
        first=self.tasks.fail(a['task_id'],a['lease_token'],dict(code='timeout'),10)
        self.assertEqual(first['task']['state'],'retryable')
        self.assertTrue(self.tasks.fail(a['task_id'],a['lease_token'],dict(code='timeout'),10)['duplicate'])
        self.assertEqual(self.tasks.claim(5)['status'],'no_work')
        self.clock+=10_000_000
        for attempt in range(2):
            b=self.tasks.claim(5)['task'];failed=self.tasks.fail(b['task_id'],b['lease_token'],dict(code='timeout'))
        self.assertEqual(failed['task']['state'],'failed')
        self.assertEqual(self.tasks.seconds()['counts']['failed'],5)
        self.tasks.retry(b['task_id']);c=self.tasks.claim(5)['task'];self.assertEqual(c['attempt_count'],4)
        with self.assertRaises(ToolError):self.tasks.complete(c['task_id'],b['lease_token'],self.value(c))
        self.tasks.complete(c['task_id'],c['lease_token'],self.value(c))

    def test_partial_window_waits_then_seals_and_gaps_do_not_mix(self):
        start=self.fill(3,closed=False)
        self.assertEqual(self.tasks.claim(5)['status'],'no_work')
        self.assertEqual(self.tasks.list()['items'],[])
        self.store.update(state='stopped');task=self.tasks.claim(5)['task']
        self.assertEqual(task['time_range'],[iso(start),iso(start+3_000_000)])
        self.tasks.complete(task['task_id'],task['lease_token'],self.value(task))
        self.fill(5,start=start+10_000_000);other=self.tasks.claim(5)['task']
        self.assertEqual(other['time_range'][0],iso(start+10_000_000))

    def test_restart_preserves_tasks_and_expires_old_lease(self):
        self.fill(5);a=self.tasks.claim(5,lease_seconds=1)["task"]
        self.store.close();self.store=Store(self.temp.name);self.clock+=2_000_000
        self.tasks=Tasks(self.store,lambda:self.clock);b=self.tasks.claim(5)["task"]
        self.assertEqual(a["task_id"],b["task_id"])
        self.assertNotEqual(a["lease_token"],b["lease_token"])
        with self.assertRaises(RuntimeError):Store(self.temp.name)

    def test_engine_complete_stop_and_failure(self):
        async def fast_sleep(delay):pass
        async def check():
            engine=Engine(self.store)
            with patch("mock_player.engine.asyncio.sleep",fast_sleep):engine.start("E");await engine.task
            self.assertEqual(self.store.get()["state"],"completed")
            self.assertEqual(self.store.get()["counts"],dict(cv=2250,sensor=90,position=90,ppe=90))
            engine.start("B");await engine.stop();self.assertEqual(self.store.get()["state"],"stopped")
            with patch("mock_player.engine.asyncio.sleep",fast_sleep),patch.object(self.store,"commit",side_effect=OSError("disk")):
                engine.start("A");await engine.task
            self.assertEqual(self.store.get()["state"],"failed")
        asyncio.run(check())

    def test_cli_options(self):
        for argv in (["--json","data","status"],["data","status","--json"],["tasks","claim","--window-seconds","2","--json"]):self.assertTrue(parser().parse_args(argv).json)

    def test_legacy_import_is_read_only_and_idempotent(self):
        start=self.clock;stamp=datetime.fromisoformat(iso(start));data=second_data("B",0,stamp)
        old_path=Path(self.temp.name)/"runs"/('a'*32)/"index.sqlite3";old_path.parent.mkdir(parents=True)
        old=sqlite3.connect(old_path)
        with old:
            old.executescript("CREATE TABLE meta(value TEXT);CREATE TABLE records(seq INTEGER,second INTEGER,source TEXT,payload TEXT);CREATE TABLE snapshots(second INTEGER,payload TEXT);CREATE TABLE results(content TEXT,seq INTEGER);")
            old.execute("INSERT INTO meta VALUES(?)",(json.dumps(dict(started_at=iso(start),scenario="B")),))
            seq=0
            for source,rows in data.items():
                for row in rows:seq+=1;old.execute("INSERT INTO records VALUES(?,?,?,?)",(seq,0,source,json.dumps(row)))
            old.execute("INSERT INTO snapshots VALUES(?,?)",(0,json.dumps(aggregate(0,data["cv"],data["sensor"],data["position"]))))
            value=dict(schema_version=1,run_id='a'*32,result_id="old",agent_id="p",task_id="t",time_range=[0,1],status="completed",summary="old",evidence=[dict(snapshot_second=0)])
            old.execute("INSERT INTO results VALUES(?,1)",(json.dumps(value),))
        old.close();digest=hashlib.sha256(old_path.read_bytes()).hexdigest()
        with self.store.db(write=True) as db:db.execute("UPDATE player_state SET legacy_imported=0")
        self.store.close();self.store=Store(self.temp.name)
        self.assertEqual(self.store.data_status()["counts"],dict(cv=75,sensor=3,position=3,ppe=3))
        self.assertEqual(digest,hashlib.sha256(old_path.read_bytes()).hexdigest())
        self.store.close();self.store=Store(self.temp.name)
        with self.store.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_results").fetchone()[0],1)


if __name__=="__main__":unittest.main()
