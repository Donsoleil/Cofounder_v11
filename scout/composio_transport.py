"""Composio transport for the evidence stage. Runs inside Composio's remote workbench.

Maps the two calls the pipeline makes (a SerpApi-style Maps or reviews call, and a Jev evaluation) onto Composio tools,
using the accounts you connected at Composio. No API key is ever seen by this code. Tool slugs and output shapes come
from Composio's own schemas (checked 2026-10-06):

  SERPAPI_GOOGLE_MAPS_SEARCH        in {q, ll, start}                  out data.results.{place_results|local_results}  (assumed like the next one)
  COMPOSIO_SEARCH_GOOGLE_MAPS       in {q, ll, start}                  out data.results.{place_results|local_results}  (no connection needed)
  SERPAPI_LIST_GOOGLE_MAPS_REVIEWS  in {place_id|data_id, sort_by, next_page_token, review_count 1-20, language}
                                    out data.{reviews, place_info, serpapi_pagination, ...}
  JEV_EVALUATE_STATE                in {model, state, questions}       out data.{model, answers, usage}

The raw response saved for each model call is the JSON that Composio returned (the tool's data), not the HTTP bytes.
"""
import json
import time

CONNECT_WORDS = ("no active connection", "not connected", "connection", "unauthorized", "authentication", "invalid api key")


def find(obj, *keys):
    """First dict (searching nested dicts and lists) that holds any of keys."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if any(k in o for k in keys):
                return o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return None


def install(lead, run_composio_tool, place_tool="SERPAPI_GOOGLE_MAPS_SEARCH"):
    """Replace lead.serp_get and lead.jev_post. place_tool may be COMPOSIO_SEARCH_GOOGLE_MAPS if SerpApi is not connected."""

    def call(slug, args, tries=4):
        last = ""
        for i in range(tries):
            res, err = run_composio_tool(slug, args)
            ok = not err and isinstance(res, dict) and res.get("successful", True) and not res.get("error")
            if ok:
                return res
            last = str(err or (res or {}).get("error") or "tool call failed")
            low = last.lower()
            if any(w in low for w in CONNECT_WORDS):
                raise lead.Fatal(f"{slug}: {last[:200]} (connect the account at Composio)")
            if any(w in low for w in ("rate", "429", "timeout", "timed out", "overload", "temporar")):
                time.sleep(2 ** i)
                continue
            break
        raise RuntimeError(f"{slug}: {last[:300]}")

    def serp_get(params):
        eng = params["engine"]
        if eng == "google_maps":
            res = call(place_tool, {"q": params["q"]})
            body = find(res, "place_results", "local_results") or {}
            out = {k: body[k] for k in ("place_results", "local_results") if k in body}
            if not out and (body.get("error") or "no results" in json.dumps(body)[:200].lower()):
                out = {"error": "Google hasn't returned any results for this query."}
            credit = place_tool.startswith("SERPAPI_")
        elif eng == "google_maps_reviews":
            args = {k: params[k] for k in ("place_id", "data_id", "sort_by", "next_page_token") if k in params}
            if "num" in params:
                args["review_count"] = params["num"]
            if "hl" in params:
                args["language"] = params["hl"]
            res = call("SERPAPI_LIST_GOOGLE_MAPS_REVIEWS", args)
            out = find(res, "reviews", "place_info") or (res.get("data") or {})
            if out.get("error"):
                raise RuntimeError("SerpApi reviews error: " + str(out["error"])[:200])
            credit = True
        else:
            raise lead.Fatal(f"no Composio mapping for engine {eng}")
        with lead._lock:
            lead.SPEND["serp_searches"] += 1 if credit else 0
        lead.jl_append(lead.LP("collector-log.jsonl"), dict(ts=lead.now(), via="composio", params=params, ok=True))
        return out

    def jev_post(payload):
        try:
            res = call("JEV_EVALUATE_STATE", {"model": payload["model"], "state": payload["state"], "questions": payload["questions"]})
        except lead.Fatal:
            raise
        except RuntimeError as e:
            return 422, json.dumps({"error": str(e)})
        body = find(res, "answers")
        if not body:
            return 422, json.dumps({"error": "no answers in Composio response"})
        return 200, json.dumps({"model": body.get("model", ""), "answers": body["answers"], "usage": body.get("usage", {})})

    lead.serp_get, lead.jev_post = serp_get, jev_post
    return serp_get, jev_post
