"""Build the scenario F specification and chart from the same playback definitions."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Docker/mock_player/src'))
from mock_player.scenario_data.long_shift import manifest
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Patch

spec=manifest();static=ROOT/'Docker/mock_player/src/mock_player/static'
font=FontProperties(fname='C:/Windows/Fonts/msyh.ttc') if Path('C:/Windows/Fonts/msyh.ttc').exists() else FontProperties(family='sans-serif')
plt.rcParams['axes.unicode_minus']=False
fig,ax=plt.subplots(figsize=(17,7.5),dpi=160);fig.patch.set_facecolor('white');ax.set_facecolor('white')
rows=spec['timeline'];colors={'abnormal':'#E36E38','warning':'#E3B540','alarm':'#D64848','absent':'#8C6BC0'}
for i,row in enumerate(rows):
 ax.barh(i,60,left=0,height=.55,color='#E9F3EE',edgecolor='none',zorder=1)
 for s in row['segments']:
  parts=[(s['start'],s['end'])]
  for d in spec['dropouts']:
   if d['source']==row['source'] and d['subject']==row['subject']:
    parts=[(a,b) for l,r in parts for a,b in ((l,min(r,d['start'])),(max(l,d['end']),r)) if b>a]
  for left_sec,right_sec in parts:
   left=left_sec/60;width=(right_sec-left_sec)/60
   ax.barh(i,width,left=left,height=.55,color=colors[s['status']],edgecolor='white',linewidth=.7,zorder=3)
   if row['source']=='sensor':
    label='火焰' if row['subject']=='flame_detector_03' else f"{s['value']:g}"
   else:
    label=f"{left_sec//60:02d}:{left_sec%60:02d}–{right_sec//60:02d}:{right_sec%60:02d}" if left_sec%60 or right_sec%60 else f"{left_sec//60}–{right_sec//60}"
    if s.get('open_at_end'):label+=' 持续'
   if width>=1:
    ax.text(left+width/2,i,label,ha='center',va='center',color='white',fontproperties=font,fontsize=9,zorder=4)
 for d in spec['dropouts']:
  if d['source']==row['source'] and d['subject']==row['subject']:
   ax.barh(i,(d['end']-d['start'])/60,left=d['start']/60,height=.55,color='#CCD3DC',edgecolor='#65758B',hatch='////',linewidth=.7,zorder=5)
   ax.annotate('缺测',xy=((d['start']+d['end'])/120,i),xytext=(0,-19),textcoords='offset points',ha='center',fontproperties=font,fontsize=9,color='#526173',zorder=6)
ax.set_yticks(range(len(rows)),[r['label'] for r in rows],fontproperties=font,fontsize=11)
ax.invert_yaxis();ax.set_xlim(0,60);ax.set_xticks(range(0,61,2));ax.set_xticklabels([str(x) for x in range(0,61,2)],fontsize=10)
ax.xaxis.tick_top();ax.xaxis.set_label_position('top');ax.set_xlabel('相对播放开始时间 / 分钟',fontproperties=font,labelpad=12,fontsize=11)
ax.grid(axis='x',color='#DDE5EE',linewidth=.7,zorder=0);ax.set_axisbelow(True)
for spine in ax.spines.values():spine.set_visible(False)
ax.tick_params(axis='both',length=0,pad=8)
legend=[Patch(facecolor='#E9F3EE',label='正常 / PPE齐全 / 在岗'),Patch(facecolor=colors['abnormal'],label='PPE缺失'),Patch(facecolor=colors['warning'],label='传感器警戒'),Patch(facecolor=colors['alarm'],label='传感器报警'),Patch(facecolor=colors['absent'],label='离岗')]
fig.legend(handles=legend,loc='lower center',ncol=3,prop=font,frameon=False,bbox_to_anchor=(.56,.06))
fig.suptitle('F 场景 · 一小时连续动火作业风险时间轴',fontproperties=font,fontsize=20,x=.39,y=.98,ha='left',fontweight='bold')
fig.text(.23,.02,'未标异常时：安全帽/护目镜/防护服齐全；气体 0.2 %LEL、温度 26°C、火焰 false；人员在动火区-A。',fontproperties=font,fontsize=10,color='#526173')
fig.subplots_adjust(left=.23,right=.985,top=.81,bottom=.23)
fig.savefig(static/'f-timeline.png',bbox_inches='tight',facecolor='white')
fig.savefig(static/'f-timeline.svg',bbox_inches='tight',facecolor='white')
(static/'f-scenario.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),'utf-8')
lines=['# F 场景：一小时连续动火作业','', '时长 3600 秒，真实时间 1×播放；未来样本不提前入库。所有时间相对播放开始，区间左闭右开。','', '人员：P7 张师傅（焊工）、P8 王师傅（焊工）、P11 赵师傅（监护人）。','', '未列异常时：三人 PPE 齐全且在动火区-A，气体 0.2 %LEL、温度 26°C、火焰 false。','', '| 对象 | 时间 | 数据设置 |','|---|---|---|']
def stamp(s):return f'{s//60:02d}:{s%60:02d}'
for row in rows:
 for s in row['segments']:
  value=('缺失' if row['source']=='cv' else str(s['value']))
  lines.append(f"| {row['label']} | {stamp(s['start'])}–{stamp(s['end'])} | {value}；{s['status']}"+('；持续至播放结束' if s.get('open_at_end') else '')+' |')
for d in spec['dropouts']:lines.append(f"| {d['subject']} | {stamp(d['start'])}–{stamp(d['end'])} | {d['source']} 原始样本缺测，不补正常 |")
lines+=['','所有来源全程正常采样，无缺测。赵师傅最后一次护目镜异常为52:00–56:00，56:00恢复；58:00–60:00所有人员与传感器均正常，不保留到结束仍持续的异常。','','预设状态不是 Agent 研判答案。是否产生事件及何时确认恢复，由实际作业范围、已配置规则和真实采集证据决定。场景加载不会自动修改作业票/规则、启动播放、调用 Agent 或发卡。','','一小时预计写入：采集秒 3600、CV 270000、PPE 10800、传感器 10800、定位 10800。A–E 仍为 30 秒。','','![风险时间轴](../src/mock_player/static/f-timeline.png)','']
(ROOT/'Docker/mock_player/docs/SCENARIO_F.md').write_text('\n'.join(lines),'utf-8')
print(static/'f-timeline.png')
