from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from .engine import Engine, scenarios
from .models import ToolError
from .storage import Store
from .tasks import Tasks
from . import results


def create_app(data_dir):
    @asynccontextmanager
    async def lifespan(app):
        app.state.store=Store(data_dir);app.state.engine=Engine(app.state.store);app.state.tasks=Tasks(app.state.store)
        try: yield
        finally:
            await app.state.engine.close();app.state.store.close()

    app=FastAPI(title="Mock Player",version="0.3.0",lifespan=lifespan)

    @app.exception_handler(ToolError)
    async def error(request,exc):
        return JSONResponse(dict(error=dict(code=exc.code,message=exc.message)),status_code=exc.status)

    @app.get("/")
    def page(): return FileResponse(Path(__file__).parent/"static/index.html")

    @app.get("/f-timeline.svg")
    def f_timeline():return FileResponse(Path(__file__).parent/"static/f-timeline.svg",media_type="image/svg+xml")

    @app.get("/health")
    def health():
        with app.state.store.db() as db: db.execute("SELECT 1 FROM player_state").fetchone()
        return dict(status="ok",schema_version=3)

    @app.get("/v2/scenarios")
    def scenario_list():return dict(items=scenarios())

    @app.get("/v2/scenarios/{sid}")
    def scenario_get(sid:str):
        for item in scenarios():
            if item["scenario"]==sid:return item
        raise ToolError("Unknown scenario",404,"not_found")

    @app.post("/v2/player/start")
    async def start(body:dict):
        scenario=body.get("scenario","B")
        if not isinstance(scenario,str):raise ToolError("Invalid scenario")
        return app.state.engine.start(scenario)

    @app.get("/v2/player/status")
    def status():return app.state.store.get()

    @app.post('/v2/player/clear-history')
    def clear_history(body:dict):
        if body.get('confirm') != 'CLEAR_PLAYER_HISTORY':
            raise ToolError('需要确认清理所有播放器历史数据', 400, 'confirmation_required')
        return app.state.store.clear_history()

    @app.post("/v2/player/stop")
    async def stop():return await app.state.engine.stop()

    @app.get("/v2/data/status")
    def data_status():return app.state.store.data_status()

    @app.get("/v2/data/window")
    def window(from_time:str|None=None,to_time:str|None=None,task_id:int|None=None):
        return app.state.store.window(from_time,to_time,task_id)

    @app.get('/v4/data/windows')
    def time_windows(window_seconds:int=10,start_time:str|None=None,end_time:str|None=None,after:str|None=None,limit:int=20):
        from .windows import list_windows
        return list_windows(app.state.store,window_seconds,start_time,end_time,after,limit)

    @app.get("/v2/data/records")
    @app.get("/v2/data/pull")
    def records(source:str="all",from_time:str|None=None,to_time:str|None=None,person_id:str|None=None,sensor_id:str|None=None,cursor:str|None=None,limit:int=100,live:bool=False,task_id:int|None=None):
        filters=dict(source=source,from_time=from_time,to_time=to_time,person_id=person_id,sensor_id=sensor_id,task_id=task_id)
        return app.state.store.records(filters,cursor,limit,live)

    @app.get("/v2/data/record/{source}/{record_id}")
    def record(source:str,record_id:int):return app.state.store.get_record(source,record_id)

    @app.get('/v2/data/seconds')
    def seconds(limit:int=100):return app.state.tasks.seconds(limit)

    @app.post("/v2/tasks/claim")
    def claim(body:dict):
        if set(body)-{'window_seconds','worker_id','lease_seconds','request_id'}:raise ToolError('Claim only accepts window/worker/lease/request parameters')
        return app.state.tasks.claim(body.get('window_seconds'),body.get('worker_id','platform'),body.get('lease_seconds',60),body.get('request_id'))

    @app.get("/v2/tasks")
    def tasks(state:str|None=None,limit:int=100):return app.state.tasks.list(state,limit)

    @app.get("/v2/tasks/{task_id}")
    def task_get(task_id:int):return app.state.tasks.get(task_id)

    @app.post("/v2/tasks/{task_id}/heartbeat")
    def heartbeat(task_id:int,body:dict):return app.state.tasks.heartbeat(task_id,body.get("lease_token"),body.get("lease_seconds",60))

    @app.post("/v2/tasks/{task_id}/complete")
    def complete(task_id:int,body:dict):return app.state.tasks.complete(task_id,body.get("lease_token"),body.get("result"))

    @app.post("/v2/tasks/{task_id}/fail")
    def fail(task_id:int,body:dict):return app.state.tasks.fail(task_id,body.get("lease_token"),body.get("error"),body.get("retry_delay_seconds",0),body.get("terminal",False))

    @app.post("/v2/tasks/{task_id}/retry")
    def retry(task_id:int):return app.state.tasks.retry(task_id)

    @app.post("/v2/results")
    def publish(body:dict):return results.publish(app.state.store,body)

    @app.get("/v2/results")
    def result_list(agent_id:str|None=None,task_id:int|None=None,status:str|None=None,from_time:str|None=None,to_time:str|None=None,cursor:str|None=None,limit:int=100):
        if status is not None and status not in {"processing","completed","failed"}:raise ToolError("Invalid status")
        return results.query(app.state.store,dict(agent_id=agent_id,task_id=task_id,status=status,from_time=from_time,to_time=to_time),cursor,limit)

    @app.get("/v2/results/{result_id:path}")
    def result_get(result_id:str):return results.get(app.state.store,result_id)

    return app
