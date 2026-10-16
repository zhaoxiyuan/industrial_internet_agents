import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import quote

from .client import Client
from .models import ToolError, TERMINAL


def parser():
    common=argparse.ArgumentParser(add_help=False)
    common.add_argument("--server",default=argparse.SUPPRESS)
    common.add_argument("--json",action="store_true",default=argparse.SUPPRESS)
    common.add_argument("--timeout",type=float,default=argparse.SUPPRESS)
    p=argparse.ArgumentParser(description="Unified time-series data and Agent task CLI",parents=[common])
    commands=p.add_subparsers(dest="command",required=True)
    def command(name,description):return commands.add_parser(name,help=description,parents=[common])
    def nested(name,description):return command(name,description).add_subparsers(dest="action",required=True)
    def sub(group,name):return group.add_parser(name,parents=[common])
    def times(cmd):cmd.add_argument("--from-time");cmd.add_argument("--to-time")
    def page(cmd):cmd.add_argument("--cursor");cmd.add_argument("--limit",type=int,default=100)
    def task(cmd):cmd.add_argument("--task-id",type=int,required=True)
    def lease(cmd):task(cmd);cmd.add_argument("--lease-token",required=True)
    def records(cmd):
        times(cmd);page(cmd);cmd.add_argument("--source",choices=["all","cv","sensor","position","ppe"],default="all")
        cmd.add_argument("--person-id");cmd.add_argument("--sensor-id")
    serve=command("serve","Start playback service and frontend")
    serve.add_argument("--host",default="127.0.0.1");serve.add_argument("--port",type=int,default=5010);serve.add_argument("--data-dir",default="./data")
    scenarios=nested("scenarios","Inspect scenarios");sub(scenarios,"list");sub(scenarios,"show").add_argument("scenario")
    command("start","Play a scenario into the shared database").add_argument("--scenario",choices=list("ABCDEF"),default="B")
    command("stop","Stop current playback idempotently")
    command("status","Inspect current playback").add_argument("--watch",action="store_true")
    data=nested("data","Inspect shared database");sub(data,"status")
    query=nested("query","Read committed time-series data")
    window=sub(query,"window");times(window);window.add_argument("--task-id",type=int)
    record_list=sub(query,"records");records(record_list);record_list.add_argument("--task-id",type=int)
    record_get=sub(query,"record");record_get.add_argument("--source",choices=["cv","sensor","position","ppe"],required=True);record_get.add_argument("--id",type=int,required=True)
    pull=command("pull","Incrementally read the unified data stream");records(pull);pull.add_argument("--follow",action="store_true")
    tasks=nested("tasks","Claim, renew, complete, fail and retry Agent tasks")
    claim=sub(tasks,"claim");claim.add_argument("--window-seconds",type=int,required=True);claim.add_argument("--request-id")
    claim.add_argument("--worker-id",default=f"{socket.gethostname()}:{os.getpid()}");claim.add_argument("--lease-seconds",type=int,default=60)
    heartbeat=sub(tasks,"heartbeat");lease(heartbeat);heartbeat.add_argument("--lease-seconds",type=int,default=60)
    complete=sub(tasks,"complete");lease(complete);complete.add_argument("--file",required=True)
    fail=sub(tasks,"fail");lease(fail);fail.add_argument("--error",required=True,help="Failure message")
    fail.add_argument("--error-code",default="agent_error");fail.add_argument("--retry-delay-seconds",type=int,default=0);fail.add_argument("--terminal",action="store_true")
    task(sub(tasks,"retry"));task(sub(tasks,"get"))
    listing=sub(tasks,"list");listing.add_argument("--state",choices=["pending","processing","completed","retryable","failed"]);listing.add_argument("--limit",type=int,default=100)
    results=nested("results","Publish/query analysis results")
    sub(results,"publish").add_argument("--file",required=True)
    listing=sub(results,"query");page(listing);times(listing)
    for name in ("agent-id","status"):listing.add_argument("--"+name)
    listing.add_argument("--task-id",type=int)
    sub(results,"get").add_argument("--result-id",required=True)
    return p


def payload(path):
    try:return json.load(sys.stdin) if path=="-" else json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError,ValueError) as exc:raise ToolError(str(exc))


def execute(args,client):
    cmd=args.command;action=getattr(args,"action",None)
    if cmd=="scenarios":return client.call("/v2/scenarios"+("/"+quote(args.scenario,safe="") if action=="show" else ""))
    if cmd=="start":return client.call("/v2/player/start","POST",dict(scenario=args.scenario))
    if cmd=="stop":return client.call("/v2/player/stop","POST",{})
    if cmd=="status":return client.call("/v2/player/status")
    if cmd=="data":return client.call("/v2/data/status")
    if cmd=="query" and action=="window":return client.call("/v2/data/window",params={k:getattr(args,k) for k in ("from_time","to_time","task_id")})
    if cmd=="query" and action=="record":return client.call(f"/v2/data/record/{args.source}/{args.id}")
    if cmd=="pull" or cmd=="query":
        params={k:getattr(args,k) for k in ("source","from_time","to_time","person_id","sensor_id","cursor","limit")}
        if cmd=="pull":params["live"]="true"
        else:params["task_id"]=args.task_id
        return client.call("/v2/data/"+("pull" if cmd=="pull" else "records"),params=params)
    if cmd=="tasks":
        if action=="claim":return client.call("/v2/tasks/claim","POST",dict(window_seconds=args.window_seconds,worker_id=args.worker_id,lease_seconds=args.lease_seconds,request_id=args.request_id))
        if action=="list":return client.call("/v2/tasks",params={k:getattr(args,k) for k in ("state","limit")})
        path=f"/v2/tasks/{args.task_id}"
        if action=="get":return client.call(path)
        if action=="retry":return client.call(path+"/retry","POST",{})
        body=dict(lease_token=args.lease_token)
        if action=="heartbeat":body["lease_seconds"]=args.lease_seconds
        elif action=="complete":body["result"]=payload(args.file)
        else:body.update(error=dict(code=args.error_code,message=args.error),retry_delay_seconds=args.retry_delay_seconds,terminal=args.terminal)
        return client.call(path+"/"+action,"POST",body)
    if cmd=="results":
        if action=="publish":return client.call("/v2/results","POST",payload(args.file))
        if action=="get":return client.call("/v2/results/"+quote(args.result_id,safe=""))
        return client.call("/v2/results",params={k:getattr(args,k) for k in ("agent_id","task_id","status","from_time","to_time","cursor","limit")})


def main(argv=None):
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):stream.reconfigure(encoding="utf-8")
    args=parser().parse_args(argv)
    if getattr(args,"timeout",10)<=0:parser().error("--timeout must be positive")
    if args.command=="serve":
        import uvicorn
        from .server import create_app
        uvicorn.run(create_app(args.data_dir),host=args.host,port=args.port,workers=1)
        return
    client=Client(getattr(args,"server",os.environ.get("MOCK_PLAYER_SERVER","http://127.0.0.1:5010")),getattr(args,"timeout",10))
    try:
        while True:
            value=execute(args,client)
            streaming=getattr(args,"follow",False) or getattr(args,"watch",False)
            print(json.dumps(value,ensure_ascii=False,indent=None if getattr(args,"json",False) or streaming else 2),flush=True)
            if getattr(args,"follow",False):
                args.cursor=value["next_cursor"]
                if not value["has_more"]:time.sleep(1)
            elif getattr(args,"watch",False):
                if value["state"] in TERMINAL|{"idle"}:break
                time.sleep(1)
            else:break
    except ToolError as exc:
        print(json.dumps(dict(error=dict(code=exc.code,message=exc.message)),ensure_ascii=False),file=sys.stderr)
        raise SystemExit({400:2,422:2,404:3,409:4,503:5}.get(exc.status,1))
    except (ValueError,TypeError) as exc:
        print(json.dumps(dict(error=dict(code="invalid_argument",message=str(exc)))),file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:raise SystemExit(130)
