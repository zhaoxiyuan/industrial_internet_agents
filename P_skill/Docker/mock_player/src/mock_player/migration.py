"""Read-only import of the first implementation. Originals remain as backups."""
import json
import sqlite3

from .storage import canonical, iso, parse_time
from . import results


def migrate(store):
    with store.db() as db:
        if db.execute("SELECT legacy_imported FROM player_state WHERE id=1").fetchone()[0]: return
    paths=list((store.root/"runs").glob("*/index.sqlite3"))
    sources=[]
    for path in paths:
        old=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
        old.row_factory=sqlite3.Row
        try:
            state=json.loads(old.execute("SELECT value FROM meta").fetchone()[0])
            sources.append((parse_time(state["started_at"]),path,state))
        finally: old.close()
    for start,path,state in sorted(sources,key=lambda item:item[0]):
        old=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True);old.row_factory=sqlite3.Row
        try:
            mapping={}
            for row in old.execute("SELECT * FROM snapshots ORDER BY second"):
                second=row["second"]; batches={s:[] for s in ("cv","sensor","position")}
                for record in old.execute("SELECT * FROM records WHERE second=? ORDER BY seq",(second,)):
                    batches[record["source"]].append(json.loads(record["payload"]))
                store.commit(state["scenario"],second,batches,json.loads(row["payload"]),start,False)
            # Translate old raw-log references to their normalized record(s).
            with store.db(write=True) as db:
                for record in old.execute("SELECT * FROM records ORDER BY seq"):
                    value=json.loads(record["payload"]); t=start+round(float(value["scenario_time"].rstrip("s"))*1_000_000)
                    table={"cv":"cv_observations","sensor":"sensor_readings","position":"position_readings"}[record["source"]]
                    sql=f"SELECT id FROM {table} WHERE observed_at_us=?";args=[t]
                    if record["source"]=="cv":sql+=" AND person_id=?";args.append(value["person_id"])
                    mapping[record["seq"]]=[dict(table=table,id=r[0]) for r in db.execute(sql,args)]
                for row in old.execute("SELECT * FROM results ORDER BY seq"):
                    value=json.loads(row["content"])
                    evidence=[]
                    for ref in value.get("evidence",[]):
                        if "snapshot_second" in ref:
                            stamp=start+ref["snapshot_second"]*1_000_000
                            evidence.extend(dict(table="ppe_second_stats",id=r[0]) for r in db.execute("SELECT id FROM ppe_second_stats WHERE window_start_us=?",(stamp,)))
                        elif "record_id" in ref:
                            evidence.extend(mapping.get(int(ref["record_id"].rsplit(":",1)[1]),[]))
                    converted={k:v for k,v in value.items() if k not in {"run_id","task_id","consumer_id","time_range","evidence","schema_version","result_id"}}
                    converted["events"]=[]
                    converted.update(schema_version=3,result_id="legacy:"+path.parent.name+":"+value["result_id"],external_task_id=value.get("task_id"),
                                     time_range=[iso(start+round(t*1_000_000)) for t in value["time_range"]],evidence=evidence)
                    results.insert(store,db,converted,trusted_import=True)
        finally: old.close()
    with store.db(write=True) as db:db.execute("UPDATE player_state SET legacy_imported=1 WHERE id=1")
