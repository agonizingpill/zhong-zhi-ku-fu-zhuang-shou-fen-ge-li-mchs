from sqlalchemy import String,Integer,Float,JSON,ForeignKey,CheckConstraint,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    email:Mapped[str]=mapped_column(String(100),unique=True)
    role:Mapped[str]=mapped_column(String(20))
    password_hash:Mapped[str]=mapped_column(String(200))
class Accession(Base):
    __tablename__='accessions'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    crop:Mapped[str]=mapped_column(String(40))
    name:Mapped[str]=mapped_column(String(100))
    origin:Mapped[str]=mapped_column(String(100))
    minimum:Mapped[int]=mapped_column(Integer)
class Lot(Base):
    __tablename__='lots'
    __table_args__=(CheckConstraint('available >= 0 AND generation >= 0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    accession_id:Mapped[int]=mapped_column(ForeignKey('accessions.id'))
    code:Mapped[str]=mapped_column(String(60),unique=True)
    generation:Mapped[int]=mapped_column(Integer)
    available:Mapped[int]=mapped_column(Integer)
    stored_months:Mapped[float]=mapped_column(Float)
    temperature:Mapped[float]=mapped_column(Float)
    initial_rate:Mapped[float]=mapped_column(Float)
    parent_lot_id:Mapped[int|None]=mapped_column(ForeignKey('lots.id'),nullable=True)
    sharing_hold:Mapped[bool]=mapped_column(default=False)
    sharing_hold_reason:Mapped[str|None]=mapped_column(String(200),nullable=True)
class GerminationTest(Base):
    __tablename__='germination_tests'
    id:Mapped[int]=mapped_column(primary_key=True)
    lot_id:Mapped[int]=mapped_column(ForeignKey('lots.id'))
    code:Mapped[str]=mapped_column(String(60),unique=True)
    sampled:Mapped[int]=mapped_column(Integer)
    germinated:Mapped[int]=mapped_column(Integer)
    state:Mapped[str]=mapped_column(String(20),default='queued')
    prediction:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    error:Mapped[str|None]=mapped_column(String(500),nullable=True)
class Regeneration(Base):
    __tablename__='regenerations'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(60),unique=True)
    test_id:Mapped[int]=mapped_column(ForeignKey('germination_tests.id'))
    lot_id:Mapped[int]=mapped_column(ForeignKey('lots.id'))
    issued:Mapped[int]=mapped_column(Integer)
    state:Mapped[str]=mapped_column(String(20),default='requested')
    reason:Mapped[str]=mapped_column(String(300),default='')
    vitality_snapshot:Mapped[dict]=mapped_column(JSON)
class Plot(Base):
    __tablename__='plots'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    row:Mapped[int]=mapped_column(Integer)
    position:Mapped[int]=mapped_column(Integer)
    capacity:Mapped[int]=mapped_column(Integer)
class Flowering(Base):
    __tablename__='flowerings'
    id:Mapped[int]=mapped_column(primary_key=True)
    regeneration_id:Mapped[int]=mapped_column(ForeignKey('regenerations.id'),unique=True)
    plot_id:Mapped[int]=mapped_column(ForeignKey('plots.id'))
    start:Mapped[str]=mapped_column(String(10))
    end:Mapped[str]=mapped_column(String(10))
    state:Mapped[str]=mapped_column(String(20),default='planned')
    # Prefilled window stays in start/end; the estimate from same-crop prior seasons is kept separately.
    sowing_date:Mapped[str|None]=mapped_column(String(10),nullable=True)
    estimate_start:Mapped[str|None]=mapped_column(String(10),nullable=True)
    estimate_end:Mapped[str|None]=mapped_column(String(10),nullable=True)
    estimate_basis:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    need_review:Mapped[bool]=mapped_column(default=False)
    # Late field observation. When present it replaces the planned/estimated window for conflict checks.
    observed_start:Mapped[str|None]=mapped_column(String(10),nullable=True)
    observed_end:Mapped[str|None]=mapped_column(String(10),nullable=True)
class FloweringHistory(Base):
    __tablename__='flowering_history'
    __table_args__=(UniqueConstraint('crop','sowing_date'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    crop:Mapped[str]=mapped_column(String(40))
    season_year:Mapped[int]=mapped_column(Integer)
    sowing_date:Mapped[str]=mapped_column(String(10))
    first_flower:Mapped[str]=mapped_column(String(10))
    last_flower:Mapped[str]=mapped_column(String(10))
    note:Mapped[str]=mapped_column(String(200),default='')
class Conflict(Base):
    __tablename__='conflicts'
    __table_args__=(UniqueConstraint('flowering_id','other_flowering_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    flowering_id:Mapped[int]=mapped_column(ForeignKey('flowerings.id'))
    other_flowering_id:Mapped[int]=mapped_column(ForeignKey('flowerings.id'))
    plot_id:Mapped[int]=mapped_column(ForeignKey('plots.id'))
    other_plot_id:Mapped[int]=mapped_column(ForeignKey('plots.id'))
    crop:Mapped[str]=mapped_column(String(40))
    overlap_start:Mapped[str]=mapped_column(String(10))
    overlap_end:Mapped[str]=mapped_column(String(10))
    basis:Mapped[str]=mapped_column(String(40))
    state:Mapped[str]=mapped_column(String(20),default='open')
    detected_at:Mapped[str]=mapped_column(String(40))
    resolved_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
class Pollination(Base):
    __tablename__='pollinations'
    id:Mapped[int]=mapped_column(primary_key=True)
    regeneration_id:Mapped[int]=mapped_column(ForeignKey('regenerations.id'),unique=True)
    mother_lot_id:Mapped[int]=mapped_column(ForeignKey('lots.id'))
    father_lot_id:Mapped[int]=mapped_column(ForeignKey('lots.id'))
    bag_code:Mapped[str]=mapped_column(String(60),unique=True)
    bagged:Mapped[bool]=mapped_column(default=False)
    flowers:Mapped[int]=mapped_column(Integer)
    state:Mapped[str]=mapped_column(String(20),default='prepared')
class Harvest(Base):
    __tablename__='harvests'
    id:Mapped[int]=mapped_column(primary_key=True)
    regeneration_id:Mapped[int]=mapped_column(ForeignKey('regenerations.id'),unique=True)
    code:Mapped[str]=mapped_column(String(60),unique=True)
    seeds:Mapped[int]=mapped_column(Integer)
    moisture:Mapped[float]=mapped_column(Float)
    state:Mapped[str]=mapped_column(String(20),default='drying')
    offspring_lot_id:Mapped[int|None]=mapped_column(ForeignKey('lots.id'),nullable=True)
    lineage:Mapped[dict|None]=mapped_column(JSON,nullable=True)
class Distribution(Base):
    __tablename__='distributions'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(60),unique=True)
    lot_id:Mapped[int]=mapped_column(ForeignKey('lots.id'))
    recipient:Mapped[str]=mapped_column(String(100))
    seeds:Mapped[int]=mapped_column(Integer)
    purpose:Mapped[str]=mapped_column(String(300))
    state:Mapped[str]=mapped_column(String(20),default='requested')
    reserve_snapshot:Mapped[dict|None]=mapped_column(JSON,nullable=True)
class Audit(Base):
    __tablename__='audits'
    id:Mapped[int]=mapped_column(primary_key=True)
    actor:Mapped[str]=mapped_column(String(100))
    action:Mapped[str]=mapped_column(String(300))
