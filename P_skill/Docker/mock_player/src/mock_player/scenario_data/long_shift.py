"""F: deterministic one-hour hot-work stream. All intervals are [start, end)."""
from .types import CVEvent, SensorSchedule, PositionSchedule

DURATION = 3600
PEOPLE = {'P7':{'name':'张师傅','role':'焊工'},'P8':{'name':'王师傅','role':'焊工'},'P11':{'name':'赵师傅','role':'监护人'}}
BASELINE = {'gas_detector_03':{'value':0.2,'status':'normal','unit':'%LEL'},'temperature_03':{'value':26.0,'status':'normal','unit':'°C'},'flame_detector_03':{'value':False,'status':'normal','unit':'bool'}}
# Single source for playback, public timeline and exported Gantt chart.
TIMELINE = [
 {'id':'ppe-p7-helmet','label':'P7 张师傅 · 未戴安全帽','source':'cv','subject':'P7','field':'helmet','segments':[
  {'start':470,'end':730,'value':False,'status':'abnormal'}, {'start':2160,'end':2400,'value':False,'status':'abnormal'}, {'start':3240,'end':3360,'value':False,'status':'abnormal'}]},
 {'id':'ppe-p11-goggles','label':'P11 赵师傅 · 未戴护目镜','source':'cv','subject':'P11','field':'goggles','segments':[
  {'start':600,'end':840,'value':False,'status':'abnormal'}, {'start':1440,'end':1920,'value':False,'status':'abnormal'}, {'start':3120,'end':3360,'value':False,'status':'abnormal'}]},
 {'id':'ppe-p8-goggles','label':'P8 王师傅 · 未戴护目镜','source':'cv','subject':'P8','field':'goggles','segments':[
  {'start':2280,'end':2520,'value':False,'status':'abnormal'}, {'start':3360,'end':3480,'value':False,'status':'abnormal'}]},
 {'id':'ppe-p8-suit','label':'P8 王师傅 · 未穿防护服','source':'cv','subject':'P8','field':'protective_suit','segments':[{'start':2520,'end':2700,'value':False,'status':'abnormal'}]},
 {'id':'gas','label':'可燃气体 gas_detector_03','source':'sensor','subject':'gas_detector_03','segments':[
  {'start':960,'end':1080,'value':0.8,'status':'warning'}, {'start':1080,'end':1320,'value':1.2,'status':'alarm'},
  {'start':1440,'end':1920,'value':1.1,'status':'alarm'},
  {'start':2760,'end':2880,'value':0.8,'status':'warning'}, {'start':2880,'end':3000,'value':1.3,'status':'alarm'}]},
 {'id':'temperature','label':'温度 temperature_03','source':'sensor','subject':'temperature_03','segments':[
  {'start':1200,'end':1440,'value':38.0,'status':'warning'}, {'start':1440,'end':1500,'value':46.0,'status':'alarm'}]},
 {'id':'flame','label':'火焰 flame_detector_03','source':'sensor','subject':'flame_detector_03','segments':[{'start':2640,'end':2760,'value':True,'status':'alarm'}]},
 {'id':'watch-position','label':'P11 赵师傅 · 离开动火区','source':'position','subject':'P11','segments':[{'start':2040,'end':2160,'value':'休息室','status':'absent'}]},
]
DROPOUTS = []


def available(source, subject, t):
    return not any(d['source']==source and d['subject']==subject and d['start']<=t<d['end'] for d in DROPOUTS)


def boundaries(rows):
    return sorted({0,DURATION}|{x[k] for row in rows for x in row['segments'] for k in ('start','end')})


def cv_schedule():
    result={}
    for pid in PEOPLE:
        rows=[r for r in TIMELINE if r['source']=='cv' and r['subject']==pid]
        points=boundaries(rows);events=[]
        for left,right in zip(points,points[1:]):
            values={'helmet':True,'goggles':True,'protective_suit':True}
            for row in rows:
                if any(s['start']<=left<s['end'] for s in row['segments']):values[row['field']]=False
            events.append(CVEvent(left,right,person_id=pid,confidence=.95,**values))
        result[pid]=events
    return result


def sensor_schedule():
    result={}
    for sid,base in BASELINE.items():
        rows=[r for r in TIMELINE if r['source']=='sensor' and r['subject']==sid]
        points=boundaries(rows);events=[]
        for left,right in zip(points,points[1:]):
            chosen=next((s for r in rows for s in r['segments'] if s['start']<=left<s['end']),base)
            events.append(SensorSchedule(left,right,value=chosen['value'],status=chosen['status']))
        result[sid]=events
    return result


def position_schedule():
    coords={'P7':[102.45,88.30],'P8':[105.20,88.10],'P11':[100.10,90.50]}
    return {pid:([PositionSchedule(0,2040,coords[pid],'动火区-A'),PositionSchedule(2040,2160,[150.,120.],'休息室'),PositionSchedule(2160,DURATION,coords[pid],'动火区-A')] if pid=='P11' else [PositionSchedule(0,DURATION,coords[pid],'动火区-A')]) for pid in PEOPLE}


def manifest():
    return {'people':PEOPLE,'baseline':BASELINE,'timeline':TIMELINE,'dropouts':DROPOUTS,
        'interval_convention':'[start_sec,end_sec); seconds relative to playback start',
        'playback':'real-time 1x; future samples are not prewritten',
        'note':'Unlisted periods have full PPE, normal sensors, and all personnel in hot-work area. All sources report continuously; all anomalies recover before the final two normal minutes.'}
