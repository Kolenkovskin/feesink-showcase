"""
FEESINK-APPLY-PATCH-API-APP-SPLIT v2026.01.16-02

Fix: keep this patch script <=700 lines (module-size guard).
Writes/overwrites:
  - feesink/api/deps.py
  - feesink/api/handlers_core.py
  - feesink/api/handlers_stripe.py
  - feesink/api/app.py
Creates backups for feesink/api/app.py.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

UTC = timezone.utc


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _utc_compact() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _write(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return _sha1(content)


def _banner(root: Path, touched: List[Tuple[str, str]]) -> None:
    print("=" * 80)
    print("FEESINK-APPLY-PATCH-API-APP-SPLIT v2026.01.16-02")
    print("TS_UTC=", _utc_now_iso())
    print("ROOT=", str(root))
    print("=" * 80)
    print("TOUCHED_FILES:")
    for rel, h in touched:
        print(f"  - {rel} sha1={h}")
    print("DONE")


def main() -> int:
    script_path = Path(__file__).resolve()
    root = script_path.parent.parent
    if not (root / "feesink").exists():
        print("ERROR: expected repo root at .. from scripts/")
        return 2

    ts = _utc_compact()
    touched: List[Tuple[str, str]] = []

    # ---- deps.py
    deps_py = (
        "from __future__ import annotations\n"
        "import os,secrets\n"
        "from typing import Dict,Optional\n\n"
        "class TokenStore:\n"
        "  def __init__(self)->None: self._m:Dict[str,str]={}\n"
        "  def issue_token(self,account_id:str)->str:\n"
        "    t=secrets.token_urlsafe(32); self._m[t]=account_id; return t\n"
        "  def link_token(self,token:str,account_id:str)->None: self._m[token]=account_id\n"
        "  def resolve(self,token:str)->Optional[str]: return self._m.get(token)\n\n"
        "def make_storage():\n"
        "  kind=(os.getenv('FEESINK_STORAGE') or 'memory').strip().lower()\n"
        "  if kind=='sqlite':\n"
        "    repo_root=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..'))\n"
        "    db_path=os.path.join(repo_root, os.getenv('FEESINK_SQLITE_DB','feesink.db'))\n"
        "    schema_path=os.path.join(repo_root, os.getenv('FEESINK_SCHEMA_SQL','schema.sql'))\n"
        "    from feesink.storage.sqlite import SQLiteStorage,SQLiteStorageConfig\n"
        "    return SQLiteStorage(SQLiteStorageConfig(db_path=db_path,schema_sql_path=schema_path))\n"
        "  from feesink.storage.memory import InMemoryStorage\n"
        "  return InMemoryStorage()\n"
    )
    touched.append(("feesink/api/deps.py", _write(root / "feesink/api/deps.py", deps_py)))

    # ---- handlers_core.py (minimal, still readable)
    handlers_core_py = """\
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple
from feesink.api._http import UTC,error,get_query_param,json_response,read_json,utc_iso
from feesink.config.canon import MIN_TOPUP_USDT, credited_units

def _now()->datetime: return datetime.now(tz=UTC)

def _auth(app,environ)->Tuple[Optional[str],Optional[Tuple[int,list,bytes]]]:
    account_id,err=app.auth_account_id(environ); return account_id,err

def handle_get_ui_success(app,environ):
    token=(get_query_param(environ,"token") or "").strip()
    html=f\"\"\"<!doctype html><html><head><meta charset="utf-8"><title>FeeSink</title></head>
<body style="font-family:Arial,sans-serif;padding:24px;">
<h2>FeeSink: Success</h2><p>Token:</p>
<pre style="background:#f6f6f6;padding:12px;border-radius:8px;">{token}</pre>
<p>You can use it as Bearer token for API calls.</p></body></html>\"\"\"
    return 200,[("Content-Type","text/html; charset=utf-8")],html.encode("utf-8")

def handle_get_me(app,environ):
    account_id,err=_auth(app,environ)
    if err: return err
    assert account_id is not None
    app.storage.ensure_account(account_id)
    return json_response(200,{"account":{"account_id":account_id}})

def handle_post_endpoints(app,environ):
    account_id,err=_auth(app,environ)
    if err: return err
    assert account_id is not None
    data,err2=read_json(environ)
    if err2: return err2
    url=(data.get("url") or "").strip()
    if not url: return error(400,"invalid_request","Missing 'url'")
    try:
        endpoint_id=app.storage.add_endpoint(account_id=account_id,url=url)
    except Exception as ex:
        return error(500,"internal_error","Failed to add endpoint",{"exception":type(ex).__name__})
    return json_response(201,{"endpoint":{"endpoint_id":endpoint_id,"url":url}})

def handle_patch_endpoint(app,environ,endpoint_id:str):
    account_id,err=_auth(app,environ)
    if err: return err
    assert account_id is not None
    data,err2=read_json(environ)
    if err2: return err2
    url=data.get("url"); url=str(url).strip() if url is not None else None
    try:
        ok=app.storage.update_endpoint(account_id=account_id,endpoint_id=endpoint_id,url=url)
    except Exception as ex:
        return error(500,"internal_error","Failed to update endpoint",{"exception":type(ex).__name__})
    if not ok: return error(404,"not_found","Endpoint not found")
    return json_response(200,{"ok":True})

def handle_delete_endpoint(app,environ,endpoint_id:str):
    account_id,err=_auth(app,environ)
    if err: return err
    assert account_id is not None
    try:
        ok=app.storage.delete_endpoint(account_id=account_id,endpoint_id=endpoint_id)
    except Exception as ex:
        return error(500,"internal_error","Failed to delete endpoint",{"exception":type(ex).__name__})
    if not ok: return error(404,"not_found","Endpoint not found")
    return json_response(200,{"ok":True})

def handle_post_alerts_test(app,environ):
    return json_response(200,{"ok":True})

def handle_post_topups_dev(app,environ):
    if (app.topup_mode or "dev").lower()!="dev":
        return error(403,"forbidden","Topups are disabled in this mode")
    account_id,err=_auth(app,environ)
    if err: return err
    assert account_id is not None
    data,err2=read_json(environ)
    if err2: return err2
    amount_raw=data.get("amount_usdt")
    if amount_raw is None: return error(400,"invalid_request","Missing 'amount_usdt'")
    try:
        amount=Decimal(str(amount_raw)).quantize(Decimal("1"))
    except Exception:
        return error(400,"invalid_request","Invalid 'amount_usdt'")
    if amount<MIN_TOPUP_USDT:
        return error(400,"invalid_request","Topup amount below minimum",{"min_usdt":str(MIN_TOPUP_USDT)})
    try:
        cu=int(credited_units(amount))
    except Exception as ex:
        return error(400,"invalid_request","Unable to convert amount_usdt to units",{"exception":type(ex).__name__})
    from feesink.domain.models import TopUp
    tx_hash=f"dev:{account_id}:{int(_now().timestamp())}"
    topup=TopUp(account_id=account_id,tx_hash=tx_hash,amount_usdt=amount,credited_units=cu,ts=_now())
    try:
        topup.validate()
    except Exception as ex:
        return error(400,"invalid_request","TopUp validation failed",{"exception":type(ex).__name__})
    try:
        res=app.storage.credit_topup(topup)
    except Exception as ex:
        return error(500,"internal_error","Failed to credit topup",{"exception":type(ex).__name__})
    return json_response(200,{"ok":True,"topup":{"account_id":account_id,"tx_hash":tx_hash,"amount_usdt":str(amount),
        "credited_units":cu,"inserted":bool(getattr(res,"inserted",False))}})
"""
    touched.append(("feesink/api/handlers_core.py", _write(root / "feesink/api/handlers_core.py", handlers_core_py)))

    # ---- handlers_stripe.py (kept compact)
    handlers_stripe_py = """\
from __future__ import annotations
import json,os,urllib.parse
from datetime import datetime
from decimal import Decimal
from typing import Optional
from feesink.api._http import UTC,error,json_response,read_raw_body,utc_iso
from feesink.api._stripe import stripe_api_get_json,stripe_api_post_form,stripe_verify_signature
from feesink.config.canon import MIN_TOPUP_USDT,USDT_TO_UNITS_RATE

def _now()->datetime: return datetime.now(tz=UTC)

def handle_post_stripe_checkout_sessions(app,environ):
    token,account_id,err=app.auth_token_and_account(environ)
    if err: return err
    assert token is not None and account_id is not None
    secret_key=(os.getenv("STRIPE_SECRET_KEY") or "").strip()
    price_id=(os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip()
    success_url=(os.getenv("STRIPE_SUCCESS_URL") or "").strip()
    cancel_url=(os.getenv("STRIPE_CANCEL_URL") or "").strip()
    if not secret_key: return error(500,"internal_error","STRIPE_SECRET_KEY is not set",{})
    if not price_id: return error(500,"internal_error","STRIPE_PRICE_ID_EUR_50 is not set",{})
    if not success_url: return error(500,"internal_error","STRIPE_SUCCESS_URL is not set",{})
    if not cancel_url: return error(500,"internal_error","STRIPE_CANCEL_URL is not set",{})
    form={"mode":"payment","success_url":success_url,"cancel_url":cancel_url,
          "line_items[0][price]":price_id,"line_items[0][quantity]":"1",
          "metadata[token]":token,"metadata[account_id]":str(account_id),"metadata[price_id]":str(price_id)}
    obj,err2=stripe_api_post_form(secret_key,"/v1/checkout/sessions",form)
    if err2 or not obj: return error(502,"bad_gateway","Stripe request failed",{"reason":err2})
    session_id=(obj.get("id") or "").strip()
    session_url=(obj.get("url") or "").strip()
    customer_id=obj.get("customer"); customer_id=customer_id.strip() if isinstance(customer_id,str) else None
    if not session_id or not session_url:
        return error(502,"bad_gateway","Stripe response missing session id/url",{"stripe_id":session_id or None})
    if not hasattr(app.storage,"upsert_stripe_link"):
        return error(500,"internal_error","Storage does not support stripe_links",{})
    try:
        app.storage.upsert_stripe_link(account_id=str(account_id),stripe_session_id=session_id,stripe_customer_id=customer_id)
    except Exception as ex:
        return error(500,"internal_error","Failed to store stripe link",{"exception":type(ex).__name__})
    return json_response(200,{"checkout_session":{"id":session_id,"url":session_url}})

def handle_post_webhooks_stripe(app,environ):
    whsec=(os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    sig=(environ.get("HTTP_STRIPE_SIGNATURE") or "").strip()
    raw=read_raw_body(environ)
    if not stripe_verify_signature(raw,sig,whsec):
        print(json.dumps({"provider":"stripe","decision":"signature_fail"},ensure_ascii=False))
        return error(400,"invalid_signature","Invalid Stripe signature")
    try: event=json.loads(raw.decode("utf-8"))
    except Exception: return error(400,"invalid_request","Invalid JSON body")
    event_id=(event.get("id") or "").strip() or None
    event_type=(event.get("type") or "").strip() or None
    if not event_id: return error(400,"invalid_request","Missing Stripe event id")
    if event_type!="checkout.session.completed":
        print(json.dumps({"provider":"stripe","decision":"ignored","event_id":event_id,"event_type":event_type},ensure_ascii=False))
        return json_response(200,{"ok":True})
    data=event.get("data") if isinstance(event.get("data"),dict) else {}
    session=(data.get("object") or {}) if isinstance(data.get("object"),dict) else {}
    session_id=(session.get("id") or "").strip() or None
    payment_status=(session.get("payment_status") or "").strip() or None
    customer_id=(session.get("customer") or "").strip() or None
    metadata=session.get("metadata") if isinstance(session.get("metadata"),dict) else {}
    if not session_id: return error(400,"invalid_request","Missing checkout session id")
    if not hasattr(app.storage,"insert_provider_event"):
        return error(500,"internal_error","Storage does not support provider_events (insert_provider_event)",{})
    dedup_event=False
    try:
        inserted=bool(app.storage.insert_provider_event("stripe",event_id,raw.decode("utf-8")))
        if not inserted: dedup_event=True
    except Exception as ex:
        print(json.dumps({"provider":"stripe","decision":"provider_event_write_failed","event_id":event_id,"exception":type(ex).__name__},ensure_ascii=False))
        return error(500,"internal_error","Failed to persist provider_event",{"exception":type(ex).__name__})
    if payment_status!="paid":
        print(json.dumps({"provider":"stripe","decision":"ignored_not_paid","event_id":event_id,"session_id":session_id,"payment_status":payment_status},ensure_ascii=False))
        return json_response(200,{"ok":True,"dedup_event":dedup_event})
    account_id=None; src=None
    if isinstance(metadata,dict):
        v=metadata.get("account_id")
        if v is not None and str(v).strip(): account_id=str(v).strip(); src="metadata"
    if not account_id:
        if not hasattr(app.storage,"resolve_account_by_stripe_session"):
            return error(500,"internal_error","Storage does not support stripe_links (resolve_account_by_stripe_session)",{})
        try:
            account_id=str(app.storage.resolve_account_by_stripe_session(session_id) or "").strip()
            if not account_id: raise ValueError("resolved_empty_account_id")
            src="stripe_links"
        except Exception:
            print(json.dumps({"provider":"stripe","decision":"unresolved_account","event_id":event_id,"session_id":session_id},ensure_ascii=False))
            return error(500,"internal_error","Unable to resolve account_id for session_id",{"session_id":session_id})
    price_id=None
    if isinstance(metadata,dict):
        for k in ("price_id","price","stripe_price_id","sku"):
            v=metadata.get(k)
            if isinstance(v,str) and v.strip(): price_id=v.strip(); break
    if price_id is None:
        li=session.get("line_items")
        if isinstance(li,dict):
            dl=li.get("data")
            if isinstance(dl,list) and dl:
                first=dl[0] if isinstance(dl[0],dict) else None
                if first and isinstance(first.get("price"),dict):
                    pid=first["price"].get("id")
                    if isinstance(pid,str) and pid.strip(): price_id=pid.strip()
    if price_id is None:
        secret_key=(os.getenv("STRIPE_SECRET_KEY") or "").strip()
        if secret_key:
            obj,errx=stripe_api_get_json(secret_key=secret_key,path=f"/v1/checkout/sessions/{urllib.parse.quote(session_id)}",
                                         query={"expand[]":"line_items.data.price"})
            if obj and isinstance(obj.get("line_items"),dict):
                dl=obj["line_items"].get("data")
                if isinstance(dl,list) and dl:
                    first=dl[0] if isinstance(dl[0],dict) else None
                    if first and isinstance(first.get("price"),dict):
                        pid=first["price"].get("id")
                        if isinstance(pid,str) and pid.strip(): price_id=pid.strip()
    eur50=(os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip() or None
    credited_units: Optional[int]=5000 if (eur50 and price_id==eur50) else None
    if credited_units is None:
        print(json.dumps({"provider":"stripe","decision":"unresolved_mapping","event_id":event_id,"account_id":str(account_id),"price_id":price_id},ensure_ascii=False))
        return error(500,"internal_error","Unable to map Stripe price_id to credited_units",{"price_id":price_id})
    tx_hash=f"stripe:{event_id}"
    from feesink.domain.models import TopUp
    rate=Decimal(str(USDT_TO_UNITS_RATE))
    amount_usdt=(Decimal(int(credited_units))/rate)
    if amount_usdt!=amount_usdt.to_integral_value():
        return error(500,"internal_error","credited_units does not map to integer USDT amount",{"credited_units":int(credited_units),"rate":str(rate)})
    if amount_usdt<MIN_TOPUP_USDT:
        return error(500,"internal_error","credited_units maps below minimal top-up",{"amount_usdt":str(amount_usdt),"min_usdt":str(MIN_TOPUP_USDT)})
    topup=TopUp(account_id=str(account_id),tx_hash=tx_hash,amount_usdt=Decimal(str(amount_usdt)),
                credited_units=int(credited_units),ts=_now())
    try: topup.validate()
    except Exception as ex:
        print(json.dumps({"provider":"stripe","decision":"topup_invalid","event_id":event_id,"exception":type(ex).__name__},ensure_ascii=False))
        return error(500,"internal_error","TopUp validation failed",{"exception":type(ex).__name__})
    try: res=app.storage.credit_topup(topup)
    except Exception as ex:
        print(json.dumps({"provider":"stripe","decision":"credit_failed","event_id":event_id,"exception":type(ex).__name__},ensure_ascii=False))
        return error(500,"internal_error","Failed to credit topup",{"exception":type(ex).__name__})
    dedup_tx=not bool(getattr(res,"inserted",False))
    decision="processed" if not dedup_tx else "dedup_tx_hash"
    print(json.dumps({"provider":"stripe","decision":decision,"dedup_event":dedup_event,"dedup_tx_hash":dedup_tx,
                      "event_id":event_id,"event_type":event_type,"session_id":session_id,"payment_status":payment_status,
                      "account_id":str(account_id),"account_id_source":src,"price_id":price_id,"credited_units":int(credited_units),
                      "tx_hash":tx_hash,"customer_id":customer_id,"ts":utc_iso(_now())},ensure_ascii=False))
    return json_response(200,{"ok":True,"dedup_event":dedup_event,"dedup_tx_hash":dedup_tx})
"""
    touched.append(("feesink/api/handlers_stripe.py", _write(root / "feesink/api/handlers_stripe.py", handlers_stripe_py)))

    # ---- app.py (thin routing, stays readable)
    app_py = """\
from __future__ import annotations
import json,os,re,time
from datetime import datetime
from wsgiref.util import setup_testing_defaults
from feesink.api._http import UTC,error,get_bearer_token,get_query_param,json_response,utc_iso
from feesink.api.deps import TokenStore,make_storage
from feesink.api.handlers_core import (
  handle_delete_endpoint,handle_get_me,handle_get_ui_success,handle_patch_endpoint,
  handle_post_alerts_test,handle_post_endpoints,handle_post_topups_dev,
)
from feesink.api.handlers_stripe import handle_post_stripe_checkout_sessions,handle_post_webhooks_stripe

class FeeSinkApiApp:
  def __init__(self,api_version:str):
    self.api_version=api_version
    self.storage=make_storage()
    self.tokens=TokenStore()
    self.topup_mode=(os.getenv("FEESINK_TOPUP_MODE") or "dev").strip().lower()
    dev_token=os.getenv("FEESINK_DEV_TOKEN","").strip()
    dev_account=os.getenv("FEESINK_DEV_ACCOUNT","demo-user").strip()
    self.storage.ensure_account(dev_account)
    if dev_token:
      self.tokens.link_token(dev_token,dev_account)
      print(f"[DEV] Linked FEESINK_DEV_TOKEN to account_id={dev_account}")
      print(f"[DEV] Success page: http://127.0.0.1:8789/ui/success?token={dev_token}")
    else:
      token=self.tokens.issue_token(dev_account)
      print(f"[DEV] Issued token for account_id={dev_account}: {token}")
      print(f"[DEV] Success page: http://127.0.0.1:8789/ui/success?token={token}")

  def get_token(self,environ):
    return get_bearer_token(environ) or get_query_param(environ,"token")

  def auth_account_id(self,environ):
    token=self.get_token(environ)
    if not token: return None,error(401,"unauthorized","Missing Bearer token")
    account_id=self.tokens.resolve(token)
    if not account_id: return None,error(401,"unauthorized","Invalid token")
    return account_id,None

  def auth_token_and_account(self,environ):
    token=self.get_token(environ)
    if not token: return None,None,error(401,"unauthorized","Missing Bearer token")
    account_id=self.tokens.resolve(token)
    if not account_id: return token,None,error(401,"unauthorized","Invalid token")
    return token,account_id,None

  def __call__(self,environ,start_response):
    setup_testing_defaults(environ)
    t0=time.monotonic()
    method=(environ.get("REQUEST_METHOD") or "GET").upper()
    path=environ.get("PATH_INFO") or "/"
    try:
      if path=="/ui/success" and method=="GET":
        status,headers,body=handle_get_ui_success(self,environ)
      elif path=="/v1/me" and method=="GET":
        status,headers,body=handle_get_me(self,environ)
      elif path=="/v1/topups" and method=="POST":
        status,headers,body=handle_post_topups_dev(self,environ)
      elif path=="/v1/endpoints" and method=="POST":
        status,headers,body=handle_post_endpoints(self,environ)
      elif path=="/v1/stripe/checkout_sessions" and method=="POST":
        status,headers,body=handle_post_stripe_checkout_sessions(self,environ)
      else:
        m=re.match(r"^/v1/endpoints/([^/]+)$",path)
        if m and method=="PATCH":
          status,headers,body=handle_patch_endpoint(self,environ,m.group(1))
        elif m and method=="DELETE":
          status,headers,body=handle_delete_endpoint(self,environ,m.group(1))
        elif path=="/v1/alerts/test" and method=="POST":
          status,headers,body=handle_post_alerts_test(self,environ)
        elif path=="/v1/webhooks/stripe" and method=="POST":
          status,headers,body=handle_post_webhooks_stripe(self,environ)
        elif path=="/healthz" and method=="GET":
          status,headers,body=json_response(200,{"ok":True,"ts":utc_iso(datetime.now(tz=UTC)),"version":self.api_version})
        else:
          status,headers,body=error(404,"not_found","Route not found")
    except Exception as ex:
      status,headers,body=error(500,"internal_error","Unhandled error",{"exception":type(ex).__name__})
    duration_ms=int((time.monotonic()-t0)*1000)
    print(json.dumps({"type":"api_request","ts":utc_iso(datetime.now(tz=UTC)),"api":self.api_version,
                      "method":method,"path":path,"status":status,"duration_ms":duration_ms},
                     ensure_ascii=False,separators=(",",":")))
    start_response(f"{status} OK",headers)
    return [body]
"""
    app_path = root / "feesink/api/app.py"
    if app_path.exists():
        backup = app_path.with_suffix(app_path.suffix + f".bak.{ts}")
        backup.write_text(app_path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        print("BACKUP=", str(backup))
    touched.append(("feesink/api/app.py", _write(app_path, app_py)))

    _banner(root, touched)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
