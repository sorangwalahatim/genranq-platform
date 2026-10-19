"""Write docs/TRACEABILITY.md from the active workbook: python -m app.spec.export"""
from pathlib import Path

from . import traceability
from .parser import parse_workbook


def main():
    s = parse_workbook()
    mods = traceability.build_map(s)
    cov = traceability.coverage(s)
    L = ["# Requirement traceability", "",
         "Generated from `Genranq_Build_Plan.xlsx` (all nine sheets). Regenerate with `python -m app.spec.export`.", "",
         f"Modules: {cov['modules']} · live {cov['by_status']['live']} · partial {cov['by_status']['partial']} · planned {cov['by_status']['planned']}  ",
         f"Unmapped backlog features: {cov['features_unmapped'] or 'none'} · unmapped integrations: {cov['integrations_unmapped'] or 'none'}", ""]
    for ph in s["roadmap"]["phases"]:
        L += [f"## {ph['name']} — {ph['focus']} ({ph['weeks']} weeks, depends on {ph['depends_on']})", "", ph["deliverables"], "",
              "| # | Module | Pri | Status | Features | Tables | Integrations | Jobs | Route |", "|---|---|---|---|---|---|---|---|---|"]
        for m in [x for x in mods if x["phase"] == ph["phase"]]:
            L.append(f"| {m['num']} | {m['name']} | {m['priority']} | **{m['status']}**{' — ' + m['gap'] if m['gap'] else ''} | "
                     f"{', '.join(f['id'] for f in m['features']) or '—'} | {', '.join(m['tables']) or '—'} | "
                     f"{', '.join(m['integrations']) or '—'} | {', '.join(m['jobs']) or '—'} | {m['route'] or '/m/' + str(m['num'])} |")
        L.append("")
    L += ["## Audit checklist → detector", "", "| Code | Category | Check | Source | Auto | Severity | Owner |", "|---|---|---|---|---|---|---|"]
    for c in s["audit_checklist"]["checks"]:
        L.append(f"| {c['code']} | {c['category']} | {c['check']} | {c['data_source']} | {c['auto']} | {c['severity']} | {c['owner']} |")
    L += ["", "Every code above has an executable detector in `backend/app/services/checks.py` (verified by `tests/test_spec.py`).", "",
          "## Integrations → connector", "", "| Provider | Category | Auth | Phase | Connector key |", "|---|---|---|---|---|"]
    from ..services.integrations import BY_SPEC
    for i in s["integrations"]:
        L.append(f"| {i['provider']} | {i['category']} | {i['auth']} | {i['phase']} | `{BY_SPEC[i['provider']].key}` |")
    out = Path(__file__).resolve().parents[3] / "docs" / "TRACEABILITY.md"
    out.write_text("\n".join(L) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
