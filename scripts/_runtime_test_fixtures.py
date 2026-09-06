"""Synthetic source observations for tests ONLY. Not browser evidence."""
from source_evidence import make_runtime_evidence

TARGETS = {
    "weibo": "https://s.weibo.com/top/summary", "zhihu": "https://www.zhihu.com/hot",
    "bilibili": "https://www.bilibili.com/blackboard/activity-trending-topic.html?navhide=1&plat_id=124",
    "baidu": "https://top.baidu.com/board?tab=realtime", "douyin": "https://www.iesdouyin.com/share/billboard/?id=0",
    "jiemian": "https://www.jiemian.com/", "bjnews": "https://www.bjnews.com.cn/", "caixin": "https://www.caixin.com/",
}


def fixture_context(url):
    return {"targetUrl": url, "topLevelUrl": url, "documentUrl": url, "isMainDocument": True}


def fixture_observation(timestamp, target, indices):
    return {"timestampMs": timestamp, "context": fixture_context(target), "itemIndices": list(indices),
            "loading": False, "unresolvedCount": 0,
            "readinessEvidence": {"observedAtMs": timestamp, "pendingState": "none",
                                  "basis": "explicit_candidate_load_complete", "detail": "Synthetic completed candidate fixture"},
            "orderEvidence": {"observedAtMs": timestamp, "basis": "full_dom_order", "detail": "Synthetic page order fixture", "itemIndices": list(indices)}}


def fixture_trace(source, rows, run_id="fixture-run", date="2026-08-06", captured_at=None, token="fixture-token"):
    indices = list(range(len(rows)))
    trace = {"trace_schema": 1, "source": source, "run_id": run_id, "token": token,
             "captured_at": captured_at or f"{date}T12:01:00+08:00"}
    if source in {"jiemian", "bjnews", "caixin"}:
        observations = []
        for timestamp, phase in [(0, "sample"), (2000, "scroll_round"), (3800, "scroll_round"), (15800, "final_recheck")]:
            observation = fixture_observation(timestamp, TARGETS[source], indices)
            observation.update({"phase": phase, "geometry": {"pageHeight": 1000, "scrollY": 500, "viewportHeight": 500},
                                "footerReached": True, "lastRelevantMutationAtMs": 0})
            if phase == "scroll_round":
                observation.update({"roundId": f"fixture-{timestamp}", "roundStartedAtMs": timestamp - 1600})
            observations.append(observation)
        trace.update({"kind": "homepage", "observations": observations})
    elif source == "people":
        url = f"https://paper.people.com.cn/rmrb/pc/layout/{date[:7].replace('-', '')}/{date[8:]}/node_01.html"
        observation = fixture_observation(1000, url, indices)
        observation.update({"pageNumber": 1, "pageEnd": {"reached": True, "detail": "Synthetic full page end"}})
        trace.update({"kind": "current_issue", "issue": {"date": date, "totalPages": 1, "pageUrls": [url],
                      "indexContext": fixture_context(url), "indexEvidence": "Synthetic current issue directory"}, "observations": [observation]})
    else:
        observation = fixture_observation(1000, TARGETS[source], indices)
        observation["listEnd"] = {"reached": True, "detail": "Synthetic fixed list tail"}
        trace.update({"kind": "fixed_hotlist", "observations": [observation]})
    return trace


def fixture_evidence(source, rows, run_id="fixture-run", date="2026-08-06"):
    trace = fixture_trace(source, rows, run_id, date)
    return make_runtime_evidence(trace, source, run_id, rows, f"{date}T12:00:00+08:00", run_date=date)
