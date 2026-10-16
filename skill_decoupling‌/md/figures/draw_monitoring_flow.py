"""Generate matching SVG/PNG diagrams with editable vector text."""
from pathlib import Path
from html import escape
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
W, H = 2500, 1040
img = Image.new('RGB', (W, H), '#f8fafc')
draw = ImageDraw.Draw(img)
font_path = 'C:/Windows/Fonts/msyh.ttc'
bold_path = 'C:/Windows/Fonts/msyhbd.ttc'
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
       '<rect width="100%" height="100%" fill="#f8fafc"/>']

def text(x, y, label, size=24, color='#334155', bold=False, anchor='start'):
    font = ImageFont.truetype(bold_path if bold else font_path, size)
    draw.text((x, y), label, font=font, fill=color, anchor={'start':'lt','middle':'mt','end':'rt'}[anchor])
    svg.append(f'<text x="{x}" y="{y+size}" font-family="Microsoft YaHei, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}" text-anchor="{anchor}">{escape(label)}</text>')

def rect(x, y, w, h, fill, stroke='#cbd5e1', radius=16):
    draw.rounded_rectangle((x,y,x+w,y+h), radius, fill=fill, outline=stroke, width=2)
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')

def line(points, color='#64748b', width=3, dashed=False):
    if dashed:
        for a,b in zip(points,points[1:]):
            dx,dy=b[0]-a[0],b[1]-a[1]; length=(dx*dx+dy*dy)**.5
            for k in range(0,int(length),16):
                end=min(k+8,length)
                draw.line((a[0]+dx*k/length,a[1]+dy*k/length,a[0]+dx*end/length,a[1]+dy*end/length),fill=color,width=width)
    else: draw.line(points, fill=color, width=width)
    svg.append(f'<polyline points="{" ".join(f"{x},{y}" for x,y in points)}" fill="none" stroke="{color}" stroke-width="{width}"'+(' stroke-dasharray="8 8"' if dashed else '')+'/>')

def arrow(x1,y,x2,color='#64748b'):
    line([(x1,y),(x2-10,y)],color,3)
    pts=[(x2,y),(x2-12,y-7),(x2-12,y+7)]
    draw.polygon(pts,fill=color)
    svg.append(f'<polygon points="{" ".join(f"{x},{yy}" for x,yy in pts)}" fill="{color}"/>')

text(48,28,'持续作业监测 · P6 → P7 → P8/P9 → P10',42,'#0f172a',True)
text(48,90,'同一张作业票（job_id）贯穿全流程；每个子智能体单次执行，长期状态由业务库保存',25)

rect(48,153,2404,104,'#eef2ff','#c7d2fe')
text(75,170,'主 Agent：周期触发',27,'#4338ca',True)
text(400,174,'查询作业状态、数据积压和在途任务 → 安排有限执行 → 本轮结束',25,'#4338ca')
text(75,213,'主 Agent 心跳 ≠ 数据采集；P6 可直接提交 P7，平台保留现有调用与授权逻辑',23,'#6366f1')

BW, GAP, X0, Y = 226, 44, 48, 324
nodes = [
 ('01','原始数据与窗口队列','#e0f2fe','#0284c7',['mock 实时采集入库','关系表＋原始证据','按消费者生成 x 秒窗'], 'pending / processing', 'completed / retryable / failed'),
 ('02','P6 · 窗口监测','#dbeafe','#2563eb',['领取一个完整窗口','分析异常与正常观测','每次输出一份 JSON'], '秒级证据 → 窗口成果', '按窗口独立执行'),
 ('03','P6 成果消费队列','#e0f2fe','#0284c7',['多窗口成果逐项入队','含事件、证据和覆盖','每项单独维护消费状态'], 'pending / processing', 'completed / retryable / failed'),
 ('04','P7 · 聚合与研判','#ede9fe','#7c3aed',['一次领取多个新成果','查询历史事件与证据','聚合、关联、风险重评'], '批次大小不固定', '同一作业默认串行写入'),
 ('05','风险事件与告警队列','#ffedd5','#ea580c',['稳定 risk_event_id','保留来源与事件版本','新建告警／更新既有告警'], '事件状态 ≠ 消费状态', '持续更新不重复开单'),
 ('06','P8 / P9 · 处置闭环','#dcfce7','#16a34a',['P8：通知与整改推进','P9：材料审核与闭环','人工等待留在业务状态'], '一个事件对应一个工单', '各次执行、回执均留存'),
 ('07','作业收尾与归档快照','#fef3c7','#d97706',['结束采集并处理尾窗','排空 P6 / P7 队列','完成闭环并固定快照'], '作业结束 ≠ 即刻归档', '缺口／未闭环显式处理'),
 ('08','P10 · 汇总报告','#ccfbf1','#0d9488',['读取固定作业快照','汇总多事件、多次记录','生成一份正式报告'], '按事件 ID 去重统计', '保留报告来源清单'),
 ('09','DOCX · 正式交付','#f1f5f9','#475569',['一张票一份正式报告','JSON／证据继续留存','修订生成新报告版本'], 'n : 1', '保存位置＋哈希＋版本')]
for i,(num,title,fill,color,rows,state1,state2) in enumerate(nodes):
    x=X0+i*(BW+GAP)
    rect(x,Y,BW,230,'#ffffff','#cbd5e1')
    rect(x+10,Y+10,BW-20,56,fill,fill,10)
    text(x+BW/2,Y+23,title,20,color,True,'middle')
    for j,label in enumerate(rows): text(x+BW/2,Y+87+j*35,label,20,'#334155',False,'middle')
    line([(x+15,Y+195),(x+BW-15,Y+195)],'#e2e8f0',2)
    text(x+BW/2,Y+203,'持久化记录／可追溯引用',17,'#64748b',False,'middle')
    text(x+BW/2,Y+251,state1,18,color,True,'middle')
    text(x+BW/2,Y+280,state2,17,'#64748b',False,'middle')
    if i<8:arrow(x+BW+3,Y+118,x+BW+GAP-3)

# Cardinality annotations above main flow.
text(410,277,'x 秒窗口 → 1 份成果',21,'#2563eb',True,'middle')
text(1135,277,'P6 候选 → P7 事件：n : m',21,'#7c3aed',True,'middle')
text(1510,277,'事件 → 工单：1 : 1',21,'#16a34a',True,'middle')
text(2200,277,'多次记录 → 报告：n : 1',21,'#0d9488',True,'middle')

rect(48,668,770,222,'#ffffff','#cbd5e1')
text(72,686,'P7 聚合：两种策略都保留原始事实',26,'#7c3aed',True)
text(72,733,'按类型：多个窗口 → 头盔事件 ＋ 火焰事件（n:m）',22)
text(72,776,'按态势：多个窗口 → 一个连续风险态势（n:1）',22)
text(72,823,'态势事件内部保留独立子风险项；历史已消费成果仍可只读',20,'#64748b')

rect(846,668,790,222,'#ffffff','#cbd5e1')
text(870,686,'交接方式：队列管状态，文件管内容',26,'#0284c7',True)
text(870,733,'业务数据库：领取／租约／完成／重试／版本／来源关系',22)
text(870,776,'JSON 落盘 → 保存回执 → 平台登记成果 → 下游按引用读取',21)
text(870,823,'调用消息只提示有工作；正常窗口也入队，用于恢复判断',20,'#64748b')

rect(1664,668,788,222,'#ffffff','#cbd5e1')
text(1688,686,'并发边界：跨作业并行，同事件受控更新',26,'#16a34a',True)
text(1688,733,'可并行：采集与分析、不同作业、不同事件的处置',22)
text(1688,776,'需串行：同作业 P7 写入、同工单变更、共享作业动作',22)
text(1688,823,'P10 等待尾窗与闭环完成；基于固定快照生成正式报告',20,'#64748b')

text(48,932,'状态统一：pending 待处理 → processing 处理中 → completed 已完成；失败进入 retryable 或 failed',23,'#334155',True)
text(48,976,'目标设计示意：当前单成果 P7 接入需演进为批量队列；作业生命周期、执行状态与消费状态分开维护。',21,'#64748b')
svg.append('</svg>')
(ROOT/'P6-P10单行流程图.svg').write_text('\n'.join(svg),encoding='utf-8')
img.save(ROOT/'P6-P10单行流程图.png')
print('SVG and PNG generated successfully.')
