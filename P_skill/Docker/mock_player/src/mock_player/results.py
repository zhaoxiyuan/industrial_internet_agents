import json

from .models import ToolError
from .storage import TABLES, canonical, bounds, iso, now_us

EVIDENCE_COLUMNS = dict(cv_observations="cv_id",sensor_readings="sensor_id",position_readings="position_id",ppe_second_stats="ppe_id")


def insert(store, db, value, task=None, trusted_import=False):
    if not isinstance(value, dict): raise ToolError("Result must be an object")
    result = dict(value)
    if any(k in result for k in ("job_id","work_permit","permit_id","consumer_id")) or result.get("events",[]):raise ToolError("Business attribution and risk events belong outside acquisition service")
    if "run_id" in result: raise ToolError("run_id is no longer supported; use absolute time_range")
    if task is not None:
        if result.get("task_id", task["id"]) != task["id"]: raise ToolError("Result task_id mismatch")
        result["task_id"] = task["id"]
        result.setdefault("time_range", [iso(task["window_start_us"]),iso(task["window_end_us"])])
        result.setdefault("status", "completed")
        if result["status"] != "completed": raise ToolError("tasks complete requires a completed result")
    elif result.get("task_id") is not None:
        raise ToolError("Task results must be submitted through tasks complete")
    required = {"schema_version","result_id","agent_id","time_range","status"}
    if not required <= result.keys(): raise ToolError("Missing result fields: "+",".join(sorted(required-result.keys())))
    if type(result["schema_version"]) is not int or result["schema_version"] != 3: raise ToolError("Expected schema_version=3")
    for field in ("result_id","agent_id"):
        if not isinstance(result[field], str) or not result[field].strip() or len(result[field])>300: raise ToolError("Invalid "+field)
    if result["status"] not in {"processing","completed","failed"}: raise ToolError("Invalid status")
    if result["status"] == "failed" and not isinstance(result.get("error"), dict): raise ToolError("Failed result requires an error object")
    if "summary" in result and not isinstance(result["summary"], str): raise ToolError("summary must be text")
    for field in ("events","evidence"):
        if not isinstance(result.get(field,[]), list): raise ToolError(field+" must be an array")
    interval = result["time_range"]
    if not isinstance(interval,list) or len(interval)!=2: raise ToolError("time_range requires two ISO timestamps")
    start,end = bounds(*interval)
    if task is not None and (start,end)!=(task["window_start_us"],task["window_end_us"]): raise ToolError("Result interval must equal its task window")
    if not trusted_import and not store.coverage(db,start,end)["complete"]: raise ToolError("Result interval includes uncommitted data or a data gap")
    try: content = canonical(result)
    except (TypeError, ValueError): raise ToolError("Result contains invalid JSON values")
    old = db.execute("SELECT * FROM agent_results WHERE result_id=?",(result["result_id"],)).fetchone()
    if old:
        if old["content_json"]!=content: raise ToolError("result_id already contains different content",409,"conflict")
        return dict(result_id=old["result_id"],result_seq=old["id"],received_at=iso(old["received_at_us"]),duplicate=True)
    references=[]
    for ref in result.get("evidence",[]):
        if not isinstance(ref,dict) or set(ref)!={"table","id"} or ref["table"] not in EVIDENCE_COLUMNS or type(ref["id"]) is not int:
            raise ToolError("Evidence must be {table,id} for a supported data table")
        table=ref["table"]; row=db.execute(f"SELECT * FROM {table} WHERE id=?",(ref["id"],)).fetchone()
        if not row: raise ToolError("Evidence record does not exist")
        timestamp=row["window_start_us"] if table=="ppe_second_stats" else row["observed_at_us"]
        if not start<=timestamp<end: raise ToolError("Evidence is outside the result interval")
        if task is not None and row["ingest_seq"]>task["cutoff_sequence"]: raise ToolError("Evidence is outside the task data snapshot")
        references.append((EVIDENCE_COLUMNS[table],ref["id"]))
    received=now_us()
    row=db.execute("INSERT INTO agent_results(result_id,task_id,agent_id,external_task_id,window_start_us,window_end_us,status,summary,events_json,error_json,content_json,received_at_us) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (result["result_id"],task["id"] if task is not None else None,result["agent_id"],result.get("external_task_id"),start,end,result["status"],result.get("summary"),canonical(result.get("events",[])),canonical(result.get("error")) if result.get("error") is not None else None,content,received))
    for column,ident in set(references):
        db.execute(f"INSERT INTO agent_evidence(result_id,{column}) VALUES(?,?)",(row.lastrowid,ident))
    return dict(result_id=result["result_id"],result_seq=row.lastrowid,received_at=iso(received),duplicate=False)


def publish(store, value):
    with store.db(write=True) as db: return insert(store,db,value)


def unpack(row):
    return dict(result_seq=row["id"],received_at=iso(row["received_at_us"]),data=json.loads(row["content_json"]))


def query(store, filters, token=None, limit=100):
    if not 1<=limit<=1000: raise ToolError("limit must be 1..1000")
    start,end=bounds(filters.get("from_time"),filters.get("to_time"))
    seq,_=store.decode_cursor("results",filters,token) if token else (0,None)
    sql="SELECT * FROM agent_results WHERE id>?"; args=[seq]
    for key in ("agent_id","task_id","status"):
        if filters.get(key) is not None: sql+=f" AND {key}=?";args.append(filters[key])
    if start is not None: sql+=" AND window_start_us<? AND window_end_us>?";args.extend([end,start])
    with store.db() as db: rows=db.execute(sql+" ORDER BY id LIMIT ?",[*args,limit+1]).fetchall()
    items=[unpack(row) for row in rows[:limit]]
    return dict(items=items,next_cursor=store.encode_cursor("results",filters,items[-1]["result_seq"] if items else seq),has_more=len(rows)>limit)


def get(store, result_id):
    with store.db() as db: row=db.execute("SELECT * FROM agent_results WHERE result_id=?",(result_id,)).fetchone()
    if not row: raise ToolError("Unknown result",404,"not_found")
    return unpack(row)
