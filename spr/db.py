import time, uuid
from sqlalchemy import create_engine, event, Column, String, Text, Float, Integer, JSON, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import DATABASE
engine = create_engine(DATABASE, connect_args={'timeout': 30, 'check_same_thread': False} if DATABASE.startswith('sqlite') else {}, pool_pre_ping=True)
if DATABASE.startswith('sqlite'):
 @event.listens_for(engine, 'connect')
 def setup(conn, _):
  conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA foreign_keys=ON'); conn.execute('PRAGMA busy_timeout=30000')
Session = sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()
def uid(): return uuid.uuid4().hex
class User(Base):
 __tablename__='users'
 id=Column(String,primary_key=True,default=uid); name=Column(String,unique=True,nullable=False); password=Column(Text,nullable=False); admin=Column(Integer,default=0)
class Member(Base):
 __tablename__='members'
 id=Column(String,primary_key=True,default=uid); user_id=Column(String,index=True); project=Column(String,index=True); role=Column(String)
 __table_args__=(UniqueConstraint('user_id','project'),)
class Login(Base):
 __tablename__='sessions'
 token=Column(String,primary_key=True); user_id=Column(String); expires=Column(Float)
class Record(Base):
 __tablename__='records'
 id=Column(String,primary_key=True,default=uid); project=Column(String,index=True); kind=Column(String,index=True); name=Column(String); body=Column(JSON,default=dict); author=Column(String); created=Column(Float,default=time.time)
class File(Base):
 __tablename__='files'
 id=Column(String,primary_key=True,default=uid); project=Column(String,index=True); dataset=Column(String,index=True); source=Column(String); name=Column(String); path=Column(Text); sha256=Column(String); size=Column(Integer); format=Column(String); status=Column(String,default='stored')
class Run(Base):
 __tablename__='runs'
 id=Column(String,primary_key=True,default=uid); project=Column(String,index=True); dataset=Column(String); author=Column(String); status=Column(String,index=True,default='queued'); stage=Column(String,default='В очереди'); progress=Column(Float,default=0); created=Column(Float,default=time.time); updated=Column(Float,default=time.time); lease=Column(Float,default=0); owner=Column(String); attempts=Column(Integer,default=0); snapshot=Column(JSON); summary=Column(JSON,default=dict); log=Column(JSON,default=list); artifact=Column(Text); error=Column(Text)
class Audit(Base):
 __tablename__='audit'
 id=Column(String,primary_key=True,default=uid); project=Column(String,index=True); user_id=Column(String); created=Column(Float,default=time.time); action=Column(String); object_id=Column(String); result=Column(String); request_id=Column(String); detail=Column(JSON,default=dict)
def audit(s,user,project,action,obj='',detail=None,result='ok',request_id=''):
 s.add(Audit(user_id=user,project=project,action=action,object_id=obj,detail=detail or {},result=result,request_id=request_id))
def serialize(obj): return {c.name:getattr(obj,c.name) for c in obj.__table__.columns if c.name!='password'}
