from datetime import date,timedelta
from statistics import median
from sqlalchemy import select,or_
from fastapi import HTTPException
from .models import Flowering,FloweringHistory,Conflict,Regeneration,Lot,Accession,Plot,Harvest
from .common import lock,pack,audit

MIN_SAMPLES=3
ACTIVE_STATES=('assigned','released')

def _day(v):return date.fromisoformat(v)

# Effective window priority: late field observation → same-crop estimate → prefilled plan.
def window(f):
    if f.observed_start and f.observed_end:return f.observed_start,f.observed_end,'observed'
    if f.estimate_start and f.estimate_end:return f.estimate_start,f.estimate_end,'estimated'
    return f.start,f.end,'prefilled'

def crop_of_regeneration(s,regeneration_id):
    r=s.get(Regeneration,regeneration_id)
    if not r:raise HTTPException(404,'复壮单不存在')
    lot=s.get(Lot,r.lot_id)
    return s.get(Accession,lot.accession_id).crop

def estimate(s,crop,sowing):
    """Estimate this season's flowering from same-crop sowing→first/last-flower days."""
    rows=s.scalars(select(FloweringHistory).where(FloweringHistory.crop==crop).order_by(FloweringHistory.season_year)).all()
    first=[(_day(h.first_flower)-_day(h.sowing_date)).days for h in rows]
    last=[(_day(h.last_flower)-_day(h.sowing_date)).days for h in rows]
    basis={'method':'same_crop_median','crop':crop,'sowing_date':str(sowing),'samples':len(rows),'min_samples':MIN_SAMPLES,'days_to_first':first,'days_to_last':last,'seasons':[h.season_year for h in rows]}
    if len(rows)<MIN_SAMPLES:
        basis.update(status='insufficient',estimate_start=None,estimate_end=None,note='同作物往年样本不足，保留预填花期并标待核')
        return None,basis
    first_days=int(median(first));last_days=int(median(last))
    start=sowing+timedelta(days=first_days);end=sowing+timedelta(days=last_days)
    basis.update(status='estimated',median_days_to_first=first_days,median_days_to_last=last_days,estimate_start=str(start),estimate_end=str(end))
    return (str(start),str(end)),basis

def plan_window(s,regeneration_id,sowing_date):
    """Fill estimate columns from the sowing date; fall back to the prefilled plan."""
    if not sowing_date:
        return None,None,{'method':'prefilled','status':'not_estimated','samples':0,'note':'未填播种日，无法按往年天数估计，保留预填花期并标待核'}
    window,basis=estimate(s,crop_of_regeneration(s,regeneration_id),sowing_date)
    return (window[0],window[1],basis) if window else (None,None,basis)

def replan(s,crop,u):
    """Recompute the estimate for still-planned flowerings of a crop after new history arrives."""
    for f in s.scalars(select(Flowering).where(Flowering.state=='planned').order_by(Flowering.id)).all():
        if not f.sowing_date or crop_of_regeneration(s,f.regeneration_id)!=crop:continue
        start,end,basis=plan_window(s,f.regeneration_id,_day(f.sowing_date))
        f.estimate_start,f.estimate_end,f.estimate_basis,f.need_review=start,end,basis,basis['status']!='estimated'
        audit(s,u,'补录后重算花期估计 '+str(f.id))
    s.flush()

def _pair(a,b):return (a,b) if a<b else (b,a)

def _overlap(a,b):
    start=max(a[0],b[0]);end=min(a[1],b[1])
    return (start,end) if start<=end else None

def lock_plots(s):
    """Field-wide lock, always first and in stable order. Serializes assignment, late observation,
    harvest return and issuance so a backfill and a return cannot both pass the conflict gate."""
    return {p.id:p for p in s.scalars(select(Plot).order_by(Plot.id).with_for_update().execution_options(populate_existing=True)).all()}

def _pair_overlap(s,plots,f,crop,other):
    hit=_overlap(window(f)[:2],window(other)[:2])
    if not hit:return None
    p=plots[f.plot_id];q=plots[other.plot_id]
    same=p.id==q.id
    near=abs(p.row-q.row)+abs(p.position-q.position)<=1
    if not same and not (near and crop_of_regeneration(s,other.regeneration_id)==crop):return None
    return hit

def detect(s,f,u):
    """Open/reopen conflicts for f's effective window; resolve the ones a correction removed."""
    plots=lock_plots(s)
    crop=crop_of_regeneration(s,f.regeneration_id)
    others=s.scalars(select(Flowering).where(Flowering.id!=f.id,Flowering.state.in_(ACTIVE_STATES)).order_by(Flowering.id)).all()
    hit_ids=set()
    for other in others:
        hit=_pair_overlap(s,plots,f,crop,other)
        if not hit:continue
        hit_ids.add(other.id)
        low,high=_pair(f.id,other.id)
        row=s.scalar(select(Conflict).where(Conflict.flowering_id==low,Conflict.other_flowering_id==high))
        # 依据按冲突行两侧顺序记录，页面 sides 与 basis 一一对应。
        basis=(window(f)[2]+'/'+window(other)[2]) if low==f.id else (window(other)[2]+'/'+window(f)[2])
        if row:
            row.state='open';row.overlap_start=str(hit[0]);row.overlap_end=str(hit[1]);row.basis=basis;row.resolved_at=None
        else:
            other_is_low=low==other.id
            s.add(Conflict(flowering_id=low,other_flowering_id=high,plot_id=other.plot_id if other_is_low else f.plot_id,other_plot_id=f.plot_id if other_is_low else other.plot_id,crop=crop,overlap_start=str(hit[0]),overlap_end=str(hit[1]),basis=basis,state='open',detected_at=date.today().isoformat()))
    for row in s.scalars(select(Conflict).where(Conflict.state=='open',or_(Conflict.flowering_id==f.id,Conflict.other_flowering_id==f.id))).all():
        other_id=row.other_flowering_id if row.flowering_id==f.id else row.flowering_id
        if other_id in hit_ids:continue
        other=s.get(Flowering,other_id)
        if other and other.state in ACTIVE_STATES and _pair_overlap(s,plots,f,crop,other):continue
        row.state='resolved';row.resolved_at=date.today().isoformat()
        audit(s,u,'实测更正排除花期冲突 #'+str(row.id))
    s.flush()
    for row in s.scalars(select(Conflict).where(Conflict.state=='open',or_(Conflict.flowering_id==f.id,Conflict.other_flowering_id==f.id)).order_by(Conflict.id)).all():
        row.basis=_basis_aligned(row,s)
    s.flush()
    return sorted(hit_ids)

def _basis_aligned(row,s):
    a=s.get(Flowering,row.flowering_id);b=s.get(Flowering,row.other_flowering_id)
    return (window(a)[2] if a else '?')+'/'+(window(b)[2] if b else '?')

def has_open_conflict(s,f):
    return s.scalar(select(Conflict.id).where(Conflict.state=='open',or_(Conflict.flowering_id==f.id,Conflict.other_flowering_id==f.id)).order_by(Conflict.id)) is not None

def refresh_holds(s,u):
    """Stored offspring of conflict-affected flowerings keep lineage but pause sharing issuance."""
    open_ids=set()
    for row in s.scalars(select(Conflict).where(Conflict.state=='open')).all():
        open_ids.add(row.flowering_id);open_ids.add(row.other_flowering_id)
    regen_ids={f.regeneration_id:f.id for f in s.scalars(select(Flowering)).all()}
    held=set()
    for h in s.scalars(select(Harvest).where(Harvest.state=='stored',Harvest.offspring_lot_id.is_not(None))).all():
        fid=regen_ids.get(h.regeneration_id)
        if fid in open_ids:held.add(h.offspring_lot_id)
    for lot in s.scalars(select(Lot).where(Lot.id.in_(held))).all():
        if not lot.sharing_hold:
            lot.sharing_hold=True;lot.sharing_hold_reason='花期冲突暂停共享签发，待实测更正排除冲突';audit(s,u,'后代种批共享暂停 '+lot.code)
    for lot in s.scalars(select(Lot).where(Lot.sharing_hold.is_(True))).all():
        if lot.id in held:continue
        if (lot.sharing_hold_reason or '').startswith('花期冲突'):
            lot.sharing_hold=False;lot.sharing_hold_reason=None;audit(s,u,'后代种批共享恢复 '+lot.code)
    s.flush()

def observe(s,id,d,u):
    pre=s.get(Flowering,id)
    if not pre:raise HTTPException(404,'花期记录不存在')
    # Plot locks first, same order as assign, so field edits serialize; then regeneration → flowering.
    lock_plots(s)
    r=lock(s,Regeneration,pre.regeneration_id);f=lock(s,Flowering,id)
    if f.state not in ACTIVE_STATES or r.state not in ('issued','harvested'):
        raise HTTPException(409,'只有已核准或已收获的隔离花期可登记实测')
    f.observed_start=str(d.observed_start);f.observed_end=str(d.observed_end)
    s.flush()
    hits=detect(s,f,u)
    refresh_holds(s,u)
    audit(s,u,'登记实测花期 '+r.code+' '+f.observed_start+'至'+f.observed_end+('，相邻重叠 '+str(len(hits))+' 处' if hits else '，无相邻重叠')+('；'+d.note if d.note else ''))
    rows=s.scalars(select(Conflict).where(Conflict.state=='open',or_(Conflict.flowering_id==f.id,Conflict.other_flowering_id==f.id)).order_by(Conflict.id)).all()
    return {'flowering':pack(f),'conflict_count':len(hits),'conflicts':conflict_view(s,rows)}

def conflict_view(s,rows):
    plots={p.id:p for p in s.scalars(select(Plot)).all()}
    flowerings={f.id:f for f in s.scalars(select(Flowering)).all()}
    regens={r.id:r for r in s.scalars(select(Regeneration)).all()}
    lots={l.id:l for l in s.scalars(select(Lot)).all()}
    harvests={h.regeneration_id:h for h in s.scalars(select(Harvest)).all()}
    def side(f):
        if not f:return None
        reg=regens.get(f.regeneration_id);lot=lots.get(reg.lot_id) if reg else None
        h=harvests.get(f.regeneration_id);child=lots.get(h.offspring_lot_id) if h and h.offspring_lot_id else None
        return {'flowering_id':f.id,'plot':plots[f.plot_id].code if f.plot_id in plots else None,'regeneration':reg.code if reg else None,'lot':lot.code if lot else None,'crop':s.get(Accession,lots[reg.lot_id].accession_id).crop if reg and reg.lot_id in lots else None,'offspring_lot':child.code if child else None,'offspring_hold':bool(child and child.sharing_hold),'window_state':window(f)[2],'observed_start':f.observed_start,'observed_end':f.observed_end}
    return [{**pack(c),'sides':[side(flowerings.get(c.flowering_id)),side(flowerings.get(c.other_flowering_id))]}for c in rows if c]

def open_conflicts(s):
    return conflict_view(s,s.scalars(select(Conflict).where(Conflict.state=='open').order_by(Conflict.id)).all())
