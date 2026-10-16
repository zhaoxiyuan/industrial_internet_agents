import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from mock_player.server import create_app
from mock_player.engine import second_data
from mock_player.aggregation import aggregate
from mock_player.storage import parse_time,iso


class ApiTests(unittest.TestCase):
    def test_task_and_result_workflow(self):
        base=Path(__file__).resolve().parents[1]/"test-data";base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            app=create_app(folder)
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").json()["schema_version"],3)
                self.assertEqual(client.post("/v2/player/start",json=dict(scenario="X")).status_code,400)
                state=client.post("/v2/player/start",json=dict(scenario="B")).json()
                self.assertNotIn("run_id",state)
                self.assertEqual(client.post("/v2/player/start",json=dict(scenario="A")).status_code,409)
                client.post("/v2/player/stop")
                stamp=datetime.fromisoformat(state["started_at"]);start=parse_time(state["started_at"])
                for sec in range(5):
                    rows=second_data("B",sec,stamp)
                    app.state.store.commit("B",sec,rows,aggregate(sec,rows["cv"],rows["sensor"],rows["position"]),start,False)
                task=client.post("/v2/tasks/claim",json=dict(window_seconds=5)).json()["task"]
                tid=task["task_id"];token=task["lease_token"]
                self.assertEqual(client.post(f"/v2/tasks/{tid}/heartbeat",json=dict(lease_token=token)).status_code,200)
                window=client.get("/v2/data/window",params=dict(task_id=tid)).json()
                value=dict(schema_version=3,result_id="api-test",agent_id="platform",summary="<script>纯文本</script>",evidence=[window["sensor"][0]["record_ref"]])
                done=client.post(f"/v2/tasks/{tid}/complete",json=dict(lease_token=token,result=value))
                self.assertEqual(done.status_code,200,done.text)
                self.assertTrue(client.post(f"/v2/tasks/{tid}/complete",json=dict(lease_token=token,result=value)).json()["receipt"]["duplicate"])
                self.assertEqual(client.get("/v2/results").json()["items"][0]["data"]["task_id"],tid)
                self.assertEqual(client.get("/v2/data/seconds").json()["counts"]["completed"],5)
                self.assertNotIn("lease_token",client.get("/v2/tasks").json()["items"][0])
            with TestClient(create_app(folder)) as client:
                self.assertEqual(client.get("/v2/tasks").json()["items"][0]["state"],"completed")
                self.assertEqual(client.get("/v2/data/status").json()["counts"]["sensor"],15)


if __name__=="__main__":unittest.main()
