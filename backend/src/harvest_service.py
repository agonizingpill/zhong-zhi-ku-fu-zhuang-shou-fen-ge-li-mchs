from fastapi import HTTPException
from sqlalchemy import select
from .models import Accession,Lot,Regeneration,Pollination,Harvest,Flowering
from .common import lock,pack,add,audit
from . import flowering_service as flower

def save(s,d,u,id=None):
    r=lock(s,Regeneration,d.regeneration_id)
    pollen=s.scalar(select(Pollination).where(Pollination.regeneration_id==r.id))
    if not pollen or pollen.state!='pollinated' or r.state not in ('issued','harvested'):raise HTTPException(409,'必须完成套袋授粉后才能登记收获')
    x=lock(s,Harvest,id) if id else None
    if x and (x.state!='drying' or x.regeneration_id!=r.id or x.code!=d.code):raise HTTPException(409,'已回库或跨单收获不可修改')
    if x:
        for k,v in d.model_dump().items():setattr(x,k,v)
    else:x=Harvest(**d.model_dump());s.add(x)
    s.flush();audit(s,u,'记录收获干燥 '+d.code);return pack(x)

def store(s,id,u):
    pre=s.get(Harvest,id)
    if not pre:raise HTTPException(404,'收获单不存在')
    # 锁序与 assign/observe 一致：先锁全部地块，再种质、复壮、收获。
    # 这样补录实测与回库并发时，两者都经同一地块串行，不会同时越过冲突门槛。
    flower.lock_plots(s)
    regen=s.get(Regeneration,pre.regeneration_id);parent=s.get(Lot,regen.lot_id);lock(s,Accession,parent.accession_id)
    r=lock(s,Regeneration,pre.regeneration_id);h=lock(s,Harvest,id)
    if h.state!='drying' or r.state!='issued':raise HTTPException(409,'回库不可重复')
    if h.moisture>12:raise HTTPException(409,'种子含水率须≤12%，请干燥复测后回库')
    f=s.scalar(select(Flowering).where(Flowering.regeneration_id==r.id))
    if f:
        flower.detect(s,f,u)
    if f and flower.has_open_conflict(s,f):raise HTTPException(409,'该隔离花期实测与相邻同作物重叠，暂停回库；请更正实测花期排除冲突后恢复')
    pollen=s.scalar(select(Pollination).where(Pollination.regeneration_id==r.id))
    child=Lot(accession_id=parent.accession_id,code=h.code+'-G'+str(parent.generation+1),generation=parent.generation+1,available=h.seeds,stored_months=0,temperature=-18,initial_rate=parent.initial_rate,parent_lot_id=parent.id)
    s.add(child);s.flush()
    h.lineage={'parent_lot':pack(parent),'father_lot':pack(s.get(Lot,pollen.father_lot_id)),'pollination':pack(pollen),'flowering':pack(f),'vitality':r.vitality_snapshot,'initial_rate_note':'Inherited planning prior; offspring requires independent germination test before sharing'}
    h.state='stored';h.offspring_lot_id=child.id;r.state='harvested';f.state='released';audit(s,u,'主管回库继承G'+str(child.generation)+' '+h.code);return pack(h)
