"""Web UI for sapar-radar - runs the same pipeline as the CLI, from a browser.

Deploy for free on Streamlit Community Cloud (share.streamlit.io) and use
it from a phone: no terminal, no Python install on the phone, just a URL.
See INSTALL.md for the deployment steps.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sapar_radar.config import Config, ConfigError  # noqa: E402
from sapar_radar.export import COLUMNS  # noqa: E402
from sapar_radar.models import VERDICT_LABELS_HE  # noqa: E402
from sapar_radar.pipeline import Pipeline  # noqa: E402
from sapar_radar.providers import GooglePlacesProvider, OSMProvider  # noqa: E402
from sapar_radar.providers.osm import OverpassUnavailable  # noqa: E402
from sapar_radar.store import CONTACT_STATUSES, Store  # noqa: E402
from sapar_radar.website_probe import WebsiteProbe  # noqa: E402

DB_PATH = Path(__file__).resolve().parent / "out" / "webapp.db"

#: Hebrew labels + usage hint for each contact_status value (kept in English
#: in the database so the CLI's `mark` command stays compatible).
STATUS_INFO_HE = {
    "new": ("חדש", "עוד לא טיפלת בכלל"),
    "reported": ("דווח", "ברירת המחדל - הופיע ברשימה, עוד לא התקשרת"),
    "contacted": ("יצרת קשר", "התקשרת אליו"),
    "interested": ("מעוניין", "הביע עניין בשירות שלך"),
    "not_interested": ("לא מעוניין", "סירב, או ביקש לא להתקשר שוב"),
    "customer": ("לקוח", "סגרת איתו עסקה"),
}


def _status_label(status: str) -> str:
    he, hint = STATUS_INFO_HE.get(status, (status, ""))
    return f"{he} - {hint}" if hint else he


#: Why a discovered shop did not become a lead - matches Pipeline's skip reasons.
SKIP_LABELS_HE = {
    "duplicate_place_id": "כפילות (אותה מספרה נמצאה פעמיים בחיפוש)",
    "duplicate_phone": "כפילות (אותו טלפון כבר נמצא)",
    "closed": "העסק סגור",
    "no_phone": "אין מספר טלפון",
    "do_not_contact": "ברשימת 'לא ליצור קשר'",
    "too_few_reviews": "מעט מדי ביקורות",
    "already_reported": "כבר הופיע בחיפוש קודם",
    "has_booking": "כבר יש מערכת תורים",
    "below_min_score": "ציון נמוך מהסף שהוגדר",
}

st.set_page_config(page_title="רדאר מספרות", page_icon="📞", layout="centered")

try:
    _google_key = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
except Exception:
    _google_key = ""
if _google_key:
    os.environ.setdefault("GOOGLE_MAPS_API_KEY", _google_key)

st.markdown(
    """
    <style>
    html, body, [class*="css"] { direction: rtl; text-align: right; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📞 רדאר מספרות")
st.caption("מוצא מספרות ללא אתר או מערכת תורים, ומחזיר טלפונים ליצירת קשר.")

if "leads" not in st.session_state:
    st.session_state.leads = []
if "status_overrides" not in st.session_state:
    st.session_state.status_overrides = {}

tab_search, tab_history = st.tabs(["🔍 חיפוש חדש", "📋 כל הלידים"])

# ---------------------------------------------------------------- search --
with tab_search:
    provider_choice = st.radio(
        "מקור החיפוש",
        ["OpenStreetMap (חינם, בלי מפתח)", "Google Places (מדויק יותר, צריך מפתח)"],
        index=0,
    )
    uses_google = provider_choice.startswith("Google")

    if uses_google and not _google_key:
        st.warning(
            "אין מפתח Google מוגדר בהגדרות האתר (Secrets). "
            "החיפוש יעבור אוטומטית ל-OpenStreetMap."
        )
        uses_google = False

    area_input = st.text_input(
        "עיר / אזור לחיפוש (אפשר כמה, מופרדות בפסיק)", value="תל אביב"
    )
    col1, col2 = st.columns(2)
    limit = col1.number_input("כמה תוצאות מקסימום", min_value=1, max_value=200, value=20)
    min_score = col2.slider("ציון מינימלי לליד", min_value=0, max_value=100, value=60, step=5)
    probe = st.checkbox(
        "בדוק את האתר של כל מספרה (מדויק יותר, קצת יותר איטי)", value=True
    )
    skip_seen = st.checkbox("דלג על מספרות שכבר הופיעו בחיפוש קודם", value=True)

    if st.button("🔍 חפש עכשיו", type="primary", width='stretch'):
        areas = [a.strip() for a in area_input.split(",") if a.strip()]
        if not areas:
            st.error("צריך לפחות עיר אחת.")
        else:
            try:
                config = Config.load()
            except ConfigError as exc:
                st.error(f"שגיאת הגדרות: {exc}")
                config = None

            if config is not None:
                config.raw.setdefault("search", {})["areas"] = areas
                config.raw.setdefault("filters", {})["min_score"] = int(min_score)
                config.raw.setdefault("filters", {})["skip_already_reported"] = skip_seen
                config.raw.setdefault("classification", {})["probe_websites"] = probe

                discovery = (
                    GooglePlacesProvider(api_key=_google_key)
                    if uses_google
                    else OSMProvider()
                )
                website_probe = WebsiteProbe() if probe else None
                store = Store(DB_PATH)
                pipeline = Pipeline(config, discovery, None, store, website_probe)

                leads = None
                with st.spinner("מחפש… זה יכול לקחת דקה."):
                    run_id = store.start_run(discovery.name)
                    try:
                        leads = pipeline.run(limit=int(limit))
                    except OverpassUnavailable:
                        st.error(
                            "שירות OpenStreetMap עמוס כרגע ולא הגיב בזמן. זה "
                            "קורה מדי פעם בשירות החינמי - נסה שוב בעוד דקה, "
                            "או נסה עיר עם שטח קטן יותר."
                        )
                    except Exception as exc:
                        st.error(f"קרתה שגיאה בלתי צפויה בזמן החיפוש: {exc}")
                    finally:
                        if website_probe:
                            website_probe.close()
                        if hasattr(discovery, "close"):
                            discovery.close()
                        if leads is not None:
                            store.finish_run(
                                run_id, pipeline.stats.discovered, pipeline.stats.leads
                            )
                        store.close()

                if leads is not None:
                    st.session_state.leads = leads
                    st.session_state.status_overrides = {}
                    st.session_state.last_stats = pipeline.stats

                    if leads:
                        st.success(f"נמצאו {len(leads)} לידים חדשים!")
                    else:
                        st.info(
                            "לא נמצאו לידים חדשים. נסה עיר אחרת, הורד את הציון "
                            "המינימלי, או בטל את 'דלג על מספרות שכבר הופיעו'."
                        )

    stats = st.session_state.get("last_stats")
    if stats is not None:
        with st.expander("פרטים טכניים על החיפוש האחרון"):
            st.write(f"נמצאו בסך הכל: {stats.discovered}")
            st.write(f"מתוכן ייחודיות (לא כפולות): {stats.unique}")
            st.write(f"הפכו לליד: {stats.leads}")
            if stats.skipped:
                st.write("למה השאר לא הפכו לליד:")
                for reason, count in sorted(stats.skipped.items(), key=lambda kv: -kv[1]):
                    st.write(f"  • {SKIP_LABELS_HE.get(reason, reason)}: {count}")

    leads = st.session_state.leads
    if leads:
        st.divider()
        st.subheader(f"תוצאות ({len(leads)})")

        with st.expander("מתי משתמשים בכל סטטוס?"):
            for status in CONTACT_STATUSES:
                he, hint = STATUS_INFO_HE.get(status, (status, ""))
                st.markdown(f"**{he}** — {hint}")

        buffer = io.StringIO()
        import csv as csv_module

        writer = csv_module.DictWriter(buffer, fieldnames=COLUMNS)
        writer.writeheader()
        for lead in leads:
            writer.writerow(lead.to_row())
        st.download_button(
            "⬇️ הורד כ-CSV (לאקסל)",
            data=buffer.getvalue().encode("utf-8-sig"),
            file_name="leads.csv",
            mime="text/csv",
            width='stretch',
        )

        for lead in leads:
            p = lead.place
            phone = p.phone_e164 or ""
            rating = f"⭐ {p.rating} ({p.review_count})" if p.rating else "ללא דירוג"
            with st.container(border=True):
                st.markdown(f"**{p.name}** — {VERDICT_LABELS_HE[lead.verdict]} [{lead.score}]")
                st.write(f"{p.address} | {rating}")
                if phone:
                    st.markdown(f"📞 [{p.phone}](tel:{phone})")
                if p.website:
                    st.markdown(f"🔗 [{p.website}]({p.website})")
                if p.maps_url:
                    st.markdown(f"🗺️ [פתח במפות]({p.maps_url})")

                current = st.session_state.status_overrides.get(p.place_id, "reported")
                mcol1, mcol2 = st.columns([3, 1])
                new_status = mcol1.selectbox(
                    "סטטוס יצירת קשר",
                    CONTACT_STATUSES,
                    index=CONTACT_STATUSES.index(current),
                    format_func=_status_label,
                    key=f"status_{p.place_id}",
                    label_visibility="collapsed",
                )
                if mcol2.button("עדכן", key=f"update_{p.place_id}"):
                    store = Store(DB_PATH)
                    updated = store.mark(phone or p.place_id, new_status)
                    store.close()
                    st.session_state.status_overrides[p.place_id] = new_status
                    if updated:
                        st.toast(f"עודכן ל: {STATUS_INFO_HE.get(new_status, (new_status,))[0]}")

# --------------------------------------------------------------- history --
with tab_history:
    store = Store(DB_PATH)
    stats = store.stats()
    rows = store.reported_rows(limit=500)
    store.close()

    if not rows:
        st.info("עדיין לא נצברו לידים. חפש קודם בלשונית 'חיפוש חדש'.")
    else:
        st.subheader(f"סה\"כ {stats.get('total', 0)} לידים במאגר")
        cols = st.columns(4)
        for i, status in enumerate(CONTACT_STATUSES):
            label = STATUS_INFO_HE.get(status, (status,))[0]
            cols[i % 4].metric(label, stats.get(status, 0))

        st.divider()
        table = [
            {
                "שם": r["name"],
                "טלפון": r["phone_e164"],
                "עיר": r["city"],
                "סטטוס": STATUS_INFO_HE.get(r["contact_status"], (r["contact_status"],))[0],
                "ציון": r["score"],
                "סיווג": VERDICT_LABELS_HE.get(r["verdict"], r["verdict"]),
            }
            for r in rows
        ]
        st.dataframe(table, width='stretch', hide_index=True)
