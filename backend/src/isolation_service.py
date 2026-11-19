from fastapi import HTTPException
from sqlalchemy import select
from .models import Plot,Flowering,Regeneration,Lot,Accession,Pollination
from .common import lock,pack,add,audit
from . import flowering_service as flower

def save(s,d,u,id=None):
    # 锁序与实测/核准/回库一致：先锁18块地，再复壮与花期。
    flower.lock_plots(s)
    r=lock(s,Regeneration,d.regeneration_id)
    if r.state!='issued':raise HTTPException(409,'隔离排期要求已领种且尚未收获的复壮单')
    plot=s.get(Plot,d.plot_id)
    if not plot:raise HTTPException(404,'隔离小区不存在')
    if r.issued>plot.capacity:raise HTTPException(409,'领种株数超过地块容量')
    x=lock(s,Flowering,id) if id else None
    if x and (x.regeneration_id!=r.id or x.state!='planned'):raise HTTPException(409,'只可修订原未分配花期')
    data=d.model_dump(mode='json')
    data.pop('sowing_date',None)
    estimate_start,estimate_end,basis=flower.plan_window(s,r.id,d.sowing_date)
    data.update(sowing_date=str(d.sowing_date) if d.sowing_date else None,estimate_start=estimate_start,estimate_end=estimate_end,estimate_basis=basis,need_review=basis['status']!='estimated')
    if x:
        for k,v in data.items():setattr(x,k,v)
    else:x=Flowering(**data);s.add(x)
    s.flush()
    note='' if basis['status']=='estimated' else '（样本不足，保留预填花期并标待核）'
    audit(s,u,'编制隔离花期 '+r.code+note);return pack(x)

def assign(s,id,u):
    # Lock all 18 plots in stable ID order to serialize adjacent-plot checks.
    plots=flower.lock_plots(s)
    pre=s.get(Flowering,id)
    if not pre:raise HTTPException(404,'花期记录不存在')
    r=lock(s,Regeneration,pre.regeneration_id);x=lock(s,Flowering,id)
    if x.state!='planned' or r.state!='issued':raise HTTPException(409,'仅已领种待分配花期可核准')
    plot=next(p for p in plots.values() if p.id==x.plot_id);lot=s.get(Lot,r.lot_id);crop=s.get(Accession,lot.accession_id).crop
    own=flower.window(x)
    active=s.scalars(select(Flowering).where(Flowering.state=='assigned')).all()
    for other in active:
        neighbour=plots[other.plot_id]
        theirs=flower.window(other)
        overlap=own[0]<=theirs[1] and own[1]>=theirs[0]
        near=abs(plot.row-neighbour.row)+abs(plot.position-neighbour.position)<=1
        oldregen=s.get(Regeneration,other.regeneration_id);oldlot=s.get(Lot,oldregen.lot_id);oldcrop=s.get(Accession,oldlot.accession_id).crop
        if overlap and (plot.id==neighbour.id or (near and crop==oldcrop)):raise HTTPException(409,'相邻同作物花期重叠：'+neighbour.code+' '+theirs[0]+'至'+theirs[1])
    x.state='assigned';audit(s,u,'主管核准隔离花期 '+r.code+('（待核估计）' if x.need_review else ''));return pack(x)

def observe(s,id,d,u):
    return flower.observe(s,id,d,u)

def _flowering_of(s,regeneration_id):
    return s.scalar(select(Flowering).where(Flowering.regeneration_id==regeneration_id))

def _conflict_gate(s,regeneration_id,action):
    f=_flowering_of(s,regeneration_id)
    if f and flower.has_open_conflict(s,f):raise HTTPException(409,'花期实测与相邻同作物重叠，暂停'+action+'；请更正实测花期排除冲突后恢复')

def prepare(s,d,u,id=None):
    r=lock(s,Regeneration,d.regeneration_id)
    f=s.scalar(select(Flowering).where(Flowering.regeneration_id==r.id))
    if r.state!='issued' or not f or f.state!='assigned':raise HTTPException(409,'先核准隔离小区和花期')
    mother=s.get(Lot,d.mother_lot_id);father=s.get(Lot,d.father_lot_id)
    if not mother or not father:raise HTTPException(404,'亲本种批不存在')
    if mother.id!=r.lot_id or mother.accession_id!=father.accession_id or mother.generation!=father.generation:raise HTTPException(409,'母本须为领种批，父本须同种质同代次；复壮不接受异源杂交')
    if d.flowers>r.issued:raise HTTPException(409,'授粉花数不能超过领种株数')
    x=lock(s,Pollination,id) if id else None
    if x and (x.regeneration_id!=r.id or x.state!='prepared'):raise HTTPException(409,'授粉后不可覆盖亲本或袋签')
    if x:
        for k,v in d.model_dump().items():setattr(x,k,v)
    else:x=Pollination(**d.model_dump());s.add(x)
    s.flush();audit(s,u,'登记亲本与套袋 '+d.bag_code);return pack(x)

def pollinate(s,id,u):
    pre=s.get(Pollination,id)
    if not pre:raise HTTPException(404,'授粉记录不存在')
    # 与实测共用同一地块锁序，保证读到最新的冲突状态。
    flower.lock_plots(s)
    # 与prepare保持同一锁序：复壮根记录 → 授粉记录。
    r=lock(s,Regeneration,pre.regeneration_id);x=lock(s,Pollination,id)
    if x.state!='prepared' or r.state!='issued':raise HTTPException(409,'授粉不可重复')
    _conflict_gate(s,r.id,'授粉确认')
    if not x.bagged:raise HTTPException(409,'先补充授粉袋隔离检查，未套袋不能授粉')
    mother=s.get(Lot,x.mother_lot_id);father=s.get(Lot,x.father_lot_id)
    if mother.id!=r.lot_id or mother.accession_id!=father.accession_id or mother.generation!=father.generation:raise HTTPException(409,'亲本代次或种质不符')
    x.state='pollinated';audit(s,u,'主管确认隔离授粉 '+x.bag_code);return pack(x)
