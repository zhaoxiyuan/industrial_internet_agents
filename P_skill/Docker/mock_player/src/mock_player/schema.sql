PRAGMA user_version=3;
CREATE TABLE IF NOT EXISTS persons(person_id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sensors(sensor_id TEXT PRIMARY KEY,sensor_type TEXT NOT NULL,unit TEXT NOT NULL,warning_threshold REAL,alarm_threshold REAL,location TEXT);
CREATE TABLE IF NOT EXISTS areas(area_id TEXT PRIMARY KEY,area_type TEXT NOT NULL,is_danger_zone INTEGER NOT NULL CHECK(is_danger_zone IN(0,1)),geometry_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cv_observations(
 id INTEGER PRIMARY KEY AUTOINCREMENT,ingest_seq INTEGER NOT NULL UNIQUE,person_id TEXT NOT NULL REFERENCES persons,camera_id TEXT NOT NULL,
 observed_at_us INTEGER NOT NULL,received_at_us INTEGER NOT NULL,scenario TEXT NOT NULL,scenario_second REAL NOT NULL,
 helmet INTEGER NOT NULL CHECK(helmet IN(0,1)),helmet_confidence REAL NOT NULL,goggles INTEGER NOT NULL CHECK(goggles IN(0,1)),goggles_confidence REAL NOT NULL,
 protective_suit INTEGER NOT NULL CHECK(protective_suit IN(0,1)),suit_confidence REAL NOT NULL,UNIQUE(camera_id,person_id,observed_at_us));
CREATE INDEX IF NOT EXISTS cv_time ON cv_observations(observed_at_us,person_id);
CREATE TABLE IF NOT EXISTS sensor_readings(
 id INTEGER PRIMARY KEY AUTOINCREMENT,ingest_seq INTEGER NOT NULL UNIQUE,sensor_id TEXT NOT NULL REFERENCES sensors,observed_at_us INTEGER NOT NULL,received_at_us INTEGER NOT NULL,
 scenario TEXT NOT NULL,scenario_second REAL NOT NULL,value REAL NOT NULL,value_type TEXT NOT NULL CHECK(value_type IN('number','boolean')),
 status TEXT NOT NULL CHECK(status IN('normal','warning','alarm')),UNIQUE(sensor_id,observed_at_us));
CREATE INDEX IF NOT EXISTS sensor_time ON sensor_readings(observed_at_us,sensor_id);
CREATE TABLE IF NOT EXISTS position_readings(
 id INTEGER PRIMARY KEY AUTOINCREMENT,ingest_seq INTEGER NOT NULL UNIQUE,person_id TEXT NOT NULL REFERENCES persons,area_id TEXT NOT NULL REFERENCES areas,
 observed_at_us INTEGER NOT NULL,received_at_us INTEGER NOT NULL,scenario TEXT NOT NULL,scenario_second REAL NOT NULL,x REAL NOT NULL,y REAL NOT NULL,speed REAL NOT NULL,
 UNIQUE(person_id,observed_at_us));
CREATE INDEX IF NOT EXISTS position_time ON position_readings(observed_at_us,person_id);
CREATE TABLE IF NOT EXISTS ppe_second_stats(
 id INTEGER PRIMARY KEY AUTOINCREMENT,ingest_seq INTEGER NOT NULL UNIQUE,person_id TEXT NOT NULL REFERENCES persons,
 window_start_us INTEGER NOT NULL,window_end_us INTEGER NOT NULL CHECK(window_end_us=window_start_us+1000000),received_at_us INTEGER NOT NULL,scenario TEXT NOT NULL,scenario_second INTEGER NOT NULL,
 total_frames INTEGER NOT NULL CHECK(total_frames>0),helmet_missing_count INTEGER NOT NULL,helmet_missing_ratio REAL NOT NULL,
 goggles_missing_count INTEGER NOT NULL,goggles_missing_ratio REAL NOT NULL,suit_missing_count INTEGER NOT NULL,suit_missing_ratio REAL NOT NULL,
 is_violating INTEGER NOT NULL CHECK(is_violating IN(0,1)),UNIQUE(person_id,window_start_us,window_end_us));
CREATE INDEX IF NOT EXISTS ppe_time ON ppe_second_stats(window_start_us,person_id);
CREATE TABLE IF NOT EXISTS player_state(id INTEGER PRIMARY KEY CHECK(id=1),state_json TEXT NOT NULL,next_sequence INTEGER NOT NULL DEFAULT 0,legacy_imported INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS processing_tasks(
 id INTEGER PRIMARY KEY AUTOINCREMENT,window_seconds INTEGER NOT NULL CHECK(window_seconds BETWEEN 1 AND 300),max_attempts INTEGER NOT NULL DEFAULT 3,window_start_us INTEGER NOT NULL,window_end_us INTEGER NOT NULL CHECK(window_end_us>window_start_us),
 cutoff_sequence INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN('pending','processing','completed','retryable','failed')),
 worker_id TEXT,request_id TEXT UNIQUE,request_window_seconds INTEGER,lease_token TEXT,lease_expires_at_us INTEGER,attempt_count INTEGER NOT NULL DEFAULT 0,attempt_base INTEGER NOT NULL DEFAULT 0,
 available_at_us INTEGER NOT NULL DEFAULT 0,error_json TEXT,completion_result_id TEXT,failure_digest TEXT,created_at_us INTEGER NOT NULL,updated_at_us INTEGER NOT NULL,
 UNIQUE(window_start_us,window_end_us));
CREATE INDEX IF NOT EXISTS task_queue ON processing_tasks(state,available_at_us,window_start_us);
CREATE TABLE IF NOT EXISTS agent_results(
 id INTEGER PRIMARY KEY AUTOINCREMENT,result_id TEXT NOT NULL UNIQUE,task_id INTEGER UNIQUE REFERENCES processing_tasks,
 agent_id TEXT NOT NULL,external_task_id TEXT,window_start_us INTEGER NOT NULL,window_end_us INTEGER NOT NULL CHECK(window_end_us>window_start_us),
 status TEXT NOT NULL CHECK(status IN('processing','completed','failed')),summary TEXT,events_json TEXT NOT NULL,error_json TEXT,content_json TEXT NOT NULL,received_at_us INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS result_time ON agent_results(window_start_us,agent_id);
CREATE TABLE IF NOT EXISTS agent_evidence(
 id INTEGER PRIMARY KEY AUTOINCREMENT,result_id INTEGER NOT NULL REFERENCES agent_results,cv_id INTEGER REFERENCES cv_observations,sensor_id INTEGER REFERENCES sensor_readings,
 position_id INTEGER REFERENCES position_readings,ppe_id INTEGER REFERENCES ppe_second_stats,
 CHECK((cv_id IS NOT NULL)+(sensor_id IS NOT NULL)+(position_id IS NOT NULL)+(ppe_id IS NOT NULL)=1));
CREATE INDEX IF NOT EXISTS evidence_result ON agent_evidence(result_id);

CREATE TABLE IF NOT EXISTS acquisition_seconds(window_start_us INTEGER PRIMARY KEY,window_end_us INTEGER NOT NULL CHECK(window_end_us=window_start_us+1000000),state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','processing','completed','retryable','failed')),task_id INTEGER REFERENCES processing_tasks);
CREATE INDEX IF NOT EXISTS seconds_queue ON acquisition_seconds(state,window_start_us);
