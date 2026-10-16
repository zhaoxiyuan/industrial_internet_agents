"""Read-only generator verification inside the installed live player container; no DB writes."""
from datetime import datetime,timezone
from collections import Counter
from mock_player.engine import second_data,scenarios,sources
from mock_player.scenario_data import scenario_duration,SCENARIOS_CV,SCENARIOS_SENSORS,SCENARIOS_POSITIONING


def verify():
    assert {x['scenario']:x['duration_sec'] for x in scenarios()}=={**{k:30 for k in 'ABCDE'},'F':3600}
    for registry in (SCENARIOS_CV,SCENARIOS_SENSORS,SCENARIOS_POSITIONING):
        for schedules in registry['F'].values():
            assert schedules[0].start_sec==0 and schedules[-1].end_sec==3600
            assert all(a.end_sec==b.start_sec for a,b in zip(schedules,schedules[1:]))
    start=datetime(2026,10,15,tzinfo=timezone.utc)
    counts=Counter();totals=Counter()
    for second in range(3600):
        batches=second_data('F',second,start)
        counts['cv']+=len(batches['cv'])
        readings=batches['sensor'][0]['readings'];counts['sensor']+=len(readings)
        counts['position']+=len(batches['position'][0]['positions'])
        people={x['person_id'] for x in batches['cv']};counts['ppe']+=len(people)
        assert people=={'P7','P8','P11'}
        assert len(readings)==3
        for pid in ('P7','P8','P11'):
            frame=next((x for x in batches['cv'] if x['person_id']==pid),None)
            if frame is None:continue
            vals={x['class_name']:x['value'] for x in frame['detections']}
            expected_helmet=not(pid=='P7' and (470<=second<730 or 2160<=second<2400 or 3240<=second<3360))
            expected_goggles=not(pid=='P11' and (600<=second<840 or 1440<=second<1920 or 3120<=second<3360) or pid=='P8' and (2280<=second<2520 or 3360<=second<3480))
            expected_suit=not(pid=='P8' and 2520<=second<2700)
            assert vals==dict(helmet=expected_helmet,goggles=expected_goggles,protective_suit=expected_suit),(pid,second)
            for item,ok in vals.items():
                if not ok:totals[pid+'_'+item]+=1
        gas=readings.get('gas_detector_03')
        if gas:
            expected=(.8,'warning') if 960<=second<1080 or 2760<=second<2880 else (1.2,'alarm') if 1080<=second<1320 else (1.1,'alarm') if 1440<=second<1920 else (1.3,'alarm') if 2880<=second<3000 else (.2,'normal')
            assert (gas['value'],gas['status'])==expected
        temp=readings['temperature_03'];assert (temp['value'],temp['status'])==((38.,'warning') if 1200<=second<1440 else (46.,'alarm') if 1440<=second<1500 else (26.,'normal'))
        flame=readings['flame_detector_03'];assert flame['value']==(2640<=second<2760)
        pos=batches['position'][0]['positions'];assert (pos['P11']['area_id']=='休息室')==(2040<=second<2160)
    assert dict(counts)=={'cv':270000,'sensor':10800,'position':10800,'ppe':10800},counts
    assert sources('F',start)[0].total_frames==90000
    assert sources('F',start)[0].total_logs==270000
    for sid in 'ABCDE':assert len(second_data(sid,29,start)['cv'])==75
    return {'status':'passed','verified_seconds':3600,'generated_counts':dict(counts),'observed_ppe_abnormal_seconds':dict(totals),'A_E_duration_sec':30,'F_duration_sec':3600,'database_writes':0,'playback_started':False}

if __name__=='__main__':
    import json
    print(json.dumps(verify(),ensure_ascii=False))
