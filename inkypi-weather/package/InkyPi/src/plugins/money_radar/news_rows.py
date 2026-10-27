"""Compact source-brand chips and readable, measured holdings-news rows."""
import re
from PIL import Image
from utils.app_utils import get_base_ui_font
from utils.local_wordmark import load_source_logo
from utils.space_typography import space_font
from . import render


def face(role, size, cjk=False):
    if cjk:
        return get_base_ui_font(size, bold=role == "title")
    return space_font({"title": "public_semibold", "ticker": "heading", "time": "public"}[role], size)


def runs(text,role,size):
    return [(part,face(role,size,bool(re.search(r'[\u2e80-\uffff]',part))))
            for part in re.findall(r'[\u2e80-\uffff]+|[^\u2e80-\uffff]+',str(text))]

def measure(canvas,text,role,size):
    return sum(canvas.draw.textlength(part,font=f) for part,f in runs(text,role,size))

def fit(canvas,text,role,size,width):
    text=' '.join(str(text or '').split())
    if measure(canvas,text,role,size)<=width:return text
    value=text
    while value and measure(canvas,value+'…',role,size)>width:value=value[:-1]
    # A complete English word is easier to scan than a cut syllable.
    if text.isascii() and ' ' in value and len(value.rsplit(' ',1)[1])<4:
        value=value.rsplit(' ',1)[0]
    return value.rstrip(' ,，·-')+'…'

def text(canvas,xy,value,role='title',size=13,color='ink',align='left',width=None):
    value=fit(canvas,str(value),role,size,width) if width is not None else str(value)
    parts=runs(value,role,size)
    advance=sum(canvas.draw.textlength(part,font=f) for part,f in parts)
    bounds=[canvas.draw.textbbox((0,0),part,font=f,anchor='ls') for part,f in parts]
    low=min((b[1] for b in bounds),default=0); high=max((b[3] for b in bounds),default=0)
    x=xy[0]-(advance if align=='right' else advance/2 if align=='center' else 0)
    baseline=xy[1]-(low+high)/2
    for part,f in parts:
        canvas.draw.text((x,baseline),part,font=f,fill=canvas.color(color),anchor='ls')
        x+=canvas.draw.textlength(part,font=f)
    return advance

def brand_color(canvas,symbol):
    if symbol=='VGIT':return render.VANGUARD_RED
    fill,ink,outline=render.chip_style(canvas,symbol)
    return outline or fill

def luminance(rgb):
    channels=[channel/255 for channel in rgb]
    return sum((channel/12.92 if channel<=.04045 else ((channel+.055)/1.055)**2.4)*weight
               for channel,weight in zip(channels,(.2126,.7152,.0722)))

def contrast(first,second):
    values=sorted((luminance(first),luminance(second)))
    return (values[1]+.05)/(values[0]+.05)

def ticker_colors(symbol):
    fill,ink,outline=render.BRAND_CHIPS.get(symbol,render.DEFAULT_CHIP)
    # Issuer colors stay fixed across themes. Theme roles such as "ink" become graphite.
    base=render.VANGUARD_RED if symbol=='VGIT' else outline or fill
    base=(18,24,28) if isinstance(base,str) else base
    white,black=(255,255,255),(0,0,0)
    preferred_white=symbol in {'AAPL','AMD','SPCX','TSLA','NTDOY','SPY','VTI','VXUS','VGIT','VOO','VT','BND','TTWO'}
    shown=base
    if preferred_white:
        # Tesla's published red is 4.489:1 with white; one RGB step darker clears 4.5:1.
        while contrast(shown,white)<4.5:
            shown=tuple(max(0,channel-1) for channel in shown)
        foreground=white
    else:
        foreground=max((white,black),key=lambda candidate:contrast(shown,candidate))
    ratio=contrast(shown,foreground)
    return base,shown,foreground,ratio

def chip(canvas,x,y,symbol,width=64):
    symbol=str(symbol or '').upper()
    edge=brand_color(canvas,symbol)
    if edge==canvas.color('paper'):edge=canvas.color('rule')
    night=canvas.color('paper')==render._rgb(render.NIGHT['paper'])
    if night and sum(channel*weight for channel,weight in zip(edge,(.2126,.7152,.0722)))<115:
        edge=tuple(round(channel*.6+255*.4) for channel in edge)
    # The logo stays on its neutral slot; brand color fills only the ticker compartment.
    canvas.draw.rounded_rectangle((x,y-8,x+width,y+8),radius=3,fill=canvas.color('paper'),outline=edge,width=1)
    path=render.HOLDING_LOGOS/f'{symbol.lower()}.png'
    # A small neutral backing keeps original blue/red/black marks legible on black.
    # The mark itself retains its original published colors, including monochrome black.
    logo=load_source_logo(path,12 if night else 14)
    if logo is not None:
        if logo.width>17:
            logo.thumbnail((17,12 if night else 14),Image.Resampling.LANCZOS)
        if night:
            canvas.draw.rounded_rectangle((x+3,y-7,x+21,y+7),radius=2,fill=(232,236,238))
        canvas.image.paste(logo,(round(x+4+(17-logo.width)/2),round(y-logo.height/2)),logo)
        compartment_left=x+22
    else:compartment_left=x+2
    compartment=(compartment_left,y-7,x+width-1,y+7)
    _primary,fill,foreground,_ratio=ticker_colors(symbol)
    canvas.draw.rounded_rectangle(compartment,radius=2,fill=fill)
    code_width=compartment[2]-compartment[0]-2
    selected=10
    while selected>9 and measure(canvas,symbol,'ticker',selected)>code_width:selected-=1
    text(canvas,((compartment[0]+compartment[2])/2,y),symbol,'ticker',selected,foreground,'center',width=code_width)
    return width

def news(canvas,items,now,left,right,top,rows):
    if not items:
        text(canvas,(left,top+4),'暂无持仓相关新闻',size=13,color='muted')
        return
    # Fixed right-aligned metadata column keeps time stable across every row.
    visible=items[:rows]
    metadata=[]
    for item in visible:
        stamp=render.parse_time(item.get('time'))
        metadata.append(render.ago(stamp,now).replace(' ','') if stamp else '')
    time_width=max((measure(canvas,item,'time',10) for item in metadata),default=0)
    for index,(item,meta) in enumerate(zip(visible,metadata)):
        y=top+index*19
        tag_width=chip(canvas,left,y,item.get('symbol'))
        title_left=left+tag_width+7
        title_right=right-time_width-8
        text(canvas,(title_left,y),item['title'],size=13,width=max(8,title_right-title_left))
        text(canvas,(right,y),meta,'time',10,'muted','right')
        if index<len(visible)-1:
            canvas.draw.line((left,y+9,right,y+9),fill=canvas.color('rule'),width=1)
