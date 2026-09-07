# שלד סוכני ה-AI (CrewAI)

מימוש ריצה-ראשונה של 4 מתוך 5 הסוכנים המתוארים ב-[`../docs/01-agents-architecture.md`](../docs/01-agents-architecture.md), כ-crew אחד שרץ ברצף שבועי: **מחקר → תוכן → מייל → דוח אישור**.

**סוכן שירות הלקוחות (`support_agent`) מוגדר ב-`agents.py` אך לא משובץ ב-crew הזה בכוונה** — הוא אירוע-מונע (רץ פעם אחת לכל פנייה נכנסת), ולכן במימוש מלא הוא מופעל ע"י n8n בנפרד לכל הודעת לקוח, לא כחלק ממחזור התכנון השבועי.

## הרצה ראשונה

```bash
cd dropshipping-ai-store/agents
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10+
pip install -r requirements.txt
cp .env.example .env   # ולמלא לפחות ANTHROPIC_API_KEY אחד
python main.py
```

בלי חיבור אמיתי ל-Shopify/לספקים, כל הכלים ב-`tools.py` מחזירים נתוני **STUB** ברורים — כך שהקוד רץ מקצה-לקצה כבר עכשיו ומדגים את מבנה שיתוף הפעולה בין הסוכנים, אבל אין להסתמך על התוצאה עד שמחברים API אמיתיים (ראו הערות `STUB`/`TODO` בתוך `tools.py`).

## מבנה

| קובץ | תוכן |
|---|---|
| `agents.py` | הגדרת 5 הסוכנים (Agent) + חיבור ה-LLM |
| `tools.py` | הכלים שהסוכנים משתמשים בהם — כרגע stub, מוכנים להחלפה ב-API אמיתי |
| `tasks.py` | המשימות (Task) של מחזור התכנון השבועי |
| `main.py` | הרכבת ה-Crew והרצתו |

## איך זה מתחבר ל-n8n בפועל

השלד הזה עומד בפני עצמו (אפשר להריץ `python main.py` ישירות), אבל בארכיטקטורה המלאה n8n הוא זה שמפעיל אותו: או ע"י הרצת הסקריפט כתהליך מתוזמן (node מסוג Execute Command / Schedule Trigger), או ע"י עטיפתו ב-endpoint קטן (FastAPI/Flask) שמופעל דרך HTTP Request node. את `support_agent` מפעילים באותה צורה, אבל מתוך Webhook node שמקבל הודעת לקוח נכנסת, לא מהתזמון השבועי.

## עדכון גרסאות

`crewai` היא ספרייה שמתפתחת מהר. אם `pip install` נכשל על חוסר תאימות גרסה, בדקו את ה-quickstart הרשמי בכתובת https://docs.crewai.com/en/quickstart לתחביר העדכני ביותר של `Agent`/`Task`/`Crew`.
