"""Postgres access. One schema, four layers kept in separate tables:
reference graph (node/edge/crosswalk), alignments, evidence (append-only), learner state (projection)."""
import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://h2:h2@localhost:5432/h2")

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=8, kwargs={"row_factory": dict_row, "autocommit": True}, open=True)
    return _pool


@contextmanager
def conn():
    with pool().connection() as c:
        yield c


def q(sql: str, params=None) -> list[dict]:
    with conn() as c:
        cur = c.execute(sql, params or ())
        return cur.fetchall() if cur.description else []


def q1(sql: str, params=None) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def ex(sql: str, params=None) -> None:
    with conn() as c:
        c.execute(sql, params or ())


def J(v):
    return Jsonb(v)


SCHEMA = """
create table if not exists meta (key text primary key, value text);

-- REFERENCE GRAPH (shared, versioned, governed)
create table if not exists node (
  id text primary key, type text not null, label text not null, description text,
  key_stage jsonb default '[]', keywords jsonb default '[]',
  graph_version text not null, status text not null default 'active', replaced_by text);
alter table node add column if not exists subject text not null default 'maths';
alter table node add column if not exists strand text;
alter table node add column if not exists extra jsonb default '{}';
create table if not exists edge (
  src text not null, rel text not null, dst text not null, weight real default 1.0,
  primary key (src, rel, dst));
create table if not exists crosswalk (
  concept text not null, scheme text not null, external_id text not null, match text not null,
  primary key (concept, scheme, external_id));

-- FIXTURES (synthetic school data)
create table if not exists item (id text primary key, prompt text, concepts jsonb, answer text, distractors jsonb);
create table if not exists class (id text primary key, teacher text, current_week int, scheme text);
alter table class add column if not exists subject text not null default 'maths';
create table if not exists pupil (id text primary key, name text, class text);
create table if not exists material (id text primary key, class text, week int, title text, body text, ingested boolean default false);

-- SCHEME OF WORK (teacher's declared plan per class; school data, never merged into the graph)
create table if not exists scheme_week (
  class text not null, week int not null, title text, objectives text, key_content text, approach text, resources text, assessment text,
  concepts jsonb not null default '[]', method text, sections jsonb not null default '[]', provenance text, graph_version text,
  source text, uploaded_at timestamptz default now(), primary key (class, week));

-- CONTENT INDEX (tenant-scoped; H3)
create table if not exists content_unit (
  id text primary key, material_id text, class text, week int, idx int,
  heading text, body text, embedding jsonb);

-- ALIGNMENTS (probabilistic, provenance-tagged, graph-version-stamped)
create table if not exists alignment (
  id bigserial primary key, subject text not null, subject_kind text not null,
  target text not null, facet text not null, confidence real not null,
  provenance text not null, reviewed boolean default false, graph_version text not null);
create index if not exists alignment_subject on alignment (subject);
create index if not exists alignment_target on alignment (target);

-- EVIDENCE (append-only; H1)
create table if not exists evidence (
  id bigserial primary key, learner text not null, tenant text not null, source text,
  item text, activity text, response text, outcome real not null,
  concepts jsonb not null, concept_provenance text not null, confidence real not null,
  misconception text, ts timestamptz default now(), graph_version text not null);
create index if not exists evidence_learner on evidence (learner, id);

-- LEARNER STATE (derived projection — rebuildable from evidence)
create table if not exists learner_state (
  learner text, concept text, mastery real, n_direct int, n_inferred int, status text,
  primary key (learner, concept));
-- LEARNING BEHAVIOUR (process events -> episode indicators -> construct patterns)
create table if not exists behaviour_event (
  id bigserial primary key, learner text not null, episode bigint not null, item text, concepts jsonb,
  type text not null, t real, payload jsonb, source text, ts timestamptz default now());
create index if not exists behaviour_event_learner on behaviour_event (learner, episode);
create table if not exists learner_indicator (
  id bigserial primary key, learner text not null, episode bigint not null, indicator text not null,
  construct text not null, polarity int not null, concept text, detail text);
create table if not exists learner_construct (
  learner text, construct text, score real, n int, positives int, negatives int, status text,
  summary text, teacher_confirmed boolean default false, primary key (learner, construct));
-- AGREED ADJUSTMENTS (teacher-authored support profile; never inferred)
create table if not exists learner_adjustment (
  learner text not null, adjustment text not null, confirmed_by text not null, confirmed_on date not null,
  review_by date not null, note text, primary key (learner, adjustment));
create table if not exists learner_misconception (
  learner text, misconception text, strength real, count int,
  primary key (learner, misconception));
"""


def init_schema():
    ex(SCHEMA)


def meta_get(key, default=None):
    r = q1("select value from meta where key=%s", (key,))
    return r["value"] if r else default


def meta_set(key, value):
    ex("insert into meta(key,value) values(%s,%s) on conflict(key) do update set value=excluded.value", (key, str(value)))
