from __future__ import annotations
import base64, json, os, urllib.request
from datetime import datetime, timezone

API_ROOT="https://api.dataforseo.com/v3"
ENV_KILL="DATAFORSEO_KILL_SWITCH"
ENV_LOGIN="DATAFORSEO_LOGIN"
ENV_PASSWORD="DATAFORSEO_PASSWORD"

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def truthy(v):
    return str(v or "").strip().lower() in {"1","true","yes","on","enabled"}

def ensure_schema(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS extension_global(
      extension_key TEXT PRIMARY KEY,
      kill_switch INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS extension_project(
      domain TEXT NOT NULL COLLATE NOCASE,
      extension_key TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 0,
      max_items_per_run INTEGER NOT NULL DEFAULT 10,
      max_calls_per_run INTEGER NOT NULL DEFAULT 2,
      enabled_features_json TEXT NOT NULL DEFAULT '[]',
      updated_at TEXT NOT NULL,
      PRIMARY KEY(domain,extension_key)
    );
    CREATE TABLE IF NOT EXISTS extension_usage(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      domain TEXT NOT NULL COLLATE NOCASE,
      extension_key TEXT NOT NULL,
      run_kind TEXT NOT NULL,
      endpoint TEXT NOT NULL,
      requested_items INTEGER NOT NULL DEFAULT 0,
      returned_items INTEGER NOT NULL DEFAULT 0,
      api_cost REAL,
      status TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dataforseo_snapshot(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      domain TEXT NOT NULL COLLATE NOCASE,
      report_run_id INTEGER,
      endpoint TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    """)
    con.execute("INSERT OR IGNORE INTO extension_global VALUES('dataforseo',0,?)",(now(),))
    con.commit()

def get_project(con,domain):
    ensure_schema(con)
    con.execute("""INSERT OR IGNORE INTO extension_project
      (domain,extension_key,enabled,max_items_per_run,max_calls_per_run,enabled_features_json,updated_at)
      VALUES(?,'dataforseo',0,10,2,'[]',?)""",(domain,now()))
    con.commit()
    return con.execute("SELECT * FROM extension_project WHERE domain=? AND extension_key='dataforseo'",(domain,)).fetchone()

def killed(con):
    ensure_schema(con)
    row=con.execute("SELECT kill_switch FROM extension_global WHERE extension_key='dataforseo'").fetchone()
    return truthy(os.environ.get(ENV_KILL)) or bool(row["kill_switch"])

def credentials_present():
    return bool(os.environ.get(ENV_LOGIN) and os.environ.get(ENV_PASSWORD))

def status(con,domain):
    p=get_project(con,domain)
    return {
      "kill_switch":killed(con),
      "env_kill_switch":truthy(os.environ.get(ENV_KILL)),
      "credentials_present":credentials_present(),
      "project_enabled":bool(p["enabled"]),
      "max_items_per_run":p["max_items_per_run"],
      "max_calls_per_run":p["max_calls_per_run"],
      "enabled_features":json.loads(p["enabled_features_json"] or "[]"),
    }

def assert_allowed(con,domain):
    st=status(con,domain)
    if st["kill_switch"]: raise RuntimeError("DataForSEO disabled by kill switch")
    if not st["credentials_present"]: raise RuntimeError("DataForSEO credentials missing")
    if not st["project_enabled"]: raise RuntimeError("DataForSEO disabled for this project")
    return st

def _post(con,domain,endpoint,task,requested_items):
    assert_allowed(con,domain)  # hard gate immediately before network access
    auth=base64.b64encode(f"{os.environ[ENV_LOGIN]}:{os.environ[ENV_PASSWORD]}".encode()).decode()
    req=urllib.request.Request(
      API_ROOT+endpoint,
      data=json.dumps([task]).encode(),
      method="POST",
      headers={"Authorization":"Basic "+auth,"Content-Type":"application/json","Accept":"application/json"}
    )
    try:
        with urllib.request.urlopen(req,timeout=30) as resp:
            payload=json.loads(resp.read().decode("utf-8","replace"))
        tasks=payload.get("tasks") or []
        cost=sum(float(t.get("cost") or 0) for t in tasks)
        returned=0
        for t in tasks:
            for result in t.get("result") or []:
                items=result.get("items")
                if isinstance(items,list): returned+=len(items)
        con.execute("""INSERT INTO extension_usage
          (domain,extension_key,run_kind,endpoint,requested_items,returned_items,api_cost,status,created_at)
          VALUES(?,'dataforseo','report',?,?,?,?,'ok',?)""",
          (domain,endpoint,requested_items,returned,cost,now()))
        con.commit()
        return payload
    except Exception:
        con.execute("""INSERT INTO extension_usage
          (domain,extension_key,run_kind,endpoint,requested_items,returned_items,api_cost,status,created_at)
          VALUES(?,'dataforseo','report',?,?,0,NULL,'error',?)""",(domain,endpoint,requested_items,now()))
        con.commit()
        raise

def minimal_conversation_starter(con,domain,report_run_id=None):
    st=assert_allowed(con,domain)
    calls=max(0,int(st["max_calls_per_run"]))
    budget=max(1,int(st["max_items_per_run"]))
    out={}
    if calls>0:
        out["domain_rank_overview"]=_post(con,domain,
          "/dataforseo_labs/google/domain_rank_overview/live",
          {"target":domain,"location_code":2840,"language_code":"en"},1)
        calls-=1
    if calls>0 and budget>1:
        limit=max(1,min(budget-1,10))
        out["ranked_keywords"]=_post(con,domain,
          "/dataforseo_labs/google/ranked_keywords/live",
          {"target":domain,"location_code":2840,"language_code":"en","limit":limit,
           "order_by":["keyword_data.keyword_info.search_volume,desc"]},limit)
    con.execute("INSERT INTO dataforseo_snapshot(domain,report_run_id,endpoint,payload_json,created_at) VALUES(?,?,?,?,?)",
                (domain,report_run_id,"minimal_conversation_starter",json.dumps(out),now()))
    con.commit()
    return out

def usage_summary(con,domain):
    ensure_schema(con)
    r=con.execute("""SELECT COUNT(*) calls,COALESCE(SUM(requested_items),0) requested_items,
      COALESCE(SUM(returned_items),0) returned_items,COALESCE(SUM(api_cost),0) api_cost
      FROM extension_usage WHERE domain=? AND extension_key='dataforseo'""",(domain,)).fetchone()
    return dict(r)
